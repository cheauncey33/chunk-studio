"""Literal report-context search used by Recovery and the production agent."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.report_context import search_report_markdown


def test_report_search_is_literal_and_bounded() -> None:
    result = search_report_markdown(
        "型号 S20-M。设备最高电压 Um 为 40.5 kV。后文再次出现 Um。",
        ["Um"],
        max_results=1,
    )
    assert result["summary"] == "found 1 report matches"
    assert result["terms"] == ["Um"]
    assert len(result["matches"]) == 1
    assert result["matches"][0]["term"] == "Um"
    assert "40.5 kV" in result["matches"][0]["snippet"]


def test_report_search_finds_tank_and_impedance_windows() -> None:
    markdown = (
        "短时过负载：油箱波纹散热器变形应在规定范围内。"
        "附录 A 样品信息 短路阻抗 3.94 %。"
    )
    result = search_report_markdown(markdown, ["波纹", "短路阻抗"], max_results=8)
    terms = {item["term"] for item in result["matches"]}
    assert terms == {"波纹", "短路阻抗"}
    assert any("3.94" in item["snippet"] for item in result["matches"])


def test_report_search_rejects_empty_terms() -> None:
    with pytest.raises(ValueError, match="1-8"):
        search_report_markdown("正文", [])


def test_search_report_context_endpoint_reads_bound_markdown(monkeypatch, tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    markdown.write_text("油箱波纹散热器变形应在规定范围内。短路阻抗 3.94 %。", encoding="utf-8")
    monkeypatch.setattr("app.routers.search._report_file_in_workspace", lambda *_args: True)
    monkeypatch.setattr("app.audit_run.resolve_markdown_path", lambda *_args: markdown)
    from app.routers.search import ReportContextSearchRequest, search_report_context

    result = search_report_context(
        ReportContextSearchRequest(terms=["波纹", "短路阻抗"], report_file_id="report"),
        None,
    )
    assert result["matches"]
    assert {item["term"] for item in result["matches"]} == {"波纹", "短路阻抗"}
