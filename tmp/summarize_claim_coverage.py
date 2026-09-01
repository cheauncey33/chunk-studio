"""Offline replay: unify each requirement, then score programmatic table coverage."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.audit_semantics import (  # noqa: E402
    annotate_candidates,
    evaluate_table_claims,
    extract_requirement_claim,
    resolve_applicability,
)


AUDIT_PATH = ROOT / "backend" / "data" / "reports" / "end_to_end_audit_oil_transformer_audit_20260726_220514.json"
TEST_SET_PATH = ROOT / "evaluation" / "test_set.json"
RULES_PATH = ROOT / "evaluation" / "manual_knowledge_rules_v1.json"
OUT_PATH = ROOT / "tmp" / "claim_coverage_summary.json"


def _load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _candidates(case: dict) -> list[dict]:
    retrieval = ((case.get("workflow_trace") or {}).get("retrieval") or {}).get("output") or {}
    raw = list(retrieval.get("candidates") or [])
    return [
        {
            "candidate_key": item.get("candidate_key"),
            "content_type": item.get("content_type"),
            "business_metadata": item.get("business_metadata") or {},
            "text": item.get("text") or "",
        }
        for item in raw
        if isinstance(item, dict)
    ]


def replay_audit(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    ready = Counter()
    modes = Counter()
    reasons = Counter()
    for case in payload.get("cases") or []:
        req = case.get("reported_requirement") or {}
        item = case.get("test_item") or {}
        text = str(req.get("text") or "")
        unit = req.get("unit")
        project = str(item.get("project_name") or "")
        extraction = extract_requirement_claim(text, unit=unit, project_name=project)
        sample_profile = case.get("sample_profile") or {}
        applicability = resolve_applicability(
            sample_profile,
            project_name=project,
            requirement_text=text,
        )
        candidates = _candidates(case)
        annotate_candidates(candidates, applicability)
        evaluated = evaluate_table_claims(
            text,
            project,
            candidates,
            unit=unit,
            requirement_extraction=extraction,
            applicability=applicability,
            manual_knowledge_rules=_load_rules(),
        )
        decision = evaluated["decision"]
        ready[extraction["reason_code"]] += 1
        modes[str(decision.get("mode") or "")] += 1
        reasons[str(decision.get("reason_code") or "")] += 1
        rows.append({
            "case_id": case.get("case_id"),
            "project_name": project,
            "requirement_text": text,
            "unit": unit,
            "program_ready": extraction["program_ready"],
            "extract_reason": extraction["reason_code"],
            "split_method": extraction["split_method"],
            "decision_mode": decision.get("mode"),
            "decision_reason": decision.get("reason_code"),
            "decision_status": decision.get("status"),
            "n_candidates": len(candidates),
            "n_tables": sum(1 for c in candidates if c.get("content_type") == "table"),
            "historical_status": (case.get("judgment") or {}).get("status"),
        })
    return {
        "source": str(path),
        "case_count": len(rows),
        "program_ready": sum(1 for row in rows if row["program_ready"]),
        "extract_reasons": dict(ready),
        "decision_modes": dict(modes),
        "decision_reasons": dict(reasons),
        "rows": rows,
    }


def replay_test_set(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ready = Counter()
    splits = Counter()
    rows = []
    for case in payload.get("cases") or []:
        project = case.get("detection_project") or {}
        req = project.get("reported_requirement") or {}
        extraction = extract_requirement_claim(
            str(req.get("text") or ""),
            unit=req.get("unit"),
            project_name=str(project.get("project_name") or ""),
        )
        ready[extraction["reason_code"]] += 1
        splits[extraction["split_method"]] += 1
        rows.append({
            "case_id": case.get("case_id"),
            "retrieval_class": case.get("retrieval_class"),
            "project_name": project.get("project_name"),
            "requirement_text": req.get("text"),
            "unit": req.get("unit"),
            "program_ready": extraction["program_ready"],
            "extract_reason": extraction["reason_code"],
            "split_method": extraction["split_method"],
            "claim": extraction["claim"]["value"],
        })
    return {
        "source": str(path),
        "case_count": len(rows),
        "program_ready": sum(1 for row in rows if row["program_ready"]),
        "extract_reasons": dict(ready),
        "split_methods": dict(splits),
        "not_ready": [
            {
                "case_id": row["case_id"],
                "project_name": row["project_name"],
                "requirement_text": row["requirement_text"],
                "extract_reason": row["extract_reason"],
            }
            for row in rows
            if not row["program_ready"]
        ],
        "rows": rows,
    }


def main() -> None:
    audit = replay_audit(AUDIT_PATH)
    test_set = replay_test_set(TEST_SET_PATH)
    not_ready_audit = [
        {
            "case_id": row["case_id"],
            "project_name": row["project_name"],
            "requirement_text": row["requirement_text"],
            "extract_reason": row["extract_reason"],
            "decision_reason": row["decision_reason"],
        }
        for row in audit["rows"]
        if not row["program_ready"]
    ]
    ready_but_fallback = [
        {
            "case_id": row["case_id"],
            "project_name": row["project_name"],
            "requirement_text": row["requirement_text"],
            "decision_reason": row["decision_reason"],
            "n_tables": row["n_tables"],
            "historical_status": row["historical_status"],
        }
        for row in audit["rows"]
        if row["program_ready"] and row["decision_mode"] not in {
            "programmatic_table",
            "programmatic_formula",
        }
    ]
    programmatic = [
        {
            "case_id": row["case_id"],
            "project_name": row["project_name"],
            "requirement_text": row["requirement_text"],
            "decision_status": row["decision_status"],
            "decision_mode": row["decision_mode"],
            "historical_status": row["historical_status"],
        }
        for row in audit["rows"]
        if row["decision_mode"] in {"programmatic_table", "programmatic_formula"}
    ]
    closed_modes = {"programmatic_table", "programmatic_formula"}
    closed_rows = [row for row in audit["rows"] if row["decision_mode"] in closed_modes]
    model_rows = [row for row in audit["rows"] if row["decision_mode"] not in closed_modes]
    status_layer = {
        "closed_count": len(closed_rows),
        "model_count": len(model_rows),
        "closed_historical_verdicts": dict(Counter(row["historical_status"] for row in closed_rows)),
        "model_historical_verdicts": dict(Counter(row["historical_status"] for row in model_rows)),
        "model_bind_reasons": dict(Counter(row["decision_reason"] for row in model_rows)),
        "model_supported": [
            {
                "case_id": row["case_id"],
                "project_name": row["project_name"],
                "requirement_text": row["requirement_text"],
                "decision_reason": row["decision_reason"],
            }
            for row in model_rows
            if row["historical_status"] == "supported"
        ],
    }
    summary = {
        "audit": {
            "source": audit["source"],
            "case_count": audit["case_count"],
            "program_ready": audit["program_ready"],
            "extract_reasons": audit["extract_reasons"],
            "decision_modes": audit["decision_modes"],
            "decision_reasons": audit["decision_reasons"],
            "not_ready": not_ready_audit,
            "ready_but_fallback": ready_but_fallback,
            "programmatic": programmatic,
            "status_layer": status_layer,
        },
        "test_set": {
            "source": test_set["source"],
            "case_count": test_set["case_count"],
            "program_ready": test_set["program_ready"],
            "extract_reasons": test_set["extract_reasons"],
            "split_methods": test_set["split_methods"],
            "not_ready": test_set["not_ready"],
        },
    }
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "wrote": str(OUT_PATH),
        "audit_cases": audit["case_count"],
        "audit_program_ready": audit["program_ready"],
        "audit_extract_reasons": audit["extract_reasons"],
        "audit_decision_modes": audit["decision_modes"],
        "audit_decision_reasons": audit["decision_reasons"],
        "status_layer": {
            "closed_count": status_layer["closed_count"],
            "model_count": status_layer["model_count"],
            "closed_historical_verdicts": status_layer["closed_historical_verdicts"],
            "model_historical_verdicts": status_layer["model_historical_verdicts"],
            "model_bind_reasons": status_layer["model_bind_reasons"],
            "model_supported_count": len(status_layer["model_supported"]),
        },
        "test_set_cases": test_set["case_count"],
        "test_set_program_ready": test_set["program_ready"],
        "test_set_extract_reasons": test_set["extract_reasons"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
