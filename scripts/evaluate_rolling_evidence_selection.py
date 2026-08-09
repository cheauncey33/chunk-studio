from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (str(ROOT), str(BACKEND), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

from app import db
from app.rolling_evidence_selection import select_judge_evidence_rolling

import run_report_audit_workflow as workflow


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate rolling LLM evidence selection over a fixed reranked Top-20 pool."
    )
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--score-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--judge-model", default="deepseek-v4-flash")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--batch-chars", type=int, default=14000)
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_key(project_name: Any, requirement: Any) -> tuple[str, str]:
    return str(project_name or "").strip(), str(requirement or "").strip()


def _top20_candidates(case: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retrieval_trace = (case.get("workflow_trace") or {}).get("retrieval") or {}
    retrieval_input = retrieval_trace.get("input") or {}
    queries = dict(case.get("queries") or retrieval_input.get("queries") or {})
    candidates, debug = workflow._retrieve_hybrid_candidates(
        str(queries.get("production") or retrieval_input.get("query") or ""),
        query_routes=queries,
        file_ids=list(retrieval_input.get("file_ids") or []),
        top_k=20,
        route_top_k=int(retrieval_input.get("route_top_k") or 20),
        candidates_per_type=max(
            20,
            int(retrieval_input.get("candidates_per_type") or 20),
        ),
        final_table=10,
        final_section=10,
        special_route_reserve=int(retrieval_input.get("special_route_reserve") or 0),
        rrf_k=int(retrieval_input.get("rrf_k") or 60),
        similarity_threshold=float(retrieval_input.get("similarity_threshold") or 0.2),
        aggregate_continuation_tables=bool(
            retrieval_input.get("aggregate_continuation_tables", False)
        ),
        expand_references=bool(retrieval_input.get("expand_references", False)),
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["candidate_key"] = f"c{rank:02d}"
    return candidates, debug


def _judge_input(case: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    sample_profile = dict(case.get("sample_profile") or {})
    applicability = workflow.resolve_applicability(
        sample_profile,
        project_name=str((case.get("test_item") or {}).get("project_name") or ""),
        requirement_text=str((case.get("reported_requirement") or {}).get("text") or ""),
    )
    sample_profile["deterministic_applicability"] = applicability
    workflow.annotate_candidates(candidates, applicability)
    applicability["candidate_evaluations"] = workflow.evaluate_candidate_applicability(
        candidates,
        applicability,
    )
    bindings = [
        {
            "candidate_key": candidate["candidate_key"],
            "standard_no": str(
                (candidate.get("business_metadata") or {}).get("standard_no") or ""
            ),
            "table_no": str(
                (candidate.get("business_metadata") or {}).get("table_no") or ""
            ),
            "table_title": str(
                (candidate.get("business_metadata") or {}).get("table_title") or ""
            ),
            "binding": candidate["table_row_binding"],
        }
        for candidate in candidates
        if isinstance(candidate.get("table_row_binding"), dict)
    ]
    comparisons = workflow.build_deterministic_comparisons(
        str((case.get("reported_requirement") or {}).get("text") or ""),
        str((case.get("test_item") or {}).get("project_name") or ""),
        candidates,
    )
    persisted = dict(
        ((case.get("workflow_trace") or {}).get("audit_judge") or {}).get("input")
        or {}
    )
    persisted.pop("evidence_compression", None)
    persisted.pop("rolling_evidence_selection", None)
    return {
        **persisted,
        "sample_profile": sample_profile,
        "deterministic_applicability": applicability,
        "deterministic_table_bindings": bindings,
        "deterministic_comparisons": comparisons,
        "test_item": case.get("test_item") or {},
        "reported_requirement": case.get("reported_requirement") or {},
        "candidates": [workflow._compact_candidate(item) for item in candidates],
    }, sample_profile


def _evaluate_case(
    case: dict[str, Any],
    *,
    judge_prompt: str,
    judge_model: str,
    score_row: dict[str, Any],
    batch_size: int,
    batch_chars: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "project_name": (case.get("test_item") or {}).get("project_name"),
        "requirement": (case.get("reported_requirement") or {}).get("text"),
        "expected_status": score_row.get("expected_status"),
        "baseline_status": (case.get("judgment") or {}).get("status"),
    }
    try:
        candidates, retrieval_debug = _top20_candidates(case)
        raw_input, sample_profile = _judge_input(case, candidates)
        selected_input, selection_trace = select_judge_evidence_rolling(
            raw_input,
            candidates,
            call_model=lambda prompt, payload: workflow._call_model(
                prompt,
                payload,
                model=judge_model,
            ),
            batch_size=batch_size,
            batch_chars=batch_chars,
        )
        judgment, judge_trace = workflow._run_audit_judge_with_consistency(
            judge_prompt=judge_prompt,
            judge_input=selected_input,
            judge_model=judge_model,
            candidates=candidates,
            sample_profile=sample_profile,
        )
        row.update({
            "candidate_count": len(candidates),
            "retrieval": retrieval_debug,
            "selection": selection_trace,
            "judgment": judgment,
            "judge_trace": judge_trace,
            "actual_status": judgment.get("status"),
            "correct": judgment.get("status") == score_row.get("expected_status"),
            "false_supported": (
                judgment.get("status") == "supported"
                and score_row.get("expected_status") != "supported"
            ),
        })
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["correct"] = False
        row["false_supported"] = False
    return row


def main() -> None:
    args = _parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    db.init_db()
    audit = _load(args.audit_report)
    score = _load(args.score_report)
    score_by_key = {
        _score_key(row.get("project_name"), row.get("requirement")): row
        for row in score.get("rows") or []
    }
    selected_ids = set(args.case_ids or [])
    cases: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case in audit.get("cases") or []:
        if selected_ids and str(case.get("case_id")) not in selected_ids:
            continue
        key = _score_key(
            (case.get("test_item") or {}).get("project_name"),
            (case.get("reported_requirement") or {}).get("text"),
        )
        score_row = score_by_key.get(key)
        if score_row is not None:
            cases.append((case, score_row))

    assistant_id = str(audit.get("assistant_id") or "")
    profile = workflow._load_assistant_version(assistant_id)
    prompt_vars = workflow._assistant_prompt_var_context(assistant_id, profile)
    judge_prompt = workflow._prompt_content(
        profile,
        "audit_judge",
        var_context=prompt_vars,
    )
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _evaluate_case,
                case,
                judge_prompt=judge_prompt,
                judge_model=args.judge_model,
                score_row=score_row,
                batch_size=args.batch_size,
                batch_chars=args.batch_chars,
            ): str(case.get("case_id"))
            for case, score_row in cases
        }
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            print(
                f"{row.get('case_id')}: {row.get('actual_status')} "
                f"correct={row.get('correct')} error={row.get('error')}",
                flush=True,
            )
    order = {str(case.get("case_id")): index for index, (case, _) in enumerate(cases)}
    results.sort(key=lambda row: order.get(str(row.get("case_id")), 10**9))
    traces = [row.get("selection") or {} for row in results if row.get("selection")]
    before = sum(int(trace.get("judge_input_chars_before") or 0) for trace in traces)
    after = sum(
        int(trace.get("judge_input_chars_after") or trace.get("judge_input_chars_before") or 0)
        for trace in traces
    )
    selector_chars = sum(int(trace.get("selector_input_chars") or 0) for trace in traces)
    summary = {
        "cases": len(results),
        "correct": sum(bool(row.get("correct")) for row in results),
        "accuracy": (
            sum(bool(row.get("correct")) for row in results) / len(results)
            if results else None
        ),
        "false_supported": sum(bool(row.get("false_supported")) for row in results),
        "errors": sum("error" in row for row in results),
        "selection_applied": sum(bool(trace.get("applied")) for trace in traces),
        "selection_fallbacks": sum(bool(trace.get("fallback")) for trace in traces),
        "selection_rounds": sum(len(trace.get("rounds") or []) for trace in traces),
        "additional_model_calls": sum(
            int(trace.get("additional_model_calls") or 0) for trace in traces
        ),
        "selector_input_chars": selector_chars,
        "judge_input_chars_before": before,
        "judge_input_chars_after": after,
        "judge_input_reduction": 1 - after / before if before else None,
        "total_input_proxy_delta": (
            (selector_chars + after) / before - 1 if before else None
        ),
    }
    output = {
        "version": 1,
        "mode": "fixed_top20_rolling_evidence_selection",
        "audit_report": str(args.audit_report),
        "score_report": str(args.score_report),
        "judge_model": args.judge_model,
        "thinking": "disabled",
        "config": {
            "top20_table": 10,
            "top20_section": 10,
            "batch_size": args.batch_size,
            "batch_chars": args.batch_chars,
            "max_retained": 8,
            "workers": args.workers,
        },
        "summary": summary,
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
