# Retrieval stage diagnosis

Evidence groups: `440`. Alternatives inside each group are merged with OR semantics.

## Stage recall

| Stage | Recall |
|---|---:|
| Any raw source Top-30 | 95.9% |
| RRF fusion pool | 95.9% |
| RRF fusion Top-8 | 55.5% |
| RRF fusion Top-30 | 87.0% |
| Reranker Top-8 | 73.6% |
| Reranker Top-30 | 92.3% |

## Final outcome attribution

| Outcome | Groups |
|---|---:|
| top8 | 324 |
| reranked_9_30 | 82 |
| not_recalled | 18 |
| reranked_below_30 | 16 |

## Reranker movement

| Movement | Groups |
|---|---:|
| promoted_into_top8 | 133 |
| demoted_out_of_top8 | 53 |
| promoted_into_top30 | 32 |
| demoted_out_of_top30 | 9 |

## Raw route recall

| Route | Recall | Groups |
|---|---:|---:|
| dense:keyword:section | 29.1% | 148 |
| dense:keyword:table | 32.4% | 148 |
| dense:production:section | 49.3% | 440 |
| dense:production:table | 37.5% | 440 |
| dense:semantic:section | 56.1% | 148 |
| dense:semantic:table | 41.2% | 148 |
| lexical:keyword:section | 33.1% | 148 |
| lexical:keyword:table | 36.5% | 148 |
| lexical:production:section | 53.2% | 440 |
| lexical:production:table | 46.6% | 440 |

## Rewrite path

| Value | Groups | Raw source | Fusion pool | Fusion Top-8 | Rerank Top-8 | Rerank Top-30 |
|---|---:|---:|---:|---:|---:|---:|
| fallback | 292 | 93.8% | 93.8% | 56.5% | 68.5% | 89.4% |
| rewritten | 148 | 100.0% | 100.0% | 53.4% | 83.8% | 98.0% |

## Evidence type

| Value | Groups | Raw source | Fusion pool | Fusion Top-8 | Rerank Top-8 | Rerank Top-30 |
|---|---:|---:|---:|---:|---:|---:|
| section | 217 | 97.7% | 97.7% | 65.4% | 88.5% | 97.7% |
| section+table | 30 | 100.0% | 100.0% | 66.7% | 90.0% | 100.0% |
| table | 193 | 93.3% | 93.3% | 42.5% | 54.4% | 85.0% |

## Failed Group patterns

| Outcome | Groups | Rewrite | Evidence | Query |
|---|---:|---|---|---|
| not_recalled | 4 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 负载损耗Pₖ（kW）：≤2.185 |
| not_recalled | 4 | fallback | section | 短路阻抗和负载损耗测量（重复例行试验） 参考温度（℃）：75 |
| not_recalled | 4 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 负载损耗Pₖ（kW）：≤2.185 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 总损耗P总（kW）：≤3.985 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 负载损耗Pₖ（kW）：≤3.615 |
| not_recalled | 1 | fallback | section | 短路阻抗和负载损耗测量（重复例行试验） 参考温度（ₖ）：75 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 总损耗P总（kW）：≤2.400 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 总损耗P总（kW）：≤3.985 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 负载损耗Pₖ（kW）：≤3.615 |

Dropped-before-fusion raw source ranks span `None`–`None` among the retained source-route hits; the production per-type candidate cap is 20.

