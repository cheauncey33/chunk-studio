from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (str(ROOT), str(BACKEND)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.agent_runtime import AgentPolicy
from app.recovery.gate import decide_recovery
from app.recovery.runner import run_recovery_agent
from app.recovery.tools import RecoveryToolEnvironment

import run_report_audit_workflow as workflow


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Recovery Agent in shadow mode against a persisted audit report."
    )
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--score-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Override the persisted Judge model (for example deepseek-v4-flash).",
    )
    parser.add_argument(
        "--refresh-judge-prompt",
        action="store_true",
        help="Compose the current assistant Judge prompt instead of replaying the persisted prompt.",
    )
    parser.add_argument(
        "--rejudge-all",
        action="store_true",
        help="Replay the current fixed Judge for selected cases even when Gate does not recover.",
    )
    parser.add_argument(
        "--evidence-compression",
        choices=("off", "active"),
        default="off",
        help="Optionally compress Judge evidence into validated Evidence Cards.",
    )
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_key(case: dict[str, Any]) -> tuple[str, str]:
    return (
        str((case.get("test_item") or {}).get("project_name") or "").strip(),
        str((case.get("reported_requirement") or {}).get("text") or "").strip(),
    )


def _score_shadow(
    score_report: dict[str, Any],
    audit_cases: list[dict[str, Any]],
    shadow_by_case_id: dict[str, str],
) -> dict[str, Any]:
    cases_by_key = {_case_key(case): case for case in audit_cases}
    rows = []
    for source in score_report.get("rows") or []:
        row = dict(source)
        case = cases_by_key.get(
            (str(row.get("project_name") or "").strip(), str(row.get("requirement") or "").strip())
        )
        case_id = str((case or {}).get("case_id") or "")
        shadow_status = shadow_by_case_id.get(case_id, str(row.get("actual_status") or ""))
        row.update(
            {
                "case_id": case_id or None,
                "baseline_status": row.get("actual_status"),
                "shadow_status": shadow_status,
                "shadow_correct": shadow_status == row.get("expected_status"),
            }
        )
        rows.append(row)
    return {
        "tracked_cases": len(rows),
        "baseline_correct": sum(bool(row.get("correct")) for row in rows),
        "shadow_correct": sum(bool(row["shadow_correct"]) for row in rows),
        "baseline_accuracy": (
            sum(bool(row.get("correct")) for row in rows) / len(rows) if rows else None
        ),
        "shadow_accuracy": (
            sum(bool(row["shadow_correct"]) for row in rows) / len(rows) if rows else None
        ),
        "changed_cases": [
            row for row in rows if row["baseline_status"] != row["shadow_status"]
        ],
        "shadow_failures": [row for row in rows if not row["shadow_correct"]],
        "rows": rows,
    }


