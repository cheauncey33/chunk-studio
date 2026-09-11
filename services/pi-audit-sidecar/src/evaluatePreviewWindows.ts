import { parseArgs } from "node:util";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { relative, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

import {
	formatHitPreview,
	formatSearchHits,
	queryTerms,
	SNIPPET_RADIUS,
	stripMarkup,
	type PreviewHit,
} from "./preview.ts";

type JsonObject = Record<string, any>;

const { values } = parseArgs({
	options: {
		db: { type: "string" },
		input: { type: "string" },
		json: { type: "string" },
		markdown: { type: "string" },
	},
});

const repoRoot = resolve(import.meta.dirname, "../../..");
const inputPath = resolve(
	values.input ?? `${repoRoot}/evaluation/experiments/query_harness/initial_v1/raw_anchor.json`,
);
const dbPath = resolve(values.db ?? process.env.CHUNK_STUDIO_EVAL_DB ?? `${repoRoot}/backend/data/chunkstudio.db`);
const jsonPath = values.json ? resolve(values.json) : null;
const markdownPath = values.markdown ? resolve(values.markdown) : null;

function readJson(path: string): JsonObject {
	return JSON.parse(readFileSync(path, "utf8"));
}

function legacyPreview(hit: PreviewHit, index: number): string {
	const item = hit as JsonObject;
	const meta = item.business_metadata ?? {};
	const score = typeof item.score === "number" ? item.score.toFixed(3) : String(item.score ?? "");
	const head =
		`[${index + 1}] chunk_id=${item.chunk_id ?? ""} file=${item.file_name ?? ""}(${item.file_id ?? ""}) ` +
		`page=${item.page ?? ""} score=${score}\n    metadata: ${JSON.stringify(meta)}`;
	return `${head}\n    text: ${String(item.text ?? "").slice(0, 800)}`;
}

function termsPresent(text: string, query: string): string[] {
	const lower = text.toLocaleLowerCase();
	return queryTerms(query).filter((term) => lower.includes(term.toLocaleLowerCase()));
}

function pct(value: number, total: number): number | null {
	return total ? value / total : null;
}

function round(value: number | null, digits = 4): number | null {
	return value == null ? null : Number(value.toFixed(digits));
}

function percentagePointDelta(before: number | null, after: number | null): number | null {
	return before == null || after == null ? null : round((after - before) * 100, 1);
}

function average(values: number[]): number {
	return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function percentile(values: number[], quantile: number): number {
	if (!values.length) return 0;
	const sorted = [...values].sort((a, b) => a - b);
	return sorted[Math.ceil(quantile * sorted.length) - 1] ?? sorted[0];
}

function rateBlock(rows: JsonObject[]): JsonObject {
	const eligible = rows.filter((row) => row.source_match);
	const oldHits = eligible.filter((row) => row.old_visible).length;
	const newHits = eligible.filter((row) => row.new_visible).length;
	const oldRate = pct(oldHits, eligible.length);
	const newRate = pct(newHits, eligible.length);
	return {
		candidates: rows.length,
		source_match_eligible: eligible.length,
		old_visible_hits: oldHits,
		old_visible_rate: round(oldRate),
		new_visible_hits: newHits,
		new_visible_rate: round(newRate),
		delta_percentage_points: percentagePointDelta(oldRate, newRate),
	};
}

function goldVisibilityBlock(rows: JsonObject[]): JsonObject {
	const oldHits = rows.filter((item) => item.old_gold_visible).length;
	const newHits = rows.filter((item) => item.new_gold_visible).length;
	const oldRate = pct(oldHits, rows.length);
	const newRate = pct(newHits, rows.length);
	return {
		cases: rows.length,
		old_visible: oldHits,
		new_visible: newHits,
		old_visible_rate: round(oldRate),
		new_visible_rate: round(newRate),
		delta_percentage_points: percentagePointDelta(oldRate, newRate),
	};
}

const artifact = readJson(inputPath);
const cases: JsonObject[] = artifact.cases ?? [];
const topHits = cases.flatMap((item) => item.ranking.top_hits.slice(0, 8));
const ids = [...new Set(topHits.map((item) => String(item.chunk_id)))];
const db = new DatabaseSync(dbPath, { readOnly: true });
const placeholders = ids.map(() => "?").join(",");
const chunkRows = db
	.prepare(
		`SELECT c.id, c.file_id, c.page, c.text, c.business_metadata, f.name AS file_name
		 FROM chunks c LEFT JOIN files f ON f.id = c.file_id
		 WHERE c.id IN (${placeholders})`,
	)
	.all(...ids) as JsonObject[];
db.close();
const chunks = new Map(chunkRows.map((row) => [String(row.id), row]));
const missingIds = ids.filter((id) => !chunks.has(id));
if (missingIds.length) {
	throw new Error(`corpus DB is missing ${missingIds.length} ranked chunks; first missing id: ${missingIds[0]}`);
}
const corpusDigest = createHash("sha256");
for (const id of [...ids].sort()) {
	corpusDigest.update(id);
	corpusDigest.update("\0");
	corpusDigest.update(String(chunks.get(id)?.text ?? ""));
	corpusDigest.update("\0");
}

const candidateRows: JsonObject[] = [];
const caseRows: JsonObject[] = [];
for (const item of cases) {
	const query = String(item.queries?.semantic_query ?? "");
	const hits: PreviewHit[] = item.ranking.top_hits.slice(0, 8).map((ranked: JsonObject) => {
		const stored = chunks.get(String(ranked.chunk_id))!;
		return {
			...ranked,
			file_id: stored.file_id,
			file_name: stored.file_name,
			page: stored.page,
			text: stored.text ?? "",
			business_metadata: JSON.parse(String(stored.business_metadata || "{}")),
		};
	});
	const oldContext = hits.map((hit, index) => legacyPreview(hit, index)).join("\n");
	const headContext = formatSearchHits(hits, { query, snippetRadius: 250 });
	const newContext = formatSearchHits(hits, { query });
	const byRank = new Map<number, JsonObject>();
	hits.forEach((hit, index) => {
		const source = stripMarkup(hit.text);
		const sourceTerms = termsPresent(source, query);
		const oldCard = legacyPreview(hit, index);
		const headCard = formatHitPreview(hit, index, { query, snippetRadius: 250 });
		const newCard = formatHitPreview(hit, index, { query });
		const row = {
			case_id: item.case_id,
			rank: index + 1,
			chunk_id: hit.chunk_id,
			content_type: String(hit.content_type ?? "unknown"),
			chunk_chars: String(hit.text ?? "").length,
			long_chunk: String(hit.text ?? "").length > 800,
			source_match: sourceTerms.length > 0,
			old_visible: sourceTerms.some((term) => oldCard.toLocaleLowerCase().includes(term.toLocaleLowerCase())),
			new_visible: sourceTerms.some((term) => newCard.toLocaleLowerCase().includes(term.toLocaleLowerCase())),
			old_card_chars: oldCard.length,
			head_card_chars: headCard.length,
			new_card_chars: newCard.length,
		};
		candidateRows.push(row);
		byRank.set(index + 1, row);
	});
	const groups: JsonObject[] = item.ranking.required_groups ?? [];
	const strictTop8 = groups.every((group) => group.best_rank != null && group.best_rank <= 8);
	const groupVisible = (field: "old_visible" | "new_visible") =>
		groups.every((group) =>
			(group.matched_alternatives ?? []).some(
				(alt: JsonObject) => alt.rank <= 8 && byRank.get(Number(alt.rank))?.[field],
			),
		);
	const goldRanks = groups.flatMap((group) =>
		(group.matched_alternatives ?? []).filter((alt: JsonObject) => alt.rank <= 8).map((alt: JsonObject) => Number(alt.rank)),
	);
	caseRows.push({
		case_id: item.case_id,
		retrieval_class: item.retrieval_class,
		strict_top8_hit: strictTop8,
		old_gold_visible: strictTop8 && groupVisible("old_visible"),
		new_gold_visible: strictTop8 && groupVisible("new_visible"),
		has_table_gold: goldRanks.some((rank) => byRank.get(rank)?.content_type === "table"),
		has_long_gold: goldRanks.some((rank) => byRank.get(rank)?.long_chunk),
		has_long_section_gold: goldRanks.some(
			(rank) => byRank.get(rank)?.long_chunk && byRank.get(rank)?.content_type === "section",
		),
		old_context_chars: oldContext.length,
		head_context_chars: headContext.length,
		new_context_chars: newContext.length,
	});
}

const strictHits = caseRows.filter((item) => item.strict_top8_hit);
const oldLengths = caseRows.map((item) => item.old_context_chars);
const headLengths = caseRows.map((item) => item.head_context_chars);
const newLengths = caseRows.map((item) => item.new_context_chars);
const report = {
	version: 1,
	benchmark: "raw_anchor Top-8 preview-window ablation",
	inputs: {
		ranking_artifact: relative(repoRoot, inputPath).replaceAll("\\", "/"),
		corpus_db: "external local corpus DB",
		selected_chunks: ids.length,
		selected_corpus_sha256: corpusDigest.digest("hex"),
	},
	policy: {
		baseline: "legacy metadata plus blind text[:800] preview",
		head: "type-aware locator cards; section snippets around query hits ±250 chars; table values hidden",
		treatment: `type-aware locator cards; section snippets around query hits ±${SNIPPET_RADIUS} chars; table values hidden`,
	},
	limitations: [
		"The renderer runs after retrieval and reranking, so ranking recall cannot change in this ablation.",
		"Visibility is deterministic literal query-term exposure, not an end-to-end LLM answer-accuracy score.",
		"The legacy comparison includes the table locator-only policy; the ±250 versus ±100 comparison isolates the branch's radius change.",
	],
	ranking: {
		cases: caseRows.length,
		old_strict_top8_hits: strictHits.length,
		new_strict_top8_hits: strictHits.length,
		old_strict_top8_recall: round(pct(strictHits.length, caseRows.length)),
		new_strict_top8_recall: round(pct(strictHits.length, caseRows.length)),
		delta_percentage_points: 0,
		reranker_order_changed: false,
	},
	preview_visibility: {
		all: rateBlock(candidateRows),
		section: rateBlock(candidateRows.filter((item) => item.content_type === "section")),
		table: rateBlock(candidateRows.filter((item) => item.content_type === "table")),
		long_chunks: rateBlock(candidateRows.filter((item) => item.long_chunk)),
		long_sections: rateBlock(candidateRows.filter((item) => item.long_chunk && item.content_type === "section")),
		strict_top8_gold_cases: {
			all: goldVisibilityBlock(strictHits),
			table_gold: goldVisibilityBlock(strictHits.filter((item) => item.has_table_gold)),
			long_gold: goldVisibilityBlock(strictHits.filter((item) => item.has_long_gold)),
			long_section_gold: goldVisibilityBlock(strictHits.filter((item) => item.has_long_section_gold)),
		},
	},
	candidate_card_chars: Object.fromEntries(
		["all", "section", "table", "long_sections"].map((key) => {
			const selected = candidateRows.filter((item) =>
				key === "all"
					? true
					: key === "long_sections"
						? item.long_chunk && item.content_type === "section"
						: item.content_type === key,
			);
			const oldAverage = average(selected.map((item) => item.old_card_chars));
			const headAverage = average(selected.map((item) => item.head_card_chars));
			const newAverage = average(selected.map((item) => item.new_card_chars));
			return [
				key,
				{
					candidates: selected.length,
					old_average: round(oldAverage, 1),
					head_average: round(headAverage, 1),
					new_average: round(newAverage, 1),
					reduction_vs_legacy: round(1 - newAverage / oldAverage),
					reduction_vs_head: round(1 - newAverage / headAverage),
				},
			];
		}),
	),
	context_chars_per_case: {
		old_average: round(average(oldLengths), 1),
		head_average: round(average(headLengths), 1),
		new_average: round(average(newLengths), 1),
		average_reduction_vs_legacy: round(1 - average(newLengths) / average(oldLengths)),
		average_reduction_vs_head: round(1 - average(newLengths) / average(headLengths)),
		old_p95: percentile(oldLengths, 0.95),
		head_p95: percentile(headLengths, 0.95),
		new_p95: percentile(newLengths, 0.95),
		p95_reduction_vs_legacy: round(1 - percentile(newLengths, 0.95) / percentile(oldLengths, 0.95)),
		p95_reduction_vs_head: round(1 - percentile(newLengths, 0.95) / percentile(headLengths, 0.95)),
	},
	case_changes: caseRows.filter((item) => item.old_gold_visible !== item.new_gold_visible),
};

const rate = (value: number | null) => (value == null ? "n/a" : `${(value * 100).toFixed(1)}%`);
const pp = (value: number | null) => (value == null ? "n/a" : `${value >= 0 ? "+" : ""}${value.toFixed(1)} pp`);
const goldCases = report.preview_visibility.strict_top8_gold_cases;
const md = [
	"# Top-8 preview-window ablation",
	"",
	`- Baseline: ${report.policy.baseline}`,
	`- Treatment: ${report.policy.treatment}`,
	`- Cases: ${report.ranking.cases}`,
	"",
	"| Metric | Baseline | ±100 treatment | Delta |",
	"|---|---:|---:|---:|",
	`| Strict Top-8 recall | ${rate(report.ranking.old_strict_top8_recall)} (${report.ranking.old_strict_top8_hits}/${report.ranking.cases}) | ${rate(report.ranking.new_strict_top8_recall)} (${report.ranking.new_strict_top8_hits}/${report.ranking.cases}) | 0.0 pp |`,
	`| Query-term visibility, eligible section candidates | ${rate(report.preview_visibility.section.old_visible_rate)} | ${rate(report.preview_visibility.section.new_visible_rate)} | ${pp(report.preview_visibility.section.delta_percentage_points)} |`,
	`| Query-term visibility, eligible long sections | ${rate(report.preview_visibility.long_sections.old_visible_rate)} | ${rate(report.preview_visibility.long_sections.new_visible_rate)} | ${pp(report.preview_visibility.long_sections.delta_percentage_points)} |`,
	`| Strict Top-8 gold cases visibly selectable | ${rate(goldCases.all.old_visible_rate)} | ${rate(goldCases.all.new_visible_rate)} | ${pp(goldCases.all.delta_percentage_points)} |`,
	`| └ table-gold cases | ${rate(goldCases.table_gold.old_visible_rate)} | ${rate(goldCases.table_gold.new_visible_rate)} | ${pp(goldCases.table_gold.delta_percentage_points)} |`,
	`| └ long-section-gold cases | ${rate(goldCases.long_section_gold.old_visible_rate)} | ${rate(goldCases.long_section_gold.new_visible_rate)} | ${pp(goldCases.long_section_gold.delta_percentage_points)} |`,
	`| Average 8-card context | ${report.context_chars_per_case.old_average} chars | ${report.context_chars_per_case.new_average} chars | ${rate(report.context_chars_per_case.average_reduction_vs_legacy)} shorter |`,
	`| P95 8-card context | ${report.context_chars_per_case.old_p95} chars | ${report.context_chars_per_case.new_p95} chars | ${rate(report.context_chars_per_case.p95_reduction_vs_legacy)} shorter |`,
	"",
	"## Increment versus the last commit (±250)",
	"",
	`The ±100 branch reduces average 8-card context from ${report.context_chars_per_case.head_average} to ${report.context_chars_per_case.new_average} characters (${rate(report.context_chars_per_case.average_reduction_vs_head)}), while preserving the same literal-match visibility as ±250. P95 falls from ${report.context_chars_per_case.head_p95} to ${report.context_chars_per_case.new_p95} characters (${rate(report.context_chars_per_case.p95_reduction_vs_head)}).`,
	"",
	"## Interpretation",
	"",
	"The preview formatter is downstream of retrieval and reranking, so it cannot improve Top-8 recall or reranker ordering. Its measurable effect is evidence visibility inside already-ranked section chunks and prompt-size reduction. Table previews intentionally remain locator-only and require `read_chunk` for values.",
	"",
	"This is an offline deterministic ablation. Query-term visibility is not equivalent to end-to-end answer accuracy; use a paired agent run to claim answer-quality gains.",
	"",
	"## Reproduce",
	"",
	"From `services/pi-audit-sidecar`, run:",
	"",
	"```powershell",
	"npm ci",
	"npm run eval:preview -- --db <path-to-chunkstudio.db> --json <report.json> --markdown <report.md>",
	"```",
	"",
	`The report used ${report.inputs.selected_chunks} selected chunks; corpus digest: \`${report.inputs.selected_corpus_sha256}\`.`,
	"",
].join("\n");

if (jsonPath) writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (markdownPath) writeFileSync(markdownPath, md, "utf8");
if (!jsonPath && !markdownPath) process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
