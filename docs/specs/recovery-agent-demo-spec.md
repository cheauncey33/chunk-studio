# Recovery Agent Demo Specification

Status: Demo implemented; shadow-validated; active mode remains opt-in  
Branch: `codex/pi-agentic-rag`  
Primary objective: improve retrieval recall and end-to-end audit correctness without replacing the deterministic audit workflow.

## 1. Product decision

The audit remains a bounded workflow:

```text
report parameter extraction
-> test item extraction
-> fixed query planner
-> fixed hybrid RAG
-> provisional judge
-> recovery gate
-> optional recovery agent
-> one final rerank
-> fixed final judge
-> report and trace
```

The Recovery Agent is a conditional evidence-recovery subsystem. It may recover report parameters, generate additional value-free retrieval queries, locate exact clauses, and expand evidence context. It must not issue the final `supported` or `mismatch` verdict.

## 2. Demo priorities

Priority order:

1. Retrieval recall on the complete test set.
2. End-to-end audit correctness on the defect-oriented test set.
3. Avoiding false definitive judgments.
4. Avoiding unnecessary model calls and repeated token consumption.
5. Traceability sufficient to explain evaluation results.

Deferred unless required by the above:

- Fine-grained runtime/database version management.
- Production-grade distributed caching and retry orchestration.
- New frontend pages and visual polish.
- Long-term user memory.
- Arbitrary Text-to-SQL and MCP integration.

Demo reproducibility requires only source code, source data, the reviewed test set, prompts, and generated evaluation reports. Runtime configuration used by an evaluation must be written into its report, but no elaborate version registry is required.

## 3. Current measured baseline

Dataset: `evaluation/test_set.json`

- Total requirements: 40.
- Scoreable retrieval cases: 33.
- Required evidence groups: 36.
- Corpus locator validation: passed.

Fresh baseline report: `backend/data/reports/recovery_baseline.json`.

| Policy | Cutoff | Evidence-group recall | Strict complete recall |
| --- | ---: | ---: | ---: |
| Hybrid RRF | 10 | 20/36 (55.56%) | 17/33 (51.52%) |
| Hybrid RRF | 20 | 31/36 (86.11%) | 28/33 (84.85%) |
| Hybrid + rerank | 10 | 33/36 (91.67%) | 30/33 (90.91%) |
| Hybrid + rerank | 20 | 36/36 (100%) | 33/33 (100%) |

Interpretation:

- Candidate recall is already complete by Top-20 on this dataset.
- Recovery must not be presented as merely increasing Top-K.
- The main retrieval target is recovering or promoting evidence into the bounded Judge delivery set.
- End-to-end correctness must be evaluated separately from retrieval recall.

## 4. Functional requirements

### FR-1: Provisional judgment

The first Judge result is stored as `provisional_judgment`. The existing `judgment` field remains the final backward-compatible result.

Required fields:

```json
{
  "status": "insufficient_context",
  "reason": "...",
  "evidence_candidate_keys": [],
  "missing_context_fields": [],
  "insufficiency_type": "retrieval_insufficient"
}
```

`insufficiency_type` is diagnostic model output and cannot alone activate recovery.

### FR-2: Recovery gate

The gate is deterministic and returns one action:

- `accept_provisional`
- `rejudge_only`
- `agent_recovery`
- `runtime_failure`

Every decision also records a generic `failure_class`, `resolution_state`, and
`recoverable_by_agent` flag. The current classes are `none`, `retrieval_gap`,
`evidence_context_gap`, `evidence_scope_gap`, `applicability_gap`,
`judge_contract_gap`, `judge_semantic_gap`, and `runtime_gap`. These fields are
diagnostic/evaluation state; they do not grant the Agent final-verdict authority.

Reason codes:

- `report_parameter_missing`
- `applicability_parameter_missing`
- `retrieval_no_candidates`
- `retrieval_low_relevance`
- `evidence_context_incomplete`
- `provisional_not_audited`
- `judge_contract_failure`
- `judge_consistency_failure`
- `retrieval_runtime_failure`
- `not_applicable`

Gate rules:

