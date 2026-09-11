/**
 * Pi agent session execution for one audit case (port of experiment runner.ts v7).
 *
 * Per case: budget reset → createAgentSession → subscribe (trace + read gate
 * counters) → prompt → read=0 gates → freeze post-read verdict → citation
 * protocol gate (restore that frozen verdict, not the first raw) → Host fills
 * evidence.text from readBodies → return result + stats + trace.
 * Host never rewrites the agent verdict. raw_verdict in stats stays first raw.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
	createAgentSession,
	DefaultResourceLoader,
	ModelRuntime,
	SessionManager,
} from "@earendil-works/pi-coding-agent";

import { createAuditTools } from "./tools.ts";
import { createEvidenceProgress, normalizeChunkId } from "./progress.ts";
import { systemPrompt } from "./prompt.ts";
import { parseVerdictDetailed } from "./parse.ts";
import { formatFirstRoundCards } from "./preview.ts";
import { applyEvidenceClosure, closeEvidence } from "./closure.ts";
import { recordAssistantMessageUsage, type ExecutionIdentity } from "./usage.ts";

// 工具调用硬预算：每个 case 一个全新 loader（extensionFactories 闭包随之独立），
// 预算计数是 case 内局部变量，天然无跨 session 泄漏；并发 case 互不干扰。
function defaultToolBudget(): number {
	return Number(process.env.PI_MAX_TOOL_CALLS ?? "12");
}

export type AgentCaseInput = {
	case_id: string;
	/** Observability / metering only. Never interpolated into the agent prompt. */
	job_id?: string | null;
	run_id?: string | null;
	job_attempt?: number | null;
	sample_context?: Record<string, unknown> | null;
	test_item?: Record<string, unknown> | null;
	reported_requirement?: Record<string, unknown> | null;
	/** Default file_ids for search_standards (assistant-bound KB scope); [] = unrestricted. */
	file_scope?: string[] | null;
	/** Current inspection report; binds search_report_context. Not shown in the prompt. */
	report_file_id?: string | null;
	/** First-recall locator cards. Agent reads these first; may still search if they are not enough. */
	retrieved_candidates?: Array<Record<string, unknown>> | null;
	/** Production query used for the Python first-round hybrid search. */
	production_query?: string | null;
	tool_budget?: number | null;
};

export type AgentCaseOutcome = {
	result: Record<string, unknown> | null;
	parse_mode: "strict" | "loose" | "prose" | "none";
	stats: {
		tool_calls: number;
		search_calls: number;
		read_chunks: number;
		report_search_calls: number;
		turns: number;
		gate_reprompt: boolean;
		duration_ms: number;
		zero_new_searches: number;
		search_blocked: boolean;
		first_round_hit_used: boolean | null;
		read_before_search: boolean | null;
		raw_verdict: unknown;
		raw_kind: unknown;
		closure: "passed" | "failed" | "skipped";
		closure_mode: string | null;
		closure_reason: string | null;
		protocol_error: boolean;
	};
	trace_file: string | null;
	final_text_preview: string;
};

// ---- model runtime (module-level, created once) ----------------------------

let modelRuntimePromise: Promise<{ runtime: any; model: any; modelId: string }> | null = null;

function apiKey(): string {
	const key =
		process.env.PI_API_KEY ||
		process.env.ZHIPU_API_KEY ||
		process.env.DASHSCOPE_API_KEY ||
		process.env.DEEPSEEK_API_KEY;
	if (!key) throw new Error("缺少 API key：设置 PI_API_KEY / ZHIPU_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY");
	return key;
}

function defaultModelId(): string {
	return process.env.PI_MODEL || process.env.DASHSCOPE_MODEL || "glm-5.3-flash";
}

function defaultBaseUrl(): string {
	return (
		process.env.PI_BASE_URL ||
		process.env.DASHSCOPE_BASE_URL ||
		"https://open.bigmodel.cn/api/paas/v4"
	).replace(/\/+$/, "");
}

function usesDashscopeThinking(baseUrl: string, modelId: string): boolean {
	return /dashscope/i.test(baseUrl) || /^qwen/i.test(modelId) || /^zhipu\//i.test(modelId);
}

/** GLM-5.3 cannot disable thinking. Official API has low/high/max (no medium). */
export function resolveThinkingLevel(modelId: string, requested?: string): string {
	const level = (requested || process.env.PI_THINKING_LEVEL || "low").toLowerCase();
	const glmAlwaysOn = /glm-5\.3/i.test(modelId);
	if (!glmAlwaysOn) return level;
	if (level === "off" || level === "minimal") return "low";
	if (level === "medium") return "high";
	return level;
}

