import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { systemPrompt } from "./prompt.ts";
import { searchReportContextRequestBody, searchStandardsRequestBody } from "./tools.ts";

const identity = {
	job_id: "job-1",
	run_id: "run-1",
	job_attempt: 2,
	case_id: "c01",
};

test("search_standards sends execution identity outside the retrieval query", () => {
	const body = searchStandardsRequestBody({
		query: "S20 400kVA 空载损耗P0",
		top_k: 8,
		scope: ["file-a"],
		identity,
	});
	assert.equal(body.query, "S20 400kVA 空载损耗P0");
	assert.deepEqual(body.query_routes, { production: "S20 400kVA 空载损耗P0" });
	assert.equal(body.job_id, "job-1");
	assert.equal(body.run_id, "run-1");
	assert.equal(body.case_id, "c01");
	assert.equal(body.job_attempt, 2);
	assert.equal(JSON.stringify(body.query_routes).includes("job-1"), false);
});

test("system prompt lists search_report_context for missing sample facts", () => {
	const prompt = systemPrompt({ hasFirstRound: true });
	assert.match(prompt, /search_report_context/);
	assert.match(prompt, /applicability_undetermined/);
});

test("search_report_context binds the current report outside the tool parameters", () => {
	const body = searchReportContextRequestBody({
		terms: ["波纹", "短路阻抗"],
		max_results: 8,
		reportFileId: "report-file",
		identity,
	});
	assert.deepEqual(body.terms, ["波纹", "短路阻抗"]);
	assert.equal(body.report_file_id, "report-file");
	assert.equal(body.job_id, "job-1");
	const source = readFileSync(fileURLToPath(new URL("./tools.ts", import.meta.url)), "utf8");
	const paramBlock = source.split('name: "search_report_context"')[1].split("async execute")[0];
	assert.equal(paramBlock.includes("report_file_id"), false);
	assert.equal(paramBlock.includes("job_id"), false);
});

test("search_standards tool parameters do not expose job identity to the prompt", () => {
	const source = readFileSync(fileURLToPath(new URL("./tools.ts", import.meta.url)), "utf8");
	const paramBlock = source.split('name: "search_standards"')[1].split("async execute")[0];
	assert.equal(paramBlock.includes("job_id"), false);
	assert.equal(paramBlock.includes("job_attempt"), false);
	assert.equal(paramBlock.includes("run_id"), false);
});
