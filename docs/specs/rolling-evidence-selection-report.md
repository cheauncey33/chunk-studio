# Rolling Evidence Selection Evaluation

Date: 2026-08-06  
Model: `deepseek-v4-flash`, thinking disabled  
Scope: 21 fixed-Judge tracked cases; no Recovery Agent

## Experiment

For every case, retrieval was rerun with a fixed typed Top-20 pool (10 tables
and 10 sections). A bounded selector scanned all candidates in batches of at
most five, carrying at most eight verified exact excerpts. It had no retrieval
tools and could not issue an audit verdict.

## Results

| Variant | Correct | Accuracy | False `supported` | Selection calls | Judge input reduction | Total input proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Uncompressed fixed Judge | 19/21 | 90.48% | not separately recorded | 0 | 0% | baseline |
| One-shot Evidence Cards, 3-run mean | 19.67/21 | 93.65% | 0 | 20/run | 45.21% | +30.30% |
| Rolling v1, selector opinions leaked to Judge | 15/21 | 71.43% | 0 | 81 | 59.47% | +48.32% |
| Rolling v2, source-only Judge isolation | 18/21 | 85.71% | 0 | 81 | 67.95% | +50.68% |

Rolling v1 is an invalid architecture for comparison because generated
unresolved/conflict text and selection reasons were injected into the Judge and
biased it toward `insufficient_context`. V2 fixed that boundary: the Judge saw
only verified quotes, source metadata, and deterministic annotations.

V2 still underperformed the uncompressed baseline by 4.76 percentage points
and the one-shot compression mean by 7.94 points. Its three misses were all
conservative rather than false approvals:

- impedance tolerance: `mismatch -> insufficient_context`;
- applied-voltage level: `mismatch -> insufficient_context`;
- insulating-liquid loss factor: `supported -> insufficient_context`.

The rolling selector corrected the previously unsafe lightning-impulse case to
`mismatch`, but that gain did not offset evidence lost or over-pruned in other
cases. One case fell back to full Top-20 input after an invalid conflict key.

## Decision

Reject the current rolling replacement-state design. It is safe-biased but less
accurate and substantially more expensive than one-shot global selection. Do
not integrate it into the production workflow or tune it with case-specific
prompt rules.

The useful retained idea is fixed-pool iteration without retrieval. A future
variant should avoid destructive rolling memory: independently extract small
evidence candidates from each batch, then perform one global consolidation over
all batch outputs. That map-then-reduce structure allows late evidence to
compete with early evidence without repeatedly rewriting and forgetting the
same mutable state.

Artifacts:

- `backend/data/reports/rolling_selection_top20_run1.json`
- `backend/data/reports/rolling_selection_top20_run2.json`
