/**
 * Pi-based standard-value audit case runner.
 *
 * Runs the Pi agent (SDK mode) over each gold case in a report JSON:
 *   for each case, prompt the agent with the product context + reported
 *   requirement, let it freely use the three audit RAG tools, capture the
 *   full session trace (tool calls + messages), parse the final verdict,
 *   and compare against the gold judgment.
 *
 * Usage:
 *   node --import tsx runner.ts               # run all cases in the default report
 *   node --import tsx runner.ts --report hbjc_end_to_end_audit_v1.json --limit 3
 *
 * Env:
 *   DEEPSEEK_API_KEY      (required) DeepSeek key, same as backend uses
 *   DEEPSEEK_BASE_URL     (optional) defaults to https://api.deepseek.com
 *   DEEPSEEK_MODEL        (optional) defaults to deepseek-v4-flash
 *   CHUNK_STUDIO_API_BASE (optional) backend base URL, default http://127.0.0.1:8000
 *   PI_TRACE_DIR          (optional) directory for per-case trace JSONL, default ./traces
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Type } from "typebox";
import {
	createAgentSession,
	DefaultResourceLoader,
	defineTool,
	ModelRuntime,
	SessionManager,
} from "@earendil-works/pi-coding-agent";

import { createAuditTools } from "./tools.ts";

const apiBase = (process.env.CHUNK_STUDIO_API_BASE ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const traceDir = process.env.PI_TRACE_DIR ?? join(process.cwd(), "traces");

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

function defaultConcurrency(): number {
	const raw = Number(process.env.AGENT_CONCURRENCY ?? "5");
	return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 5;
}

async function mapPool<T, R>(
	items: T[],
	limit: number,
	fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
	const out = new Array<R>(items.length);
	let next = 0;
	async function worker(): Promise<void> {
		for (;;) {
			const i = next++;
			if (i >= items.length) return;
			out[i] = await fn(items[i], i);
		}
	}
	const n = Math.max(1, Math.min(limit, items.length || 1));
	await Promise.all(Array.from({ length: items.length ? n : 0 }, () => worker()));
	return out;
}

// Which report to audit (contains embedded gold cases).
function defaultReportPath(): string {
	return join(process.cwd(), "..", "..", "backend", "data", "reports", "hbjc_end_to_end_audit_v1.json");
}

function parseArgs(argv: string[]): {
	report: string;
	limit: number | null;
	caseId?: string;
	caseIds?: string[];
	out?: string;
	concurrency: number;
} {
	const out = {
		report: process.env.PI_REPORT_PATH ?? defaultReportPath(),
		limit: null as number | null,
		caseId: undefined as string | undefined,
		caseIds: undefined as string[] | undefined,
		out: undefined as string | undefined,
		concurrency: defaultConcurrency(),
	};
	for (let i = 0; i < argv.length; i++) {
		if (argv[i] === "--report" && argv[i + 1]) out.report = argv[i + 1];
		if (argv[i] === "--limit" && argv[i + 1]) out.limit = Number(argv[i + 1]);
		if (argv[i] === "--case" && argv[i + 1]) out.caseId = argv[i + 1];
		if (argv[i] === "--cases" && argv[i + 1]) {
			out.caseIds = argv[i + 1].split(",").map((s) => s.trim()).filter(Boolean);
		}
		if (argv[i] === "--out" && argv[i + 1]) out.out = argv[i + 1];
		if (argv[i] === "--concurrency" && argv[i + 1]) out.concurrency = Math.max(1, Number(argv[i + 1]));
	}
	return out;
}

function toolStats(trace: any[]): {
	tool_calls: number;
	search_calls: number;
	read_chunks: number;
	read_report: number;
	search_queries: string[];
} {
	const starts = trace.filter((t) => t.type === "tool_execution_start");
	const names = starts.map((t) => String(t.toolName ?? ""));
	const search_queries = trace
		.filter((t) => (t.type === "tool_call" || t.type === "tool_execution_start") && t.toolName === "search_standards")
		.map((t) => String(t.args?.query ?? t.input?.query ?? ""))
		.filter(Boolean);
	return {
		tool_calls: names.filter(Boolean).length,
		search_calls: names.filter((n) => n === "search_standards").length,
		read_chunks: names.filter((n) => n === "read_chunk").length,
		read_report: names.filter((n) => n === "read_report").length,
		search_queries: [...new Set(search_queries)],
	};
}

function percentile(values: number[], p: number): number | null {
	if (!values.length) return null;
	const sorted = [...values].sort((a, b) => a - b);
	const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
	return sorted[idx];
}

async function loadReport(path: string): Promise<any> {
	const raw = await readFile(path, "utf8");
	const payload = JSON.parse(raw);
	if (!payload.cases) throw new Error(`报告 ${path} 没有 cases 字段`);
	// test_set.json（human_reviewed 检索评测集）与审计报告 JSON 形状不同，归一化：
	// detection_project.{project_name, reported_requirement, sample_context} → 内部 case 形状。
	const first = payload.cases[0] ?? {};
	if (first.detection_project) {
		payload.cases = payload.cases.map((c: any) => ({
			case_id: c.case_id,
			sample_context: c.detection_project?.sample_context ?? {},
			test_item: { project_name: c.detection_project?.project_name ?? "" },
			reported_requirement: c.detection_project?.reported_requirement ?? {},
			answerability_status: c.answerability_status ?? null,
			relevant_evidence: c.relevant_evidence ?? [],
			judgment: c.judgment ?? null,
		}));
		payload.is_test_set = true;
	}
	return payload;
}

/** Build the per-case task prompt from the case object. */
function casePrompt(c: any): string {
	const ctx = c.sample_context ?? {};
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
		``,
		`请检索标准知识库，核实该声称值是否与适用标准的限值一致，并按任务定义给出的 JSON 格式输出判定。`,
	].join("\n");
}

