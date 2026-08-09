# Judge Evidence Compression Specification

Status: Implemented as opt-in; default remains off  
Scope: fixed audit Judge input only

## Objective

Reduce the fixed Judge's semantic load by replacing full retrieved chunks with
at most five source-grounded Evidence Cards. The compressor may select and
extract evidence, but it may not judge compliance, invent values, or expand the
knowledge scope.

## Cost boundary

The compressor still reads the full delivered candidate set. It is expected to
reduce Judge context and variance, not automatically reduce total pipeline
tokens. Evaluation must report compressor input characters, compressed Judge
input characters, additional model calls, correctness, and repeated-run
variance.

## Contract

Each card must contain:

```json
{
  "candidate_key": "c01",
  "evidence_role": "nominal_rule|tolerance_rule|method_rule|applicability_rule",
  "quote": "contiguous verbatim excerpt from that candidate",
  "property": "",
  "standard_value": "",
  "unit": "",
  "operator": "eq|le|lt|ge|gt|range|tolerance|unknown",
  "scope": "",
  "conditions": [],
  "selection_reason": ""
}
```

The compressor returns `cards` and `missing_roles`. It never returns
`supported`, `mismatch`, or another audit status.

## Program validation

- Candidate keys must exist in the delivered set.
- Evidence roles must already be assigned to that candidate.
- Quotes must be contiguous substrings of source chunks.
- Numbers emitted as standard values must occur in the quote.
- Optional conditions are kept only when copied from the quote; ungrounded
  optional conditions are dropped without discarding an otherwise valid card.
- Required deterministic-comparison candidates cannot be omitted.
- At most five unique cards are accepted.
- If a numeric report claim has an available nominal candidate, compression
  must preserve at least one nominal card.
- If tolerance evidence is available for a tolerance claim, it must be
  preserved.
- Invalid individual cards are discarded. Compression may continue only when
  the remaining cards still cover every mandatory candidate and required role.
- A malformed response or incomplete remaining evidence chain falls back to
  the original full-candidate Judge input.
- If mandatory candidates already exceed the five-card budget, compression is
  skipped before any model call.

## Judge input

The compressed candidate keeps its original key, metadata, roles, and stable
locator. Its `text` becomes the verified quote and it receives an
`evidence_card` object. Table bindings, applicability evaluations, and
deterministic comparisons are filtered to selected candidate keys.

Full candidates remain in retrieval trace and are never deleted from audit
artifacts.

## Activation

Modes:

- `off`: current behavior;
- `active`: validated Evidence Cards are sent to Judge, with automatic fallback.

Compression remains opt-in until three-run evaluation shows no correctness or
false-definitive regression.

The first fixed-Judge ablation is recorded in
`judge-evidence-compression-report.md`. It is not sufficient to cross this
activation gate.
