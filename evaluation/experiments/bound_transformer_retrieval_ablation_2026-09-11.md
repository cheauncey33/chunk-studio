# Bound transformer retrieval ablation

Population: 440 AI corpus-verified evidence groups from the transformer extension. Each eligible case has one required evidence group. Query routes and rewrite fallback status are held fixed for the quota A/B.

## A. Per-type candidate quota 20 to 30

| Metric | Top 20 quota | Top 30 quota | Change |
|---|---:|---:|---:|
| Fusion-pool recall | 90.2% | 95.9% | +5.7 pp / +25 groups |
| Reranker Top-8 recall | 73.4% (323/440) | 73.6% (324/440) | +0.2 pp / +1 group |
| Reranker Top-15 recall | 85.5% (376/440) | 88.4% (389/440) | +3.0 pp / +13 groups |
| Reranker Top-20 recall | 87.5% (385/440) | 90.7% (399/440) | +3.2 pp / +14 groups |
| Reranker Top-30 recall | 88.9% (391/440) | 92.3% (406/440) | +3.4 pp / +15 groups |
| Average rerank candidates | 64.0 | 95.4 | +49.0% |
| Average rerank stage | 351.6 ms | 389.3 ms | +37.7 ms / +10.7% |

The quota increase removes all 25 deterministic pre-fusion truncation losses. It does not materially improve Top-8 because the larger pool also changes reranker competition: 133 groups move into Top-8 and 53 move out, leaving a net gain of one group versus the quota-20 run.

## B. Initial-miss query ablation

Population: 18 groups, 9 unique original queries, and 3 unique Gold chunks. Reranking is disabled; the metric is production-only candidate-pool Top-30 recall.

| Query variant | Top-30 recall |
|---|---:|
| Original | 0/18 (0.0%) |
| Symbol normalization | 0/18 (0.0%) |
| Fields and table headers | 0/18 (0.0%) |
| Fields, headers, and all declared standards | 0/18 (0.0%) |
| Restored model/capacity/voltage only | 9/18 (50.0%) |
| Parameter-targeted standard only | 5/18 (27.8%) |
| Restored context and targeted standard | 18/18 (100.0%) |

The transformer-extension cases omit sample model, rated capacity, rated voltage, and sample name even though the legacy cases retain them. Restoring those fields recovers nine table groups. Routing the reference-temperature query narrowly to GB/T 1094.1-2013 recovers five section groups. The combined variant also routes loss-table queries to GB/T 6451-2023 and recovers all 18 groups without using a Gold table number, section number, locator, or quote.

The 100% result is a deterministic diagnostic upper bound, not a production rewrite score: the parameter-to-standard mapping is handcrafted. It shows that the indexed chunks are retrievable and that the main failure is missing discriminative query context plus overly broad or failed standard routing, rather than symbol spelling alone or reranker behavior.

## Decision

- Do not make quota 30 the default solely for Top-8. Keep it as a configurable option or use it when Top-15/30 coverage matters.
- Restore sample model/capacity/voltage before retrieval.
- Add deterministic parameter-family routes for high-value structured fields, then evaluate the rule set on the full 440 groups before changing production behavior.