export async function getModel(): Promise<{ runtime: any; model: any; modelId: string }> {
	if (!modelRuntimePromise) {
		modelRuntimePromise = (async () => {
			const modelId = defaultModelId();
			const baseUrl = defaultBaseUrl();
			const providerId = process.env.PI_PROVIDER || "zhipu";
			const runtime = await ModelRuntime.create({
				modelsPath: null,
				refreshOnCreate: false,
				allowModelNetwork: false,
			});
			runtime.registerProvider(providerId, {
				name: providerId,
				baseUrl,
				api: "openai-completions",
				models: [
					{
						id: modelId,
						name: modelId,
						reasoning: true,
						input: ["text"],
						cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
						contextWindow: 1048576,
						maxTokens: 16384,
						// 智谱官方：thinking: { type: enabled }（Pi thinkingFormat=zai）。
						// 百炼 Qwen/ZHIPU/*：enable_thinking + reasoning_effort。
						compat: {
							thinkingFormat: usesDashscopeThinking(baseUrl, modelId) ? "qwen" : "zai",
							supportsReasoningEffort: true,
							supportsDeveloperRole: false,
						},
					},
				],
			});
			const model = runtime.getModel(providerId, modelId);
			if (!model) throw new Error(`注册 provider 后未找到模型 ${providerId}/${modelId}`);
			runtime.setRuntimeApiKey(model.provider, apiKey());
			return { runtime, model, modelId };
		})();
	}
	return modelRuntimePromise;
}

// ---- per-case session execution --------------------------------------------

function toolResultText(result: unknown): string {
	if (!result || typeof result !== "object") return "";
	const content = (result as { content?: unknown }).content;
	if (!Array.isArray(content)) return "";
	return content
		.map((block) => {
			if (typeof block === "string") return block;
			if (block && typeof block === "object" && "text" in block) {
				return String((block as { text?: unknown }).text || "");
			}
			return "";
		})
		.join("\n");
}

function isMatchMismatch(result: Record<string, unknown> | null | undefined): boolean {
	const verdict = String(result?.verdict || "");
	return verdict === "match" || verdict === "mismatch";
}

function evidenceChunkIds(result: Record<string, unknown> | null): string[] {
	const evidence = result && Array.isArray(result.evidence) ? result.evidence : [];
	const ids: string[] = [];
	for (const item of evidence) {
		if (!item || typeof item !== "object") continue;
		const id = normalizeChunkId((item as Record<string, unknown>).chunk_id);
		if (id && !id.includes("placeholder")) ids.push(id);
	}
	return ids;
}

function firstRoundQuery(c: AgentCaseInput): string {
	const explicit = String(c.production_query || "").trim();
	if (explicit) return explicit;
	const reported =
		c.reported_requirement && typeof c.reported_requirement === "object"
			? String((c.reported_requirement as Record<string, unknown>).text || "").trim()
			: "";
	const project =
		c.test_item && typeof c.test_item === "object"
			? String((c.test_item as Record<string, unknown>).project_name || "").trim()
			: "";
	return [project, reported].filter(Boolean).join(" ");
}

function casePrompt(c: AgentCaseInput): string {
	const ctx = c.sample_context ?? {};
	const retrieved = Array.isArray(c.retrieved_candidates) ? c.retrieved_candidates : [];
	const retrievedBlock =
		retrieved.length > 0 ? `\n${formatFirstRoundCards(retrieved, firstRoundQuery(c))}` : "";
	const closer =
		retrieved.length > 0
			? `请先阅读第一轮候选，核实该声称值对应的标准限值；不够再检索。按任务定义给出的 JSON 格式输出判定。`
			: `请检索标准知识库，核实该声称值是否与适用标准的限值一致，并按任务定义给出的 JSON 格式输出判定。`;
	return [
		`请审查以下检测报告标准值。`,
		``,
		`case_id: ${c.case_id}`,
		``,
		`## 产品型号参数`,
		JSON.stringify(ctx, null, 2),
		``,
		`## 试验项目`,
		JSON.stringify(c.test_item ?? {}, null, 2),
		``,
		`## 报告声称值`,
		JSON.stringify(c.reported_requirement ?? {}, null, 2),
		retrievedBlock,
		``,
		closer,
	].join("\n");
}

const TRACE_TYPES = new Set([
	"agent_start",
	"agent_end",
	"agent_settled",
	"turn_start",
	"turn_end",
	"tool_call",
	"tool_execution_start",
	"tool_execution_update",
	"tool_execution_end",
	"tool_result",
	"message_end",
	"model_select",
	"context",
]);

