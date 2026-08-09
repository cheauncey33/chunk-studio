# Audit Workflow De-case Optimization Specification

Status: Core runtime implemented; evaluation recorded in `decase-implementation-report.md`  
Branch: `codex/pi-agentic-rag`  
Objective: remove development-case-shaped behavior while preserving or improving retrieval recall and audit correctness on unseen cases.

## 1. Non-negotiable constraints

The following constraints are architecture gates, not implementation suggestions:

1. Freeze evaluation before implementation. Separate development, validation, hidden holdout, and generated mutation sets. Development-case replay is regression evidence only.
2. Remove case-shaped patches. Runtime code and prompts must not contain case IDs, fixed clause numbers, tracked-case values, project-name routing tables, or sentence-shaped fixes derived from one failure.
3. Normalize report requirements and standard evidence into one generic audit-claim intermediate representation before comparison.
4. Resolve applicability as generic constraints over report parameters and retrieved standard evidence. Project keywords must not select an applicability branch in code.
5. Drive Recovery by missing evidence roles or unresolved constraints, not by known cases, projects, clause numbers, or expected answers.
6. Bind tables through parsed header/row semantics and explicit conditions. Project-to-column dictionaries and model-selected rows are forbidden.
7. Restrict the fixed Judge to generic relation operations. It must not contain domain-project rules.

Any change that violates one of these constraints is rejected even when it improves the development-set score.

## 2. Evidence and metric boundary

The existing 22/23 result is a development-set replay result because the implementation was changed after observing e16/e18/e25. It must not be used as generalization evidence.

Before refactoring:

- preserve the current source, source data, test set, prompts, and evaluation reports;
- label the current tracked cases as `development_seen`;
- construct a hidden holdout whose expected verdicts are not read during implementation;
- group near-duplicates by standard clause/rule family before splitting so paraphrases of one rule cannot cross the split;
- generate mutation cases by changing values, units, comparators, scopes, names, table layouts, and irrelevant distractors;
- report retrieval recall and audit correctness separately.

Required metrics:

- strict complete retrieval recall at the fixed Judge delivery cutoff;
- evidence-role recall: applicability, nominal, tolerance, and method;
- end-to-end verdict accuracy and macro accuracy by audit-property family;
- false-definitive rate: incorrect `supported` or `mismatch` when evidence is insufficient;
- worst-group accuracy across standards, projects, table/text evidence, and comparison kinds;
- result variance across at least three model runs;
- Judge calls, Recovery activations, tool calls, input/output tokens, and wall time.

Token and latency metrics are constraints. Recall and correctness remain the primary objectives.

## 3. Runtime content prohibition

The following content is forbidden in generic runtime modules and Judge prompts:

- evaluation IDs such as `e16` or item identifiers;
- literal standard clause routing such as `branch -> 7.3.1.1`;
- concrete regression values such as `Dyn5/Dyn11` or a tracked formula pair;
- project/abbreviation-to-table-column routing tuples;
- regexes that encode one observed sentence instead of a reusable syntax;
- expected verdicts or gold evidence locators;
- per-case query expansions.

Standard numbers, clause numbers, formulas, project names, and values may appear in retrieved source evidence and evaluation fixtures. They must not determine runtime behavior through source-code branches.

Add a static anti-leakage check over:

- `backend/app/`;
- production portions of `scripts/run_report_audit_workflow.py`;
- Judge and Recovery prompts.

Tests and evaluation artifacts are excluded from the content ban, but production code must not import them.

## 4. Generic intermediate representation

Both report claims and evidence claims use the same representation:

```json
{
  "claim_id": "stable-within-run",
  "subject": {"type": "entity", "name": "..."},
  "property": {"concept": "...", "source_text": "..."},
  "value": {
    "kind": "quantity|range|expression|enum|text|count",
    "raw": "...",
    "normalized": null,
    "unit": null,
    "operator": "eq|lt|le|gt|ge|between|in|unknown"
  },
  "scope": {
    "basis": "total|per_entity|per_phase|per_group|unknown",
    "denominator": null,
    "multiplicity": null
  },
  "conditions": [],
  "evidence_role": "nominal_rule|tolerance_rule|method_rule|applicability_rule",
  "source_locator": {}
}
```

