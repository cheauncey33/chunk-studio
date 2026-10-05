/** Normalize presentation only; preserve words, numbers, units and operators. */
function quoteText(text: string): string {
	return text.replace(/<[^>]+>/g, ' ')
		.replace(/&(?:nbsp|amp|lt|gt|quot|apos);/g, entity => ({
			'&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'",
		}[entity] || entity))
		.replace(/[：，；（）]/g, char => ({ '：': ':', '，': ',', '；': ';', '（': '(', '）': ')' }[char] || char))
		.replace(/\s+/g, ' ')
		// Do not join separate ASCII/numeric tokens: "1 0" must not become "10".
		.replace(/([^A-Za-z0-9]) (?=\S)|(?<=\S) (?=[^A-Za-z0-9])/g, '$1')
		.trim();
}

export function quoteMatch(quote: string, sources: string[]): 'exact' | 'format_only' | 'unmatched' {
	if (!quote.trim()) return 'unmatched';
	if (sources.some(source => source.includes(quote))) return 'exact';
	const normalized = quoteText(quote);
	return normalized && sources.some(source => quoteText(source).includes(normalized)) ? 'format_only' : 'unmatched';
}

export function applicabilityDiagnostics(result: Record<string, unknown> | null, sources: string[], standardSources: string[] = []) {
	if (!Array.isArray(result?.applicability_checks)) return [];
	return result.applicability_checks.map((item, index) => {
		const check = item && typeof item === 'object' ? item as Record<string, unknown> : {};
		const role = check.evidence_role || 'sample_fact';
		return { index, condition: check.condition, source: check.source, evidence_role: role,
			quote_match: quoteMatch(String(check.evidence_quote || ''), role === 'standard_condition' ? standardSources : sources) };
	});
}

/** Validate the evidence contract, not the semantic truth of a model decision. */
export function applicabilityIssues(result: Record<string, unknown> | null, sources: string[], standardSources: string[] = [], reviewedFields: Record<string, Record<string, unknown>> = {}): string[] {
	if (!result || !['match', 'mismatch'].includes(String(result.verdict))) return [];
	if (!Array.isArray(result.applicability_checks) || !result.applicability_checks.length) return ['missing_applicability_checks'];
	const issues: string[] = [];
	let sampleFacts = 0;
	for (const item of result.applicability_checks) {
		if (!item || typeof item !== 'object') { issues.push('invalid_applicability_check'); continue; }
		const check = item as Record<string, unknown>;
		const role = check.evidence_role || 'sample_fact';
		if (role === 'sample_fact') sampleFacts += 1;
		else if (role !== 'standard_condition') issues.push('invalid_applicability_evidence_role');
		if (!String(check.condition || '').trim() || !String(check.source || '').trim()) issues.push('missing_condition_source');
		if (check.state !== 'confirmed') issues.push('unresolved_applicability_condition');
		const quote = String(check.evidence_quote || '').trim();
		const reviewed = reviewedFields[String(check.parameter || '')];
		if (role === 'sample_fact' && reviewed?.state === 'confirmed' && String(check.value) !== String(reviewed.value)) issues.push('reviewed_parameter_value_changed');
		if (role === 'sample_fact' && reviewed && reviewed.state !== 'confirmed' && quote === String(reviewed.evidence_quote || '')) issues.push('unresolved_reviewed_parameter');
		if (quoteMatch(quote, role === 'standard_condition' ? standardSources : sources) === 'unmatched') issues.push('unverified_applicability_quote');
	}
	if (!sampleFacts) issues.push('missing_sample_parameter_evidence');
	return [...new Set(issues)];
}