/** Race a promise against a deadline, clearing the timer when either side settles. */
export function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
	let timer: NodeJS.Timeout | undefined;
	const timeout = new Promise<never>((_, reject) => {
		timer = setTimeout(() => reject(new Error(message)), ms);
		timer.unref?.();
	});
	return Promise.race([promise, timeout]).finally(() => {
		if (timer) clearTimeout(timer);
	});
}

function strip(event: any): any {
	if (typeof event !== "object" || event === null) return {};
	const out: Record<string, unknown> = {};
	for (const k of ["toolName", "toolCallId", "isError"]) {
		if (k in event) out[k] = event[k];
	}
	for (const k of ["args", "input", "result", "partialResult"]) {
		if (k in event && event[k] !== undefined) {
			out[k] = truncateValue(event[k], 2000);
		}
	}
	// message 只保留摘要字段（GLM thinking 内容极大，整包会把 trace 撑到几百 MB）。
	if ("message" in event && event.message && typeof event.message === "object") {
		const m = event.message as Record<string, unknown>;
		out.message = {
			role: m.role,
			stopReason: m.stopReason,
			model: m.model,
			provider: m.provider,
			errorMessage: m.errorMessage,
			finalText: summarizeMessageText(m.content),
		};
	}
	return out;
}

function truncateValue(v: unknown, max: number): unknown {
	if (typeof v === "string") {
		if (v.length <= max) return v;
		return v.slice(0, max) + "...(+chars " + (v.length - max) + ")";
	}
	if (Array.isArray(v)) return v.slice(0, 10).map((x) => truncateValue(x, max));
	if (v && typeof v === "object") {
		const out: Record<string, unknown> = {};
		for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
			out[k] = truncateValue(x, 800);
		}
		return out;
	}
	return v;
}

function summarizeMessageText(content: unknown): string {
	if (typeof content === "string") return content.slice(0, 2000);
	if (Array.isArray(content)) {
		const parts = (content as unknown[])
			.map((b) => {
				if (typeof b === "string") return b;
				const blk = b as any;
				if (typeof blk?.text === "string" && blk.text.trim()) return blk.text;
				if (blk?.type === "thinking" || typeof blk?.thinking === "string") return "[thinking]";
				return "";
			})
			.filter(Boolean);
		return parts.join(" ").slice(0, 2000);
	}
	return "";
}

/**
 * Run one audit case through a fresh Pi agent session.
 * Throws on transport-level failures (caller decides retryability);
 * returns outcome with result=null when no verdict could be parsed.
 */