Requirements:

- parsing and comparison are separate operations;
- raw text and normalized values are both preserved;
- no numeric extraction from alphanumeric enum labels unless the grammar identifies a quantity;
- units, operators, scope, conditions, and provenance are mandatory for a definitive numeric comparison;
- parsing ambiguity is represented explicitly and cannot be silently resolved by the Judge.

## 5. Applicability constraint engine

Applicability conditions come from retrieved standard evidence, not Python project-name rules.

Represent conditions with generic operators:

```text
equals(field, value)
one_of(field, values)
less_than / less_or_equal / greater_than / greater_or_equal
between(field, lower, upper)
all_of / any_of / not
requires(field)
derive(target, expression, sources)
```

Execution result:

```json
{
  "state": "applicable|not_applicable|unresolved|ambiguous",
  "satisfied_constraints": [],
  "failed_constraints": [],
  "missing_parameters": [],
  "supporting_evidence": []
}
```

Rules:

- no branch may be selected without cited applicability evidence;
- derived parameters must include source fields and derivation provenance;
- conflicting applicability clauses produce `ambiguous`, not first-hit selection;
- an unresolved condition may trigger Recovery but cannot be converted to `rejudge_only` through a fixed field-name list;
- structured standard rules, if materialized offline, must be generated systematically for the corpus and retain source locators. Hand-written rules added in response to one failed case are forbidden.

## 6. Generic table normalization and binding

The table pipeline performs:

1. HTML/table structure normalization, including multi-row headers, merged cells, continuation tables, and units.
2. Header-path extraction for every value column.
3. Separation of condition columns and result columns through generic claim parsing.
4. Matching report/sample parameters against all row conditions.
5. Matching the report property against normalized header concepts.
6. Returning every fully compatible cell with its complete row and header path.

Deterministic selection is allowed only when exactly one cell satisfies all explicit conditions. Zero matches return `unresolved`; multiple matches return `ambiguous`.

The binder must not:

- route by project name or abbreviation;
- choose the first or highest-scoring partially matched row as authoritative;
- infer a missing condition from the expected verdict;
- compare values before property and applicability binding succeed.

## 7. Generic comparison engine

The comparison engine accepts two normalized claims and supports only reusable operations:

- quantity comparison with unit conversion;
- exact, upper/lower bound, open/closed range, and tolerance comparison;
- symbolic-expression canonicalization and equivalence;
- enum/text equality;
- set membership;
- scoped-count normalization when denominator and multiplicity are explicit;
- condition satisfaction and contradiction.

It returns `supports`, `conflicts`, `not_comparable`, or `ambiguous`, plus a machine-readable derivation trace.

It must not:

- search for evidence;
- select a standard clause;
- infer domain rules from project names;
- use the first formula or number found in a candidate;
- force `mismatch` when property, scope, unit, or applicability is unresolved.

## 8. Recovery and fixed-Judge boundary

Recovery receives typed gaps only:

```text
missing_applicability_evidence
missing_nominal_evidence
missing_tolerance_evidence
unresolved_reference
unbound_table
missing_report_parameter
ambiguous_claim
```

Allowed tools:

- scoped hybrid knowledge search;
- exact lexical search using terms extracted from the current report/evidence, not hard-coded terms;
- fetch chunk context;
- follow an explicit standard reference;
- search/extract report parameters;
- normalize a retrieved table;
- finish with recovered evidence or an unresolved reason.

A separate grep tool is not required. Scoped exact lexical search already provides the needed capability without exposing repository or filesystem search.

Recovery cannot produce the final verdict, execute Text2SQL, query audit history, render charts, or access unrelated knowledge bases.

The fixed Judge may:

