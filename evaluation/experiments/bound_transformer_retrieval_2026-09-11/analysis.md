# Bound retrieval error analysis

This analysis reuses the saved Top-30 result set; it does not call the query planner, retriever, or reranker again.

## Main metrics

| K | Case-weighted recall | Unique-gold coverage | Gold-chunk macro recall |
|---:|---:|---:|---:|
| 1 | 33.4% (147/440) | 18.3% (13/71) | 11.9% |
| 3 | 47.7% (210/440) | 36.6% (26/71) | 26.5% |
| 5 | 67.7% (298/440) | 56.3% (40/71) | 44.0% |
| 8 | 73.4% (323/440) | 60.6% (43/71) | 52.2% |
| 10 | 78.0% (343/440) | 62.0% (44/71) | 56.0% |
| 15 | 85.5% (376/440) | 70.4% (50/71) | 62.8% |
| 20 | 87.5% (385/440) | 78.9% (56/71) | 69.2% |
| 30 | 88.9% (391/440) | 87.3% (62/71) | 77.1% |

## Top-8 slices

### Query rewrite path

| Value | Recall | Cases |
|---|---:|---:|
| fallback | 67.4% | 301 |
| rewritten | 86.3% | 139 |

### Gold content type

| Value | Recall | Cases |
|---|---:|---:|
| section | 86.2% | 217 |
| table | 56.5% | 193 |
| section+table | 90.0% | 30 |

### Expected status

| Value | Recall | Cases |
|---|---:|---:|
| supported | 74.2% | 306 |
| not_audited | 67.6% | 71 |
| mismatch | 53.1% | 32 |
| insufficient_context | 100.0% | 31 |

### Domain

| Value | Recall | Cases |
|---|---:|---:|
| dielectric | 96.8% | 157 |
| electrical_parameters | 37.3% | 142 |
| mechanical | 83.7% | 141 |

## Top-30 diagnostic slices

| Slice | Value | Recall | Cases |
|---|---|---:|---:|
| rewrite_mode | fallback | 84.7% | 301 |
| rewrite_mode | rewritten | 97.8% | 139 |
| gold_content_type | section | 95.4% | 217 |
| gold_content_type | table | 79.8% | 193 |
| gold_content_type | section+table | 100.0% | 30 |
| domain | dielectric | 100.0% | 157 |
| domain | electrical_parameters | 67.6% | 142 |
| domain | mechanical | 97.9% | 141 |

## Confirmed failure signals

- Top-30 misses: 49 cases, 24 queries, covering 11 distinct gold chunks.
- Exact-content duplicates consume an average of 0.68 of 8 slots and 2.15 of 30 slots; 54/130 queries contain duplicates in Top-8.
- Dataset weighting: 440 cases collapse to 130 queries and 71 gold chunks; one gold chunk is repeated by as many as 62 cases.
- A case-level Top-K summary cannot prove whether a miss was absent from first-stage candidates or was pushed down by fusion/reranking; use the stage-level diagnostics for that split.

### Largest missed families

| Family | Missed cases |
|---|---:|
| 短路阻抗和负载损耗测量（重复例行试验） | 15 |
| 短路阻抗和负载损耗测量（例行试验） | 14 |
| 空载损耗和空载电流测量（重复例行试验） | 6 |
| 电压比测量和联结组标号检定（例行试验） | 4 |
| 空载损耗和空载电流测量（例行试验） | 4 |
| 声级测定（特殊试验） | 3 |
| 电压比测量和联结组标号检定（重复例行试验） | 3 |

### Gold chunks involved in Top-30 misses

| Standard locator | Type | Missed/total bindings | Covered by another query |
|---|---|---:|---:|
| GB/T 6451-2023 3 | table | 16/37 | yes |
| GB/T 6451-2023 1 | table | 12/26 | yes |
| GB/T 6451-2023 7 | table | 10/21 | yes |
| GB/T 1094.1-2013 11.1 | section | 10/10 | no |
| GB/T 6451-2023 4 | table | 8/8 | no |
| GB/T 25438-2010 1 | table | 5/6 | yes |
| GB/T 6451-2023 5 | table | 3/8 | no |
| GB/T 1094.10-2022 - | table | 1/1 | no |
| GB/T 1094.10-2022 - | table | 1/1 | no |
| GB/T 25438-2010 2 | table | 1/6 | yes |
| GB/T 6451-2023 7 | table | 1/4 | yes |

## Interpretation

1. Query rewrite degradation is strongly associated with misses, but this observational split is not an A/B test: the fallback queries may also be intrinsically harder.
2. Table evidence and the electrical_parameters domain are the dominant weak slices at Top-8.
3. Duplicate corpus rows consume retrieval slots and are a concrete efficiency defect, though they do not alone explain all misses.
4. Case-weighted recall overstates performance relative to equal-weight gold-chunk macro recall because a small set of chunks is reused heavily.
5. To separate first-stage recall from reranker loss, the next evaluation must persist the pre-rerank candidate list and score both stages from the same run.
