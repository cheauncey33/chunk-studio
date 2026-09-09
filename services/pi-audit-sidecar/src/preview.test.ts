import assert from "node:assert/strict";
import { test } from "node:test";

import { extractSnippets, formatFirstRoundCards } from "./preview.ts";

function paddedSection(): string {
	const pad = "适用条件与试验布置说明。".repeat(80);
	return [
		pad,
		"雷电冲击电压试验按下列要求进行。",
		pad,
		"绕组端子应耐受 75 kV。",
		pad,
		"限值列于表3。",
		pad,
	].join("");
}

test("first-round section cards emit multiple query-aware snippets", () => {
	const productionQuery = "GB/T 1094.3 雷电冲击电压 75 kV 表3";
	const cards = formatFirstRoundCards(
		[
			{
				chunk_id: "chk-section",
				candidate_key: "c02",
				content_type: "section",
				standard_no: "GB/T 1094.3-2017",
				section: "5.3",
				section_title: "绝缘水平",
				text: paddedSection(),
			},
		],
		productionQuery,
	);

	assert.match(cards, /条款 5\.3 绝缘水平/);
	assert.match(cards, /snippet 1: 命中“/);
	assert.match(cards, /snippet 2: 命中“/);
	assert.match(cards, /雷电冲击电压/);
	assert.match(cards, /75 kV/);
	assert.match(cards, /表3/);
	assert.doesNotMatch(cards, /无关键词窗口/);

	const snippets = extractSnippets(paddedSection(), productionQuery);
	assert.ok(snippets.length >= 2, `expected multiple snippets, got ${snippets.length}`);
	assert.ok(new Set(snippets.map((item) => item.term)).size >= 2);
});

test("first-round section cards stay locator-only without a query", () => {
	const cards = formatFirstRoundCards([
		{
			chunk_id: "chk-section",
			content_type: "section",
			section: "5.3",
			text: paddedSection(),
		},
	]);
	assert.match(cards, /无关键词窗口/);
	assert.doesNotMatch(cards, /snippet 1:/);
});

test("first-round table cards never dump matched cell values", () => {
	const cards = formatFirstRoundCards(
		[
			{
				chunk_id: "chk-table",
				content_type: "table",
				standard_no: "Q/GDW 12126.4-2024",
				table_no: "6",
				table_title: "10 kV 配电变压器性能参数",
				table_columns: ["额定容量", "空载损耗"],
				bind_state: "matched",
				row_binding: { state: "matched", column_values: { 空载损耗: "0.370" } },
				text: "<table><tr><td>空载损耗</td><td>0.370</td></tr></table>",
			},
		],
		"空载损耗 0.370",
	);
	assert.match(cards, /匹配状态：unique/);
	assert.match(cards, /空载损耗/);
	assert.doesNotMatch(cards, /0\.370/);
	assert.doesNotMatch(cards, /<table>/);
});