- verify selected evidence roles;
- consume applicability, table-binding, and comparison traces;
- reject unsupported definitive conclusions;
- return `insufficient_context` when the generic engines remain unresolved.

The Judge may not override an unresolved generic trace using model domain knowledge.

## 9. Refactoring plan

### Phase 0: freeze and characterize

- Preserve the current replay reports and record the current branch SHA/configuration.
- Mark all cases used to design existing fixes as seen development cases.
- Build grouped validation, hidden holdout, and mutation manifests.
- Run the unchanged baseline and store per-case retrieval, verdict, cost, and trace results.

Exit gate: every reported metric has an explicit denominator and dataset label.

### Phase 1: remove case-shaped behavior

- Remove `applicability_exact_terms` and its fixed exact-retrieval injection.
- Remove project-name routing from `_target_header`.
- Replace the aggregate/subgroup sentence regex with structured scope parsing.
- Remove concrete regression examples from prompts.
- Replace the Ur-specific first-formula comparison with the generic expression path.
- Remove Recovery Gate field-name suppression.

Exit gate: the anti-leakage check passes and the old cases remain only in tests/evaluation data.

### Phase 2: introduce claim parsing

- Add typed claim, value, scope, condition, and locator schemas.
- Parse report requirements and candidate evidence independently.
- Preserve ambiguity and parse provenance.
- Add property-based tests across randomized quantities, enums, operators, units, and scopes.

Exit gate: parser tests are expressed as grammar classes, not tracked-case replicas.

### Phase 3: applicability and table execution

- Extract applicability constraints from retrieved evidence.
- Implement the generic constraint evaluator.
- Normalize tables and bind only fully satisfied rows/cells.
- Return unresolved/ambiguous states instead of best-effort authoritative matches.

Exit gate: the engines pass cross-standard and mutation tests without project-specific code paths.

### Phase 4: comparison and Judge integration

- Implement the generic comparison algebra.
- Pass typed traces to the fixed Judge.
- Fail closed when evidence roles, property binding, applicability, units, or scope are incomplete.
- Retain at most one Judge repair and do not add new model loops.

Exit gate: no deterministic override is possible without a complete trace to retrieved evidence.

### Phase 5: Recovery integration

- Trigger Recovery from typed unresolved states.
- Generate searches only from current report claims, retrieved evidence, and explicit references.
- Deduplicate queries and evidence before model reinjection.
- Stop when the requested role/constraint is resolved or the bounded budget is exhausted.

Exit gate: unaffected fixed-RAG cases add no Agent calls or tokens.

### Phase 6: blinded evaluation

- Freeze code and prompts before revealing hidden-holdout labels.
- Run development, validation, holdout, mutation, and control sets separately.
- Repeat model-dependent evaluation at least three times.
- Report per-group results, failures, cost, and variance; do not merge seen and unseen scores.

Exit gate: accept the refactor only when holdout/mutation correctness improves or remains stable, false-definitive results do not increase, and retrieval recall does not regress at the fixed delivery cutoff.

## 10. Change-admission rule

Every future audit-semantic change must include a short design record answering:

1. Can the rule be described without a case name, concrete project name, clause number, or tracked value?
2. Does it remain valid after changing the standard, property name, values, units, scope wording, and table layout?
3. Is it a syntax/logic capability or merely a phrase-specific pattern?
4. Does it pass automatically generated positive, negative, ambiguous, and distractor mutations?
5. Does it improve or preserve hidden-group performance rather than only seen-case replay?

Failure of any answer blocks the change from production runtime. A failing development case remains unresolved until a genuinely general capability satisfies these gates.

## 11. Deliverables

- generic audit-claim schemas and parsers;
- applicability constraint extractor/evaluator;
- table normalizer and deterministic binding trace;
- generic comparison engine;
- typed Recovery gap contract;
- runtime anti-leakage test;
- grouped evaluation manifests and mutation generator;
- before/after evaluation report separating seen and unseen performance;
- updated workflow trace showing every definitive verdict's evidence and computation path.