- Valid `supported` and `mismatch` judgments do not trigger recovery.
- Invalid status, invalid evidence keys, or reason/status conflict trigger `rejudge_only`.
- Missing parameters not present in the pinned sample profile may trigger recovery.
- Empty/filtered candidate delivery, incomplete table/reference context, and provisional `not_audited` may trigger recovery.
- Embedding, lexical, rerank, parse, timeout, or tool failures must not become `knowledge_rule_not_found`.

### FR-3: Generic agent runtime

Implement a small Python runtime inspired by Pi Agent's loop:

```text
model action
-> validate action and tool arguments
-> execute registered tool
-> append structured tool result
-> rebuild context from pinned state
-> continue or finish
```

Required runtime capabilities:

- Tool registry with JSON schemas.
- Model-provider abstraction.
- Maximum turns, tool calls, retrieval calls, and elapsed time.
- Cancellation and deterministic stop reasons.
- Duplicate tool-call/query rejection.
- Structured event trace.
- Pinned state injection on every turn.
- Deterministic context compaction that does not require an additional LLM call.

The runtime must be reusable by a later knowledge Q&A Agent, but this milestone only integrates the audit Recovery Agent.

### FR-4: Recovery state

Immutable state:

- run/job/case identifiers
- assistant and knowledge-base identifiers
- report file and declared standards
- original test item and reported requirement
- initial sample profile with value provenance
- initial queries and retrieval diagnostics
- provisional judgment and gate decision
- allowed file identifiers

Mutable state:

- recovered parameters with locators
- attempted query signatures
- candidate registry
- expanded evidence identifiers
- remaining gaps
- consumed budget
- tool errors

The model cannot overwrite immutable state. Runtime code applies tool results to mutable state.

### FR-5: Context management

Every model turn receives:

```text
system policy
immutable state
compact mutable state
previous deterministic summary
recent tool calls
available tool schemas
```

The following may never be lost through compaction:

- case and requirement text
- parameter values and provenance
- allowed file/standard scope
- candidate keys and source locators
- attempted query signatures
- degraded/error state
- remaining budget

Older natural-language reasoning and full rejected candidate text may be reduced to structured summaries. Full candidate content remains outside the message history in a candidate registry and is reloaded only when required.

### FR-6: Recovery tools

#### T1 `search_report_context`

Search only the current report's parsed Markdown/chunks for literal terms and aliases. Return bounded snippets with page/source locators.

#### T2 `extract_report_parameters`

Extract requested fields only from evidence returned by `search_report_context`. Each value must be `found`, `ambiguous`, or `not_found` and include provenance when found.

#### T3 `search_kb_candidates`

Run value-free semantic/keyword/table/section retrieval inside the allowed knowledge-base file scope. Return an unrereanked candidate pool for later fusion.

#### T4 `search_kb_exact`

Perform bounded literal lookup for standard numbers, clause numbers, table numbers, parameter symbols, and domain phrases inside the allowed knowledge-base file scope.

This is the domain-safe replacement for a generic filesystem `grep`.

#### T5 `expand_evidence_context`

Expand an existing candidate through adjacent chunks, parent section, continuation table, or an explicit clause/table reference. Preserve file/page/bbox provenance.

#### T6 `locate_standard_clause`

Search within a specified allowed standard for a clause, table, or referenced target.

#### T7 `finish_recovery`

Finish with one of:

- `evidence_found`
- `parameter_found`
- `partially_recovered`
- `exhausted`
- `runtime_failed`

The result lists recovered parameters, candidate keys, remaining gaps, and attempted queries. It cannot contain the final audit verdict.

#### T8 `follow_evidence_references`

Extract explicit clause references from already-retrieved candidates and follow
them within the same source file and standard. Decimal measurements without a
reference cue are not treated as clauses. This deterministic convenience tool
replaces an Agent guess plus a second broad-search decision.

### FR-7: Forbidden tools and actions

The audit Recovery Agent must not receive:

- filesystem `grep`, read, write, edit, or shell tools
- arbitrary SQL
- network or web search
- database write tools
- cross-knowledge-base search outside the pinned scope
- a tool that emits `supported` or `mismatch`

### FR-8: Retrieval fusion

Recovery queries must not independently rerank and then feed those rankings into another RRF.

Required sequence:

```text
initial route pools + recovery route pools + exact matches
-> deduplicate by chunk_text_sha256
-> RRF from raw source/route ranks
-> one rerank with the original value-free audit question
-> bounded candidate delivery to final Judge
```

