import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applicabilityIssues, quoteMatch } from './applicability.ts';
import { systemPrompt } from './prompt.ts';

test('reviewed sample facts are reusable but cannot silently change value', () => {
    const fields = { core: { state: 'confirmed', value: '电工钢', evidence_quote: '铁芯电工钢' } };
    const check = { condition: '铁芯', parameter: 'core', value: '电工钢', source: '报告', state: 'confirmed', evidence_quote: '铁芯电工钢' };
    assert.deepEqual(applicabilityIssues({ verdict: 'match', applicability_checks: [check] }, ['铁芯电工钢'], [], fields), []);
    assert.ok(applicabilityIssues({ verdict: 'match', applicability_checks: [{ ...check, value: '非晶合金' }] }, ['铁芯电工钢'], [], fields).includes('reviewed_parameter_value_changed'));
    assert.ok(applicabilityIssues({ verdict: 'match', applicability_checks: [check] }, ['铁芯电工钢'], [], { core: { ...fields.core, state: 'conflict' } }).includes('unresolved_reviewed_parameter'));
});

test('report review changes the case instructions to reuse confirmed facts', () => {
    const prompt = systemPrompt({ hasFirstRound: true, hasParameterReview: true });
    assert.ok(prompt.includes('不要再次解码型号'));
    assert.ok(prompt.includes('只有影响当前标准适用性时才搜索报告'));
});

test('presentation differences pass without fuzzy numeric matching', () => {
	assert.equal(quoteMatch('样品名称：油浸式变压器', ['样品名称: \n油浸式变压器']), 'format_only');
	assert.equal(quoteMatch('铁芯材质 电工钢 —', ['<tr><td>铁芯材质</td><td>电工钢</td><td>—</td></tr>']), 'format_only');
	assert.equal(quoteMatch('≤0.370 kW', ['≤0.410 kW']), 'unmatched');
	assert.equal(quoteMatch('≤0.370 kW', ['≥0.370 kW']), 'unmatched');
	assert.equal(quoteMatch('10', ['1 0']), 'unmatched');
	assert.equal(quoteMatch('10 kV', ['10 V']), 'unmatched');
});

test('stitched, paraphrased and absent conditions do not pass', () => {
	assert.equal(quoteMatch('型号S20……容量400', ['型号S20；其他信息；容量400']), 'unmatched');
	assert.equal(quoteMatch('电工钢——（无字母）', ['电工钢 —']), 'unmatched');
	assert.equal(quoteMatch('闭口', ['密封式']), 'unmatched');
});

test('read standard quotes pass separately from sample evidence', () => {
	const checks = [
		{ condition: '调压方式', source: '报告', state: 'confirmed', evidence_role: 'sample_fact', evidence_quote: 'regulation_method: 有载调压' },
		{ condition: '表9范围', source: '表9', state: 'confirmed', evidence_role: 'standard_condition', evidence_quote: '表9 有载调压' },
	];
	assert.deepEqual(applicabilityIssues({ verdict: 'match', applicability_checks: checks }, ['regulation_method: 有载调压'], ['表9 有载调压']), []);
	assert.ok(applicabilityIssues({ verdict: 'match', applicability_checks: checks }, ['regulation_method: 有载调压']).includes('unverified_applicability_quote'));
});

test('standard title cannot replace sample facts', () => {
	const check = { condition: '闭口', source: '表6', state: 'confirmed', evidence_role: 'sample_fact', evidence_quote: '闭口' };
	assert.ok(applicabilityIssues({ verdict: 'match', applicability_checks: [check] }, ['密封式'], ['表6 闭口']).includes('unverified_applicability_quote'));
	assert.ok(applicabilityIssues({ verdict: 'match', applicability_checks: [{ ...check, evidence_role: 'standard_condition' }] }, [], ['表6 闭口']).includes('missing_sample_parameter_evidence'));
});

test('definitive verdict cannot omit condition evidence', () => {
	assert.deepEqual(applicabilityIssues({ verdict: 'match' }, []), ['missing_applicability_checks']);
});
test('unknown or fabricated sample conditions fail the evidence contract', () => {
	const issues = applicabilityIssues({ verdict: 'match', applicability_checks: [{ condition: '闭口', source: '型号经验', state: 'unknown', evidence_quote: '闭口' }] }, ['报告仅记载密封式']);
	assert.ok(issues.includes('unresolved_applicability_condition'));
	assert.ok(issues.includes('unverified_applicability_quote'));
});
test('literal source evidence passes; unevaluable needs no forced confirmation', () => {
	assert.deepEqual(applicabilityIssues({ verdict: 'match', applicability_checks: [{ condition: '铁芯材料', source: '命名文件', state: 'confirmed', evidence_quote: '无H表示电工钢' }] }, ['默认规则：无H表示电工钢']), []);
	assert.deepEqual(applicabilityIssues({ verdict: 'unevaluable' }, []), []);
});
