"""Literal search over the current inspection-report markdown.

Shared by Recovery ``search_report_context`` and the production Pi agent tool
of the same name. This is not hybrid retrieval: terms are matched as substrings
and returned as bounded windows.
"""
from __future__ import annotations

from typing import Any


def normalize_report_search_terms(terms: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(terms, list):
        raise ValueError("terms must be an array")
    values = [str(item).strip() for item in terms if str(item).strip()]
    if not values or len(values) > limit:
        raise ValueError(f"terms must contain 1-{limit} values")
    return values


def search_report_markdown(
    text: str,
    terms: list[str],
    *,
    max_results: int = 8,
    window: int = 350,
) -> dict[str, Any]:
    """Return literal windows for ``terms`` inside report markdown."""
    cleaned = normalize_report_search_terms(terms)
    bound = max(1, min(int(max_results or 8), 20))
    body = str(text or "")
    matches: list[dict[str, Any]] = []
    lower = body.lower()
    for term in cleaned:
        start = 0
        term_lower = term.lower()
        while len(matches) < bound:
            index = lower.find(term_lower, start)
            if index < 0:
                break
            left = max(0, index - window)
            right = min(len(body), index + len(term) + window)
            matches.append({
                "term": term,
                "char_start": index,
                "snippet": body[left:right],
            })
            start = index + len(term)
        if len(matches) >= bound:
            break
    return {
        "summary": f"found {len(matches)} report matches",
        "terms": cleaned,
        "matches": matches,
    }
