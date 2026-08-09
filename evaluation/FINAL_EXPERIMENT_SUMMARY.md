# Final Retrieval Experiment Summary

## Current benchmark status

`test_set.json` contains 40 report requirements: 33 answerable retrieval candidates, 3 context-required cases, and 4 deterministic-prefilter cases. The project owner reviewed the set; its scoreable retrieval denominator is 33. The file validates stable evidence locators against the local corpus.

## Retained historical baseline

The immutable snapshot at `frozen/retrieval_eval_v1_candidate_2026-07-13/` is candidate gold pending domain review. It is retained for relative policy comparison only.

| Frozen candidate comparison | Direct cases | Case recall | Direct MRR |
|---|---:|---:|---:|
| Baseline | 26/38 | 0.684 | 0.192 |
| C: reference expansion | 28/38 | 0.737 | 0.194 |
| BC: reference expansion plus continuation aggregation | 28/38 | 0.737 | 0.220 |
| A: table-column embedding | 21/38 | 0.553 | 0.197 |

The candidate comparison supports keeping reference expansion, optionally paired with continuation aggregation, and keeping table-column embedding disabled. It does not establish production accuracy or generalization.

## Migration decision

The v1 working pools, manually assembled seed cases, intermediate candidate reviews, and duplicated root-level ablation outputs were removed. The production report-audit workflow no longer has a `--case-pool` mode or a runtime dependency on those files. Future scoring must begin only after a qualified reviewer changes the relevant v2 cases to `human_approved`.
