/**
 * Type-aware search / first-round previews for the audit agent.
 *
 * Search locates evidence; read_chunk fetches it. Previews must not dump
 * matched table cell values or a blind text[:800] prefix.
 */
export type PreviewHit = {
	chunk_id?: unknown;
	candidate_key?: unknown;
	file_name?: unknown;
	file_id?: unknown;
	page?: unknown;
	score?: unknown;
	content_type?: unknown;
	text?: unknown;
	business_metadata?: unknown;
	row_binding?: unknown;
	table_no?: unknown;
	table_title?: unknown;
	table_columns?: unknown;
	section?: unknown;
	section_title?: unknown;
	standard_no?: unknown;
	bind_state?: unknown;
	headers?: unknown;
};

export type RowFilterPreview = {
	capacity_kva?: number;
	system_nominal_voltage_kv?: number;
};

const SNIPPET_RADIUS = 250;
const MAX_SNIPPETS = 3;

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === "object" && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

function asText(value: unknown): string {
	return value == null ? "" : String(value).trim();
}

function contentType(hit: PreviewHit): string {
	const meta = asRecord(hit.business_metadata);
	return asText(hit.content_type || meta.content_type).toLowerCase();
}

function columnNames(hit: PreviewHit): string[] {
	const meta = asRecord(hit.business_metadata);
	const binding = asRecord(hit.row_binding);
	const raw = hit.table_columns ?? meta.table_columns ?? hit.headers ?? binding.headers;
	if (!Array.isArray(raw)) return [];
	return raw.map((item) => asText(item)).filter(Boolean);
}

export function stripMarkup(value: unknown): string {
	let text = asText(value);
	text = text.replace(/<eq\b[^>]*>|<\/eq>/gi, " ");
	text = text.replace(/<[^>]+>/g, " ");
	return text.replace(/\s+/g, " ").trim();
}

export function queryTerms(query: string): string[] {
	const source = asText(query);
	if (!source) return [];
	const found: string[] = [];
	const push = (term: string) => {
		const value = term.trim();
		if (value.length < 2) return;
		if (!found.some((item) => item.toLowerCase() === value.toLowerCase())) found.push(value);
	};
	for (const match of source.matchAll(/表\s*[A-Za-z]?\s*\d+/g)) push(match[0].replace(/\s+/g, ""));
	for (const match of source.matchAll(/第?\s*\d+(?:\.\d+)*\s*条/g)) push(match[0].replace(/\s+/g, ""));
	for (const match of source.matchAll(/\d+(?:\.\d+)?\s*(?:kVA|kV|kW|A|V|%)?/gi)) push(match[0]);
	for (const match of source.matchAll(/[\u4e00-\u9fff]{2,12}/g)) push(match[0]);
	for (const match of source.matchAll(/[A-Za-z][A-Za-z0-9._-]{1,}/g)) push(match[0]);
	return found;
}

export function extractSnippets(text: string, query: string): { term: string; snippet: string }[] {
	const body = stripMarkup(text);
	if (!body) return [];
	const terms = queryTerms(query);
	if (!terms.length) return [];
	const lower = body.toLowerCase();
	const matches: { term: string; index: number; length: number }[] = [];
	for (const term of terms) {
		const needle = term.toLowerCase();
		let start = 0;
		while (start < lower.length) {
			const index = lower.indexOf(needle, start);
			if (index < 0) break;
			matches.push({ term, index, length: term.length });
			start = index + Math.max(term.length, 1);
		}
	}
	if (!matches.length) return [];
	matches.sort((a, b) => b.length - a.length || a.index - b.index);
	const windows: { term: string; left: number; right: number }[] = [];
	for (const match of matches) {
		if (windows.length >= MAX_SNIPPETS) break;
		const left = Math.max(0, match.index - SNIPPET_RADIUS);
		const right = Math.min(body.length, match.index + match.length + SNIPPET_RADIUS);
		if (windows.some((window) => !(right <= window.left || left >= window.right))) continue;
		windows.push({ term: match.term, left, right });
	}
	windows.sort((a, b) => a.left - b.left);
	return windows.map((window) => {
		const prefix = window.left > 0 ? "…" : "";
		const suffix = window.right < body.length ? "…" : "";
		return {
			term: window.term,
			snippet: `${prefix}${body.slice(window.left, window.right).trim()}${suffix}`,
		};
	});
}

