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
