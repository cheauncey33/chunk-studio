# Dual Retrieval Top-10 Miss Review v1

Status: `analysis_pending_domain_review`

Source: `backend/data/reports/dual_retrieval_v2_2026-07-15.json`

## Summary

The reported nine misses do not represent nine independent reranker failures.
They map to five unique target chunks, and none of those target inputs was
truncated by the 2,400-character reranker limit.

| Classification | Groups |
|---|---:|
| Strong missing gold alternative | 3 |
| Alternative pending domain review | 1 |
| Deterministic context policy mismatch | 2 |
| Evidence conflict pending domain review | 1 |
| True query/rerank miss | 2 |

## Cases

| Case | Reported target rank | Audit result | Evidence |
|---|---:|---|---|
| `hbjc-14-r1` | 13 | True query/rerank miss | Query combines LI voltage with T1/T2 waveform tolerances; procedure passages outrank the voltage table. |
| `hbjc-13-r4` | 17 | Evidence conflict | Query asks for phase reactance difference <=2%; gold gives each-phase change from original <=7.5%. Equivalence is unverified. |
| `ezc-4-r1` | >40 | Gold alternative missing | Q/GDW table 6 directly answers 0.215 kW at rank 4. |
| `ezc-10-r1` | 11 | Alternative pending review | JB/T 501 table 11 gives 10 kV -> LI 75 kV at rank 7. Gold explanation incorrectly describes AV 35 kV. |
| `whc-4-r1` | >40 | Gold alternative missing | Q/GDW table 6 directly answers 0.215 kW at rank 5. |
| `whc-4-r2` | 17 | Context policy mismatch | The target is a global +30% tolerance table, not the model-specific nominal-value evidence. |
| `whc-10-r1` | 15 | True query/rerank miss | Same multi-intent LI query problem as `hbjc-14-r1`. |
| `xyc-5-r2` | 32 | Gold alternative missing | Q/GDW table 6 directly answers 2.185 kW at rank 3. |
| `xyc-5-r3` | >40 | Context policy mismatch | Q/GDW table 6 gives 4.0% at rank 7; the gold target only gives the separate +/-10% tolerance. |

## Root Causes

### Reranker input

Input truncation is not the cause. The five unique target documents produce
reranker inputs between 280 and 1,944 characters, below the 2,400-character
limit.

The GB 20052 performance table is nevertheless difficult input: merged headers
and row/column relationships are flattened into a long number sequence. More
importantly, a shorter and more model-specific Q/GDW table already appears in
Top-10 for all three affected numeric cases, so these should be fixed as gold
alternatives before changing serialization.

### Query expression

The two confirmed misses use one query for both LI voltage and waveform
tolerances. The target table answers the voltage mapping only, while highly
overlapping procedure sections answer the test name and waveform concepts.
Split these into evidence subqueries before reranking.

The reactance case also embeds the report value `2%`, while the gold evidence
contains `7.5%` and may describe a different metric. That is an evidence-contract
problem, not yet a retrieval defect.

### Evaluation contract

Generic tolerance tables should be deterministic context or separate evidence
groups. A tolerance-only chunk must not be labeled as an independently complete
answer to a query that also requires a model-specific nominal value.

## Recommended Order

1. Review and repair gold alternatives and the conflicting reactance case.
2. Move general tolerance tables to deterministic context or separate groups.
3. Recompute Top-10 metrics.
4. Test LI voltage/waveform query decomposition on the two remaining misses.
5. Tune or replace the reranker only if misses remain after these corrections.
