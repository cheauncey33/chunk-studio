/**
 * Chunk Studio audit RAG tools for the Pi harness (sidecar port).
 *
 * Three tools call the chunk-studio backend over HTTP:
 *   - search_standards — hybrid retrieval over the standards KB
 *     (injects query_routes.production so the backend skips LLM rewrite;
 *     the agent owns query diversity by calling search again)
 *   - read_chunk      — read one retrieved standard chunk in full (incl. tables)
 *   - read_report     — read a parsed inspection report's sections
 *   - search_report_context — literal search in the current report markdown
 *
 * Base URL comes from CHUNK_STUDIO_API_BASE (default http://127.0.0.1:8000).
 * Tools are built per case via createAuditTools(fileScope, progress, identity, reportFileId).
 * Search identity (job_id / run_id / case_id / job_attempt) is observability
 * only: it is posted to /api/search, never added to the query or prompt.
 * Search returns type-aware locators (table schema / query snippets), never
 * matched cell values. First-round workflow hits stay in the prompt; the agent
 * may still search if those hits are not enough.
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

import { formatSearchHits, toLocatorHit } from "./preview.ts";
import type { EvidenceProgress } from "./progress.ts";
import type { ExecutionIdentity } from "./usage.ts";
import { sidecarAuthHeaders } from "./usage.ts";

function apiBase(): string {
	return (process.env.CHUNK_STUDIO_API_BASE ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

export async function apiFetch(
	path: string,
	signal: AbortSignal | undefined,
	init?: RequestInit,
): Promise<unknown> {
	const headers = {
		...sidecarAuthHeaders(),
		...(init?.headers as Record<string, string> | undefined),
	};
	const res = await fetch(`${apiBase()}${path}`, { ...init, headers, signal });
	if (!res.ok) {
		const body = await res.text().catch(() => "");
		throw new Error(`chunk-studio ${path} -> HTTP ${res.status}: ${body.slice(0, 400)}`);
	}
	return res.json();
}

export function normalizeScope(ids: string[] | undefined | null): string[] {
	return Array.isArray(ids) ? ids.filter((x) => typeof x === "string" && x) : [];
}

function readableHits(
	payload: any,
	options?: { query?: string; rowFilter?: { capacity_kva?: number; system_nominal_voltage_kv?: number } },
): string {
	return formatSearchHits(payload?.hits ?? [], {
		query: options?.query,
		rowFilter: options?.rowFilter,
	});
}

/** Observability fields for /api/search. Never mixed into the retrieval query. */
export function searchStandardsRequestBody(args: {
	query: string;
	top_k?: number;
	file_ids?: string[] | null;
	row_filter?: Record<string, unknown>;
	scope: string[];
	identity?: ExecutionIdentity | null;
}): Record<string, unknown> {
	const identity = args.identity;
	const fileIds = args.file_ids ?? (args.scope.length ? args.scope : undefined);
	const body: Record<string, unknown> = {
		query: args.query,
		top_k: Math.min(args.top_k ?? 8, 20),
		query_routes: { production: args.query },
	};
	if (fileIds !== undefined) body.file_ids = fileIds;
	if (args.row_filter) body.row_filter = args.row_filter;
	const jobId = String(identity?.job_id || "").trim();
	const runId = String(identity?.run_id || "").trim();
	const caseId = String(identity?.case_id || "").trim();
	if (jobId) body.job_id = jobId;
	if (runId) body.run_id = runId;
	if (caseId) body.case_id = caseId;
	if (typeof identity?.job_attempt === "number" && Number.isFinite(identity.job_attempt)) {
		body.job_attempt = Math.floor(identity.job_attempt);
	}
	return body;
}

export function searchReportContextRequestBody(args: {
	terms: string[];
	max_results?: number;
	reportFileId: string;
	identity?: ExecutionIdentity | null;
}): Record<string, unknown> {
	const body: Record<string, unknown> = {
		terms: args.terms,
		max_results: Math.min(args.max_results ?? 8, 20),
		report_file_id: args.reportFileId,
	};
	const jobId = String(args.identity?.job_id || "").trim();
	if (jobId) body.job_id = jobId;
	return body;
}