function systemPrompt(): string {
	return [
		`你是检测报告标准值审查员。你将收到一个审查任务：给定产品型号参数、试验项目、报告声称值，`,
		`你需要检索标准知识库并判定声称值是否与标准一致。`,
		``,
		`约束（必须遵守）：`,
		`1. 不用行业常识填补标准值，证据不足判 unevaluable（检索不到标准用 kind=standard_not_found，缺适用性参数用 kind=applicability_undetermined）。`,
		`2. 每个判定都给出可定位的标准证据（标准号、表号/条款、片段）。`,
		`3. 最终一条消息只输出一个紧凑 JSON，不要 Markdown 代码块包裹。`,
		`   JSON 字段：case_id, verdict, kind, standard_no, standard_value, reported_value, evidence[], reasoning。`,
		`   verdict ∈ {match, mismatch, unevaluable, out_of_scope}，kind 必填（仅 out_of_scope 时为 null）：`,
		`   - match：exact（数值/条件精确一致）| unit_equivalent（单位换算后一致）| formula_aggregate（派生公式一致，如总损耗=两项限值加和）`,
		`   - mismatch：numeric_looser（限值放宽）| numeric_tighter（自行加严）| comparator_flip（比较方向反转）| bandwidth_exceeded（超出允许偏差带宽）| wrong_level（电压/能效等级写错）| wrong_condition（试验条件/次数/时长写错）| wrong_label（标号/联结组写错）| magnitude_error（数量级错误）| formula_aggregate（派生公式与限值加和不符）`,
		`   - unevaluable：standard_not_found（检索不到适用标准/条款）| applicability_undetermined（缺决定适用性的参数/上下文）`,
		`   standard_not_found 准入门槛：判它之前必须 ①用至少 2 种不同表述检索（按标准号/项目名/参数名），②对最相关命中执行 read_chunk 读完整原文——search 只返回定位预览，预览里没有不等于条款不存在；reasoning 须列出已尝试的检索式与已读片段。`,
		`   - out_of_scope：kind=null（声称值不构成限值声称——无数值/条件可比，无需检索即可判）`,
		`   kind 优先级：若同一问题同时符合多个 mismatch kind，选最能刻画错误机制的——comparator_flip / wrong_level / wrong_condition / wrong_label / magnitude_error / bandwidth_exceeded 优先于 numeric_looser / numeric_tighter（后者仅在问题只是单纯数值宽严时使用）。`,
		``,
		`判定口径（统一按以下规则，不要自行发挥）：`,
		`4. 总损耗型声称值：GB/T 1094.1 第3.6.4条定义总损耗=空载损耗+负载损耗，但标准限值表中不存在独立的"总损耗"限值列。`,
		`   因此若报告声称 P总 ≤ 空载损耗限值 + 负载损耗限值（两项限值之和），判 match + kind=formula_aggregate（reasoning 注明"总损耗口径=两项限值加和"）；`,
		`   若声称值不等于两项限值之和（无论偏大偏小），判 mismatch + kind=formula_aggregate。不要把 P总 当作单项负载损耗限值去比。`,
		`5. 单位等价：声称值经单位换算后与标准限值一致（如 0.010 与 0.010%、10 与 10000 V），判 match + kind=unit_equivalent，evidence 注明换算关系；`,
		`   仅当换算后仍不一致才判 mismatch。`,
		`6. 加严指标：报告声称值严于标准限值（如标准 ≤4% 而报告 ≤3%），判 mismatch + kind=numeric_tighter，reasoning 注明"属自行加严，严于标准要求"。`,
		``,
		`工具可用：search_standards / read_chunk / read_report。`,
		`search_standards 按你写的 query 原样检索，后端不会再自动改写；命中不好时换表述再搜。检索 2–3 次后应 read_chunk 并判定，禁止为同一条款反复搜索。`,
	].join("\n");
}

