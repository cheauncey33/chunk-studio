# Hybrid Retrieval Ablation

Status: `complete`
Cases: `40/40`; errors: `0`

> Gold limitation: all labels are pending domain review. These numbers compare retrieval policies; they are not final business accuracy.

## Overall

| Policy | K | Case recall | Evidence recall | MRR |
|---|---:|---:|---:|---:|
| dense_original | 1 | 0.053 (2/38) | 0.047 | 0.053 |
| dense_original | 3 | 0.079 (3/38) | 0.070 | 0.066 |
| dense_original | 5 | 0.132 (5/38) | 0.116 | 0.079 |
| dense_original | 10 | 0.263 (10/38) | 0.233 | 0.097 |
| dense_original | 20 | 0.500 (19/38) | 0.442 | 0.112 |
| dense_original | 40 | 0.763 (29/38) | 0.698 | 0.121 |
| hybrid_rrf | 1 | 0.053 (2/38) | 0.047 | 0.053 |
| hybrid_rrf | 3 | 0.079 (3/38) | 0.070 | 0.066 |
| hybrid_rrf | 5 | 0.184 (7/38) | 0.163 | 0.088 |
| hybrid_rrf | 10 | 0.342 (13/38) | 0.302 | 0.110 |
| hybrid_rrf | 20 | 0.474 (18/38) | 0.419 | 0.118 |
| hybrid_rrf | 40 | 0.789 (30/38) | 0.721 | 0.130 |
| hybrid_rerank | 1 | 0.316 (12/38) | 0.279 | 0.316 |
| hybrid_rerank | 3 | 0.447 (17/38) | 0.419 | 0.368 |
| hybrid_rerank | 5 | 0.526 (20/38) | 0.488 | 0.387 |
| hybrid_rerank | 10 | 0.526 (20/38) | 0.488 | 0.387 |
| hybrid_rerank | 20 | 0.658 (25/38) | 0.605 | 0.396 |
| hybrid_rerank | 40 | 0.789 (30/38) | 0.721 | 0.401 |

## Rerank vs RRF

| K | Wins | Losses | Net case hits | Both hit | Both miss |
|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 0 | 10 | 2 | 26 |
| 3 | 14 | 0 | 14 | 3 | 21 |
| 5 | 14 | 1 | 13 | 6 | 17 |
| 10 | 8 | 1 | 7 | 12 | 17 |
| 20 | 8 | 1 | 7 | 17 | 12 |
| 40 | 0 | 0 | 0 | 30 | 8 |

## By Retrieval Class at K=10

| Class | Dense | RRF | Rerank |
|---|---:|---:|---:|
| insulation_voltage | 0.375 | 0.375 | 0.500 |
| numeric_parameter_table | 0.083 | 0.083 | 0.333 |
| repeat_routine | 0.000 | 0.000 | 0.500 |
| report_specific | 0.375 | 0.750 | 0.875 |
| rule_formula | 0.375 | 0.375 | 0.500 |

## Timing

- Initial hybrid recall average: `2.481s`
- Reranker average with cached recall: `0.358s`
- Dense search average with reused embedding: `0.141s`
