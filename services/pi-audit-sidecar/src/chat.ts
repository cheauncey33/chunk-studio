/**
 * One in-memory Pi session per user chat turn.
 *
 * Cross-turn memory stays in Python. This process creates a session, runs
 * tools, optionally re-prompts to read cited chunks, then dispose().
 */
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
	createAgentSession,
	DefaultResourceLoader,
	SessionManager,
} from "@earendil-works/pi-coding-agent";

import { getModel, resolveThinkingLevel, withTimeout } from "./agent.ts";
import { chatSystemPrompt } from "./chatPrompt.ts";
import { createChatTools } from "./chatTools.ts";
import { createEvidenceProgress, normalizeChunkId } from "./progress.ts";
import { recordAssistantMessageUsage, type ExecutionIdentity } from "./usage.ts";

export type ChatTurnInput = {
	conversation_id?: string | null;
	assistant_id?: string | null;
	current_question: string;
	conversation_summary?: string | null;
	recent_turns?: Array<{ role: string; content: string }> | null;
	file_ids?: string[] | null;
	workspace_id?: string | null;
	tool_intent?: string | null;
	retrieval_config?: { top_k?: number } | null;
	tool_budget?: number | null;
};

export type ChatCitation = {
	chunk_id: unknown;
	file_id: unknown;
	file_name: unknown;
	page: unknown;
	score: unknown;
	snippet: string;
};

export type ChatTurnOutcome = {
	answer: string;
	citations: ChatCitation[];
	charts: Array<Record<string, unknown>>;
	stats: {
		tool_calls: number;
		search_calls: number;
		read_chunks: number;
		turns: number;
		gate_reprompt: boolean;
		duration_ms: number;
		zero_new_searches: number;
		search_blocked: boolean;
		knowledge_grounded: boolean;
	};
	trace_file: string | null;
};

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

