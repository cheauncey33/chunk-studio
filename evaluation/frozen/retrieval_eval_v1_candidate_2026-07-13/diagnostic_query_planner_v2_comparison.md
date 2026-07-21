# Query Planner v2 Top20 Comparison

| Report | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | MRR | Rescued vs v1 top20 | Regressed vs v1 top20 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1_top10 | 26/38 | 0.684 | 0.628 | 0.719 | 0.191 | 0 | 5 |
| v1_top20 | 31/38 | 0.816 | 0.744 | 0.871 | 0.196 | 0 | 0 |
| v2_top20 | 29/38 | 0.763 | 0.698 | 0.784 | 0.224 | 3 | 5 |

## v2 vs v1_top20

- Rescued: hbjc-15-5-r1, hbjc-5-r1, hbjc-5-r3
- Regressed: ezc-4-r2, ezc-5-r2, whc-4-r2, whc-5-r2, xyc-5-r2
- Remaining misses: ezc-4-r2, ezc-5-r2, ezc-5-r4, whc-4-r2, whc-5-r2, whc-5-r4, xyc-4-r1, xyc-5-r2, xyc-5-r4
