# Direct Miss Analysis

Source: `baseline_current_retrieval.json` all_routes direct misses.

This analysis looks at the 12 cases where no direct gold evidence was recalled. They contain 16 missed direct evidence items because some rule/formula cases require both a value table and a formula/method section.

## Headline

- The corpus/chunks are mostly present: every missed direct evidence item appears somewhere in route top100 except none marked as corpus-missing here.
- The current failure is mainly ranking and query specificity, not proof that the standard files are absent.
- Baseline uses route top20 and final top10 per content type; many gold items are just outside that window or get diluted by RRF.

## Categories

| Category | Direct evidence items | Cases | Meaning |
|---|---:|---:|---|
| `energy_efficiency_table_rerank_gap` | 3 | 3 | GB 20052 table 1 is found by some routes but falls outside final table top10. |
| `formula_section_rerank_gap` | 4 | 4 | GB/T 1094.2 section 7.3 is usually found only by section_target and then RRF dilutes it. |
| `insulation_voltage_table_rerank_gap` | 2 | 2 | GB/T 1094.3 table 2 is found but table 3 / annex / method chunks outrank it. |
| `product_specific_table_gap` | 7 | 7 | Q/GDW 12126.4 table 6 loses to generic transformer performance tables. |

## Evidence-Level Detail

| Case | Target | Best route rank top100 | Merged rank with route top20 | Merged rank with route top100 | Baseline issue |
|---|---|---:|---:|---:|---|
| hbjc-5-r1 | Q/GDW 12126.4-2024 table 6 | 13 | 25 | 32 | route can find it, but RRF/final cutoff pushes it down |
| hbjc-7-r1 | GB/T 1094.3-2017 table 2 | 5 | 13 | 21 | near miss; final top10 cutoff/rerank problem |
| hbjc-5-r3 | Q/GDW 12126.4-2024 table 6 | 10 | 23 | 26 | route can find it, but RRF/final cutoff pushes it down |
| hbjc-5-r3 | GB/T 1094.2-2013 section 7.3 | 17 | 41 | 91 | route can find it, but RRF/final cutoff pushes it down |
| hbjc-15-5-r1 | Q/GDW 12126.4-2024 table 6 | 13 | 27 | 34 | route can find it, but RRF/final cutoff pushes it down |
| ezc-5-r2 | GB 20052-2024 table 1 | 11 | 19 | 20 | near miss; final top10 cutoff/rerank problem |
| ezc-10-r1 | GB/T 1094.3-2017 table 2 | 12 | 14 | 13 | near miss; final top10 cutoff/rerank problem |
| ezc-5-r4 | Q/GDW 12126.4-2024 table 6 | 31 | None | 37 | not in route top20; query depth/specificity problem |
| ezc-5-r4 | GB/T 1094.2-2013 section 7.3 | 13 | 36 | 96 | route can find it, but RRF/final cutoff pushes it down |
| whc-5-r2 | GB 20052-2024 table 1 | 5 | 15 | 9 | near miss; final top10 cutoff/rerank problem |
| whc-5-r4 | Q/GDW 12126.4-2024 table 6 | 29 | None | 32 | not in route top20; query depth/specificity problem |
| whc-5-r4 | GB/T 1094.2-2013 section 7.3 | 9 | 30 | 112 | route can find it, but RRF/final cutoff pushes it down |
| whc-14-2-5-r2 | GB 20052-2024 table 1 | 8 | 20 | 26 | near miss; final top10 cutoff/rerank problem |
| xyc-4-r1 | Q/GDW 12126.4-2024 table 6 | 18 | 27 | 20 | route can find it, but RRF/final cutoff pushes it down |
| xyc-5-r4 | Q/GDW 12126.4-2024 table 6 | 29 | None | 42 | not in route top20; query depth/specificity problem |
| xyc-5-r4 | GB/T 1094.2-2013 section 7.3 | 17 | 46 | 125 | route can find it, but RRF/final cutoff pushes it down |

## Interpretation

1. Q/GDW table 6 misses are the largest group. The queries often contain loss values and generic transformer terms, so generic GB/T 10228, GB/T 6451, GB/T 25446, JB/T 501 tables outrank the product-specific Q/GDW table.
2. GB 20052 table 1 and GB/T 1094.3 table 2 are not absent. They are near misses: route ranks are often 5-16, but final top10 table cutoff loses them.
3. GB/T 1094.2 section 7.3 is a routing issue. It is usually retrieved only by `section_target`; equal-weight RRF undervalues single-route method/formula evidence.
4. `table_target` helps overall, but for Q/GDW table 6 it is often weak because the generated query does not reliably preserve the product-specific standard/table identity.

## Suggested Next Fix Order

1. Add a diagnostic evaluator mode with larger `final_per_type` such as 20 to measure how many misses are pure cutoff misses.
2. Try a table-aware rerank that boosts exact metadata intent: standard family, table title, voltage level, capacity, model-derived product features, and evidence type.
3. Preserve strong single-route hits for specialized routes. For example, keep top N from `table_target` and `section_target` before global RRF merge.
4. Improve query planner prompts for report-value anchored retrieval, especially energy-efficiency table vs generic performance table and product-specific Q/GDW table routing.
5. Do not expand the test set until these 12 misses have a stable category and before/after metric.