Prohibited:

- Deduplication by unstable chunk UUID only.
- Report target values or comparison operators in retrieval/rerank queries.
- Concatenating all recovery queries as the reranker query.
- Using evaluation evidence labels or target locators in production recovery.

### FR-9: Final judgment

The fixed Judge receives:

- original report requirement
- pinned and recovered report parameters with provenance
- initial and recovered candidates after unified rerank
- deterministic peer/manual context already supported by the workflow

The Recovery Agent output is diagnostic input, not a verdict.

For numeric, tolerance, range, and count cases the Judge also emits a structured
`comparison` containing kind, separate nominal values and tolerance bands,
report/standard units, scopes, relation, and conclusion. Runtime consistency checks recompute simple exact/bound/tolerance/
scope-count relations and force one fixed-Judge retry on a contradiction. This is
a Judge guardrail, not a Recovery-Agent verdict tool.

`not_audited` is allowed only when:

- applicability parameters are sufficient,
- all required source files were available,
- fixed and recovery retrieval completed without technical degradation,
- bounded semantic, exact, and context-expansion attempts were exhausted,
- no normative rule was found inside the allowed scope.

Otherwise the result remains `insufficient_context` with a reason code.

### FR-10: Trace and checkpoint

Each recovered case records:

```json
{
  "provisional_judgment": {},
  "recovery_decision": {},
  "agent_recovery": {
    "events": [],
    "result": {},
    "usage": {}
  },
  "judgment": {}
}
```

Checkpointing may reuse the current report checkpoint JSON. A separate session database is not required for the demo.

## 5. Token and latency requirements

### NFR-1: Trigger only on eligible cases

Noneligible cases incur no Recovery model call.

### NFR-2: Reuse existing work

- Initial candidates and embeddings are reused.
- Recovered parameters are shared across cases within the same report when their provenance is identical.
- Identical normalized requirements may reuse a recovery result within the same report run.
- Tool results are referenced by compact identifiers rather than repeatedly copied in full.

### NFR-3: Default budgets

- Maximum agent turns: 4.
- Maximum total tool calls: 6.
- Maximum knowledge-base searches: 5 (observed full-shadow mean: 2.22 per activated case).
- Maximum report searches: 2.
- Maximum recovery wall time per case: 90 seconds.
- Recovery concurrency: 2, separate from fixed Judge concurrency.

These are source/config defaults for the demo, not a full versioned configuration product.

### NFR-4: Model

- Provider: official DeepSeek API configured by `DEEPSEEK_API_KEY`.
- Model: `deepseek-v4-flash`.
- Temperature: 0.
- Thinking: disabled.

The runtime provider may use native tool calls if a smoke test confirms compatibility. Otherwise it uses a strict JSON action protocol without changing the runtime/tool interfaces.

## 6. Evaluation requirements

### EV-1: Retrieval evaluation

Use all scoreable cases in `evaluation/test_set.json`; do not optimize against a single case or restore deleted legacy GOLD files.

Report separately:

- initial candidate recall
- bounded Judge-delivery recall
- recovery candidate recall
- final unified-rerank recall
- evidence-group recall
- strict complete recall
- actual routes and degraded state

### EV-2: End-to-end defect evaluation

Run the existing defect-oriented end-to-end audit/scoring path after retrieval recovery is integrated. Report:

- attack/defect detection correctness
- control correctness
- overall correctness
- `supported`, `mismatch`, `insufficient_context`, and `not_audited` distribution
- false definitive judgments
- evidence locator validity

Retrieval improvements must not be presented as audit-accuracy improvements until this evaluation passes.

### EV-3: Token/cost evaluation

Report:

- Recovery activation count and rate
- model calls per activated case
- tool calls per activated case
- duplicate calls rejected
- input/output tokens when supplied by the provider
- additional wall time
- recovered cases per model call

### EV-4: Generalization checks

Break results down by retrieval class, dataset split, document/report family, and failure reason. An optimization is accepted only when it improves aggregate results or a principled class of failures without materially harming other classes.

## 7. Implementation stages

### Stage 0: Spec and baselines

- Commit this specification to the working tree.
- Validate the test set and corpus locators.
- Run the current retrieval baseline.
- Inspect the current defect end-to-end scorer and establish its runnable baseline.