function locatorLines(hit: PreviewHit): string[] {
	const meta = asRecord(hit.business_metadata);
	const standardNo = asText(hit.standard_no || meta.standard_no);
	const tableNo = asText(hit.table_no || meta.table_no);
	const tableTitle = asText(hit.table_title || meta.table_title);
	const section = asText(hit.section || meta.section);
	const sectionTitle = asText(hit.section_title || meta.section_title);
	const lines: string[] = [];
	if (standardNo) lines.push(`标准号：${standardNo}`);
	if (tableNo || tableTitle) {
		lines.push(tableTitle ? `表${tableNo || "?"}：${tableTitle}` : `表${tableNo}`);
	}
	if (section || sectionTitle) {
		lines.push(sectionTitle ? `条款 ${section || ""} ${sectionTitle}`.trim() : `条款 ${section}`);
	}
	return lines;
}

function bindState(hit: PreviewHit): string {
	const binding = asRecord(hit.row_binding);
	return asText(hit.bind_state || binding.state);
}

function formatRowFilter(rowFilter?: RowFilterPreview): string {
	if (!rowFilter) return "";
	const parts: string[] = [];
	if (rowFilter.capacity_kva != null) parts.push(`${rowFilter.capacity_kva} kVA`);
	if (rowFilter.system_nominal_voltage_kv != null) {
		parts.push(`${rowFilter.system_nominal_voltage_kv} kV`);
	}
	return parts.join(" / ");
}

function formatTablePreview(hit: PreviewHit, rowFilter?: RowFilterPreview): string {
	const columns = columnNames(hit);
	const state = bindState(hit) || "none";
	const unique = state === "matched";
	const lines = [
		...locatorLines(hit),
		columns.length ? `列：\n${columns.join("\n")}` : "列：（未知，请 read_chunk）",
	];
	const filter = formatRowFilter(rowFilter);
	if (filter) lines.push(`row_filter:\n${filter}`);
	lines.push(`匹配状态：${unique ? "unique" : state}`);
	lines.push("不返回整张表，不返回单元格数值。要用限值请 read_chunk。");
	return lines.join("\n");
}

function formatSectionPreview(hit: PreviewHit, query: string): string {
	const lines = [...locatorLines(hit)];
	const snippets = extractSnippets(asText(hit.text), query);
	if (!snippets.length) {
		lines.push("无关键词窗口，正文未展开。要用条款原文请 read_chunk。");
		return lines.join("\n");
	}
	for (const [index, item] of snippets.entries()) {
		lines.push(`snippet ${index + 1}: 命中“${item.term}”`);
		lines.push(item.snippet);
	}
	return lines.join("\n");
}

export function formatHitPreview(
	hit: PreviewHit,
	index: number,
	options?: { query?: string; rowFilter?: RowFilterPreview },
): string {
	const score = hit.score;
	const scoreText =
		typeof score === "number" && Number.isFinite(score) ? score.toFixed(3) : asText(score);
	const head =
		`[${index + 1}] chunk_id=${asText(hit.chunk_id)}` +
		(asText(hit.candidate_key) ? ` key=${asText(hit.candidate_key)}` : "") +
		(asText(hit.file_name) || asText(hit.file_id)
			? ` file=${asText(hit.file_name)}(${asText(hit.file_id)})`
			: "") +
		(hit.page != null && asText(hit.page) ? ` page=${asText(hit.page)}` : "") +
		(scoreText ? ` score=${scoreText}` : "");
	const kind = contentType(hit);
	const body =
		kind === "table"
			? formatTablePreview(hit, options?.rowFilter)
			: formatSectionPreview(hit, options?.query || "");
	return `${head}\n${body}`;
}

export function toLocatorHit(hit: PreviewHit): Record<string, unknown> {
	const meta = asRecord(hit.business_metadata);
	const binding = asRecord(hit.row_binding);
	return {
		chunk_id: hit.chunk_id,
		content_type: contentType(hit) || undefined,
		standard_no: hit.standard_no || meta.standard_no,
		table_no: hit.table_no || meta.table_no,
		section: hit.section || meta.section,
		bind_state: hit.bind_state || binding.state,
		score: hit.score,
	};
}

export function formatSearchHits(
	hits: PreviewHit[],
	options?: { query?: string; rowFilter?: RowFilterPreview },
): string {
	if (!hits.length) return "（无命中）";
	return hits.map((hit, index) => formatHitPreview(hit, index, options)).join("\n\n");
}

export function formatFirstRoundCards(candidates: PreviewHit[], query?: string): string {
	if (!candidates.length) return "";
	const cards = candidates.map((hit, index) =>
		formatHitPreview(hit, index, { query: query || "" }),
	);
	return [
		`## 第一轮候选（先看这些）`,
		`工作流已经做过混合检索。下面是定位卡，不是完整证据，不含表格数值。`,
		`1. 先对看起来相关的 chunk_id 执行 read_chunk`,
		`2. 读完能取出适用限值 → 判定`,
		`3. 读完仍不够 → 再 search_standards，对后继命中继续 read_chunk 后判定`,
		`不要跳过 read、不要凭定位卡里的表号/条款号直接下数值结论。`,
		``,
		...cards,
	].join("\n");
}
