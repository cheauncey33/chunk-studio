"""Optional static notes for report_parameters (field brief is system-built)."""
from __future__ import annotations


def looks_like_full_extraction_prompt(content: str) -> bool:
    """Detect a full extraction system prompt wrongly stored as category notes."""
    text = (content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    has_role = (
        "参数提取" in text
        or "参数提取器" in text
        or ("parameter" in lowered and "提取" in text)
    )
    has_fields = "字段：" in text or '"parameters"' in lowered or "parameter_schema" in lowered
    has_output = (
        "严格输出" in text
        or ('"key"' in lowered and '"value"' in lowered)
    )
    return has_role and has_fields and (has_output or len(text) > 400)


def build_unified_report_parameters_prompt(
    *,
    title: str = "",
    category_notes: str = "",
    confusion_notes: str = "",
    forbid_notes: str = "",
    allow_extra: bool | None = None,
) -> str:
    """Return optional user notes only.

    The runnable extraction brief (role, fields, output shape) is built at runtime
    from parameter_schema via ``format_extraction_brief`` — do not duplicate it here.
    """
    del title, allow_extra  # kept for call-site compatibility
    chunks: list[str] = []
    for block in (category_notes, confusion_notes, forbid_notes):
        text = (block or "").strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks).strip()
