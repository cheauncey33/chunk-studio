from __future__ import annotations

import pytest

from app.recovery.tools import (
    RecoveryToolEnvironment,
    _focus_exact_hit,
    build_recovery_tools,
    query_uses_report_target_value,
)


def _tools(env: RecoveryToolEnvironment):
    return {tool.name: tool for tool in build_recovery_tools(env)}


def test_recovery_tool_surface_excludes_business_analytics_and_text2sql() -> None:
    env = RecoveryToolEnvironment(
        report_markdown="",
        allowed_file_ids=[],
        requirement_text="应符合规定",
        original_query="审查依据",
    )
    names = set(_tools(env))

    assert names == {
        "search_report_context",
        "extract_report_parameters",
        "search_kb_candidates",
        "search_kb_exact",
        "expand_evidence_context",
        "follow_evidence_references",
        "locate_standard_clause",
        "finish_recovery",
    }
    assert names.isdisjoint(
        {"query_business_data", "aggregate_audit_results", "render_chart_spec"}
    )


def test_report_search_is_literal_and_bounded() -> None:
    env = RecoveryToolEnvironment(
        report_markdown="型号 S20-M。设备最高电压 Um 为 40.5 kV。后文再次出现 Um。",
        allowed_file_ids=[],
        requirement_text="绝缘电阻应符合规定",
        original_query="绝缘电阻适用要求",
    )
    result = _tools(env)["search_report_context"].execute(
        {"terms": ["Um"], "max_results": 1}
    )
    assert len(result["matches"]) == 1
    assert result["matches"][0]["term"] == "Um"


def test_recovery_search_rejects_report_target_value() -> None:
    assert query_uses_report_target_value("寻找 10000 MΩ 限值", "应不低于 10000 MΩ")
    env = RecoveryToolEnvironment(
        report_markdown="",
        allowed_file_ids=["f1"],
        requirement_text="应不低于 10000 MΩ",
        original_query="绝缘电阻要求",
        candidate_search=lambda *args, **kwargs: {"hits": []},
    )
    with pytest.raises(ValueError, match="target value"):
        _tools(env)["search_kb_candidates"].execute({"query": "10000 MΩ 限值"})


def test_candidate_search_is_scoped_and_stored_outside_model_result() -> None:
    captured = {}

    def search(query, **kwargs):
        captured.update({"query": query, **kwargs})
        return {
            "hits": [{
                "chunk_id": "c1",
                "file_id": "f1",
                "file_name": "std.pdf",
                "page": 2,
                "text": "规范证据",
                "business_metadata": {"content_type": "section"},
            }],
            "degraded": [],
        }

    env = RecoveryToolEnvironment(
        report_markdown="",
        allowed_file_ids=["f1"],
        requirement_text="应符合规定",
        original_query="绝缘电阻要求",
        candidate_search=search,
    )
    result = _tools(env)["search_kb_candidates"].execute({"query": "绝缘电阻适用要求"})
    assert captured["file_ids"] == ["f1"]
    assert result["candidate_count"] == 1
    assert result["pool_id"] in env.pool_store
    key = result["candidates"][0]["candidate_key"]
    assert env.candidate_store[key]["text"] == "规范证据"


def test_exact_search_has_no_generic_filesystem_scope() -> None:
    captured = {}

    def exact(terms, file_ids, standard_no, limit):
        captured.update(
            terms=terms,
            file_ids=file_ids,
            standard_no=standard_no,
            limit=limit,
        )
        return {"hits": [], "degraded": []}

    env = RecoveryToolEnvironment(
        report_markdown="",
        allowed_file_ids=["allowed"],
        requirement_text="应符合规定",
        original_query="query",
        exact_search=exact,
    )
    _tools(env)["search_kb_exact"].execute(
        {"terms": ["表7", "Um"], "standard_no": "GB/T 1"}
    )
    assert captured["file_ids"] == ["allowed"]
    assert captured["terms"] == ["表7", "Um"]


def test_exact_search_excerpt_centers_specific_clause_reference() -> None:
    text = "prefix " * 500 + "4.2.5.5 total test count is nine" + " suffix" * 500
    focused = _focus_exact_hit(
        {
            "text": text,
            "business_metadata": {"content_type": "section"},
            "source_trace": {},
        },
        ["test", "4.2.5.5"],
        radius=80,
    )

    assert len(focused["text"]) < len(text)
    assert "4.2.5.5" in focused["text"]
    assert focused["source_trace"]["recovery_excerpt"]["matched_term"] == "4.2.5.5"


def test_finish_recovery_cannot_return_audit_verdict() -> None:
    env = RecoveryToolEnvironment("", [], "", "")
    finish = _tools(env)["finish_recovery"]
    with pytest.raises(ValueError, match="invalid recovery outcome"):
        finish.execute({"outcome": "supported"})


def test_follow_references_is_derived_and_scoped_to_source_file_and_standard() -> None:
    captured = []

    def exact(terms, file_ids, standard_no, limit):
        captured.append((terms, file_ids, standard_no, limit))
        return {
            "hits": [{
                "chunk_id": "target",
                "file_id": file_ids[0],
                "text": "referenced clause",
                "business_metadata": {"standard_no": standard_no},
            }],
            "degraded": [],
        }

    env = RecoveryToolEnvironment("", ["f1", "f2"], "plain requirement", "query", exact_search=exact)
    env.candidate_store["seed"] = {
        "chunk_id": "seed",
        "file_id": "f1",
        "text": "The requirement shall follow 3.22; a measured value is 1.5.",
        "business_metadata": {"standard_no": "GB/T 1-2020"},
    }

    result = _tools(env)["follow_evidence_references"].execute(
        {"candidate_keys": ["seed"]}
    )

    assert captured == [(["3.22"], ["f1"], "GB/T 1-2020", 20)]
    assert result["references_by_candidate"] == {"seed": ["3.22"]}
    assert result["candidate_count"] == 1
    assert "1.5" not in captured[0][0]
