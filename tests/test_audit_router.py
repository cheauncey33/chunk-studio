from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.audit import _build_workflow_trace, _report_kind, _safe_report_path


def test_report_kind_classifies_workflow_outputs() -> None:
    assert _report_kind("hbjc_end_to_end_audit_top20_context_v6.json") == "end_to_end_audit"
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
