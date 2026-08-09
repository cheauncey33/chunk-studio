# Rolling Evidence Selection Experiment

Status: experimental; production workflow unchanged  
Primary objective: improve fixed-pool Judge correctness without new retrieval

## Hypothesis

Hybrid retrieval plus reranking already reaches complete recall at Top-20 on
the reviewed retrieval set. The remaining bottleneck is selecting a small,
complete, source-grounded evidence set for the fixed Judge. A bounded LLM loop
will scan all twenty candidates in batches while carrying only retained exact
quotes, unresolved questions, and conflicts.

## Boundary

- The experiment consumes one immutable reranked Top-20 pool.
- It cannot issue a query, call retrieval, follow references, or inspect files.
- It processes every candidate; `evidence_sufficient=true` changes later rounds
  to counter-evidence/exception review but never stops the scan early.
- It does not output `supported`, `mismatch`, or another audit verdict.
- Production compression and Recovery defaults remain off.

## Loop state

Immutable state:

- report requirement and test item;
- sample profile;
- candidate key, source metadata, and original text.

Mutable state:

```json
{
  "retained": [
    {"candidate_key": "c01", "quote": "exact source excerpt", "reason": ""}
  ],
  "unresolved": [],
  "conflicts": [
    {"candidate_keys": ["c01", "c07"], "description": ""}
  ],
  "evidence_sufficient": false
}
```

The host validates candidate keys and exact quotes. One round may keep,
replace, or remove earlier evidence. Invalid output fails the selection and
falls back to the uncompressed Top-20 Judge input; it does not trigger
Recovery.

## Batching

- Default maximum five new candidates per round.
- A character budget may end a batch before five candidates.
- Retained evidence is exact excerpts, never full prior chunks.
- At most eight retained excerpts are delivered to the Judge.
- All Top-20 candidates are processed exactly once unless a failed round forces
  full-input fallback.

## Judge delivery

Selected candidates preserve source metadata, evidence roles, locators, and
table-row bindings. Their text is replaced by the verified quote. Deterministic
bindings, comparisons, and applicability evaluations are filtered to retained
candidate keys.

Selector reasons, unresolved questions, conflicts, and sufficiency opinions
remain in the selection trace. They are never injected into the Judge input;
the Judge receives only verified source excerpts and deterministic annotations.

## Evaluation

Use the same 21 fixed-Judge tracked cases as the one-shot compression ablation;
exclude the two persisted `agent_recovery` cases. Report:

- correct / 21 and false `supported`;
- selection applied/fallback rate;
- rounds and added model calls;
- Judge input reduction and total input-character proxy;
- per-case changes versus uncompressed fixed Judge and one-shot compression;
- cross-run variance if the first run is not worse than baseline.

No case names, standard numbers, clause numbers, tracked expected answers, or
fixed evidence-role requirements may appear in the selector prompt or policy.
