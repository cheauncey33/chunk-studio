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
    delete_audit_report,
    delete_case_review,
    get_audit_report,
    list_audit_reports,
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
    assert all(node["id"] != "agent_audit" for node in trace["nodes"])


def test_workflow_trace_includes_agent_node_and_skips_planner() -> None:
    payload = {
        "judge_mode": "agent",
        "judge_provider": "pi-agent-sidecar",
        "workflow_definition": {
            "provider_config": {"provider": "deepseek", "judge_mode": "agent"},
            "global_trace": {
                "report_parameters": {"input": {}, "output": {}},
            },
        },
    }
    case = {
        "case_id": "item_abc",
        "workflow_trace": {
            "audit_judge": {
                "input": {"judge_mode": "agent"},
                "output": {"status": "supported"},
            },
            "agent_audit": {
                "input": {"case_id": "item_abc"},
                "output": {"ok": True, "parse_mode": "strict"},
            },
        },
    }

    trace = _build_workflow_trace(payload, case, current_llm_config={})
    node_ids = [node["id"] for node in trace["nodes"]]

    assert "agent_audit" in node_ids
    assert "query_planner" not in node_ids
    assert "retrieval" not in node_ids
    assert "gold_comparison" not in node_ids
    agent_node = next(node for node in trace["nodes"] if node["id"] == "agent_audit")
    assert agent_node["configuration"]["provider"] == "pi-agent-sidecar"
    assert agent_node["configuration"]["judge_mode"] == "agent"


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


def test_list_audit_reports_history_filters_eval_artefacts(monkeypatch, tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(audit_module, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(db, "_conn", sqlite3.connect(tmp_path / "hist.db"))
    db.get_conn().row_factory = sqlite3.Row
    db.get_conn().executescript(db._SCHEMA)
    db.get_conn().commit()

    (reports_dir / "end_to_end_audit_oil_20260726_010203.json").write_text(
        json.dumps(
            {
                "audit_mode": "full_report",
                "scope": "full_report_audit",
                "assistant_id": "assistant_oil_transformer_audit",
                "assistant_name": "油浸式变压器审查",
                "report_file_id": "file_abc",
                "report_file_name": "出厂报告.pdf",
                "started_at": "2026-07-26T01:02:03",
                "summary": {
                    "cases": 2,
                    "judgments": {"supported": 1, "mismatch": 1},
                },
                "cases": [{"case_id": "a"}, {"case_id": "b"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (reports_dir / "hbjc_end_to_end_audit_top20.json").write_text(
        json.dumps({"cases": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (reports_dir / "retrieval_candidates_v1.json").write_text(
        json.dumps({"cases": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    all_reports = list_audit_reports()
    assert len(all_reports["reports"]) == 3

    history = list_audit_reports(history=True)
    assert len(history["reports"]) == 1
    item = history["reports"][0]
    assert item["report_file_name"] == "出厂报告.pdf"
    assert item["report_file_id"] == "file_abc"
    assert item["assistant_name"] == "油浸式变压器审查"
    assert item["judgments"]["mismatch"] == 1
    assert item["case_count"] == 2

    detail = get_audit_report("end_to_end_audit_oil_20260726_010203.json")
    assert detail["report_file_id"] == "file_abc"
    assert detail["payload"]["assistant_name"] == "油浸式变压器审查"
    db.get_conn().close()


def test_delete_audit_report_removes_json_checkpoint_and_reviews(monkeypatch, tmp_path: Path) -> None:
    conn = _review_env(monkeypatch, tmp_path)
    name = "end_to_end_audit_test.json"
    reports_dir = audit_module.REPORTS_DIR
    checkpoint = reports_dir / "end_to_end_audit_test.checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")

    upsert_case_review(name, "item_abc", CaseReviewRequest(status="confirmed"))
    result = delete_audit_report(name)
    assert result["ok"] is True
    assert name in result["removed"]
    assert checkpoint.name in result["removed"]
    assert not (reports_dir / name).exists()
    assert not checkpoint.exists()
    with pytest.raises(HTTPException) as exc:
        get_audit_report(name)
    assert exc.value.status_code == 404
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_case_reviews WHERE report_name=?",
        (name,),
    ).fetchone()
    assert int(rows["n"]) == 0
    conn.close()