Exit condition: baseline reports exist and metric semantics are recorded.

### Stage 1: Contracts, gate, and runtime tests

- Add runtime/recovery data models.
- Add deterministic gate.
- Add the generic loop, tool registry, policy, context builder, and compactor.
- Use fake providers/tools in unit tests.

Exit condition: no production behavior changes; runtime and gate tests pass.

### Stage 2: Retrieval primitives and tools

- Expose candidate-pool retrieval separately from final rerank.
- Implement hash deduplication and unified rerank.
- Implement the eight bounded tools.
- Add scope and value-leakage tests.

Exit condition: all tool and retrieval tests pass; existing fixed retrieval remains compatible.

### Stage 3: Workflow integration in shadow mode

- Store provisional judgment.
- Run gate and eligible Recovery sessions.
- Produce recovered candidates and shadow final judgments.
- Preserve the existing production judgment during the first comparison run.

Exit condition: full trace exists for every activated case and nonactivated cases incur zero extra model calls.

### Stage 4: Evaluation and general optimization

- Run the full retrieval test set.
- Run the end-to-end defect evaluation.
- Group misses by systematic failure class.
- Change generic query/tool/gate behavior only; do not add case identifiers, target hashes, expected evidence, or report-specific rules.
- Repeat until the change is supported by aggregate metrics and regression checks.

Exit condition: measured benefit with no evidence leakage or unacceptable correctness regression.

### Stage 5: Activate final Recovery path

- Make recovered unified candidates feed the fixed final Judge.
- Keep a feature switch for fixed-only comparison.
- Run the full Python suite, test-set validation, retrieval evaluation, and end-to-end defect evaluation.

Exit condition: acceptance criteria below are met and artifacts are saved.

## 8. Planned code locations

New:

- `backend/app/agent_runtime/models.py`
- `backend/app/agent_runtime/loop.py`
- `backend/app/agent_runtime/tool_registry.py`
- `backend/app/agent_runtime/context.py`
- `backend/app/agent_runtime/policy.py`
- `backend/app/recovery/models.py`
- `backend/app/recovery/gate.py`
- `backend/app/recovery/tools.py`
- `backend/app/recovery/runner.py`
- `tests/test_agent_runtime.py`
- `tests/test_recovery_gate.py`
- `tests/test_recovery_tools.py`

Modified:

- `backend/app/llm.py`
- `backend/app/retrieval.py`
- `scripts/run_report_audit_workflow.py`
- `tests/test_retrieval.py`
- `tests/test_report_audit_workflow.py`

Evaluation scripts may be added under `scripts/`, with outputs under the existing untracked runtime report/experiment locations.

## 9. Acceptance criteria

### AC-1 Correct architecture

- Fixed RAG remains the default first path.
- Recovery is gate-controlled.
- Recovery Agent cannot issue the final verdict.
- Final verdict comes from the fixed Judge.

### AC-2 Retrieval quality

- Full 33-case evaluation is run before and after.
- Aggregate Top-10 strict/evidence-group recall does not regress.
- Recovery improves the identified bounded-delivery misses or demonstrates with evidence that they are solely a delivery-budget issue.

### AC-3 Audit correctness

- End-to-end defect evaluation is rerun.
- No increase in false definitive judgments is accepted merely for higher recall.
- Every final `supported` or `mismatch` retains valid evidence locators.

### AC-4 Efficiency

- Nontriggered cases use zero Recovery model calls.
- Duplicate search calls are rejected.
- Context compaction adds no model call in this milestone.
- Usage and elapsed time are reported.

### AC-5 Generalization

- No code/prompt contains test case IDs, target hashes, expected evidence snippets, or one-report special branches.
- Results are reported across the complete dataset and by failure class.

### AC-6 Verification

- Targeted runtime/recovery/retrieval/workflow tests pass.
- Full Python tests pass.
- `scripts/validate_test_set.py` passes against the local corpus.
- Retrieval and defect evaluation artifacts are generated and summarized with exact denominators.

## 10. Explicitly deferred follow-up

After the Recovery milestone is validated, the same runtime may power a global knowledge Q&A page with persistent conversations, knowledge-base selection, audit-history tools, aggregate statistics, and chart artifacts. That work is not allowed to delay the current recall/correctness milestone.

