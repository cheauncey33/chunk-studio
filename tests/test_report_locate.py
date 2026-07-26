from __future__ import annotations

from app.report_locate import _page_kind, _score_page, compact_locate_text, normalize_locate_text


def test_normalize_strips_html_and_space() -> None:
    assert normalize_locate_text("<td>高压(线) 电阻</td>") == "高压(线)电阻"
    assert "高压线电阻" in compact_locate_text("高压(线)电阻三相不平衡率最大值(%):≤2")


def test_prefers_summary_table_over_body_detail() -> None:
    summary = [{
        "type": "text",
        "content": "检测结果汇总",
    }, {
        "type": "table",
        "content": (
            "<table><tr><td>序号</td><td>检测项目</td><td>标准要求</td><td>结论</td></tr>"
            "<tr><td>2</td><td>绕组电阻测量</td><td>高压(线)电阻三相不平衡率最大值(%):≤2</td>"
            "<td>符合</td></tr></table>"
        ),
    }]
    body = [{
        "type": "text",
        "content": "报告正文",
    }, {
        "type": "paragraph_title",
        "content": "2 绕组电阻测量",
    }, {
        "type": "table",
        "content": (
            "<table><tr><td>高压(线)电阻三相不平衡率最大值(%)</td><td>0.27</td>"
            "<td>≤2</td></tr></table>"
        ),
    }]
    summary_score = _score_page(
        summary,
        project="绕组电阻测量",
        requirement="高压(线)电阻三相不平衡率最大值(%):≤2",
    )
    body_score = _score_page(
        body,
        project="绕组电阻测量",
        requirement="高压(线)电阻三相不平衡率最大值(%):≤2",
    )
    assert _page_kind(normalize_locate_text("检测结果汇总 检测项目 标准要求 结论")) == "summary"
    assert _page_kind(normalize_locate_text("报告正文 2绕组电阻测量")) == "body"
    assert summary_score > body_score