export async function runCase(input: AgentCaseInput): Promise<AgentCaseOutcome> {
	const started = Date.now();
	const { model, runtime, modelId } = await getModel();
	const usageIdentity: ExecutionIdentity = {
		job_id: input.job_id,
		run_id: input.run_id,
		job_attempt: typeof input.job_attempt === "number" ? input.job_attempt : Number(input.job_attempt || 0) || null,
		case_id: input.case_id,
	};
	const usagePosts: Promise<unknown>[] = [];

	// Per-case budget (request override wins; else env default).
	let toolCallBudget = defaultToolBudget();
	if (typeof input.tool_budget === "number" && input.tool_budget > 0) {
		toolCallBudget = Math.floor(input.tool_budget);
	}
	const hasFirstRound = Array.isArray(input.retrieved_candidates) && input.retrieved_candidates.length > 0;
	const firstRoundIds = (input.retrieved_candidates ?? [])
		.map((item) => normalizeChunkId(item.chunk_id ?? item.id))
		.filter(Boolean);
	const progress = createEvidenceProgress(firstRoundIds);
	const tools = createAuditTools(
		input.file_scope,
		progress,
		usageIdentity,
		input.report_file_id,
	);

	const trace: any[] = [];
	let finalText = "";
	let readCount = 0;
	let toolCalls = 0;
	let searchCalls = 0;
	let reportSearchCalls = 0;
	let turns = 0;
	let gateReprompt = false;
	let firstToolName: string | null = null;
	const readBodies = new Map<string, string>();
	const pendingReads = new Map<string, string>();

	const loader = new DefaultResourceLoader({
		cwd: process.cwd(),
		agentDir: join(process.env.HOME ?? process.env.USERPROFILE ?? ".", ".pi", "agent"),
		systemPromptOverride: () => systemPrompt({ hasFirstRound }),
		appendSystemPromptOverride: () => [],
		extensionFactories: [
			// 硬预算：超过上限即阻止并让模型直接收尾出判定（省 token/时间）。
			(_pi: any) => {
				(_pi as any).on("tool_call", (_event: any) => {
					if (toolCallBudget > 0) {
						toolCallBudget -= 1;
						return undefined; // 放行
					}
					return {
						block: true,
						reason: `工具调用已达预算上限 ${toolCallBudget} 次，请基于已获取的证据直接输出最终 JSON 判定，不要再调用工具。`,
					};
				});
			},
		],
	});
	await loader.reload();

	const { session } = await createAgentSession({
		model,
		modelRuntime: runtime,
		customTools: tools,
		resourceLoader: loader,
		sessionManager: SessionManager.inMemory(),
		// GLM-5.3 默认 low（官方仅 low/high/max；medium 会映射成 high）。
		thinkingLevel: resolveThinkingLevel(modelId) as any,
	});
	session.setActiveToolsByName(tools.map((t) => t.name));

	session.subscribe((event: any) => {
		// host 端门禁计数：standard_not_found 必须建立在 read_chunk 证据上。
		if (event.type === "tool_execution_start") {
			toolCalls += 1;
			if (!firstToolName && typeof event.toolName === "string") firstToolName = event.toolName;
			if (event.toolName === "read_chunk") {
				readCount += 1;
				const chunkId = normalizeChunkId(event.args?.chunk_id ?? event.input?.chunk_id);
				const callId = String(event.toolCallId || event.callId || `read-${readCount}`);
				if (chunkId) pendingReads.set(callId, chunkId);
			}
			if (event.toolName === "search_standards") searchCalls += 1;
			if (event.toolName === "search_report_context") reportSearchCalls += 1;
		}
		if (event.type === "tool_execution_end" && event.toolName === "read_chunk") {
			const callId = String(event.toolCallId || event.callId || "");
			const chunkId = pendingReads.get(callId) || [...pendingReads.values()].at(-1) || "";
			if (callId) pendingReads.delete(callId);
			else pendingReads.clear();
			const text = toolResultText(event.result);
			if (chunkId && text) readBodies.set(chunkId, text);
		}
		if (event.type === "turn_start") turns += 1;
		if (TRACE_TYPES.has(event.type)) {
			trace.push({ type: event.type, ts: Date.now(), ...strip(event) });
		}
		if (event.type === "message_end" && event.message?.role === "assistant") {
			usagePosts.push(recordAssistantMessageUsage(usageIdentity, event.message));
			const content = event.message.content ?? [];
			const text = (Array.isArray(content) ? content : [content])
				.map((b: any) => (typeof b === "string" ? b : b?.text ?? ""))
				.join("");
			if (text.trim()) {
				finalText = text;
				trace.push({ type: "_final_assistant", ts: Date.now(), text });
			}
		}
	});

	const timeoutMs = Number(process.env.AGENT_CASE_TIMEOUT_MS ?? "420000");
	let promptError: unknown = undefined;
	try {
		await withTimeout(
			session.prompt(casePrompt(input)),
			timeoutMs,
			`case timeout after ${timeoutMs}ms`,
		);
		await new Promise<void>((r) => setTimeout(r, 500));
	} catch (err) {
		promptError = err instanceof Error ? err.message : String(err);
	}

	let parsed = parseVerdictDetailed(finalText);
	const firstRawVerdict = parsed.value?.verdict;
	const firstRawKind = parsed.value?.kind;

	async function repromptOnce(message: string, label: string): Promise<void> {
		gateReprompt = true;
		try {
			await withTimeout(session.prompt(message), 120000, `${label} timeout`);
			await new Promise<void>((r) => setTimeout(r, 500));
		} catch (err) {
			promptError = promptError ?? (err instanceof Error ? err.message : String(err));
		}
		parsed = parseVerdictDetailed(finalText);
	}

	// host 端门禁：standard_not_found 但从未 read_chunk → 追加一次重判提示。
	if (
		!promptError &&
		parsed.value?.verdict === "unevaluable" &&
		parsed.value?.kind === "standard_not_found" &&
		readCount === 0
	) {
		await repromptOnce(
			hasFirstRound
				? `你给出了 unevaluable + standard_not_found，但本会话从未用 read_chunk 读取任何片段的完整原文。` +
					`请先对第一轮最相关候选执行 read_chunk；若读完仍不够，再 search_standards 并对后继命中 read_chunk 后重新输出最终 JSON 判定。` +
					`定位预览里没有不等于条款不存在。若 read 后仍无法定位条款，可维持 unevaluable，但 reasoning 须列出已读片段与已尝试的检索式。`
				: `你给出了 unevaluable + standard_not_found，但本会话从未用 read_chunk 读取任何命中片段的完整原文。` +
					`search_standards 只返回定位预览，预览里没有不等于条款不存在。` +
					`请对最相关的命中执行 read_chunk 核实原文后重新输出最终 JSON 判定；若 read 后仍无法定位条款，可维持 unevaluable，但 reasoning 须列出已读片段与已尝试的检索式。`,
			"gate re-prompt",
		);
	}

	// match/mismatch 必须至少 read 过一次；允许读完后重新判定。
	if (!promptError && isMatchMismatch(parsed.value) && readCount === 0) {
		await repromptOnce(
			`你给出了 match/mismatch，但本会话从未用 read_chunk 读取任何标准原文。` +
				`请先 read_chunk 核实标准原文后再判定。`,
			"read gate re-prompt",
		);
	}

	// 引用协议只锁 read 之后的判定，不锁第一次 raw。
	const postReadVerdict = parsed.value?.verdict;
	const postReadKind = parsed.value?.kind;

	let protocolError = false;
	const firstProvenance = closeEvidence({
		verdict: parsed.value?.verdict,
		evidence: Array.isArray(parsed.value?.evidence)
			? (parsed.value.evidence as Array<Record<string, unknown>>)
			: [],
		readBodies,
	});
	if (!promptError && firstProvenance.applied && !firstProvenance.passed) {
		const readIds = [...readBodies.keys()];
		const idBlock =
			readIds.length > 0
				? `本会话已经 read_chunk 的 chunk_id：\n${readIds.map((id) => `- ${id}`).join("\n")}\n`
				: "";
		await repromptOnce(
			`你给出了 match/mismatch，但证据引用未通过来源核对（${firstProvenance.reason}）。` +
				idBlock +
				`不要重新判断。请原样保留刚才的 verdict 和 kind，只返回实际读过的 evidence chunk_id（source/location 可保留）。不要写 evidence.text。`,
			"citation protocol re-prompt",
		);
		if (parsed.value && isMatchMismatch({ verdict: postReadVerdict })) {
			parsed.value.verdict = postReadVerdict;
			if (postReadKind !== undefined) parsed.value.kind = postReadKind;
		} else if (!parsed.value && isMatchMismatch({ verdict: postReadVerdict })) {
			parsed = {
				...parsed,
				value: {
					verdict: postReadVerdict,
					kind: postReadKind,
					evidence: [],
				},
			};
		}
	}
	session.dispose();
	await Promise.allSettled(usagePosts);

	const closed = applyEvidenceClosure(parsed.value, readBodies);
	parsed = { ...parsed, value: closed.result };
	protocolError = Boolean(closed.closure.applied && !closed.closure.passed);

	// Persist per-case trace JSONL (auto-pruned by server housekeeping).
	let traceFile: string | null = null;
	const traceDir = process.env.TRACE_DIR || join(process.cwd(), "traces");
	try {
		await mkdir(traceDir, { recursive: true });
		const safeId = String(input.case_id || "case").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 80);
		traceFile = join(traceDir, `${Date.now()}_${safeId}.jsonl`);
		await writeFile(traceFile, trace.map((t) => JSON.stringify(t)).join("\n"), "utf8");
	} catch {
		traceFile = null;
	}

	const usedIds = evidenceChunkIds(parsed.value);
	const firstRoundHitUsed =
		firstRoundIds.length === 0 || usedIds.length === 0
			? null
			: usedIds.some((id) => firstRoundIds.includes(id));
	const snap = progress.snapshot();
	let outcome: AgentCaseOutcome = {
		result: null,
		parse_mode: parsed.mode,
		stats: {
			tool_calls: toolCalls,
			search_calls: searchCalls,
			read_chunks: readCount,
			report_search_calls: reportSearchCalls,
			turns: turns,
			gate_reprompt: gateReprompt,
			duration_ms: Date.now() - started,
			zero_new_searches: snap.zero_new_searches,
			search_blocked: snap.search_blocked,
			first_round_hit_used: firstRoundHitUsed,
			read_before_search: firstToolName == null ? null : firstToolName === "read_chunk",
			raw_verdict: firstRawVerdict,
			raw_kind: firstRawKind,
			closure: !closed.closure.applied ? "skipped" : closed.closure.passed ? "passed" : "failed",
			closure_mode: closed.closure.mode,
			closure_reason: closed.closure.reason || null,
			protocol_error: protocolError,
		},
		trace_file: traceFile,
		final_text_preview: finalText.slice(0, 4000),
	};

	if (promptError && !parsed.value) {
		throw new Error(String(promptError));
	}

	outcome.result = parsed.value;
	return outcome;
}
