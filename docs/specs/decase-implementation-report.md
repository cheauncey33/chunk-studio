# Audit Workflow De-case Implementation Report

Date: 2026-08-05  
Branch: `codex/pi-agentic-rag`  
Specification: `docs/specs/decase-optimization-spec.md`

## Implemented boundaries

- Removed fixed applicability clause lookup and its extra candidate injection.
- Removed project-name-to-table-column routing and Ur-specific formula override.
- Removed the sentence-shaped aggregate/subgroup regex.
- Removed fixed applicability-field suppression from Recovery Gate.
- Removed tracked category values from production prompts.
- Added generic report/evidence claim parsing for property, value kind, operator, unit, and scope.
- Added generic explicit applicability-predicate extraction and per-candidate evaluation. The evaluator never chooses a standard branch.
- Added multi-row/rowspan/colspan table normalization, full-row matching, unique property binding, and explicit unresolved/ambiguous states.
- Limited deterministic Judge override to a uniquely bound table claim carrying a complete trace.
- Added typed Recovery gaps and a production anti-leakage regression test.

The workflow intentionally no longer restores a failed development case by adding a clause, project, formula, or phrase rule.

## Verification

### Source and contract tests

- De-case semantic/Judge/Recovery focused tests: 89 passed.
- Executable Python suite excluding five pre-existing tests whose imported scripts are deleted in the current worktree: 304 passed.
- Full collection boundary: five collection errors from deleted scripts unrelated to this change.
- Frontend lint: passed with existing warnings.
- Frontend production build: passed.
- API health: `GET /api/health` returned 200 with `{"ok":true}`.
- Frontend: `http://127.0.0.1:5173` returned 200.

### Retrieval evaluation

Dataset: `evaluation/test_set.json`; 40 total cases, 33 scoreable retrieval cases, 36 required evidence groups. Corpus locator validation passed.

Hybrid plus rerank:

| Cutoff | Strict complete recall | Evidence-group recall |
| --- | ---: | ---: |
| Top-5 | 23/33 (69.70%) | 26/36 (72.22%) |
| Top-10 | 30/33 (90.91%) | 33/36 (91.67%) |
| Top-20 | 33/33 (100%) | 36/36 (100%) |

Split results at Top-10 / Top-20:

| Split | Cases | Strict@10 | Group@10 | Strict@20 | Group@20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| development | 7 | 6/7 (85.71%) | 7/8 (87.50%) | 7/7 (100%) | 8/8 (100%) |
| validation | 9 | 8/9 (88.89%) | 8/9 (88.89%) | 9/9 (100%) | 9/9 (100%) |
| test | 17 | 16/17 (94.12%) | 18/19 (94.74%) | 17/17 (100%) | 19/19 (100%) |

Artifact: `backend/data/reports/decase_retrieval.json`.

### End-to-end tracked replay

Model: `deepseek-v4-flash`; thinking disabled. The 23 tracked cases are already-seen development cases and are not generalization evidence.

| Final run | Correct | Accuracy | Recovery activations | Turns | Tool calls | Searches | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| run5 | 20/23 | 86.96% | 2 | 8 | 8 | 8 | 0 |
| run6 | 22/23 | 95.65% | 2 | 8 | 8 | 8 | 0 |
| run7 | 19/23 | 82.61% | 2 | 8 | 8 | 6 | 0 |

Three-run aggregate: 61/69 (88.41%), range 82.61%-95.65%. Persisted fixed baseline: 19/23 (82.61%).

Unstable or consistently unresolved classes across the final runs:

- impedance tolerance evidence: 0/3 correct;
- induction-level evidence recovery: 1/3 correct;
- impulse table/value binding: 2/3 correct;
- duration evidence judgment: 2/3 correct;
- one upper-bound judgment: 2/3 correct.

The earlier 22/23 targeted result is not treated as the new baseline because it used the removed fixed applicability lookup and was reported from one final run.

Artifacts:

- `backend/data/reports/decase_recovery_shadow_run5.json`
- `backend/data/reports/decase_recovery_shadow_run6.json`
- `backend/data/reports/decase_recovery_shadow_run7.json`

## Interpretation and remaining boundary

- Retrieval remains strong: complete strict recall at Top-20, including every declared split.
- The clean runtime improves the three-run mean over the persisted 19/23 baseline, but Judge/Recovery variance is still too large for an automatic production-default claim.
- Removing targeted rules converts some wrong definitive answers into `insufficient_context`, which is safer but lowers tracked defect accuracy.
- Exact input/output token counts are not exposed by the current `llm.chat_json` adapter. Calls, turns, searches, and elapsed time remain available; provider token accounting is a separate instrumentation task.
- The existing `test` retrieval split is reported separately, but it is not retroactively blind because the repository and results were already inspected during development. A genuinely hidden end-to-end audit set must be created and sealed before the next semantic iteration.

Do not add another case-shaped rule to close these failures. The next admissible work is corpus-wide property/condition claim extraction, unit-aware symbolic comparison, and stronger evidence segmentation, each validated on mutation and sealed holdout sets.