function toolDetails(result: unknown): Record<string, unknown> {
	if (!result || typeof result !== "object") return {};
	const details = (result as { details?: unknown }).details;
	return details && typeof details === "object" && !Array.isArray(details)
		? (details as Record<string, unknown>)
		: {};
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

function citationFromChunk(chunk: Record<string, unknown> | null | undefined): ChatCitation | null {
	if (!chunk || typeof chunk !== "object") return null;
	const chunkId = chunk.id ?? chunk.chunk_id;
	if (!chunkId) return null;
	const text = String(chunk.text || chunk.content || "");
	return {
		chunk_id: chunkId,
		file_id: chunk.file_id || chunk.doc_id || null,
		file_name: chunk.file_name || chunk.doc_id || null,
		page: chunk.page ?? null,
		score: chunk.rerank_score ?? chunk.score ?? null,
		snippet: text.slice(0, 240),
	};
}

export function looksLikeAbstain(text: string): boolean {
	return /没有检索到足够证据|无法可靠回答|知识库中没有|未绑定知识库|无法检索文档/.test(
		String(text || ""),
	);
}

export function needsKnowledgeReadGate(options: {
	searchCalls: number;
	readCount: number;
	answer: string;
}): boolean {
	if (options.readCount > 0) return false;
	if (options.searchCalls <= 0) return false;
	const answer = String(options.answer || "").trim();
	if (!answer) return false;
	if (looksLikeAbstain(answer)) return false;
	return true;
}

function turnPrompt(input: ChatTurnInput): string {
	const parts: string[] = [];
	const summary = String(input.conversation_summary || "").trim();
	if (summary) {
		parts.push("## 更早对话摘要", summary, "");
	}
	const turns = Array.isArray(input.recent_turns) ? input.recent_turns : [];
	if (turns.length) {
		parts.push("## 最近对话");
		for (const turn of turns) {
			const role = String(turn?.role || "").trim() || "user";
			const content = String(turn?.content || "").trim();
			if (!content) continue;
			parts.push(`${role}: ${content}`);
		}
		parts.push("");
	}
	const intent = String(input.tool_intent || "").trim();
	if (intent) {
		parts.push(`宿主意图提示（仅供参考，全部工具仍可用）：${intent}`, "");
	}
	parts.push("## 当前问题", String(input.current_question || "").trim());
	return parts.join("\n");
}

function defaultChatBudget(): number {
	return Math.max(1, Number(process.env.PI_CHAT_MAX_TOOL_CALLS ?? "8"));
}

export async function runChatTurn(input: ChatTurnInput): Promise<ChatTurnOutcome> {
	const started = Date.now();
	const currentQuestion = String(input.current_question || "").trim();
	if (!currentQuestion) {
		throw new Error("current_question is required");
	}
	const { model, runtime, modelId } = await getModel();
	const usageIdentity: ExecutionIdentity = {
		job_id: null,
		run_id: null,
		job_attempt: null,
		case_id: String(input.conversation_id || input.assistant_id || "chat").trim(),
	};
	const usagePosts: Promise<unknown>[] = [];
	const fileIds = Array.isArray(input.file_ids)
		? input.file_ids.filter((id) => typeof id === "string" && id)
		: [];
	const topK = Number(input.retrieval_config?.top_k || 8);
	const progress = createEvidenceProgress([]);
	const tools = createChatTools(
		{
			fileIds,
			workspaceId: String(input.workspace_id || ""),
			currentQuestion,
			topK,
		},
		progress,
	);

	let toolCallBudget = defaultChatBudget();
	if (typeof input.tool_budget === "number" && input.tool_budget > 0) {
		toolCallBudget = Math.floor(input.tool_budget);
	}

	const trace: any[] = [];
	let finalText = "";
	let readCount = 0;
	let toolCalls = 0;
	let searchCalls = 0;
	let turns = 0;
	let gateReprompt = false;
	const citations: ChatCitation[] = [];
	const charts: Array<Record<string, unknown>> = [];
	const seenCitation = new Set<string>();

	const loader = new DefaultResourceLoader({
		cwd: process.cwd(),
		agentDir: join(process.env.HOME ?? process.env.USERPROFILE ?? ".", ".pi", "agent"),
		systemPromptOverride: () => chatSystemPrompt({ hasKnowledgeBase: fileIds.length > 0 }),
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
						reason: `工具调用已达预算上限，请基于已获取的证据直接给出最终回答，不要再调用工具。`,
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
		thinkingLevel: resolveThinkingLevel(modelId) as any,
	});
	session.setActiveToolsByName(tools.map((t) => t.name));

	session.subscribe((event: any) => {
		if (event.type === "tool_execution_start") {
			toolCalls += 1;
			if (event.toolName === "read_chunk") readCount += 1;
			if (event.toolName === "search_knowledge_base") searchCalls += 1;
		}
		if (event.type === "tool_execution_end") {
			const details = toolDetails(event.result);
			if (event.toolName === "read_chunk") {
				const citation = citationFromChunk(
					details.chunk && typeof details.chunk === "object"
						? (details.chunk as Record<string, unknown>)
						: null,
				);
				if (citation) {
					const key = String(citation.chunk_id);
					if (!seenCitation.has(key)) {
						seenCitation.add(key);
						citations.push(citation);
					}
				}
			}
			const chart = details.chart;
			if (chart && typeof chart === "object" && !Array.isArray(chart)) {
				charts.push(chart as Record<string, unknown>);
			}
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

	const timeoutMs = Number(process.env.AGENT_CHAT_TIMEOUT_MS ?? "120000");
	let promptError: unknown = undefined;
	try {
		await withTimeout(
			session.prompt(turnPrompt(input)),
			timeoutMs,
			`chat turn timeout after ${timeoutMs}ms`,
		);
		await new Promise<void>((r) => setTimeout(r, 300));
	} catch (err) {
		promptError = err instanceof Error ? err.message : String(err);
	}

	async function repromptOnce(message: string, label: string): Promise<void> {
		gateReprompt = true;
		try {
			await withTimeout(session.prompt(message), 60000, `${label} timeout`);
			await new Promise<void>((r) => setTimeout(r, 300));
		} catch (err) {
			promptError = promptError ?? (err instanceof Error ? err.message : String(err));
		}
	}

	if (
		!promptError &&
		needsKnowledgeReadGate({
			searchCalls,
			readCount,
			answer: finalText,
		})
	) {
		await repromptOnce(
			`你引用了知识库内容，但本轮从未用 read_chunk 读取任何片段全文。` +
				`search_knowledge_base 只返回定位预览，不能当作原文。` +
				`请对最相关的命中执行 read_chunk 后再给出最终回答；若读完仍不够，说明缺什么，不要编造。`,
			"chat read gate",
		);
	}

	session.dispose();
	await Promise.allSettled(usagePosts);

	let traceFile: string | null = null;
	const traceDir = process.env.TRACE_DIR || join(process.cwd(), "traces");
	try {
		await mkdir(traceDir, { recursive: true });
		const safeId = String(input.conversation_id || input.assistant_id || "chat")
			.replace(/[^A-Za-z0-9_.-]/g, "_")
			.slice(0, 80);
		traceFile = join(traceDir, `${Date.now()}_${safeId}.jsonl`);
		await writeFile(traceFile, trace.map((t) => JSON.stringify(t)).join("\n"), "utf8");
	} catch {
		traceFile = null;
	}

	if (promptError && !finalText.trim()) {
		throw new Error(String(promptError));
	}

	const snap = progress.snapshot();
	const answer = finalText.trim() || "The model returned no displayable answer.";
	return {
		answer,
		citations,
		charts,
		stats: {
			tool_calls: toolCalls,
			search_calls: searchCalls,
			read_chunks: readCount,
			turns,
			gate_reprompt: gateReprompt,
			duration_ms: Date.now() - started,
			zero_new_searches: snap.zero_new_searches,
			search_blocked: snap.search_blocked,
			knowledge_grounded: readCount > 0,
		},
		trace_file: traceFile,
	};
}
