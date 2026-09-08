/**
 * Chunk Studio audit RAG tools for the Pi harness.
 *
 * Three tools call the chunk-studio backend (sqlite/local profile) over HTTP:
 *   - search_standards — hybrid retrieval over the standards KB
 *   - read_chunk      — read one retrieved standard chunk in full (incl. tables)
 *   - read_report     — read a parsed inspection report's sections
 *
 * Base URL defaults to http://127.0.0.1:8000 and can be overridden with
 * CHUNK_STUDIO_API_BASE.
 */
import { Type, type TSchema, type Static } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

function apiBase(): string {
	return (process.env.CHUNK_STUDIO_API_BASE ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function apiFetch(
	path: string,
	signal: AbortSignal | undefined,
	init?: RequestInit,
): Promise<unknown> {
	const res = await fetch(`${apiBase()}${path}`, { ...init, signal });
	if (!res.ok) {
		const body = await res.text().catch(() => "");
		throw new Error(`chunk-studio ${path} -> HTTP ${res.status}: ${body.slice(0, 400)}`);
	}
	return res.json();
}

function readableHits(payload: any): string {
	const hits = payload?.hits ?? [];
	if (hits.length === 0) return "（无命中）";
	return hits
		.map((h: any, i: number) => {
			const meta = h.business_metadata ?? {};
			const head =
				`[${i + 1}] chunk_id=${h.chunk_id} file=${h.file_name}(${h.file_id}) page=${h.page} score=${h.score?.toFixed?.(3) ?? h.score}\n` +
				`    metadata: ${JSON.stringify(meta)}`;
			// 有行绑定时优先展示匹配行的列值——模型直接可读，无需读 HTML 表格。
			if (h.row_binding && h.row_binding.state === "matched" && h.row_binding.column_values) {
				const cv = h.row_binding.column_values;
				const cells = Object.entries(cv)
					.map(([k, v]) => `${k}=${v}`)
					.join(" | ");
				return head + `\n    ▶ 匹配行(按样机参数绑定): ${cells}`;
			}
			if (h.row_binding) {
				return head + `\n    ▶ 行绑定状态: ${h.row_binding.state}（未唯一匹配，详见原文）`;
			}
			return head + `\n    text: ${String(h.text ?? "").slice(0, 800)}`;
		})
		.join("\n");
}

export function createAuditTools() {
	const maxCalls = Math.max(1, Number(process.env.PI_MAX_TOOL_CALLS ?? "12"));
	let remaining = maxCalls;
	const budgetExceeded = () => ({
		content: [
			{
				type: "text" as const,
				text: `工具调用已达预算上限 ${maxCalls} 次。请基于已获取的证据直接输出最终 JSON 判定，不要再调用工具。`,
			},
		],
		details: { budget_exceeded: true },
	});
	function takeBudget(): ReturnType<typeof budgetExceeded> | null {
		if (remaining <= 0) return budgetExceeded();
		remaining -= 1;
		return null;
	}

	const searchStandards = defineTool({
	name: "search_standards",
	label: "检索标准知识库",
	description:
		"在标准知识库中按你给出的检索式原样做混合检索（后端不会再自动改写）。建议包含型号、额定容量、电压、试验项目、参数名；返回最相关的标准片段（表格/条款），含文件、页、业务元数据（标准号、表号）。命中不好时换一种表述再调用本工具。传入 row_filter（样机容量/电压）时，表格命中会直接给出匹配行的具体数值（如 400 kVA 行的空载损耗/空载电流/短路阻抗），无需再逐个读表格原文。",
	promptSnippet: "按原检索式检索标准知识库；命中不好时换表述再搜，可按样机参数直接返回表格匹配行",
	promptGuidelines: [
		"使用 search_standards 时 query 会原样作为检索式，后端不再自动改写。写成完整自然语言，包含型号参数（如 S20-M.RL-400/10-NX2 400 kVA 10/0.4 kV）、试验项目名和要核对的参数名（如 空载损耗P0）。",
		"第一次命中不够时，用另一种表述再调 search_standards（例如按标准号/表号/参数名拆开），不要指望一次调用内部帮你扩写。",
		"审查表格限值（损耗/电流/阻抗等）时务必传 row_filter（capacity_kva、system_nominal_voltage_kv，从产品型号参数解析为数字），命中表格会直接给出匹配行的全部列值，避免再读表格原文。",
		"file_ids 可选：当已确定适用标准文档时，可把检索限定在该文件内提高精度。",
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
				body: JSON.stringify({
					query: params.query,
					top_k: Math.min(params.top_k ?? 8, 20),
					file_ids: params.file_ids ?? undefined,
					row_filter: rowFilter,
					query_routes: { production: params.query },
				}),
			},
		);
		const text = `检索式: ${params.query}\n命中 ${(payload as any)?.hits?.length ?? 0} 条：\n` + readableHits(payload);
		return { content: [{ type: "text", text }], details: { hits: (payload as any)?.hits ?? [] } };
	},
});

	const readChunk = defineTool({
	name: "read_chunk",
	label: "读取标准片段全文",
	description:
		"按 chunk_id 读取一个标准知识库片段的完整原文，含表格数据和业务元数据（标准号、表号、标题）。search_standards 只返回截断的预览（最多 800 字符），涉及限值/数值/条款的判定，必须在拿到命中后用 read_chunk 读取对应片段的完整内容，确认表格数值或条款原文后再下结论，不得仅凭预览片段判 insufficient_context。",
	promptSnippet: "读取检索命中的标准片段完整原文（含表格数值）",
	promptGuidelines: [
		"使用 read_chunk 时 chunk_id 应来自 search_standards 命中的 chunk_id 字段，不要凭空构造。",
		"用 read_chunk 读取包含目标限值/数值/条款的片段，检索命中后必须读取完整原文（尤其表格）确认数值，再给出判定。",
	],
	parameters: Type.Object({
		chunk_id: Type.String({ description: "标准知识库片段 id（来自 search_standards 命中）" }),
	}),
	async execute(_id, params, signal, _onUpdate, _ctx) {
		const blocked = takeBudget();
		if (blocked) return blocked;
		const payload = await apiFetch(`/api/chunks/${encodeURIComponent(params.chunk_id)}`, signal);
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

		// product-facing core info from the audit payload
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

		// report field is a path string (e.g. MinerU markdown). Report that it's not inline content.
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

	return [searchStandards, readChunk, readReport];
}

export const auditTools = createAuditTools();