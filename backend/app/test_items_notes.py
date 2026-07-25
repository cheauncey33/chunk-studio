"""Optional notes for test_items (framework brief is system-built)."""
from __future__ import annotations


def looks_like_full_test_items_prompt(content: str) -> bool:
    """Detect a full test-item extraction system prompt stored as notes."""
    text = (content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    has_role = (
        "项目提取" in text
        or "结构化提取器" in text
        or ("检测报告" in text and "提取" in text)
    )
    has_task = (
        "检测结果汇总" in text
        or "sample_context" in lowered
        or "test_items" in lowered
        or "实际检测项目" in text
    )
    has_output = (
        "严格输出" in text
        or '"phase"' in lowered
        or ("json" in lowered and "report_id" in lowered)
    )
    return has_role and has_task and (has_output or len(text) > 400)
