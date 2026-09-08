"""Replay a recorded audit run through the current judge layer.

A recorded run stores the retrieved candidates and the judgment the model
actually produced. Replaying it holds retrieval and the model fixed and swaps
only the judge layer, so the resulting score difference is attributable to the
judge and nothing else. Cases the caliber declines to close keep the recorded
model judgment verbatim, which is exactly what production would do.

    $env:PYTHONPATH='backend'; uv run python scripts/replay_judge_layer.py `
        backend/data/reports/e2e_hbjc_adversarial_rich_audit_v4pro.json `
        --out backend/data/reports/replay_caliber_rich_v4pro.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_report_audit_workflow as workflow  # noqa: E402
from app.audit_semantics import annotate_candidates, resolve_applicability  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _replay_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return the judge outcome for one recorded case under the current layer."""
    recorded = dict(case.get("judgment") or {})
    reported = dict(case.get("reported_requirement") or {})
    test_item = dict(case.get("test_item") or {})
    sample_profile = dict(case.get("sample_profile") or {})
    trace_input = ((case.get("workflow_trace") or {}).get("audit_judge") or {}).get("input") or {}
    candidates = [dict(item) for item in (trace_input.get("candidates") or [])]
    if not candidates:
        return {"status": recorded.get("status"), "source": "no_candidates_recorded"}

    applicability = resolve_applicability(
        sample_profile,
        project_name=str(test_item.get("project_name") or ""),
        requirement_text=str(reported.get("text") or ""),
    )
    annotate_candidates(candidates, applicability)
    for rank, candidate in enumerate(candidates, start=1):
        candidate.setdefault("candidate_key", f"c{rank:02d}")

    def replay_model(prompt: str, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        # The recorded run already asked the model this question; reuse its answer
        # so the only thing that can move the score is the judge layer.
        return {
            "status": recorded.get("status"),
            "reason": recorded.get("reason"),
            "evidence_candidate_keys": list(recorded.get("evidence_candidate_keys") or []),
            "missing_context_fields": list(recorded.get("missing_context_fields") or []),
        }

    original = workflow._call_model
    workflow._call_model = replay_model
    try:
        judgment, trace = workflow._run_audit_judge_with_consistency(
            judge_prompt="replay",
            judge_input={
                "sample_profile": sample_profile,
                "deterministic_applicability": applicability,
                "test_item": test_item,
                "reported_requirement": reported,
                "candidates": candidates,
            },
            judge_model="replay",
            candidates=candidates,
            sample_profile=sample_profile,
        )
    finally:
        workflow._call_model = original

    source = str(trace.get("judge_source") or "")
    if source != workflow.CALIBER_AUTHORITY:
        # Not closed by the caliber: keep exactly what shipped, so revalidation
        # and rejudge noise cannot be mistaken for a judge-layer effect.
        return {"status": recorded.get("status"), "source": source or "model"}
    return {
        "status": judgment.get("status"),
        "source": source,
        "verdict": judgment.get("verdict"),
        "kind": judgment.get("kind"),
        "rules": (judgment.get("caliber") or {}).get("rules"),
        "judgment": judgment,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    audit = _read_json(args.audit_report)
    cases = list(audit.get("cases") or [])

    changes: list[dict[str, Any]] = []
    sources: Counter[str] = Counter()
    for case in cases:
        recorded_status = str((case.get("judgment") or {}).get("status") or "")
        outcome = _replay_case(case)
        sources[outcome["source"]] += 1
        if outcome["source"] == workflow.CALIBER_AUTHORITY:
            case["judgment"] = outcome["judgment"]
        if outcome["status"] != recorded_status:
            changes.append(
                {
                    "case_id": case.get("case_id"),
                    "requirement": (case.get("reported_requirement") or {}).get("text"),
                    "before": recorded_status,
                    "after": outcome["status"],
                    "kind": outcome.get("kind"),
                    "rules": outcome.get("rules"),
                }
            )

    audit["replay"] = {
        "source_report": str(args.audit_report),
        "judge_sources": dict(sources),
        "changed": changes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"cases": len(cases), "judge_sources": dict(sources)}, ensure_ascii=False, indent=2))
    print(f"\nstatus changes: {len(changes)}")
    for change in changes:
        print(
            f"  {change['before']} -> {change['after']}  kind={change['kind']} "
            f"rules={change['rules']}\n    {change['requirement']}"
        )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
