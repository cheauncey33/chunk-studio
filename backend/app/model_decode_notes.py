"""Optional notes for model_decode (framework brief is system-built)."""
from __future__ import annotations


def looks_like_full_model_decode_prompt(content: str) -> bool:
    """Detect a full model-decode system prompt stored as notes."""
    text = (content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    has_role = (
        "型号命名" in text
        or "命名规则解析" in text
        or ("型号" in text and ("解析" in text or "解码" in text))
    )
    has_task = (
        "decoded_features" in lowered
        or "retrieval_terms" in lowered
        or "unresolved_segments" in lowered
        or "命名规则" in text
    )
    has_output = (
        "严格输出" in text
        or '"raw_model"' in lowered
        or ("json" in lowered and "decoded" in lowered)
    )
    return has_role and has_task and (has_output or len(text) > 300)
