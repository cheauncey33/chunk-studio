# Standard Value Audit Evaluation

`standard_value_audit_v1.json` is a domain-review draft for four transformer audit projects: induced withstand voltage, applied withstand voltage, no-load loss, and load loss.

## Contracts

- `backend/schemas/standard_value_audit_card.schema.json` defines one report rule plus report, sample, test, and source context.
- `backend/schemas/standard_value_audit_result.schema.json` separates basis completeness, audit status, error types, adopted evidence, and human review.

`basis_assessment.status` and `audit_assessment.status` are independent. For example, a rule can be `supported` by available standards while its scope remains `available_bases_only` because a declared tender specification is missing.

## Cases

The 12 cases contain eight report-derived cases, two counterfactual wrong-value cases, and two context-ablation cases. They are executable gold drafts, not expert-approved production truth. `gold_status` must remain `draft_pending_domain_review` until a qualified reviewer confirms the applicability and normalized rules.

Evidence never stores a chunk UUID. A locator combines standard number, content type, section or table number, page range, and a SHA-256 of whitespace-normalized chunk text. Rebuilding unchanged parses resolves the same evidence; changed text intentionally fails validation and requires gold review.

## Retrieval seed experiment

`prompts/report_test_item_extraction_v1.md` defines the shared extraction
contract used on HBJC, EZC, WHC, and XYC. The model outputs remain runtime
reports; `report_test_item_extraction_review_v1.json` records item counts,
initial/repeat coverage, and source-quality exceptions without storing measured
results.

`retrieval_case_pool_v1.json` is a stratified 40-case pool with 10 reported
requirements per report. HBJC is development, EZC validation, and WHC plus XYC
test. Every case remains `candidate_pending_evidence_review`; the pool is not a
retrieval gold set until standard evidence locators are confirmed.

`retrieval_evidence_candidates_v1.json` contains the typed Top-20 candidate
review for all 40 cases. A first-pass `qwen3.6-27b` judge is followed by an
adversarial downgrade/reject pass and explicit main-review overrides. Selected
evidence uses stable locators only; non-verbatim model quotes are cleared. The
file remains pending domain review and must not be treated as final gold.

`retrieval_gold_candidates_v1.json` extends that review with leakage-isolated
gold-discovery queries and exact corpus inspection. It resolves every stored
locator uniquely and currently has direct evidence for 38 of 40 cases. The two
remaining cases depend on report attributes not yet confirmed by the extraction
schema: oil-tank construction for the 70% residual-pressure rule and winding
construction for the 2% reactance-change rule. They intentionally remain
uncertain instead of being forced into gold. Gold-discovery queries are never
valid evaluated retrieval inputs.

`retrieval_seed_cases_v1.json` defines eight HBJC retrieval seeds. Each case has
one production-style query plus semantic and keyword rewrites. Candidate
generation retrieves Top 20 tables and Top 10 sections independently for each
route and merges each type by reciprocal-rank fusion. Table candidates then
receive bounded, deterministic `table_title` and `table_columns` bonuses before
the final fixed quota of 10 tables plus 10 sections. Section scores are unchanged.

`retrieval_candidate_review_v1.json` preserves the first untyped, soft-prior
baseline review. It is not domain-approved gold: `gold_status` remains
`candidate_model_reviewed_pending_domain_review`. The later typed experiment is
written separately to `backend/data/reports/retrieval_candidates_typed_v1.json`
so the baseline is not presented as a review of a different candidate set.

`table_metadata_vector_rerank_v1.json` records an isolated experiment that gave
table-title vectors the highest fusion weight and table-header vectors a lower
weight. All three target ranks degraded, so the experiment is explicitly marked
`experiment_rejected_for_production`; its temporary embeddings were never stored
in the database.

Table-oriented cases may also define an optional `table_target` query. This
rewrite describes the expected table subject without copying the report's
claimed standard value. It is an experimental retrieval route, not yet an
online query-planner contract.

`table_target_query_experiment_v1.json` compares the three failed cases before
and after that route. The manually authored, leakage-controlled rewrites improve
all three base ranks and bring all targets into the existing metadata-reranked
table Top 10. The asset remains pending an LLM-generation test because manually
writing the intended table description is easier than generating it reliably
from report context.

`model_naming_rule_experiment_v1.json` replaces the manual Pk rewrite with a
`qwen-flash` interpretation of `S20-M.RL-400/10-NX2` using JB/T 3837-2024. The
generated table query ranks the target Q/GDW table 6 at 3 by itself, but evidence
quality issues and an equal-RRF fused rank of 13 keep the experiment out of the
production path.

`model_naming_rule_model_comparison_v1.json` compares the same experiment on
`qwen-flash` and `qwen3.6-27b`. The 27B model produces better segment evidence
and a target-table standalone rank of 2, but still fails exact-quote and
per-feature-evidence requirements; both models remain at fused rank 13.

The full 20-candidate texts are runtime reports under `backend/data/reports/` and
remain untracked because they contain current Chunk IDs and can be regenerated by:

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/build_retrieval_candidates.py
```

## Validation

Run contract and label checks without local corpus files:

```powershell
uv run python scripts/validate_audit_eval.py --skip-corpus
```

Run the full local check when the authorized report fixture and rebuilt chunk database are present:

```powershell
uv run python scripts/validate_audit_eval.py
```

The full check also verifies the report SHA-256, unique evidence resolution, and that each `source_excerpt` occurs verbatim in its resolved chunk.
