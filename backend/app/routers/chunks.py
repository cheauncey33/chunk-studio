"""Chunk CRUD + region cropping + metadata editing."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, HTTPException

from .. import chunk_schema, config, db, extractors, jobs, pdf
from ..models import BBox, ChunkCreate, ChunkOut, ChunkUpdate

router = APIRouter(prefix="/chunks", tags=["chunks"])

_ALLOWED_STATUS_TRANSITIONS = {
    "pending": {"reviewed"},
    "reviewed": {"approved", "rejected"},
    "approved": set(),
    "rejected": set(),
}

_REVIEW_SENSITIVE_JSON_FIELDS = (
    "metadata",
    "business_metadata",
    "metadata_llm",
    "source_trace",
    "chunk_logic",
    "relations",
)


@router.post("")
async def create_chunk(body: ChunkCreate):
    """Box-select endpoint: crop region at 300DPI, extract embedded text, store chunk."""
    f = db.get_conn().execute("SELECT * FROM files WHERE id=?", (body.file_id,)).fetchone()
    if not f:
        raise HTTPException(404, "file not found")
    f = dict(f)
    if body.page < 1 or body.page > f["page_count"]:
        raise HTTPException(404, "page out of range")

    # crop + extract in a thread so we don't block the loop
    result = await asyncio.to_thread(
        pdf.crop_region, f["path"], body.page - 1, body.bbox.model_dump()
    )

    cid = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    # Auto-extract deterministic metadata (standard_no from filename, plus
    # content_type/table_* from any embedded <table>). Merged into an empty
    # file-level metadata copy, so all derived keys fill only empty slots;
    # re-runs (e.g. after OCR) won't clobber file or user edits.
    meta = extractors.merge_auto_metadata(meta, result.text, f["name"])
    source_trace = chunk_schema.source_trace_for_region(body.page, body.bbox.model_dump())
    chunk_logic = chunk_schema.chunk_logic_for_manual()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, business_metadata, metadata_llm, source_trace, chunk_logic, relations,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, body.file_id, body.page,
                json.dumps(body.bbox.model_dump()),
                0, result.crop_rel, result.text, result.text_source,
                "{}", json.dumps(meta, ensure_ascii=False), "{}",
                json.dumps(source_trace, ensure_ascii=False),
                json.dumps(chunk_logic, ensure_ascii=False),
                "{}",
                "pending", now, now,
            ),
        )
    if (
        result.text_source == "pending"
        and db.get_setting("ocr.auto_on_create", "false") == "true"
        and _mineru_configured()
    ):
        await jobs.ocr_chunk_sync(cid)
    return _get_chunk(cid)


@router.get("")
def list_chunks(
    file_id: str | None = None,
    page: int | None = None,
    has_llm_suggestions: bool = False,
):
    sql = "SELECT * FROM chunks"
    args: list = []
    clauses = []
    if file_id:
        clauses.append("file_id=?")
        args.append(file_id)
    if page is not None:
        clauses.append("page=?")
        args.append(page)
    if has_llm_suggestions:
        clauses.append(
            """(
              COALESCE(json_array_length(metadata_llm, '$.keywords.value'), 0) > 0
              OR COALESCE(json_array_length(metadata_llm, '$.questions.value'), 0) > 0
            )"""
        )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY page, created_at"
    rows = db.get_conn().execute(sql, args).fetchall()
    return [_row_to_out(r) for r in rows]


@router.get("/{chunk_id}")
def get_chunk(chunk_id: str):
    return _get_chunk(chunk_id)


@router.patch("/{chunk_id}")
def update_chunk(chunk_id: str, body: ChunkUpdate):
    cur = db.get_conn().execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if not cur:
        raise HTTPException(404, "chunk not found")
    cur = dict(cur)
    content_changed = _review_sensitive_change(cur, body)
    if body.status is not None:
        if content_changed:
            raise HTTPException(409, "content and review status must be updated separately")
        _validate_status_transition(cur["status"], body.status)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        if body.text is not None and body.text != cur.get("text"):
            # manual text edit switches source to 'manual'
            conn.execute(
                "UPDATE chunks SET text=?, text_source='manual', updated_at=? WHERE id=?",
                (body.text, now, chunk_id),
            )
        if body.metadata is not None:
            business_metadata, source_trace, chunk_logic, relations = chunk_schema.split_flat_metadata_for_write(body.metadata)
            conn.execute(
                """UPDATE chunks
                   SET metadata=?, business_metadata=?, source_trace=?, chunk_logic=?, relations=?, updated_at=?
                   WHERE id=?""",
                (
                    json.dumps(body.metadata, ensure_ascii=False),
                    json.dumps(business_metadata, ensure_ascii=False),
                    json.dumps(source_trace, ensure_ascii=False),
                    json.dumps(chunk_logic, ensure_ascii=False),
                    json.dumps(relations, ensure_ascii=False),
                    now,
                    chunk_id,
                ),
            )
        if body.business_metadata is not None:
            conn.execute(
                "UPDATE chunks SET business_metadata=?, updated_at=? WHERE id=?",
                (json.dumps(body.business_metadata, ensure_ascii=False), now, chunk_id),
            )
        if body.metadata_llm is not None:
            conn.execute(
                "UPDATE chunks SET metadata_llm=?, updated_at=? WHERE id=?",
                (json.dumps(body.metadata_llm, ensure_ascii=False), now, chunk_id),
            )
        if body.source_trace is not None:
            conn.execute(
                "UPDATE chunks SET source_trace=?, updated_at=? WHERE id=?",
                (json.dumps(body.source_trace, ensure_ascii=False), now, chunk_id),
            )
        if body.chunk_logic is not None:
            conn.execute(
                "UPDATE chunks SET chunk_logic=?, updated_at=? WHERE id=?",
                (json.dumps(body.chunk_logic, ensure_ascii=False), now, chunk_id),
            )
        if body.relations is not None:
            conn.execute(
                "UPDATE chunks SET relations=?, updated_at=? WHERE id=?",
                (json.dumps(body.relations, ensure_ascii=False), now, chunk_id),
            )
        if body.ui_state is not None:
            conn.execute(
                "UPDATE chunks SET ui_state=?, updated_at=? WHERE id=?",
                (json.dumps(body.ui_state, ensure_ascii=False), now, chunk_id),
            )
        if body.indexing is not None:
            conn.execute(
                "UPDATE chunks SET indexing=?, updated_at=? WHERE id=?",
                (json.dumps(body.indexing, ensure_ascii=False), now, chunk_id),
            )
        if body.text_source is not None:
            conn.execute(
                "UPDATE chunks SET text_source=?, updated_at=? WHERE id=?",
                (body.text_source, now, chunk_id),
            )
        if body.status is not None:
            conn.execute(
                "UPDATE chunks SET status=?, updated_at=? WHERE id=?",
                (body.status, now, chunk_id),
            )
        elif content_changed and cur["status"] != "pending":
            conn.execute(
                "UPDATE chunks SET status='pending', updated_at=? WHERE id=?",
                (now, chunk_id),
            )
    return _get_chunk(chunk_id)


@router.delete("/{chunk_id}")
def delete_chunk(chunk_id: str):
    cur = db.get_conn().execute("SELECT crop_path FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if not cur:
        raise HTTPException(404, "chunk not found")
    with db.transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))
    if cur["crop_path"]:
        try:
            config.from_rel(cur["crop_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


# --- helpers ---
def _mineru_configured() -> bool:
    import os
    token = db.get_setting("mineru.token", "") or os.environ.get("MINERU_TOKEN", "")
    return bool(token.strip())


def _validate_status_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = _ALLOWED_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(409, f"invalid chunk status transition: {current} -> {target}")


def _review_sensitive_change(current: dict, body: ChunkUpdate) -> bool:
    if body.text is not None and body.text != current.get("text"):
        return True
    if body.text_source is not None and body.text_source != current.get("text_source"):
        return True
    for field in _REVIEW_SENSITIVE_JSON_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        if value != chunk_schema.parse_json_object(current.get(field)):
            return True
    return False


def _get_chunk(cid: str) -> ChunkOut:
    row = db.get_conn().execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "chunk not found")
    return _row_to_out(row)


def _row_to_out(r) -> ChunkOut:
    d = dict(r)
    bbox = json.loads(d["bbox"])
    crop_url = f"/crops/{d['crop_path'].split('/')[-1]}" if d.get("crop_path") else None
    ocr_job = jobs.latest_job_for_target(d["id"], "ocr")
    layers = chunk_schema.ensure_layered_chunk(
        metadata=d.get("metadata"),
        business_metadata=d.get("business_metadata"),
        source_trace=d.get("source_trace"),
        chunk_logic=d.get("chunk_logic"),
        relations=d.get("relations"),
    )
    metadata = chunk_schema.flatten_for_legacy(
        layers["business_metadata"],
        layers["source_trace"],
        layers["chunk_logic"],
        layers["relations"],
        d.get("metadata"),
    )
    return ChunkOut(
        id=d["id"], file_id=d["file_id"], page=d["page"], bbox=BBox(**bbox),
        rotation=d.get("rotation", 0), crop_path=d.get("crop_path"), crop_url=crop_url,
        text=d.get("text"), text_source=d["text_source"],
        metadata=metadata,
        business_metadata=layers["business_metadata"],
        metadata_llm=chunk_schema.parse_json_object(d.get("metadata_llm")),
        source_trace=layers["source_trace"],
        chunk_logic=layers["chunk_logic"],
        relations=layers["relations"],
        ui_state=chunk_schema.parse_json_object(d.get("ui_state")),
        indexing=chunk_schema.parse_json_object(d.get("indexing")),
        status=d["status"],
        ocr_status=ocr_job["status"] if ocr_job else None,
        ocr_error=ocr_job["error"] if ocr_job else None,
        ocr_job_id=ocr_job["id"] if ocr_job else None,
        created_at=d["created_at"], updated_at=d["updated_at"],
    )
