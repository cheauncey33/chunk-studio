# Standard Value Audit Evaluation

`standard_value_audit_v1.json` is a domain-review draft for four transformer audit projects: induced withstand voltage, applied withstand voltage, no-load loss, and load loss.

## Contracts

- `backend/schemas/standard_value_audit_card.schema.json` defines one report rule plus report, sample, test, and source context.
- `backend/schemas/standard_value_audit_result.schema.json` separates basis completeness, audit status, error types, adopted evidence, and human review.

`basis_assessment.status` and `audit_assessment.status` are independent. For example, a rule can be `supported` by available standards while its scope remains `available_bases_only` because a declared tender specification is missing.

## Cases

The 12 cases contain eight report-derived cases, two counterfactual wrong-value cases, and two context-ablation cases. They are executable gold drafts, not expert-approved production truth. `gold_status` must remain `draft_pending_domain_review` until a qualified reviewer confirms the applicability and normalized rules.

Evidence never stores a chunk UUID. A locator combines standard number, content type, section or table number, page range, and a SHA-256 of whitespace-normalized chunk text. Rebuilding unchanged parses resolves the same evidence; changed text intentionally fails validation and requires gold review.

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
