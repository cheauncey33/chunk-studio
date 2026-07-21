# Retrieval Policy Diagnostics

All rows use `all_routes` against the same frozen candidate gold. These are diagnostic numbers, not final benchmark claims.

| Policy | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | MRR | Rescued | Regressed |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 26/38 | 0.684 | 0.628 | 0.719 | 0.191 | 0 | 0 |
| final_per_type_20 | 31/38 | 0.816 | 0.744 | 0.871 | 0.196 | 5 | 0 |
| preserve_special_routes | 26/38 | 0.684 | 0.628 | 0.727 | 0.192 | 0 | 0 |
| table_aware_rerank | 26/38 | 0.684 | 0.628 | 0.741 | 0.274 | 6 | 6 |
| combined_policy | 27/38 | 0.711 | 0.651 | 0.763 | 0.272 | 5 | 4 |

## Rescued From Baseline

- final_per_type_20: ezc-10-r1, ezc-5-r2, hbjc-7-r1, whc-14-2-5-r2, whc-5-r2
- preserve_special_routes: none
- table_aware_rerank: ezc-10-r1, hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3, hbjc-7-r1, xyc-4-r1
- combined_policy: ezc-10-r1, hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3, hbjc-7-r1

## Regressed From Baseline

- final_per_type_20: none
- preserve_special_routes: none
- table_aware_rerank: ezc-4-r1, ezc-4-r2, whc-4-r1, whc-4-r2, xyc-11-r2, xyc-5-r3
- combined_policy: ezc-11-r2, ezc-4-r1, hbjc-4-r1, whc-4-r1

## Remaining Misses

- baseline: ezc-10-r1, ezc-5-r2, ezc-5-r4, hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3, hbjc-7-r1, whc-14-2-5-r2, whc-5-r2, whc-5-r4, xyc-4-r1, xyc-5-r4
- final_per_type_20: ezc-5-r4, hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3, whc-5-r4, xyc-4-r1, xyc-5-r4
- preserve_special_routes: ezc-10-r1, ezc-5-r2, ezc-5-r4, hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3, hbjc-7-r1, whc-14-2-5-r2, whc-5-r2, whc-5-r4, xyc-4-r1, xyc-5-r4
- table_aware_rerank: ezc-4-r1, ezc-4-r2, ezc-5-r2, ezc-5-r4, whc-14-2-5-r2, whc-4-r1, whc-4-r2, whc-5-r2, whc-5-r4, xyc-11-r2, xyc-5-r3, xyc-5-r4
- combined_policy: ezc-11-r2, ezc-4-r1, ezc-5-r2, ezc-5-r4, hbjc-4-r1, whc-14-2-5-r2, whc-4-r1, whc-5-r2, whc-5-r4, xyc-4-r1, xyc-5-r4
