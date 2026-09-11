/**
 * Chat-turn tools for the Pi sidecar.
 *
 * search_knowledge_base / read_chunk share HTTP helpers with audit tools.
 * file_ids and workspace_id are bound by the host, never exposed as tool
 * parameters. Business tools call FastAPI analytics over HTTP.
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

import { formatSearchHits, toLocatorHit } from "./preview.ts";
import type { EvidenceProgress } from "./progress.ts";
import { apiFetch, normalizeScope } from "./tools.ts";

export type ChatToolScope = {
	fileIds: string[];
	workspaceId: string;
	currentQuestion: string;
	topK: number;
};

export function searchKnowledgeBaseRequestBody(args: {
	currentQuestion: string;
	modelQuery?: string;
	top_k?: number;
	scope: string[];
}): Record<string, unknown> {
	const production = String(args.currentQuestion || "").trim();
	const rewrite = String(args.modelQuery || "").trim();
	const queryRoutes: Record<string, string> = { production };
	if (rewrite && rewrite !== production) {
		queryRoutes.semantic = rewrite;
	}
	const body: Record<string, unknown> = {
		query: production,
		top_k: Math.min(args.top_k ?? 8, 20),
		query_routes: queryRoutes,
	};
	if (args.scope.length) body.file_ids = args.scope;
	return body;
}

function readableHits(
	payload: any,
	options?: { query?: string },
): string {
	return formatSearchHits(payload?.hits ?? [], { query: options?.query });
}

function analyticsPath(path: string, workspaceId: string): string {
	if (!workspaceId) return path;
	const join = path.includes("?") ? "&" : "?";
	return `${path}${join}workspace_id=${encodeURIComponent(workspaceId)}`;
}

export function createChatTools(scope: ChatToolScope, progress?: EvidenceProgress) {
	const fileIds = normalizeScope(scope.fileIds);
	const workspaceId = String(scope.workspaceId || "").trim();
	const currentQuestion = String(scope.currentQuestion || "").trim();
	const defaultTopK = Math.min(Math.max(scope.topK || 8, 1), 20);
	const maxCalls = Math.max(1, Number(process.env.PI_CHAT_MAX_TOOL_CALLS ?? "8"));
	let remaining = maxCalls;
	const budgetExceeded = () => ({
		content: [
			{
				type: "text" as const,
				text: `工具调用已达预算上限 ${maxCalls} 次。请基于已获取的证据直接给出最终回答，不要再调用工具。`,
			},
		],
		details: { budget_exceeded: true },
	});
	function takeBudget(): ReturnType<typeof budgetExceeded> | null {
		if (remaining <= 0) return budgetExceeded();
		remaining -= 1;
		return null;
	}

	const searchKnowledgeBase = defineTool({
		name: "search_knowledge_base",
		label: "检索知识库",
		description:
			"在助手绑定的知识库文件中做一次混合检索。返回定位预览：表格给表题/列名，条款给 query 命中窗口。不含表格单元格数值。引用文档事实前必须再 read_chunk。query 只能改写当前用户问题，不能换成不同意图。",
		promptSnippet: "按当前用户问题检索知识库定位预览；引用前必须 read_chunk",
		promptGuidelines: [
			"search_knowledge_base 的 production 检索式由宿主固定为当前用户问题；你写的 query 只作同意图改写。",
			"返回的是定位预览，不是全文。涉及条款、数值、定义必须再 read_chunk。",
			"连续两次没有新增片段后不要再搜。",
		],
		parameters: Type.Object({
			query: Type.Optional(
				Type.String({
					description: "对当前用户问题的检索改写；省略则只用原问题。不得更换意图。",
				}),
			),
			top_k: Type.Optional(Type.Integer({ description: "返回条数，默认 8，最大 20" })),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			if (progress?.isSearchBlocked()) {
				return {
					content: [
						{
							type: "text" as const,
							text: "连续两次检索没有新增片段，请停止搜索。对尚未读取的命中执行 read_chunk，或说明证据不足，不要编造。",
						},
					],
					details: { search_blocked: true, hits: [], ...progress.snapshot() },
				};
			}
			const blocked = takeBudget();
			if (blocked) return blocked;
			if (!fileIds.length) {
				return {
					content: [
						{
							type: "text" as const,
							text: "当前助手未绑定知识库文件，无法检索文档。",
						},
					],
					details: { hits: [], unbound: true },
				};
			}
			const production = currentQuestion || String(params.query || "").trim();
			const payload = await apiFetch("/api/search", signal, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(
					searchKnowledgeBaseRequestBody({
						currentQuestion: production,
						modelQuery: params.query,
						top_k: params.top_k ?? defaultTopK,
						scope: fileIds,
					}),
				),
			});
			const hits = (payload as any)?.hits ?? [];
			const locators = hits.map(toLocatorHit);
			const searchProgress = progress
				? progress.recordSearch(
						production,
						locators.map((hit) => hit.chunk_id),
					)
				: null;
			const note = searchProgress
				? [
						`本次检索：${searchProgress.returned} 个结果，新增 chunk：${searchProgress.newCount}`,
						searchProgress.newCount === 0
							? "该检索没有获得新证据。不要继续使用相似查询。"
							: "",
						searchProgress.blocked
							? "已连续两次没有新增片段，后续检索将被阻止。"
							: "",
					]
						.filter(Boolean)
						.join("\n")
				: "";
			const text =
				`检索式: ${production}\n命中 ${hits.length} 条（定位预览，不含表格数值）：\n` +
				readableHits(payload, { query: production }) +
				(note ? `\n\n${note}` : "");
			return {
				content: [{ type: "text", text }],
				details: { hits: locators, ...(progress ? progress.snapshot() : {}) },
			};
		},
	});

	const readChunk = defineTool({
		name: "read_chunk",
		label: "读取知识库片段全文",
		description:
			"按 chunk_id 读取一个知识库片段的完整原文，含表格数据和业务元数据。search_knowledge_base 只返回定位预览；引用文档事实前必须 read_chunk。",
		promptSnippet: "读取检索命中的知识库片段完整原文",
		promptGuidelines: [
			"chunk_id 应来自 search_knowledge_base 命中，不要凭空构造。",
			"回答里引用条款、数值、定义之前必须先读原文。",
		],
		parameters: Type.Object({
			chunk_id: Type.String({
				description: "知识库片段 id（来自 search_knowledge_base 命中）",
			}),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			const payload = await apiFetch(`/api/chunks/${encodeURIComponent(params.chunk_id)}`, signal);
			progress?.recordRead(params.chunk_id);
			const chunk = payload as any;
			const meta = JSON.stringify(chunk.business_metadata ?? chunk.metadata ?? {});
			const text =
				`chunk_id=${chunk.id} file_id=${chunk.file_id} page=${chunk.page}\n` +
				`metadata: ${meta}\n` +
				`--- 全文 ---\n${String(chunk.text ?? "")}`;
			return { content: [{ type: "text", text }], details: { chunk } };
		},
	});

	const queryBusinessData = defineTool({
		name: "query_business_data",
		label: "查询业务数据",
		description:
			"对工作区只读业务库做 Text2SQL / 聚合查询，可返回饼图、柱状图、指标或表格。不能用来编造文档条款。",
		promptSnippet: "只读查询工作区业务统计，可附带图表",
		promptGuidelines: [
			"只问工作区指标、审查分布、数量、schema 能覆盖的字段。",
			"不要用本工具检索标准或知识库条款。",
		],
		parameters: Type.Object({
			question: Type.String({ description: "业务问题，如审查状态分布、知识库数量" }),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			const payload = (await apiFetch("/api/analytics/query", signal, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					question: params.question,
					workspace_id: workspaceId || undefined,
				}),
			})) as Record<string, unknown>;
			const rows = Array.isArray(payload.rows) ? payload.rows.slice(0, 50) : [];
			const chart =
				payload.chart && typeof payload.chart === "object" && !Array.isArray(payload.chart)
					? payload.chart
					: null;
			const answer = String(payload.answer || payload.summary || "business query completed");
			const text = [
				answer,
				rows.length ? `rows(${rows.length}): ${JSON.stringify(rows).slice(0, 2000)}` : "",
				chart ? `chart: ${JSON.stringify(chart).slice(0, 800)}` : "",
			]
				.filter(Boolean)
				.join("\n");
			return {
				content: [{ type: "text", text }],
				details: { ...payload, rows, chart },
			};
		},
	});

	const getBusinessSchema = defineTool({
		name: "get_business_schema",
		label: "业务库 schema",
		description: "读取只读业务分析 schema 与可用图表类型。",
		promptSnippet: "读取业务分析 schema",
		parameters: Type.Object({}),
		async execute(_id, _params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			const payload = await apiFetch(analyticsPath("/api/analytics/schema", workspaceId), signal);
			return {
				content: [{ type: "text", text: JSON.stringify(payload).slice(0, 4000) }],
				details: { schema: payload },
			};
		},
	});

	const getBusinessOverview = defineTool({
		name: "get_business_overview",
		label: "业务总览",
		description: "读取当前工作区指标与审查状态分布。",
		promptSnippet: "读取工作区业务总览",
		parameters: Type.Object({}),
		async execute(_id, _params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			const payload = await apiFetch(analyticsPath("/api/analytics/overview", workspaceId), signal);
			return {
				content: [{ type: "text", text: JSON.stringify(payload).slice(0, 4000) }],
				details: { overview: payload },
			};
		},
	});

	return [
		searchKnowledgeBase,
		readChunk,
		queryBusinessData,
		getBusinessSchema,
		getBusinessOverview,
	];
}