## 11. Implementation and evaluation record

Implemented on `codex/pi-agentic-rag`:

- Generic bounded Python Agent loop, policy, event trace, immutable/mutable state injection, duplicate-call rejection, and deterministic compaction.
- Deterministic Recovery Gate and eight domain-scoped tools. A generic filesystem grep was not added; `search_kb_exact` / `locate_standard_clause` provide scoped literal lookup, long section hits are reduced to source-traced focused excerpts, and `follow_evidence_references` follows source-declared clauses without another broad-search guess.
- Generic gate failure/resolution taxonomy persisted in case traces and aggregate summaries, plus a structured numeric/scope comparison contract checked by the fixed Judge retry path.
- Raw candidate-pool retrieval, SHA-256 text deduplication, RRF across source/route ranks, and one final rerank with the original value-free question.
- Workflow modes `off`, `shadow`, and `active`. `off` remains the default; `active` is available for explicit demo runs.
- Persisted per-case provisional judgment, gate decision, Agent events/usage, shadow judgment, and aggregate Recovery summary.
- A persisted-audit shadow harness: `scripts/evaluate_recovery_shadow.py`.

Measured artifacts:

- Retrieval baseline: `backend/data/reports/recovery_baseline.json`.
- Complete latest shadow run: `backend/data/reports/recovery_shadow_final_focused.json`.
- Focused short-circuit evidence run: `backend/data/reports/recovery_shadow_e25_focused.json`.
- Structured-Judge targeted runs: `backend/data/reports/recovery_shadow_structured_judge_targeted.json`,
  `backend/data/reports/recovery_shadow_structured_judge_targeted_v2.json`, and
  `backend/data/reports/recovery_shadow_structured_judge_targeted_v3.json`.

Latest complete shadow result (57 audit cases, 23 tracked defect/control edits):

| Metric | Fixed baseline | Recovery shadow |
| --- | ---: | ---: |
| Tracked correctness | 19/23 (82.61%) | 20/23 (86.96%) |
| Gate eligible / activated | 0 | 9/9 |
| Shadow final-Judge coverage | 0 | 9/9 |
| Agent turns | 0 | 33 |
| Tool calls | 0 | 31 |
| Knowledge searches | 0 | 20 |
| Runtime errors | 0 | 0 |

The stable full-run gain was the short-circuit test-count failure (`not_audited -> mismatch`) after clause-focused exact retrieval exposed the total-versus-per-phase count. A targeted run also corrected the induced-voltage setpoint failure (`insufficient_context -> mismatch`), producing 20/23 when run alone, but the latest complete run did not reproduce that correction. This is recorded as Flash/Judge stability risk, not claimed as an aggregate gain.

Known remaining end-to-end misses:

- Impedance tolerance bandwidth: Judge numeric semantics, not solved by additional retrieval.
- Induced-voltage setpoint: evidence is recovered, but the fixed Judge inconsistently requests irrelevant applicability fields.
- Lightning impulse level: provisional Judge returned a false definitive `supported`, so the current Gate correctly did not invoke Recovery; this requires fixed-Judge validation rather than broader Agent authority.

Verification record:

- Recovery/retrieval/workflow focused suite: passed.
- Broad Python run excluding five tests whose imported legacy scripts are deleted in the current user worktree: 264 passed, 1 pre-existing prompt mojibake assertion failed.
- Unfiltered Python collection additionally has five errors caused by those deleted legacy evaluation scripts.
- Test-set validation: 40 cases, 33 scoreable cases, corpus locators verified.
- Frontend lint: completed with existing warnings only.
- Frontend production build: passed.

Acceptance conclusion for this demo: architecture, bounded recovery, traceability, value-free retrieval, and a measured full-run correctness gain are demonstrated. Automatic production activation is intentionally not the default because two Judge-semantic misses and one cross-run stability issue remain.

Post-demo extension record (2026-08-05):

- A four-case targeted replay with the current prompt and `deepseek-v4-flash`
  reached 21/23 tracked correctness once (e16 and e25 corrected), but repeated
  runs ranged from 20/23 to 21/23. This remains targeted stability evidence, not
  a new aggregate full-run claim.