def _maybe_compress(
    judge_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    mode: str,
    judge_model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode != "active":
        return judge_input, {
            "mode": "off",
            "applied": False,
            "fallback": False,
            "additional_model_calls": 0,
        }
    return workflow.compress_judge_input(
        judge_input,
        candidates,
        call_model=lambda prompt, payload: workflow._call_model(
            prompt,
            payload,
            model=judge_model,
        ),
    )


def main() -> None:
    args = _parse_args()
    audit = _load(args.audit_report)
    cases = list(audit.get("cases") or [])
    retrieval_config = dict(
        (audit.get("workflow_definition") or {}).get("retrieval_config") or {}
    )
    judge_prompt = str(
        (((audit.get("workflow_definition") or {}).get("prompts") or {}).get("audit_judge") or {}).get("content")
        or ""
    )
    report_markdown = str(
        (((audit.get("workflow_definition") or {}).get("global_trace") or {}).get("report_parameters") or {}).get("input", {}).get("report_markdown")
        or ""
    )
    judge_model = str(args.judge_model or audit.get("judge_model") or "deepseek-v4-flash")
    if not judge_prompt or args.refresh_judge_prompt:
        assistant_id = str(audit.get("assistant_id") or "")
        profile = workflow._load_assistant_version(assistant_id)
        prompt_vars = workflow._assistant_prompt_var_context(assistant_id, profile)
        judge_prompt = workflow._prompt_content(
            profile, "audit_judge", var_context=prompt_vars
        )
    if not report_markdown:
        raise ValueError("audit report does not contain the persisted report input")

    results: list[dict[str, Any]] = []
    activated = 0
    for index, case in enumerate(cases, start=1):
        retrieval_trace = (case.get("workflow_trace") or {}).get("retrieval") or {}
        retrieval_input = retrieval_trace.get("input") or {}
        provisional = dict(case.get("judgment") or {})
        sample_profile = dict(case.get("sample_profile") or audit.get("sample_profile") or {})
        deterministic_applicability = workflow.resolve_applicability(
            sample_profile,
            project_name=str((case.get("test_item") or {}).get("project_name") or ""),
            requirement_text=str((case.get("reported_requirement") or {}).get("text") or ""),
        )
        sample_profile["deterministic_applicability"] = deterministic_applicability
        decision = decide_recovery(
            judgment=provisional,
            retrieval_trace=retrieval_trace,
            sample_profile=sample_profile,
        )
        row: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "project_name": (case.get("test_item") or {}).get("project_name"),
            "reported_requirement": (case.get("reported_requirement") or {}).get("text"),
            "provisional_status": provisional.get("status"),
            "recovery_decision": decision,
        }
        if args.case_ids and str(case.get("case_id")) not in set(args.case_ids):
            row["skipped"] = "case_filter"
            results.append(row)
            continue
        if decision.get("action") != "agent_recovery" and args.rejudge_all:
            candidates = list(retrieval_trace.get("output", {}).get("candidates") or [])
            workflow.annotate_candidates(candidates, deterministic_applicability)
            deterministic_applicability["candidate_evaluations"] = (
                workflow.evaluate_candidate_applicability(
                    candidates,
                    deterministic_applicability,
                )
            )
            judge_input = dict(
                ((case.get("workflow_trace") or {}).get("audit_judge") or {}).get("input")
                or {}
            )
            judge_input.update(
                {
                    "sample_profile": sample_profile,
                    "deterministic_applicability": deterministic_applicability,
                    "deterministic_table_bindings": [
                        {
                            "candidate_key": candidate.get("candidate_key"),
                            "standard_no": str(
                                (candidate.get("business_metadata") or {}).get("standard_no") or ""
                            ),
                            "table_no": str(
                                (candidate.get("business_metadata") or {}).get("table_no") or ""
                            ),
                            "binding": candidate["table_row_binding"],
                        }
                        for candidate in candidates
                        if isinstance(candidate.get("table_row_binding"), dict)
                    ],
                    "candidates": [
                        workflow._compact_candidate(candidate) for candidate in candidates
                    ],
                    "deterministic_comparisons": workflow.build_deterministic_comparisons(
                        str((case.get("reported_requirement") or {}).get("text") or ""),
                        str((case.get("test_item") or {}).get("project_name") or ""),
                        candidates,
                    ),
                }
            )
            try:
                judge_input, compression_trace = _maybe_compress(
                    judge_input,
                    candidates,
                    mode=args.evidence_compression,
                    judge_model=judge_model,
                )
                shadow_judgment, shadow_trace = workflow._run_audit_judge_with_consistency(
                    judge_prompt=judge_prompt,
                    judge_input=judge_input,
                    judge_model=judge_model,
                    candidates=candidates,
                    sample_profile=sample_profile,
                )
                shadow_trace["evidence_compression"] = compression_trace
                row["shadow_judgment"] = shadow_judgment
                row["shadow_judge_trace"] = shadow_trace
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            results.append(row)
            continue
        if decision.get("action") != "agent_recovery":
            results.append(row)
            continue
        if args.max_cases is not None and activated >= args.max_cases:
            row["skipped"] = "max_cases"
            results.append(row)
            continue

        activated += 1
        retrieval_output = retrieval_trace.get("output") or {}
        queries = dict(case.get("queries") or retrieval_input.get("queries") or {})
        candidates = list(retrieval_output.get("candidates") or [])
        workflow.annotate_candidates(candidates, deterministic_applicability)
        deterministic_applicability["candidate_evaluations"] = (
            workflow.evaluate_candidate_applicability(
                candidates,
                deterministic_applicability,
            )
        )
        environment = RecoveryToolEnvironment(
            report_markdown=report_markdown,
            allowed_file_ids=list(retrieval_input.get("file_ids") or []),
            requirement_text=str((case.get("reported_requirement") or {}).get("text") or ""),
            original_query=str(
                queries.get("semantic")
                or queries.get("keyword")
                or (case.get("test_item") or {}).get("project_name")
                or ""
            ),
            model=judge_model,
            retrieval_config=retrieval_config,
        )
        immutable_state = {
            "case_id": case.get("case_id"),
            "allowed_file_ids": environment.allowed_file_ids,
            "test_item": case.get("test_item"),
            "reported_requirement": case.get("reported_requirement"),
            "sample_profile": sample_profile,
            "initial_queries": queries,
            "initial_candidates": [
                workflow._recovery_candidate_summary(candidate) for candidate in candidates
            ],
            "provisional_judgment": provisional,
            "recovery_decision": decision,
        }
        try:
            recovery = run_recovery_agent(
                immutable_state=immutable_state,
                environment=environment,
                initial_pool={
                    "hits": candidates,
                    "candidate_count": len(candidates),
                    "retrieval_mode": "persisted_initial_pool",
                },
                policy=AgentPolicy(
                    max_turns=4,
                    max_tool_calls=6,
                    max_search_calls=5,
                    timeout_seconds=90,
                ),
            )
            row["agent"] = recovery
            merged = recovery.get("merged_retrieval") or {}
            recovered_candidates = []
            for rank, hit in enumerate(merged.get("hits") or [], start=1):
                metadata = hit.get("business_metadata") or {}
                recovered_candidates.append(
                    {
                        **hit,
                        "candidate_key": f"r{rank:02d}",
                        "content_type": str(
                            metadata.get("content_type")
                            or hit.get("content_type")
                            or "section"
                        ),
                    }
                )
            if recovered_candidates:
                recovered_profile = workflow._sample_profile_with_recovery(
                    sample_profile,
                    list((recovery.get("mutable_state") or {}).get("recovered_parameters") or []),
                )
                recovered_applicability = workflow.resolve_applicability(
                    recovered_profile,
                    project_name=str((case.get("test_item") or {}).get("project_name") or ""),
                    requirement_text=str((case.get("reported_requirement") or {}).get("text") or ""),
                )
                recovered_profile["deterministic_applicability"] = recovered_applicability
                workflow.annotate_candidates(recovered_candidates, recovered_applicability)
                recovered_applicability["candidate_evaluations"] = (
                    workflow.evaluate_candidate_applicability(
                        recovered_candidates,
                        recovered_applicability,
                    )
                )
                original_judge_input = dict(
                    ((case.get("workflow_trace") or {}).get("audit_judge") or {}).get("input")
                    or {}
                )
                judge_input = {
                    **original_judge_input,
                    "sample_profile": recovered_profile,
                    "deterministic_applicability": recovered_applicability,
                    "deterministic_table_bindings": [
                        {
                            "candidate_key": candidate["candidate_key"],
                            "standard_no": str(
                                (candidate.get("business_metadata") or {}).get("standard_no") or ""
                            ),
                            "table_no": str(
                                (candidate.get("business_metadata") or {}).get("table_no") or ""
                            ),
                            "binding": candidate["table_row_binding"],
                        }
                        for candidate in recovered_candidates
                        if isinstance(candidate.get("table_row_binding"), dict)
                    ],
                    "deterministic_comparisons": workflow.build_deterministic_comparisons(
                        str((case.get("reported_requirement") or {}).get("text") or ""),
                        str((case.get("test_item") or {}).get("project_name") or ""),
                        recovered_candidates,
                    ),
                    "candidates": [
                        workflow._compact_candidate(candidate)
                        for candidate in recovered_candidates
                    ],
                    "recovery_context": {
                        "decision": decision,
                        "result": recovery.get("result") or {},
                    },
                }
                judge_input, compression_trace = _maybe_compress(
                    judge_input,
                    recovered_candidates,
                    mode=args.evidence_compression,
                    judge_model=judge_model,
                )
                shadow_judgment, shadow_trace = workflow._run_audit_judge_with_consistency(
                    judge_prompt=judge_prompt,
                    judge_input=judge_input,
                    judge_model=judge_model,
                    candidates=recovered_candidates,
                    sample_profile=recovered_profile,
                )
                shadow_trace["evidence_compression"] = compression_trace
                row["shadow_judgment"] = shadow_judgment
                row["shadow_judge_trace"] = shadow_trace
        except Exception as exc:  # Preserve the rest of a long shadow run.
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
        print(
            f"recovery {activated}: {case.get('case_id')} "
            f"{row.get('provisional_status')} -> "
            f"{(row.get('shadow_judgment') or {}).get('status')}",
            flush=True,
        )

    shadow_by_case_id = {
        str(row["case_id"]): str(row["shadow_judgment"]["status"])
        for row in results
        if isinstance(row.get("shadow_judgment"), dict)
    }
    failure_classes: dict[str, int] = {}
    resolution_states: dict[str, int] = {}
    for row in results:
        decision = row.get("recovery_decision") or {}
        failure_class = str(decision.get("failure_class") or "unknown")
        resolution_state = str(decision.get("resolution_state") or "unknown")
        failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
        resolution_states[resolution_state] = resolution_states.get(resolution_state, 0) + 1
    compression_traces = [
        trace
        for row in results
        for trace in [
            ((row.get("shadow_judge_trace") or {}).get("evidence_compression") or {})
        ]
        if trace.get("mode") == "active"
    ]
    compression_summary = {
        "mode": args.evidence_compression,
        "attempted": len(compression_traces),
        "applied": sum(bool(trace.get("applied")) for trace in compression_traces),
        "fallbacks": sum(bool(trace.get("fallback")) for trace in compression_traces),
        "additional_model_calls": sum(
            int(trace.get("additional_model_calls") or 0)
            for trace in compression_traces
        ),
        "compressor_input_chars": sum(
            int(trace.get("compressor_input_chars") or 0)
            for trace in compression_traces
        ),
        "judge_input_chars_before": sum(
            int(trace.get("judge_input_chars_before") or 0)
            for trace in compression_traces
        ),
        "judge_input_chars_after": sum(
            int(
                trace.get("judge_input_chars_after")
                or trace.get("judge_input_chars_before")
                or 0
            )
            for trace in compression_traces
        ),
    }
    output: dict[str, Any] = {
        "version": 1,
        "mode": "recovery_shadow",
        "audit_report": str(args.audit_report),
        "judge_model": judge_model,
        "thinking": "disabled",
        "summary": {
            "cases": len(cases),
            "failure_classes": dict(sorted(failure_classes.items())),
            "resolution_states": dict(sorted(resolution_states.items())),
            "eligible": sum(
                row["recovery_decision"].get("action") == "agent_recovery"
                for row in results
            ),
            "activated": activated,
            "shadow_judged": len(shadow_by_case_id),
            "errors": sum("error" in row for row in results),
            "turns": sum(int((row.get("agent") or {}).get("usage", {}).get("turns") or 0) for row in results),
            "tool_calls": sum(int((row.get("agent") or {}).get("usage", {}).get("tool_calls") or 0) for row in results),
            "search_calls": sum(int((row.get("agent") or {}).get("usage", {}).get("search_calls") or 0) for row in results),
            "evidence_compression": compression_summary,
        },
        "cases": results,
    }
    if args.score_report:
        output["tracked_score"] = _score_shadow(
            _load(args.score_report), cases, shadow_by_case_id
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": output["summary"], "tracked_score": output.get("tracked_score", {})}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
