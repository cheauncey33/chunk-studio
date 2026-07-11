"""Stable standard-evidence locators for evaluation and audit results."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping


def canonicalize_chunk_text(text: str | None) -> str:
    """Normalize layout-only whitespace before fingerprinting chunk content."""
    return re.sub(r"\s+", " ", text or "").strip()


def chunk_text_sha256(text: str | None) -> str:
    canonical = canonicalize_chunk_text(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_evidence_locator(
    conn: sqlite3.Connection,
    locator: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return current chunks that exactly match a stable evidence locator."""
    clauses = [
        "json_extract(business_metadata, '$.standard_no') = ?",
        "json_extract(business_metadata, '$.content_type') = ?",
    ]
    params: list[Any] = [locator["standard_no"], locator["content_type"]]

    for field in ("section", "table_no"):
        if locator.get(field):
            clauses.append(f"json_extract(business_metadata, '$.{field}') = ?")
            params.append(locator[field])

    rows = conn.execute(
        f"""
        SELECT id, page, text, business_metadata, source_trace
        FROM chunks
        WHERE {' AND '.join(clauses)}
        """,
        params,
    ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0],
            "page": row[1],
            "text": row[2],
            "business_metadata": row[3],
            "source_trace": row[4],
        }
        business = _json_object(item["business_metadata"])
        trace = _json_object(item["source_trace"])
        page_start = trace.get("page_start", item["page"])
        page_end = trace.get("page_end", item["page"])

        if page_start != locator["page_start"] or page_end != locator["page_end"]:
            continue
        if locator.get("section_title") != business.get("section_title") and locator.get("section_title"):
            continue
        if locator.get("table_title") != business.get("table_title") and locator.get("table_title"):
            continue
        if chunk_text_sha256(item["text"]) != locator["text_sha256"]:
            continue
        matches.append({
            "current_chunk_id": item["id"],
            "page_start": page_start,
            "page_end": page_end,
            "business_metadata": business,
            "canonical_text": canonicalize_chunk_text(item["text"]),
        })
    return matches


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
