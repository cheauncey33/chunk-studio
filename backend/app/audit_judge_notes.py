"""Helpers for optional audit_judge category notes (not full Judge prompts)."""
from __future__ import annotations


def looks_like_full_audit_judge_prompt(content: str) -> bool:
    """Detect legacy full Judge prompts wrongly stored as category notes.

    Matches both current statuses (supported/mismatch/not_audited) and older
    labels (correct/incorrect/evidence_not_found).
    """
    text = (content or "").strip()
    if not text:
        return False
    lowered = text.lower()

    has_current_statuses = (
        "supported" in lowered
        and "mismatch" in lowered
        and "insufficient_context" in lowered
        and "not_audited" in lowered
    )
    has_legacy_statuses = (
        "correct" in lowered
        and "incorrect" in lowered
        and "insufficient_context" in lowered
        and "evidence_not_found" in lowered
    )
    has_statuses = has_current_statuses or has_legacy_statuses

    has_role = (
        "候选" in text
        or "标准要求" in text
        or "evidence_candidate_keys" in lowered
        or ("审查" in text and "判断" in text)
        or "不得用常识" in text
    )
    has_output = (
        "evidence_candidate_keys" in lowered
        or '"status"' in lowered
        or "严格输出" in text
    )
    return has_statuses and has_role and (has_output or len(text) > 400)