async function main(): Promise<void> {
	const args = parseArgs(process.argv.slice(2));
	const report = await loadReport(args.report);
	await mkdir(traceDir, { recursive: true });

	const apiKey = process.env.PI_API_KEY || process.env.ZHIPU_API_KEY || process.env.DASHSCOPE_API_KEY || process.env.DEEPSEEK_API_KEY;
	if (!apiKey) {
		throw new Error("缺少 API key：设置 PI_API_KEY / ZHIPU_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY");
	}
	// 默认走智谱官方 OpenAI 兼容端点，模型 glm-5.3-flash。
	const baseUrl = (
		process.env.PI_BASE_URL ||
		process.env.DASHSCOPE_BASE_URL ||
		"https://open.bigmodel.cn/api/paas/v4"
	).replace(/\/+$/, "");
	const modelId = process.env.PI_MODEL || process.env.DASHSCOPE_MODEL || "glm-5.3-flash";
	const providerId = process.env.PI_PROVIDER || "zhipu";

	// Build a ModelRuntime with only our registered provider; avoid any
	// network/model-catalog refresh (models are supplied explicitly).
	const modelRuntime = await ModelRuntime.create({
		modelsPath: null,
		refreshOnCreate: false,
		allowModelNetwork: false,
	});
	modelRuntime.registerProvider(providerId, {
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
				// 智谱官方：thinking { type: enabled }（Pi thinkingFormat=zai）。
				// 百炼 Qwen/ZHIPU/*：enable_thinking + reasoning_effort。
				// supportsDeveloperRole=false：两端都拒绝 developer 角色。
				compat: {
					thinkingFormat: /dashscope/i.test(baseUrl) || /^qwen/i.test(modelId) || /^zhipu\//i.test(modelId)
						? "qwen"
						: "zai",
					supportsReasoningEffort: true,
					supportsDeveloperRole: false,
				},
			},
		],
	});
	const model = modelRuntime.getModel(providerId, modelId);
	if (!model) throw new Error(`注册 provider 后未找到模型 ${providerId}/${modelId}`);
	// 显式注入 key：避免依赖 env 引用的命名拼接。
	modelRuntime.setRuntimeApiKey(model.provider, apiKey);

	const cases = report.cases as any[];
	const idFilter = args.caseIds?.length
		? new Set(args.caseIds)
		: args.caseId
			? new Set([args.caseId])
			: null;
	let selected = idFilter ? cases.filter((c: any) => idFilter.has(c.case_id)) : cases;
	if (args.limit) selected = selected.slice(0, args.limit);
	if (idFilter) {
		const missing = [...idFilter].filter((id) => !selected.some((c: any) => c.case_id === id));
		if (missing.length) console.warn(`未找到 case: ${missing.join(", ")}`);
	}
	const requestedThinking = String(process.env.PI_THINKING_LEVEL || "low").toLowerCase();
	const glmAlwaysOn = /glm-5\.3/i.test(modelId);
	let thinkingLevel = requestedThinking;
	if (glmAlwaysOn) {
		if (requestedThinking === "off" || requestedThinking === "minimal") thinkingLevel = "low";
		else if (requestedThinking === "medium") thinkingLevel = "high";
	}
	const concurrency = args.concurrency;
	console.log(`报告 ${args.report}: 共 ${cases.length} case，本次跑 ${selected.length} 个`);
	console.log(`model=${modelId} thinking=${thinkingLevel} concurrency=${concurrency}`);

	const results = await mapPool(selected, concurrency, async (c, idx) => {
		console.log(`--- case ${idx + 1}/${selected.length}: ${c.case_id} ---`);
		const caseStarted = Date.now();
		const trace: any[] = [];
		let finalText = "";
		let promptError: unknown = undefined;
		let readCount = 0;
		let toolCallBudget = Number(process.env.PI_MAX_TOOL_CALLS ?? "12");
		const tools = createAuditTools();

		const loader = new DefaultResourceLoader({
			cwd: process.cwd(),
			agentDir: join(process.env.HOME ?? process.env.USERPROFILE ?? ".", ".pi", "agent"),
			systemPromptOverride: () => systemPrompt(),
			appendSystemPromptOverride: () => [],
			extensionFactories: [
				(_pi: any) => {
					(_pi as any).on("tool_call", (_event: any) => {
						if (toolCallBudget > 0) {
							toolCallBudget -= 1;
							return undefined;
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
			modelRuntime,
			customTools: tools,
			resourceLoader: loader,
			sessionManager: SessionManager.inMemory(),
			thinkingLevel,
		});
		session.setActiveToolsByName(["search_standards", "read_chunk", "read_report"]);

		session.subscribe((event: any) => {
			if (event.type === "tool_execution_start" && event.toolName === "read_chunk") {
				readCount += 1;
			}
			if (TRACE_TYPES.has(event.type)) {
				trace.push({ type: event.type, ts: Date.now(), ...strip(event) });
			}
			if (event.type === "message_end" && event.message?.role === "assistant") {
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

		try {
			await session.prompt(casePrompt(c));
			await new Promise<void>((r) => setTimeout(r, 500));
		} catch (err) {
			promptError = err instanceof Error ? err.message : String(err);
		}

		let parsed = parseVerdict(finalText);
		if (parsed?.verdict === "unevaluable" && parsed?.kind === "standard_not_found" && readCount === 0) {
			console.log(`  [gate] ${c.case_id} standard_not_found 但 read_chunk=0，追加重判提示`);
			try {
				await session.prompt(
					`你给出了 unevaluable + standard_not_found，但本会话从未用 read_chunk 读取任何命中片段的完整原文。` +
						`search_standards 只返回定位预览，预览里没有不等于条款不存在。` +
						`请对最相关的命中执行 read_chunk 核实原文后重新输出最终 JSON 判定；若 read 后仍无法定位条款，可维持 unevaluable，但 reasoning 须列出已读片段与已尝试的检索式。`,
				);
				await new Promise<void>((r) => setTimeout(r, 500));
			} catch (err) {
				promptError = err instanceof Error ? err.message : String(err);
			}
			parsed = parseVerdict(finalText);
		}
		session.dispose();
		const duration_ms = Date.now() - caseStarted;
		const stats = toolStats(trace);
		const row = {
			case_id: c.case_id,
			edit_kind: c.edit_kind ?? null,
			gold: c.judgment?.status ?? null,
			answerability: c.answerability_status ?? null,
			verdict: parsed?.verdict ?? null,
			parsed_json: parsed ?? null,
			final_assistant_text: finalText.slice(0, 4000),
			error: promptError ?? null,
			duration_ms,
			stats,
			trace: trace,
		};
		const traceFile = join(traceDir, `${c.case_id}.jsonl`);
		await writeFile(traceFile, trace.map((t) => JSON.stringify(t)).join("\n"), "utf8");
		console.log(
			`  gold=${c.judgment?.status ?? c.answerability_status ?? "-"} verdict=${parsed?.verdict ?? "(parse fail)"} kind=${parsed?.kind ?? "-"} ${duration_ms}ms search=${stats.search_calls} read=${stats.read_chunks} -> ${traceFile}`,
		);
		return row;
	});

	const reportOut = {
		report: args.report,
		model: modelId,
		base_url: baseUrl,
		thinking_level: thinkingLevel,
		concurrency,
		total: results.length,
		summary: summarize(results),
		results,
	};
	const outPath = args.out
		? (args.out.includes("\\") || args.out.includes("/") ? args.out : join(process.cwd(), args.out))
		: join(process.cwd(), "results.json");
	await writeFile(outPath, JSON.stringify(reportOut, null, 2), "utf8");
	console.log(`\n汇总已写入 ${outPath}`);
	console.log(JSON.stringify(reportOut.summary, null, 2));
}

/** Pull a minimal JSON object out of the final assistant text. */
function parseVerdict(text: string): any | null {
	if (!text) return null;
	const stripped = text.replace(/```(?:json)?/gi, "").trim();
	// 严格路径：找最外层 {} 对象并 JSON.parse
	const start = stripped.indexOf("{");
	const end = stripped.lastIndexOf("}");
	if (start >= 0 && end > start) {
		try {
			const parsed = JSON.parse(stripped.slice(start, end + 1));
			if (parsed) return parsed;
		} catch {
			// 继续走宽松路径
		}
	}
	// 宽松路径：模型偶尔把字段写成 Markdown 散列（无 {} 包裹、字段名/值拼错）。
	// 至少提取 verdict 与 case_id，其余字段尽力而为。
	const out: Record<string, unknown> = {};
	const verdictMatch = stripped.match(/["']?verdict["']?\s*[:：]\s*["']?([a-z_]+)/i);
	if (verdictMatch) out.verdict = verdictMatch[1];
	const kindMatch = stripped.match(/["']?kind["']?\s*[:：]\s*["']?([a-z_]+)/i);
	if (kindMatch) out.kind = kindMatch[1];
	const caseMatch = stripped.match(/["']?case_id["']?\s*[:：]\s*["']?([^"',\s}]+)/i);
	if (caseMatch) out.case_id = caseMatch[1];
	const svMatch = stripped.match(/["']?standard_value["']?\s*[:：]\s*["']?([^"'\n,}]+)/i);
	if (svMatch) out.standard_value = svMatch[1];
	if ("verdict" in out) return out;
	// 中文散文兜底：模型偶尔不按 JSON 收尾（如"判定结果为'无法评估'……故判为'适用性未定'"）。
	// 只在前面全部失败时使用，顺序敏感：unevaluable/mismatch 语义必须在 match 之前匹配。
	const zhVerdicts: Array<[RegExp, string]> = [
		[/适用性未定|无法评估|无法判定|证据不足|证据不够/, "unevaluable"],
		[/不构成限值|无需检索|超出审查范围|不予审查/, "out_of_scope"],
		[/不一致|不匹配|不符|不通过/, "mismatch"],
		[/一致|匹配|相符|通过/, "match"],
	];
	for (const [re, v] of zhVerdicts) {
		if (re.test(stripped)) {
			out.verdict = v;
			break;
		}
	}
	if (out.verdict === "unevaluable") {
		if (/适用性|是否适用于|缺.*参数|参数.*缺/.test(stripped)) out.kind = "applicability_undetermined";
		else if (/未检索到|检索不到|未找到|知识库/.test(stripped)) out.kind = "standard_not_found";
	}
	return "verdict" in out ? out : null;
}

function strip(event: any): any {
	if (typeof event !== "object" || event === null) return {};
	const out: Record<string, unknown> = {};
	// 工具调用相关小字段直接保留（args/input 可能较大，截断到 2000 字符/字段）。
	for (const k of ["toolName", "toolCallId", "isError"]) {
		if (k in event) out[k] = event[k];
	}
	for (const k of ["args", "input", "result", "partialResult"]) {
		if (k in event && event[k] !== undefined) {
			out[k] = truncateValue(event[k], 2000);
		}
	}
	// message 只保留摘要字段，绝不整包保留（GLM thinking 内容极大，整包会把 trace 撑到几百 MB）。
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

function expectedKind(editKind: string | null | undefined): string | null {
	if (!editKind) return null;
	return (
		{
			numeric_looser: "numeric_looser",
			numeric_tighter: "numeric_tighter",
			formula_aggregate: "formula_aggregate",
			comparator_flip: "comparator_flip",
			bandwidth_looser: "bandwidth_exceeded",
			wrong_level: "wrong_level",
			wrong_condition: "wrong_condition",
			wrong_label: "wrong_label",
			magnitude_error: "magnitude_error",
			unit_equivalence: "unit_equivalent",
			positive_control: "exact",
			existing_conflict_control: null,
		} as Record<string, string | null>
	)[editKind] ?? null;
}

function summarize(results: any[]): any {
	const rows = results.map((r) => {
		const kind = r.parsed_json?.kind ?? null;
		const wantKind = expectedKind(r.edit_kind);
		return {
			case_id: r.case_id,
			edit_kind: r.edit_kind ?? null,
			gold: r.gold,
			answerability: r.answerability,
			verdict: r.verdict,
			kind,
			match: r.gold && r.verdict ? goldToVerdict(r.gold) === r.verdict : null,
			kind_match: wantKind ? wantKind === kind : null,
			duration_ms: r.duration_ms ?? null,
			search_calls: r.stats?.search_calls ?? null,
			read_chunks: r.stats?.read_chunks ?? null,
			tool_calls: r.stats?.tool_calls ?? null,
			search_queries: r.stats?.search_queries ?? [],
		};
	});
	const match = rows.filter((r) => r.match === true).length;
	const mismatch = rows.filter((r) => r.match === false).length;
	const unparsed = rows.filter((r) => r.verdict === null).length;
	const kindRows = rows.filter((r) => r.kind_match !== null);
	const durations = rows.map((r) => r.duration_ms).filter((n): n is number => typeof n === "number");
	const searchCounts = rows.map((r) => r.search_calls).filter((n): n is number => typeof n === "number");
	const readCounts = rows.map((r) => r.read_chunks).filter((n): n is number => typeof n === "number");
	const distinctQueries = rows.filter((r) => (r.search_queries?.length ?? 0) >= 2).length;

	// test_set.json 口径：answerable 期望产出判定（match/mismatch），
	// context_required 期望 unevaluable，not_applicable_to_retrieval 期望 out_of_scope。
	const answerable = rows.filter((r) => r.answerability === "answerable_candidate");
	const judgmentProduced = answerable.filter(
		(r) => r.verdict === "match" || r.verdict === "mismatch",
	).length;
	const contextRequired = rows.filter((r) => r.answerability === "context_required");
	const prefilter = rows.filter((r) => r.answerability === "not_applicable_to_retrieval");
	const gateCorrect =
		contextRequired.filter((r) => r.verdict === "unevaluable").length +
		prefilter.filter((r) => r.verdict === "out_of_scope").length;
	const gateTotal = contextRequired.length + prefilter.length;

	return {
		cases: rows.length,
		exact_match: match,
		verdict_mismatch: mismatch,
		unparsed,
		kind_match: kindRows.filter((r) => r.kind_match === true).length,
		kind_total: kindRows.length,
		latency_ms: {
			p50: percentile(durations, 50),
			p90: percentile(durations, 90),
			max: durations.length ? Math.max(...durations) : null,
			mean: durations.length ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length) : null,
		},
		tools: {
			search_p50: percentile(searchCounts, 50),
			read_p50: percentile(readCounts, 50),
			multi_query_cases: distinctQueries,
		},
		test_set: answerable.length
			? {
					answerable: answerable.length,
					judgment_produced: judgmentProduced,
					context_required: contextRequired.length,
					prefilter: prefilter.length,
					gate_correct: gateCorrect,
					gate_total: gateTotal,
				}
			: undefined,
		rows,
	};
}

/** gold 判定用生产 judge 的形容词，模型输出用新四向判定，需映射后对比。 */
function goldToVerdict(gold: string): string {
	return ({
		correct: "match",
		incorrect: "mismatch",
		supported: "match",
		mismatch: "mismatch",
		insufficient_context: "unevaluable",
		unevaluable: "unevaluable",
		not_applicable_to_retrieval: "out_of_scope",
		out_of_scope: "out_of_scope",
	}[gold] ?? gold) as string;
}

// 仅直接运行本文件时执行 main；被 import 时不触发（便于类型/结构验证）。
const isMain = process.argv[1] && (await import("node:path")).resolve(process.argv[1]) === import.meta.filename;
if (isMain) {
	main().catch((err) => {
		console.error("runner 失败:", err);
		process.exit(1);
	});
}