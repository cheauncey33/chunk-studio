from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.audit import _report_kind, _safe_report_path


def test_report_kind_classifies_workflow_outputs() -> None:
    assert _report_kind("hbjc_end_to_end_audit_top20_context_v6.json") == "end_to_end_audit"
    assert _report_kind("hbjc_retrieval_group_eval_top20_context_v6.json") == "retrieval_group_eval"
    assert _report_kind("retrieval_candidates_40_v2.json") == "retrieval"
    assert _report_kind("chunk_quality_report.json") == "report"


def test_safe_report_path_rejects_path_traversal() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _safe_report_path("../evaluation/manual_knowledge_rules_v1.json")

    assert exc_info.value.status_code == 400
