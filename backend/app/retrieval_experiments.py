"""Phase-4 retrieval-strategy helpers (continuation aggregation + reference expansion).

Production ``hybrid_search`` applies these after rerank (C then B). Evaluation
scripts can still toggle them independently behind explicit flags.

- Experiment B: aggregate continued-table fragments (``表30（续）``) into a
  single evidence unit, optionally pulling missing sibling fragments from DB.
- Experiment C: one-hop reference expansion — when a retrieved section says
  ``见表 N``, bring the corresponding table chunk(s) along as candidates.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import chunk_schema, db

TABLE_REF_RE = re.compile(r"见\s*表\s*([0-9A-Za-z][0-9A-Za-z.\-]*)")


def continuation_group_key(candidate: dict[str, Any]) -> tuple[str, str, str] | None:
    """Grouping key for continued-table fragments; None for non-table chunks.

    Continued fragments keep the base ``table_no`` (see
    ``auto_chunks._apply_table_continuation_metadata``), so grouping by
    (file_id, standard_no, table_no) reunites a split table.
    """
    metadata = candidate.get("business_metadata") or {}
    content_type = candidate.get("content_type") or metadata.get("content_type")
    if content_type != "table":
        return None
    table_no = str(metadata.get("table_no") or "").strip()
    if not table_no:
        return None
    return (
        str(candidate.get("file_id") or ""),
        str(metadata.get("standard_no") or ""),
        table_no,
    )


def aggregate_continuation_tables(
    candidates: list[dict[str, Any]],
    *,
    complete_groups: Callable[[str, str, str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Merge continued-table fragments into single evidence units (experiment B).

    Fragments of the same table collapse into the best-ranked fragment, which
    gains an ``evidence_unit`` with all members (page order) and a merged
    ``text``. When ``complete_groups`` is provided, sibling fragments that were
    not retrieved are pulled into the unit as ``continuation_completion``
    members. Input candidates are not mutated.
    """
    units: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = continuation_group_key(candidate)
        if key is None:
            units.append(candidate)
            continue
        unit = by_key.get(key)
        if unit is None:
            unit = {
                **candidate,
                "evidence_unit": {
                    "kind": "table_group",
                    "group_key": list(key),
                    "members": [dict(candidate)],
                },
            }
            by_key[key] = unit
            units.append(unit)
        else:
            unit["evidence_unit"]["members"].append(dict(candidate))

    for key, unit in by_key.items():
        members = unit["evidence_unit"]["members"]
        if complete_groups is not None:
            member_ids = {str(member.get("chunk_id")) for member in members}
            for sibling in complete_groups(*key):
                sibling_id = str(sibling.get("chunk_id"))
                if sibling_id in member_ids:
                    continue
                members.append({**sibling, "added_by": "continuation_completion"})
                member_ids.add(sibling_id)
        members.sort(key=lambda member: (member.get("page") or 0))
        if len(members) > 1:
            unit["text"] = "\n\n".join(
                str(member.get("text") or "").strip() for member in members
            ).strip()
    return units


def table_references(candidate: dict[str, Any]) -> list[str]:
    """Table numbers referenced by a chunk, from relations or the text itself."""
    refs: list[str] = []
    relations = candidate.get("relations") or {}
    for item in relations.get("references") or []:
        if isinstance(item, dict) and item.get("kind") == "table" and item.get("no"):
            refs.append(str(item["no"]).strip())
    if not refs:
        refs = [match.strip() for match in TABLE_REF_RE.findall(str(candidate.get("text") or ""))]
    return list(dict.fromkeys(ref for ref in refs if ref))


def expand_table_references(
    candidates: list[dict[str, Any]],
    *,
    fetch_table_chunks: Callable[[str, str], list[dict[str, Any]]],
    max_added: int = 5,
) -> list[dict[str, Any]]:
    """Append table chunks referenced by retrieved sections (experiment C).

    Added candidates carry ``added_by='reference_expansion'`` and the source
    section's chunk id so the evaluation can attribute recall gains.
    """
    existing = {str(candidate.get("chunk_id")) for candidate in candidates}
    added: list[dict[str, Any]] = []
    for candidate in candidates:
        metadata = candidate.get("business_metadata") or {}
        content_type = candidate.get("content_type") or metadata.get("content_type")
        if content_type != "section":
            continue
        for table_no in table_references(candidate):
            if len(added) >= max_added:
                break
            for chunk in fetch_table_chunks(str(candidate.get("file_id") or ""), table_no):
                chunk_id = str(chunk.get("chunk_id"))
                if chunk_id in existing or len(added) >= max_added:
                    continue
                added.append({
                    **chunk,
                    "content_type": "table",
                    "added_by": "reference_expansion",
                    "source_chunk_id": candidate.get("chunk_id"),
                })
                existing.add(chunk_id)
    return [*candidates, *added]


def db_table_group_members(
    file_id: str, standard_no: str, table_no: str
) -> list[dict[str, Any]]:
    """Approved table chunks for a table number in a file (page order).

    ``standard_no`` narrows the match when provided; pass ``""`` to match any.
    """
    if not file_id or not table_no:
        return []
    rows = db.get_conn().execute(
        """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.crop_path,
                  c.text, c.business_metadata, c.source_trace
           FROM chunks c
           JOIN files f ON f.id=c.file_id
           WHERE c.status='approved' AND c.file_id=?
             AND json_extract(c.business_metadata, '$.content_type')='table'
             AND json_extract(c.business_metadata, '$.table_no')=?
           ORDER BY c.page, c.created_at""",
        (file_id, table_no),
    ).fetchall()
    members = []
    for row in rows:
        metadata = chunk_schema.parse_json_object(row["business_metadata"])
        if standard_no and str(metadata.get("standard_no") or "") != standard_no:
            continue
        crop_path = row["crop_path"]
        members.append({
            "chunk_id": row["id"],
            "file_id": row["file_id"],
            "file_name": row["file_name"] or "",
            "page": row["page"],
            "crop_url": f"/crops/{crop_path.split('/')[-1]}" if crop_path else None,
            "text": row["text"] or "",
            "business_metadata": metadata,
            "source_trace": chunk_schema.parse_json_object(row["source_trace"]),
            "content_type": "table",
        })
    return members


def db_fetch_table_chunks(file_id: str, table_no: str) -> list[dict[str, Any]]:
    """DB fetcher for reference expansion (any standard within the file)."""
    return db_table_group_members(file_id, "", table_no)
