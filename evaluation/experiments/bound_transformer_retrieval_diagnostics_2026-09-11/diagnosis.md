# Retrieval stage diagnosis

Evidence groups: `440`. Alternatives inside each group are merged with OR semantics.

## Stage recall

| Stage | Recall |
|---|---:|
| Any raw source Top-30 | 95.9% |
| RRF fusion pool | 90.2% |
| RRF fusion Top-8 | 55.0% |
| RRF fusion Top-30 | 81.1% |
| Reranker Top-8 | 73.4% |
| Reranker Top-30 | 88.9% |

## Final outcome attribution

| Outcome | Groups |
|---|---:|
| top8 | 323 |
| reranked_9_30 | 68 |
| dropped_before_fusion | 25 |
| not_recalled | 18 |
| reranked_below_30 | 6 |

## Reranker movement

| Movement | Groups |
|---|---:|
| promoted_into_top8 | 130 |
| demoted_out_of_top8 | 49 |
| promoted_into_top30 | 39 |
| demoted_out_of_top30 | 5 |

## Raw route recall

| Route | Recall | Groups |
|---|---:|---:|
| dense:keyword:section | 16.3% | 129 |
| dense:keyword:table | 18.6% | 129 |
| dense:production:section | 49.3% | 440 |
| dense:production:table | 37.5% | 440 |
| dense:semantic:section | 60.5% | 129 |
| dense:semantic:table | 35.7% | 129 |
| lexical:keyword:section | 12.4% | 129 |
| lexical:keyword:table | 24.8% | 129 |
| lexical:production:section | 53.2% | 440 |
| lexical:production:table | 46.6% | 440 |

## Rewrite path

| Value | Groups | Raw source | Fusion pool | Fusion Top-8 | Rerank Top-8 | Rerank Top-30 |
|---|---:|---:|---:|---:|---:|---:|
| fallback | 311 | 94.2% | 86.8% | 56.9% | 66.9% | 85.2% |
| rewritten | 129 | 100.0% | 98.4% | 50.4% | 89.1% | 97.7% |

## Evidence type

| Value | Groups | Raw source | Fusion pool | Fusion Top-8 | Rerank Top-8 | Rerank Top-30 |
|---|---:|---:|---:|---:|---:|---:|
| section | 217 | 97.7% | 95.4% | 65.0% | 86.2% | 95.4% |
| section+table | 30 | 100.0% | 100.0% | 66.7% | 90.0% | 100.0% |
| table | 193 | 93.3% | 82.9% | 42.0% | 56.5% | 79.8% |

## Failed Group patterns

| Outcome | Groups | Rewrite | Evidence | Query |
|---|---:|---|---|---|
| dropped_before_fusion | 5 | fallback | section | 短路阻抗和负载损耗测量（例行试验） 参考温度（℃）：75 |
| dropped_before_fusion | 4 | fallback | table | 电压比测量和联结组标号检定（例行试验） 联结组标号：Dyn11 |
| not_recalled | 4 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 负载损耗Pₖ（kW）：≤2.185 |
| not_recalled | 4 | fallback | section | 短路阻抗和负载损耗测量（重复例行试验） 参考温度（℃）：75 |
| not_recalled | 4 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 负载损耗Pₖ（kW）：≤2.185 |
| dropped_before_fusion | 3 | fallback | table | 电压比测量和联结组标号检定（重复例行试验） 联结组标号：D/yn11 |
| dropped_before_fusion | 2 | rewritten | table | 声级测定（特殊试验） 测量距离X(m)：0.3 |
| dropped_before_fusion | 2 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 负载损耗Pₖ（kW）：≤1.265 |
| dropped_before_fusion | 2 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 负载损耗Pₖ（kW）：≤1.265 |
| dropped_before_fusion | 2 | fallback | table | 空载损耗和空载电流测量（例行试验） 空载损耗P₀（kW）：≤0.135 |
| dropped_before_fusion | 2 | fallback | table | 空载损耗和空载电流测量（重复例行试验） 空载损耗Pₒ（kW）：≤0.135 |
| dropped_before_fusion | 1 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 总损耗P总（kW）：≤1.400 |
| dropped_before_fusion | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 总损耗P总（kW）：≤1.400 |
| dropped_before_fusion | 1 | fallback | table | 空载损耗和空载电流测量（重复例行试验） 空载损耗Pₒ（kW）：≤0.37 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 总损耗P总（kW）：≤3.985 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（例行试验） 负载损耗Pₖ（kW）：≤3.615 |
| not_recalled | 1 | fallback | section | 短路阻抗和负载损耗测量（重复例行试验） 参考温度（ₖ）：75 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 总损耗P总（kW）：≤2.400 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 总损耗P总（kW）：≤3.985 |
| not_recalled | 1 | fallback | table | 短路阻抗和负载损耗测量（重复例行试验） 负载损耗Pₖ（kW）：≤3.615 |

Dropped-before-fusion raw source ranks span `21`–`30` among the retained source-route hits; the production per-type candidate cap is 20.

