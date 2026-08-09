# Retrieval Evaluation

This directory intentionally has one editable retrieval benchmark and one immutable historical baseline.

- `test_set.json`: the 40-case evidence-group test set. It has 33 answerable candidates, but every case remains pending qualified domain review. Its reported scoreable denominator is therefore **0**.
- `frozen/retrieval_eval_v1_candidate_2026-07-13/`: immutable candidate-gold baseline snapshot. It is retained only for historical, relative-policy comparison and must not be presented as domain-approved accuracy.
- `FINAL_EXPERIMENT_SUMMARY.md`: the sole retained interpretation of the frozen experiments.

Run the contract and locator checks with:

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/validate_test_set.py
```

The root-level v1 candidate pools, seed sets, intermediate reviews, and one-off ablation outputs were removed after migration. Runtime prompts, the domain tokenizer dictionary, and versioned manual rules remain because they are application configuration rather than evaluation datasets.

## Human review

Open `test_set.json` and review the report requirement, direct evidence and applicability. For a personal demo, approve the whole set in one command:

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/review_test_set.py --approve-all
uv run python scripts/validate_test_set.py
```

The project owner is the human reviewer; AI locator checks are supporting evidence, not approval.