- Splitting nominal values from tolerance bands prevents the e15 impedance case
  from becoming a false `supported` result when the cited standard evidence has
  no tolerance value.
- Selected-evidence grounding prevents the e18 impulse case from remaining a
  false definitive `supported` result when the cited method clause supplies only
  waveform/tolerance rules and not the claimed nominal kV level. The current
  targeted outcome is safer (`not_audited`/`insufficient_context`) but is not yet
  the expected `mismatch`.
- e16 still varies between `mismatch` and `insufficient_context`; the remaining
  improvement direction is deterministic applicability resolution and stronger
  table-row selection in the fixed Judge, not broader Recovery-Agent authority.

## Deterministic Judge semantics extension (2026-08-05)

This extension keeps the workflow boundary unchanged: fixed retrieval first,
Recovery Agent only for recoverable report/context/evidence gaps, and the fixed
Judge as the only verdict owner.

### Applicability resolver

- Parse reported `Um`, high/low `Ur`, rated capacity, and insulation type without
  a model call.
- When `Um` is absent, map an exact standard system-voltage class to `Um` with
  explicit `derived_from_standard_voltage_class` provenance.
- Resolve the voltage branch (`Um <= 72.5`, `72.5 < Um <= 170`, `Um > 170`),
  capacity branch, and insulation branch before Judge invocation.
- Compute `required_parameters` per project/branch. The Judge cannot request an
  insulation or customer-special-requirement field when the resolved branch does
  not depend on it.

### Evidence roles

Every delivered candidate receives one or more deterministic roles:

- `nominal_rule`: supplies the prescribed value, formula, limit, or table row.
- `tolerance_rule`: supplies deviation, tolerance, or waveform bands.
- `method_rule`: supplies the test or measurement procedure.
- `applicability_rule`: selects the applicable branch or conditions.

A numeric `supported` or `mismatch` conclusion must select `nominal_rule`
evidence. A tolerance conclusion must additionally select `tolerance_rule`.
Method/tolerance/applicability clauses cannot by themselves establish a nominal
value. Violations trigger at most one fixed-Judge repair, then fail closed as
`insufficient_context`.

### Table-row binder

- Parse already retrieved HTML tables without another search or model call.
- Match explicit system voltage, `Um`, or capacity columns against the resolved
  sample parameters.
- Inject the exact row, column map, selector count, and candidate key into the
  Judge input as `deterministic_table_bindings`.
- Bind only rows present in retrieved evidence; never synthesize a standard value.

### Permission and cost boundary

Recovery exposes only report search/extraction, scoped KB retrieval, exact clause
lookup, evidence expansion/reference following, and finish. Text2SQL, audit-history
queries, business aggregation, and chart rendering remain in the later knowledge-QA
Agent and are explicitly excluded from the Recovery tool surface.

### Performance acceptance

- Report retrieval recall separately from end-to-end tracked audit correctness.
- Report e16/e18 outcomes, repeated-run variance, Judge calls/rejudges, Recovery
  turns/tool/search calls, errors, and wall-clock duration.
- Do not claim generalization from the 23 tracked edits or retrieval accuracy from
  end-to-end verdicts.

### Verification record

Observed on 2026-08-05 with `deepseek-v4-flash`, thinking disabled:

- Retrieval test set validated: 40 cases, 33 scoreable, 36 required evidence
  groups, and all corpus locators uniquely resolved.
- Hybrid+rerank strict complete recall: 22/33 at Top-5, 30/33 at Top-10,
  and 33/33 at Top-20. Evidence-group recall: 33/36 at Top-10 and 36/36
  at Top-20.
- Full 57-case shadow replay: 22/23 tracked defect/control judgments correct
  (95.65%), versus persisted fixed baseline 19/23 (82.61%).
- Corrected tracked cases: e16 applicability/formula, e18 bound impulse table
  row, and e25 short-circuit count. Remaining tracked miss: e15 impedance
  tolerance evidence.
- Recovery: 8 activated cases, 26 turns, 20 tool calls, 15 knowledge searches,
  and 0 runtime errors. e16 used the fixed applicability clause lookup and
  therefore added 0 Recovery turns/tools/searches.
- Three pre-final targeted e16/e18 replays were both correct with 0 Recovery
  turns/tools/searches; the final full replay supersedes their aggregate score.
