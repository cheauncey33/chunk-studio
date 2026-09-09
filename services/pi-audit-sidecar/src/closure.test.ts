import assert from "node:assert/strict";
import { test } from "node:test";

import { applyEvidenceClosure, attachAuthoritativeEvidence, closeEvidence } from "./closure.ts";

test("cited unread chunk fails provenance", () => {
	const result = closeEvidence({
		verdict: "match",
		evidence: [{ chunk_id: "table6" }],
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "cited_chunks_not_read");
});

test("match without any cited chunk fails provenance", () => {
	const result = closeEvidence({
		verdict: "mismatch",
		evidence: [],
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "no_cited_chunk");
});

test("placeholder chunk_id does not count as a citation", () => {
	const result = closeEvidence({
		verdict: "mismatch",
		evidence: [{ chunk_id: "7c549...placeholder" }],
		readBodies: { real: "body" },
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "no_cited_chunk");
});

test("cited and actually read chunk passes without any agent quote", () => {
	const result = closeEvidence({
		verdict: "match",
		evidence: [{ chunk_id: "sc", source: "GB/T 1", location: "第1条" }],
		readBodies: {
			sc: "短路试验后，各相电抗与平均电抗之差不应大于 2%。",
		},
	});
	assert.equal(result.passed, true);
	assert.equal(result.mode, "read");
	assert.equal(result.reason, "cited_chunks_read");
});

test("agent paraphrase or extra commentary is ignored by provenance", () => {
	const result = closeEvidence({
		verdict: "mismatch",
		evidence: [
			{
				chunk_id: "p0",
				text: "<tr><td>400</td><td>0.370</td></tr>（表头：空载损耗kW 分 S13、S14 / S20-NX2 两列）",
			},
		],
		readBodies: {
			p0: "<tr><td>400</td><td>0.410</td><td>0.370</td></tr>",
		},
	});
	assert.equal(result.passed, true);
});

test("host overwrites evidence.text from the read body", () => {
	const sealed = attachAuthoritativeEvidence(
		{
			verdict: "mismatch",
			evidence: [
				{
					chunk_id: "p0",
					source: "Q/GDW 12126.4-2024",
					location: "表6",
					text: "模型改写的 0.370 旁注",
				},
			],
		},
		{ p0: "<tr><td>400</td><td>0.370</td></tr>" },
	);
	assert.equal(
		(sealed?.evidence as Array<Record<string, unknown>>)[0].text,
		"<tr><td>400</td><td>0.370</td></tr>",
	);
});

test("two cited chunks must both have been read", () => {
	const result = closeEvidence({
		verdict: "match",
		evidence: [{ chunk_id: "table6" }, { chunk_id: "table1" }],
		readBodies: {
			table6: "额定容量kVA 400 空载电流% 0.16 短路阻抗% 4.0",
		},
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "cited_chunks_not_read");
});

test("failed provenance does not rewrite match/mismatch to unevaluable", () => {
	const applied = applyEvidenceClosure({
		verdict: "match",
		kind: "exact",
		evidence: [{ chunk_id: "c01", text: "tanδ ≤0.010" }],
	});
	assert.equal(applied.result?.verdict, "match");
	assert.equal(applied.result?.kind, "exact");
	assert.equal(applied.raw_verdict, "match");
	assert.equal(applied.closure.passed, false);
	assert.equal(applied.closure.reason, "cited_chunks_not_read");
});

test("unevaluable is not rewritten by the provenance guard", () => {
	const applied = applyEvidenceClosure({
		verdict: "unevaluable",
		kind: "applicability_undetermined",
		standard_value: null,
		evidence: [],
	});
	assert.equal(applied.result?.verdict, "unevaluable");
	assert.equal(applied.closure.applied, false);
});