/**
 * Build audit tools bound to one case's file scope. Each case gets its own
 * tool instances so per-case state (default file_ids) stays isolated under
 * concurrency.
 */
export function createAuditTools(
	fileScope: string[] | undefined | null,
	progress?: EvidenceProgress,
	identity?: ExecutionIdentity | null,
	reportFileId?: string | null,
) {
	const scope = normalizeScope(fileScope);
	const maxCalls = Math.max(1, Number(process.env.PI_MAX_TOOL_CALLS ?? "12"));
	let remaining = maxCalls;
	const budgetExceeded = () => ({
		content: [
			{
				type: "text" as const,
				text:
					`工具调用已达预算上限 ${maxCalls} 次。请基于已获取的证据直接输出最终 JSON 判定，不要再调用工具。`,
			},
		],
		details: { budget_exceeded: true },
	});
	function takeBudget(): ReturnType<typeof budgetExceeded> | null {
		if (remaining <= 0) return budgetExceeded();
		remaining -= 1;
		return null;
	}

	const boundReportId = String(reportFileId || "").trim();

	const searchStandards = defineTool({
		name: "search_standards",
		label: "检索标准知识库",
		description:
			"在标准知识库中按你给出的检索式原样做混合检索（后端不会再自动改写）。返回定位预览：表格给表题/列名/行绑定状态，条款给 query 命中窗口。不含表格单元格数值，不能凭预览判定。涉及限值/数值/条款必须再 read_chunk。任务里已有第一轮候选时，先读那些 chunk_id；读完仍不够再用本工具换表述补召回。",
		promptSnippet: "按原检索式检索标准知识库；返回定位预览，数值必须 read_chunk",
		promptGuidelines: [
			"使用 search_standards 时 query 会原样作为检索式，后端不再自动改写。写成完整自然语言，包含型号参数（如 S20-M.RL-400/10-NX2 400 kVA 10/0.4 kV）、试验项目名和要核对的参数名（如 空载损耗P0）。",
			"任务里已有第一轮候选时，先 read_chunk 那些片段；读完仍取不到适用限值，再用另一种表述调用本工具。",
			"审查表格限值时传 row_filter（capacity_kva、system_nominal_voltage_kv），预览只告诉你行绑定是否 unique，不会给出空载损耗等单元格数值。",
			"file_ids 可选：不传时默认限定在适用标准范围内；当已确定适用标准文档时也可显式传入以提高精度。",
		],
		parameters: Type.Object({
			query: Type.String({ description: "检索式，建议含型号、容量、电压、项目名、参数名" }),
			file_ids: Type.Optional(
				Type.Array(Type.String(), { description: "限定检索的标准文件 id 列表，可空" }),
			),
			top_k: Type.Optional(Type.Integer({ description: "返回条数，默认 8，最大 20" })),
			row_filter: Type.Optional(
				Type.Object({
					capacity_kva: Type.Optional(Type.Number({ description: "样机额定容量，如 400" })),
					system_nominal_voltage_kv: Type.Optional(
						Type.Number({ description: "系统标称电压（高压侧），如 10" }),
					),
				}),
			),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			if (progress?.isSearchBlocked()) {
				return {
					content: [{ type: "text" as const, text: progress.searchBlockedMessage() }],
					details: { search_blocked: true, ...progress.snapshot() },
				};
			}
			const blocked = takeBudget();
			if (blocked) return blocked;
			// 组装 applicability 形状的 row_filter（后端 bind_table_row 契约）。
			let rowFilter: Record<string, unknown> | undefined;
			if (params.row_filter) {
				const p: Record<string, number> = {};
				if (params.row_filter.capacity_kva != null) p.capacity_kva = params.row_filter.capacity_kva;
				if (params.row_filter.system_nominal_voltage_kv != null) {
					p.system_nominal_voltage_kv = params.row_filter.system_nominal_voltage_kv;
				}
				if (Object.keys(p).length > 0) rowFilter = { parameters: p };
			}
			const payload = await apiFetch(
				"/api/search",
				signal,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(
						searchStandardsRequestBody({
							query: params.query,
							top_k: params.top_k,
							file_ids: params.file_ids,
							row_filter: rowFilter,
							scope,
							identity,
						}),
					),
				},
			);
			const hits = (payload as any)?.hits ?? [];
			const locators = hits.map(toLocatorHit);
			const note = progress
				? progress.formatSearchNote(
						progress.recordSearch(
							params.query,
							locators.map((hit) => hit.chunk_id),
						),
					)
				: "";
			const text =
				`检索式: ${params.query}\n命中 ${hits.length} 条（定位预览，不含表格数值）：\n` +
				readableHits(payload, { query: params.query, rowFilter: params.row_filter }) +
				(note ? `\n\n${note}` : "");
			return {
				content: [{ type: "text", text }],
				details: { hits: locators, ...(progress ? progress.snapshot() : {}) },
			};
		},
	});

	const readChunk = defineTool({
		name: "read_chunk",
		label: "读取标准片段全文",
		description:
			"按 chunk_id 读取一个标准知识库片段的完整原文，含表格数据和业务元数据（标准号、表号、标题）。第一轮候选和 search_standards 都只返回定位预览；涉及限值/数值/条款的判定必须 read_chunk 确认原文后再下结论，不得仅凭预览判 insufficient_context。",
		promptSnippet: "读取第一轮候选或检索命中的标准片段完整原文（含表格数值）",
		promptGuidelines: [
			"使用 read_chunk 时 chunk_id 应来自任务里第一轮候选或 search_standards 命中，不要凭空构造。",
			"有第一轮候选时先读看起来相关的片段；读完仍不够再搜索，并对后继命中继续 read_chunk。",
			"涉及限值/数值/条款必须读取完整原文（尤其表格）确认数值，再给出判定。",
		],
		parameters: Type.Object({
			chunk_id: Type.String({
				description: "标准知识库片段 id（来自第一轮候选或 search_standards 命中）",
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

	const readReport = defineTool({
		name: "read_report",
		label: "读取检测报告",
		description:
			"读取一份检测报告的解析结果。按报告文件名读取，返回报告的核心信息：产品型号参数（parameters）、型号解码（model_decode）、审查汇总（summary），以及报告正文分段（若 report 字段是分段对象）。用于核对报告写入的试验项目与声称值。",
		promptSnippet: "读取检测报告的产品参数/型号解码/正文分段",
		promptGuidelines: [
			"使用 read_report 时报告名必须是已有报告文件名，形如 *.json。",
		],
		parameters: Type.Object({
			report_name: Type.String({ description: "报告文件名，例如 hbjc_end_to_end_audit_v1.json" }),
			section: Type.Optional(
				Type.String({ description: "可选；仅当报告正文是分段对象时用，读取该分段的内容。" }),
			),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			const payload = (await apiFetch(`/api/audit/reports/${encodeURIComponent(params.report_name)}`, signal)) as any;
			const report = payload?.payload;
			if (!report) throw new Error("报告响应缺少 payload 字段");

			// 报告正文：可能是分段对象（{seg: text}），也可能是路径字符串（MinerU markdown 路径）。
			const body = report.report;
			const bodyIsObject = body && typeof body === "object" && !Array.isArray(body);

			const parameters = report.parameters
				? JSON.stringify(report.parameters, null, 2).slice(0, 1600)
				: "(无)";
			const modelDecode = report.model_decode
				? (() => {
						const raw = typeof report.model_decode === "string" ? report.model_decode : JSON.stringify(report.model_decode, null, 2);
						return raw.slice(0, 1600);
					})()
				: "(无)";
			const summary = report.summary ? JSON.stringify(report.summary) : "(无)";

			const prefix = [
				`报告：${params.report_name}`,
				`--- 产品参数 parameters ---`,
				parameters,
				`--- 型号解码 model_decode ---`,
				modelDecode,
				`--- 审查汇总 summary ---`,
				summary,
				`--- 报告正文 ---`,
			];

			if (params.section != null) {
				if (bodyIsObject) {
					const sec = body[params.section];
					return {
						content: [{ type: "text", text: `报告 ${params.report_name} 分段[${params.section}]:\n${String(sec ?? "(无此分段)")}` }],
						details: { section: params.section, text: sec },
					};
				}
				return {
					content: [{ type: "text", text: `报告正文不是分段对象（内容: ${String(body).slice(0, 200)}），无分段[${params.section}]。${prefix.join("\n")}` }],
					details: { section: params.section },
				};
			}

			if (bodyIsObject) {
				const keys = Object.keys(body as Record<string, unknown>);
				const preview = keys.slice(0, 12).map((k) => `[${k}] ${String((body as Record<string, unknown>)[k]).slice(0, 200)}`);
				return {
					content: [{ type: "text", text: prefix.concat(`分段总数：${keys.length}`, "", ...preview).join("\n") }],
					details: { summary: report.summary },
				};
			}

			return {
				content: [
					{
						type: "text",
						text: prefix.concat(`[正文为外部文件路径，未内嵌；路径/内容: ${String(body).slice(0, 200)}]`).join("\n"),
					},
				],
				details: { summary: report.summary },
			};
		},
	});

	const searchReportContext = defineTool({
		name: "search_report_context",
		label: "检索当前检测报告",
		description:
			"在当前正在审查的检测报告正文里按字面词检索，返回命中窗口。用于补样本/试验事实（油箱结构、绝缘油类型、短路阻抗、绕组型式等），不能用来检索标准限值。sample_context 缺适用性参数时，判 applicability_undetermined 之前应先调用本工具。",
		promptSnippet: "在当前检测报告正文里按字面词检索样品/试验事实",
		promptGuidelines: [
			"使用 search_report_context 时 terms 写成报告里可能出现的原文，如 油箱、波纹、绝缘油、短路阻抗、绕组，不要用标准条款号当检索词。",
			"只能把命中窗口里写明的事实当作报告侧证据；窗口没有的不得用常识补。",
			"标准限值仍须 search_standards + read_chunk，不要用本工具搜标准。",
		],
		parameters: Type.Object({
			terms: Type.Array(Type.String({ minLength: 1 }), {
				minItems: 1,
				maxItems: 8,
				description: "报告正文里的字面检索词，1–8 个",
			}),
			max_results: Type.Optional(Type.Integer({ description: "返回窗口数，默认 8，最大 20" })),
		}),
		async execute(_id, params, signal, _onUpdate, _ctx) {
			const blocked = takeBudget();
			if (blocked) return blocked;
			if (!boundReportId) {
				return {
					content: [
						{
							type: "text" as const,
							text: "当前 case 未绑定检测报告，无法检索报告正文。",
						},
					],
					details: { matches: [], unbound: true },
				};
			}
			const payload = (await apiFetch("/api/search/report-context", signal, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(
					searchReportContextRequestBody({
						terms: params.terms,
						max_results: params.max_results,
						reportFileId: boundReportId,
						identity,
					}),
				),
			})) as { summary?: string; terms?: string[]; matches?: Array<Record<string, unknown>> };
			const matches = Array.isArray(payload?.matches) ? payload.matches : [];
			const lines = [
				payload?.summary || `found ${matches.length} report matches`,
				`terms: ${(payload?.terms || params.terms).join("、")}`,
			];
			if (!matches.length) {
				lines.push("报告正文未命中这些词。");
			} else {
				matches.forEach((item, index) => {
					lines.push(
						`[${index + 1}] term=${String(item.term || "")} @${item.char_start ?? "?"}\n${String(item.snippet || "")}`,
					);
				});
			}
			return {
				content: [{ type: "text" as const, text: lines.join("\n") }],
				details: { matches },
			};
		},
	});

	return [searchStandards, readChunk, readReport, searchReportContext];
}
