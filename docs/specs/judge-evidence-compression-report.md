# Judge Evidence Compression Evaluation

Date: 2026-08-05  
Model: `deepseek-v4-flash`, thinking disabled  
Artifacts:

- `backend/data/reports/judge_compression_repeat_run1.json`
- `backend/data/reports/judge_compression_repeat_run2.json`
- `backend/data/reports/judge_compression_repeat_run3.json`

## Scope

Each run rejudged the same 21 tracked cases through the fixed Judge. Two tracked
cases whose persisted Recovery Gate action was `agent_recovery` were excluded,
so Recovery search cost and quality do not contaminate the compression result.

## Result

| Run | Correct | Accuracy | Applied | Fallback | False `supported` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 19/21 | 90.48% | 14/21 | 7/21 | 0 |
| 2 | 20/21 | 95.24% | 15/21 | 6/21 | 0 |
| 3 | 20/21 | 95.24% | 14/21 | 7/21 | 0 |
| Mean | 19.67/21 | 93.65% | 14.33/21 | 6.67/21 | 0 |

The uncompressed fixed-Judge baseline is 19/21 (90.48%). Compression improved
mean accuracy by 3.17 percentage points. The population standard deviation was
2.24 percentage points and the observed range was 90.48%-95.24%. The worst
compressed run equalled rather than underperformed the baseline.

Mean Judge-input reduction was 45.21%. After adding compressor input, the mean
end-to-end input-character proxy rose 30.30%. Every run made 20 additional
compressor calls; one case was skipped before calling the model because six
mandatory candidates could not fit into the five-card budget.

Two cases remained unstable:

- `item_a1925c5f10a4`: `insufficient_context`, `mismatch`, `mismatch`; expected
  `mismatch`.
- `item_14ac164a4090`: `insufficient_context`, `not_audited`,
  `insufficient_context`; expected `mismatch`. The uncompressed baseline was
  the riskier false `supported` result.

## Decision

The three-run development-set result satisfies the narrow no-accuracy-regression
and no-false-definitive gates: mean accuracy improved, the worst run matched the
baseline, and no run produced an incorrect `supported`. It does not establish
generalization or deterministic behavior because two cases changed status
across runs and the evaluation set is small and already used during
development.

Product decision on 2026-08-06: use validated one-shot evidence compression as
the production default because audit accuracy and avoiding false definitive
support take priority over the observed extra model-input cost. The in-app
production runner explicitly passes `--evidence-compression active`; CLI and
environment overrides remain available for controlled ablations. Invalid or
incomplete compression still falls back to the full candidate set.

Recovery is also enabled in production, but remains a bounded exception path:
three turns, four tool calls, and two search calls at most. Untyped Judge
uncertainty alone does not authorize broader retrieval; Recovery requires an
explicit missing report parameter, no delivered candidates, or `not_audited`
evidence absence.
