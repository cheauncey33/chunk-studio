import assert from "node:assert/strict";
import { test } from "node:test";

import { applyEvidenceClosure, closeEvidence } from "./closure.ts";

test("unitless 0.010 does not close against method text without a tanδ limit", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "unit_equivalent",
		standard_value: "0.010",
		evidence: [{ chunk_id: "c01" }],
		readBodies: {
			c01: "4.1 电容率和介质损耗因数(tan δ) 本标准规定测量方法。试样应保持 10 min ～ 15 min。",
		},
	});
	assert.equal(result.passed, false);
});

test("method-only tanδ evidence cannot close a 1% match (e05)", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "unit_equivalent",
		standard_value: "1%",
		evidence: [{ chunk_id: "c01" }],
		readBodies: {
			c01: "4.1 电容率和介质损耗因数(tan δ) 本标准规定测量方法，不给出限值。试样应保持 10 min。",
		},
	});
	assert.equal(result.passed, false);
	const applied = applyEvidenceClosure(
		{
			verdict: "match",
			kind: "unit_equivalent",
			standard_value: "1%",
			evidence: [{ chunk_id: "c01" }],
		},
		{ c01: "测量方法，不给出限值。" },
	);
	assert.equal(applied.result?.verdict, "unevaluable");
	assert.equal(applied.result?.kind, "standard_not_found");
	assert.equal(applied.raw_verdict, "match");
});

test("1% in oil-quality table closes unit-equivalent 0.010", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "unit_equivalent",
		standard_value: "0.010",
		evidence: [{ chunk_id: "oil" }],
		readBodies: {
			oil: "运行中变压器油质量：介质损耗因数 tanδ(90℃) 不应大于 1%。",
		},
	});
	assert.equal(result.passed, true);
	assert.equal(result.mode, "unit");
});

test("I0 0.16%(1+30%) fails when only 0.16 is in the read body", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "formula_aggregate",
		standard_value: "空载电流 0.16%(1+30%)",
		evidence: [{ chunk_id: "table6" }],
		readBodies: {
			table6: "额定容量kVA 400 空载电流% 0.16 短路阻抗% 4.0",
		},
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "quantity_not_in_evidence:30%");
});

test("I0 0.16%(1+30%) passes when 0.16 and 30 are both in read bodies", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "formula_aggregate",
		standard_value: "空载电流 0.16%(1+30%)",
		evidence: [{ chunk_id: "table6" }, { chunk_id: "table1" }],
		readBodies: {
			table6: "额定容量kVA 400 空载电流% 0.16 短路阻抗% 4.0",
			table1: "空载电流允许偏差 +30%",
		},
	});
	assert.equal(result.passed, true);
});

test("cited unread chunk cannot close from agent evidence.text", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "formula_aggregate",
		standard_value: "空载电流 0.16%(1+30%)",
		evidence: [
			{
				chunk_id: "table6",
				text: "额定容量kVA 400 空载电流% 0.16 短路阻抗% 4.0 允许偏差 +30%",
			},
		],
	});
	assert.equal(result.passed, false);
	assert.equal(result.reason, "cited_chunks_not_read");
});

test("no-load plus load-loss sum closes formula_aggregate", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "formula_aggregate",
		standard_value: "3.985 kW",
		evidence: [{ chunk_id: "a" }, { chunk_id: "b" }],
		readBodies: {
			a: "空载损耗 0.370 kW",
			b: "负载损耗 3.615 kW",
		},
	});
	assert.equal(result.passed, true);
	assert.equal(result.mode, "formula");
});

test("2% short-circuit rule in a standard clause closes a numeric match (hbjc-13-r4)", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "exact",
		standard_value: "试验后相电抗差 ≤2%",
		evidence: [{ chunk_id: "sc" }],
		readBodies: {
			sc: "短路试验后，各相电抗与平均电抗之差不应大于 2%。",
		},
	});
	assert.equal(result.passed, true);
	assert.equal(result.mode, "direct");
});

test("space thousands in a watt table closes 3.615 kW (hbjc-5-r1)", () => {
	const result = closeEvidence({
		verdict: "match",
		kind: "exact",
		standard_value: "≤3615 W（即 ≤3.615 kW）",
		evidence: [{ chunk_id: "gb20052-t1" }],
		readBodies: {
			"gb20052-t1": "400 kVA 2级 电工钢带 Dyn11/Yzn11 负载损耗 3 615 W",
		},
	});
	assert.equal(result.passed, true);
});

test("unevaluable is not rewritten by the closure guard", () => {
	const applied = applyEvidenceClosure({
		verdict: "unevaluable",
		kind: "applicability_undetermined",
		standard_value: null,
		evidence: [],
	});
	assert.equal(applied.result?.verdict, "unevaluable");
	assert.equal(applied.closure.applied, false);
});
