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
    """Return logical evidence matches for a stable locator.

    A standard PDF can be registered more than once while pointing at the same
    stored source path.  Those rows produce physically distinct chunk IDs but
    are not distinct evidence.  Collapse only that exact duplicate condition;
    different source paths remain separate matches and keep the locator
    ambiguous for review.
    """
    clauses = [
        "json_extract(business_metadata, '$.standard_no') = ?",
        "json_extract(business_metadata, '$.content_type') = ?",
    ]
    params: list[Any] = [locator["standard_no"], locator["content_type"]]

    for field in ("section", "table_no"):
        if locator.get(field):
            clauses.append(f"json_extract(business_metadata, '$.{field}') = ?")
            params.append(locator[field])

    try:
        rows = conn.execute(
            f"""
            SELECT c.id, c.file_id, f.path AS source_path, c.page, c.text,
                   c.business_metadata, c.source_trace
            FROM chunks c
            JOIN files f ON f.id=c.file_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError as error:
        # Tiny in-memory tests and third-party callers may provide only the
        # chunks table. Keep the original resolver contract in that case.
        if "no such table: files" not in str(error):
            raise
        rows = conn.execute(
            f"""
            SELECT id, id AS file_id, id AS source_path, page, text,
                   business_metadata, source_trace
            FROM chunks
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0],
            "file_id": row[1],
            "source_path": row[2],
            "page": row[3],
            "text": row[4],
            "business_metadata": row[5],
            "source_trace": row[6],
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
            "current_chunk_ids": [item["id"]],
            "source_path": item["source_path"],
            "page_start": page_start,
            "page_end": page_end,
            "business_metadata": business,
            "canonical_text": canonicalize_chunk_text(item["text"]),
        })

    logical: dict[tuple[str, str], dict[str, Any]] = {}
    for match in matches:
        key = (str(match["source_path"]), chunk_text_sha256(match["canonical_text"]))
        existing = logical.get(key)
        if existing is None:
            logical[key] = match
        else:
            existing["current_chunk_ids"].extend(match["current_chunk_ids"])
    return list(logical.values())


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
