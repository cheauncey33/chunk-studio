# Query Harness v1

This file defines repeatable query-rewrite experiments for the human-reviewed
33-case retrieval test set. It is an evaluation harness, not the production
query planner.

## Shared contract

Input fields:

- `project_name`: report detection-item name.
- `reported_requirement`: report wording or claimed requirement.
- `sample_context`: extracted report parameters. Only non-empty values are usable.
- `standard_anchors`: standard identifiers explicitly present in the input.

Every model strategy must obey all of these rules:

1. Preserve `project_name` exactly.
2. Use only supplied facts; never infer missing parameters, model meanings, standard clauses, or a conclusion.
3. A reported requirement is an anchor to verify, never a confirmed fact.
4. Output JSON only: `{"semantic_query":"...","keyword_query":"..."}`.

## Strategies

### `raw_anchor`

No model call. Both queries are exactly `project_name`.

### `declarative_requirement`

Rewrite the detection item as a concise declarative search phrase for the
standard requirement governing the item. Do not add a generic prefix such as
"please find" and do not assume a table, a limit, or a conclusion exists.

### `interrogative_requirement`

Rewrite the detection item as a question that standard text can answer. Ask
what requirement, method, condition, or decision rule applies. Do not assume
that any one of those forms exists.

### `reverse_verification`

Write a neutral question asking whether the report's stated requirement for
the detection item has supporting standard evidence. Treat the stated
requirement as unverified. If it is empty, fall back to an interrogative
requirement query.

### `parameter_declarative`

Use every relevant non-empty sample parameter only as a qualifier, then write
a concise declarative phrase for the standard requirement governing the
detection item. Do not add missing parameters.

### `parameter_interrogative`

Use every relevant non-empty sample parameter only as a qualifier, then ask
what standard requirement applies to the detection item for that sample. Do
not add missing parameters.

### `numeric_table_exploration`

Generate a search phrase that expands recall toward numerical criteria,
allowed deviations, limits, table captions, or formulas related to the
detection item. This is an exploration direction: do not claim that a table
or a numerical criterion must exist.

### `condition_section_exploration`

Generate a search phrase that expands recall toward clauses about scope,
conditions, methods, exceptions, or applicability related to the detection
item. This is an exploration direction: do not claim that any condition must
exist.

## Evaluation protocol

1. Generate and persist queries before retrieval.
2. Run each strategy alone for all 33 answerable cases.
3. Compare Top-5, Top-10, MRR, evidence-group recall, and strict-complete recall.
4. Record per-case gains and losses against `raw_anchor`.
5. Only then evaluate selected two-, three-, and four-strategy RRF combinations.
6. Never overwrite an existing run directory; a run records prompt version,
   model, corpus state, retrieval settings, generated queries, and rankings.
