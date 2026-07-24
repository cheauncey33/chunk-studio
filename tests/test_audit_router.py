from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import audit as audit_module
from app.routers.audit import (
    CaseReviewRequest,
    _build_workflow_trace,
    _report_kind,
    _safe_report_path,
    delete_case_review,
    get_audit_report,
    upsert_case_review,
)


def test_report_kind_classifies_workflow_outputs() -> None:
    assert _report_kind("hbjc_end_to_end_audit_top20_context_v6.json") == "end_to_end_audit"
    assert _report_kind("end_to_end_audit_oil_transformer_20260723_101010.json") == "end_to_end_audit"
    assert _report_kind("hbjc_retrieval_group_eval_top20_context_v6.json") == "retrieval_group_eval"
    assert _report_kind("retrieval_candidates_40_v2.json") == "retrieval"
    assert _report_kind("chunk_quality_report.json") == "report"


def test_safe_report_path_rejects_path_traversal() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _safe_report_path("../evaluation/manual_knowledge_rules_v1.json")

    assert exc_info.value.status_code == 400


def test_workflow_trace_reconstructs_legacy_report_without_hiding_gaps() -> None:
    payload = {
        "judge_model": "deepseek-v4-flash",
        "report": "report.md",
        "parameters": {"model": "S20"},
        "model_decode": {"raw_model": "S20"},
    }
    case = {
        "case_id": "case-1",
        "test_item": {"project_name": "负载损耗"},
        "reported_requirement": {"text": "Pk ≤ 2.185 kW"},
        "queries": {"production": "负载损耗 Pk"},
        "candidate_counts": {"table": 20, "section": 20},
        "judgment": {"status": "correct", "evidence": []},
        "direct_gold_available": True,
        "direct_gold_recalled": False,
    }

    trace = _build_workflow_trace(
        payload,
        case,
        current_llm_config={"provider": "deepseek", "model": "deepseek-v4-flash"},
    )

    assert trace["trace_source"] == "reconstructed"
    assert len(trace["nodes"]) == 7
    assert trace["warnings"]
    assert trace["nodes"][0]["configuration"]["provider"] == "deepseek"
    assert "旧报告" in trace["nodes"][0]["note"]
    assert trace["nodes"][-1]["diagnostic_only"] is True


def test_workflow_trace_prefers_recorded_runtime_input_output() -> None:
    payload = {
        "workflow_definition": {
            "provider_config": {"provider": "deepseek", "model": "deepseek-v4-flash"},
            "global_trace": {
                "report_parameters": {
                    "input": {"report_markdown": "source"},
                    "output": {"model": "S20"},
                },
            },
        },
    }
    case = {
        "case_id": "case-1",
        "workflow_trace": {
            "query_planner": {
                "input": {"reported_requirement": {"text": "Pk"}},
                "output": {"semantic": "寻找 Pk 证据"},
            },
        },
    }

    trace = _build_workflow_trace(
        payload,
        case,
        current_llm_config={"provider": "deepseek"},
    )

    assert trace["trace_source"] == "recorded"
    assert trace["nodes"][0]["input"] == {"report_markdown": "source"}
    assert trace["nodes"][3]["output"] == {"semantic": "寻找 Pk 证据"}


def test_workflow_trace_omits_gold_node_for_recorded_full_report_runs() -> None:
    payload = {
        "workflow_definition": {
            "provider_config": {"provider": "deepseek"},
            "global_trace": {
                "report_parameters": {"input": {}, "output": {}},
            },
        },
    }
    case = {"case_id": "item_abc", "workflow_trace": {}}

    trace = _build_workflow_trace(payload, case, current_llm_config={})

    assert all(node["id"] != "gold_comparison" for node in trace["nodes"])


def _review_env(monkeypatch, tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "audit-reviews.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(audit_module, "REPORTS_DIR", reports_dir)
    (reports_dir / "end_to_end_audit_test.json").write_text(
        json.dumps({
            "summary": {"cases": 1},
            "cases": [{"case_id": "item_abc", "judgment": {"status": "supported"}}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return conn


def test_case_review_upsert_get_and_delete(monkeypatch, tmp_path: Path) -> None:
    conn = _review_env(monkeypatch, tmp_path)
    name = "end_to_end_audit_test.json"

    saved = upsert_case_review(
        name, "item_abc", CaseReviewRequest(status="confirmed", note="核对无误")
    )
    assert saved["status"] == "confirmed"
    assert saved["corrected_status"] == ""

    corrected = upsert_case_review(
        name,
        "item_abc",
        CaseReviewRequest(status="corrected", corrected_status="mismatch", note="限值抄错"),
    )
    assert corrected["status"] == "corrected"
    assert corrected["corrected_status"] == "mismatch"

    detail = get_audit_report(name)
    assert detail["reviews"]["item_abc"]["status"] == "corrected"

    assert delete_case_review(name, "item_abc") == {"ok": True}
    assert get_audit_report(name)["reviews"] == {}
    conn.close()


def test_case_review_rejects_invalid_corrections(monkeypatch, tmp_path: Path) -> None:
    conn = _review_env(monkeypatch, tmp_path)
    name = "end_to_end_audit_test.json"

    with pytest.raises(HTTPException) as exc:
        upsert_case_review(
            name, "item_abc", CaseReviewRequest(status="corrected", corrected_status="totally_fine")
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        upsert_case_review(
            name, "missing-case", CaseReviewRequest(status="confirmed")
        )
    assert exc.value.status_code == 404
    conn.close()
