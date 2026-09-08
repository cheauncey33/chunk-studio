/**
 * Verdict parsing with layered fallbacks (port of experiment runner.ts v7).
 *
 * 1. Strict: outermost {...} JSON.parse.
 * 2. Loose: field-by-field regex (models occasionally emit unbraced fields).
 * 3. Chinese prose: models occasionally close in natural language instead of
 *    JSON ("判定结果为'无法评估'……故判为'适用性未定'").
 */

export type ParsedVerdict = { value: Record<string, unknown> | null; mode: "strict" | "loose" | "prose" | "none" };

/** Detailed parse: value + which fallback layer produced it. */
export function parseVerdictDetailed(text: string): ParsedVerdict {
	if (!text) return { value: null, mode: "none" };
	const stripped = text.replace(/```(?:json)?/gi, "").trim();
	// 严格路径：找最外层 {} 对象并 JSON.parse
	const start = stripped.indexOf("{");
	const end = stripped.lastIndexOf("}");
	if (start >= 0 && end > start) {
		try {
			const parsed = JSON.parse(stripped.slice(start, end + 1));
			if (parsed && typeof parsed === "object" && "verdict" in parsed) {
				return { value: parsed, mode: "strict" };
			}
		} catch {
			// 继续走宽松路径
		}
	}
	// 宽松路径：模型偶尔把字段写成 Markdown 散列（无 {} 包裹、字段名/值拼错）。
	const out: Record<string, unknown> = {};
	const verdictMatch = stripped.match(/["']?verdict["']?\s*[:：]\s*["']?([a-z_]+)/i);
	if (verdictMatch) out.verdict = verdictMatch[1];
	const kindMatch = stripped.match(/["']?kind["']?\s*[:：]\s*["']?([a-z_]+)/i);
	if (kindMatch) out.kind = kindMatch[1];
	const caseMatch = stripped.match(/["']?case_id["']?\s*[:：]\s*["']?([^"',\s}]+)/i);
	if (caseMatch) out.case_id = caseMatch[1];
	const svMatch = stripped.match(/["']?standard_value["']?\s*[:：]\s*["']?([^"'\n,}]+)/i);
	if (svMatch) out.standard_value = svMatch[1];
	if ("verdict" in out) return { value: out, mode: "loose" };
	// 中文散文兜底：顺序敏感——unevaluable/mismatch 语义必须在 match 之前匹配。
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
	return "verdict" in out ? { value: out, mode: "prose" } : { value: null, mode: "none" };
}

/** Pull a minimal verdict object out of the final assistant text. */
export function parseVerdict(text: string): any | null {
	return parseVerdictDetailed(text).value;
}

/** Map sidecar verdict taxonomy to the production judgment status vocabulary. */
export function verdictToStatus(verdict: unknown): string | null {
	const map: Record<string, string> = {
		match: "supported",
		mismatch: "mismatch",
		unevaluable: "insufficient_context",
		out_of_scope: "not_audited",
	};
	const v = String(verdict ?? "");
	return map[v] ?? null;
}
