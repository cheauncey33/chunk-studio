from __future__ import annotations

import json
import sqlite3

import pytest

from app import business_analytics


def _source() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE knowledge_bases (id TEXT, name TEXT, status TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE knowledge_base_files (knowledge_base_id TEXT, file_id TEXT, enabled INTEGER);
        CREATE TABLE assistant_knowledge_bases (assistant_id TEXT, knowledge_base_id TEXT, enabled INTEGER);
        CREATE TABLE files (id TEXT, name TEXT, created_at TEXT);
        CREATE TABLE chunks (id TEXT, file_id TEXT, status TEXT);
        INSERT INTO knowledge_bases VALUES ('kb1','Oil','active','2026-01-01','2026-01-02');
        INSERT INTO files VALUES ('f1','std.pdf','2026-01-01');
        INSERT INTO knowledge_base_files VALUES ('kb1','f1',1);
        INSERT INTO assistant_knowledge_bases VALUES ('a1','kb1',1);
        INSERT INTO chunks VALUES ('c1','f1','approved'), ('c2','f1','pending');
        """
    )
    return conn


def _write_report(tmp_path) -> None:
    payload = {
        "scope": "full_report_audit",
        "audit_mode": "full_report",
        "report_file_name": "report.pdf",
        "assistant_id": "a1",
        "knowledge_base_id": "kb1",
        "cases": [
            {
                "case_id": "case1",
                "test_item": {"project_name": "Loss"},
                "reported_requirement": {"text": "P0 <= 1"},
                "judgment": {"status": "supported"},
            },
            {
                "case_id": "case2",
                "test_item": {"project_name": "Loss"},
                "reported_requirement": {"text": "Pk <= 2"},
                "judgment": {"status": "mismatch"},
            },
        ],
    }
    (tmp_path / "end_to_end_audit_demo.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (tmp_path / "recovery_shadow_demo.json").write_text(
        json.dumps({"cases": [{"case_id": "ignored"}]}), encoding="utf-8"
    )


def test_snapshot_and_overview_use_curated_business_rows(tmp_path) -> None:
    source = _source()
    _write_report(tmp_path)
    snapshot = business_analytics.build_business_snapshot(
        source=source, reports_dir=tmp_path
    )
    try:
        overview = business_analytics.get_business_overview(snapshot)
    finally:
        snapshot.close()

    assert overview["metrics"] == {
        "knowledge_bases": 1,
        "files": 1,
        "approved_chunks": 1,
        "audit_reports": 1,
        "audit_cases": 2,
    }
    assert overview["denominator"] == 2
    assert {row["label"]: row["value"] for row in overview["status_distribution"]} == {
        "mismatch": 1,
        "supported": 1,
    }


def test_schema_explains_curated_read_only_source() -> None:
    schema = business_analytics.describe_business_schema()

    assert schema["read_only"] is True
    assert "持久化 SQLite" in schema["source"]
    assert schema["table_descriptions"]["audit_cases"]
    assert schema["column_descriptions"]["audit_cases.status"]


def test_overview_keeps_legacy_statuses_in_denominator(tmp_path) -> None:
    source = _source()
    _write_report(tmp_path)
    snapshot = business_analytics.build_business_snapshot(
        source=source, reports_dir=tmp_path
    )
    try:
        snapshot.execute(
            """
            INSERT INTO audit_cases (
                report_name, case_id, project_name, requirement,
                status, assistant_id, knowledge_base_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("old.json", "legacy-case", "Loss", "", "legacy_status", "a1", "kb1"),
        )
        overview = business_analytics.get_business_overview(snapshot)
    finally:
        snapshot.close()

    assert overview["denominator"] == 3
    assert sum(row["value"] for row in overview["status_distribution"]) == 3
    assert {row["label"]: row["value"] for row in overview["status_distribution"]}["other"] == 1


def test_aggregate_audit_results_rejects_unknown_group(tmp_path) -> None:
    snapshot = business_analytics.build_business_snapshot(
        source=_source(), reports_dir=tmp_path
    )
    try:
        with pytest.raises(ValueError):
            business_analytics.aggregate_audit_results(snapshot, group_by="requirement_text")
    finally:
        snapshot.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM audit_cases",
        "PRAGMA table_info(audit_cases)",
        "SELECT 1; SELECT 2",
        "SELECT 1 -- comment",
        "ATTACH DATABASE 'x' AS x",
    ],
)
def test_sql_policy_rejects_unsafe_statements(sql: str) -> None:
    with pytest.raises(ValueError):
        business_analytics.validate_readonly_sql(sql)


def test_fixed_business_question_uses_zero_model_calls(monkeypatch, tmp_path) -> None:
    source = _source()
    _write_report(tmp_path)
    monkeypatch.setattr(
        business_analytics.llm,
        "chat_json",
        lambda *_args, **_kwargs: pytest.fail("fixed metric must not call the model"),
    )

    result = business_analytics.query_business_data(
        "现在有多少个知识库？", source=source, reports_dir=tmp_path
    )

    assert result["route"] == "fixed_metric"
    assert result["rows"] == [{"value": 1}]
    assert result["chart"] == {"type": "metric", "value": 1}


def test_common_audit_aggregation_uses_zero_model_calls(monkeypatch, tmp_path) -> None:
    source = _source()
    _write_report(tmp_path)
    monkeypatch.setattr(
        business_analytics.llm,
        "chat_json",
        lambda *_args, **_kwargs: pytest.fail("fixed aggregation must not call the model"),
    )

    result = business_analytics.query_business_data(
        "按检测项目统计不符合项数量", source=source, reports_dir=tmp_path
    )

    assert result["route"] == "fixed_metric"
    assert result["rows"] == [{"label": "Loss", "value": 1}]
    assert result["chart"]["type"] == "bar"


def test_text2sql_calls_model_once_then_executes_on_snapshot(monkeypatch, tmp_path) -> None:
    source = _source()
    _write_report(tmp_path)
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return {
            "sql": "SELECT project_name AS label, COUNT(*) AS value FROM audit_cases GROUP BY project_name",
            "title": "按项目统计",
            "chart_type": "bar",
        }

    monkeypatch.setattr(business_analytics.llm, "chat_json", fake_chat)
    result = business_analytics.query_business_data(
        "按检测项目统计审查条目", source=source, reports_dir=tmp_path
    )

    assert len(calls) == 1
    assert result["route"] == "text2sql"
    assert result["rows"] == [{"label": "Loss", "value": 2}]
    assert result["chart"]["type"] == "bar"
    assert result["chart"]["denominator"] == 2
