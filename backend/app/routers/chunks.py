"""Chunk CRUD + region cropping + metadata editing."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, HTTPException

from .. import config, db, extractors, jobs, pdf
from ..models import BBox, ChunkCreate, ChunkOut, ChunkUpdate

router = APIRouter(prefix="/chunks", tags=["chunks"])


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
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, metadata_llm, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, body.file_id, body.page,
                json.dumps(body.bbox.model_dump()),
                0, result.crop_rel, result.text, result.text_source,
                json.dumps(meta, ensure_ascii=False), "{}", "pending", now, now,
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
def list_chunks(file_id: str | None = None, page: int | None = None):
    sql = "SELECT * FROM chunks"
    args: list = []
    clauses = []
    if file_id:
        clauses.append("file_id=?")
        args.append(file_id)
    if page is not None:
        clauses.append("page=?")
        args.append(page)
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
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        if body.text is not None:
            # manual text edit switches source to 'manual'
            conn.execute(
                "UPDATE chunks SET text=?, text_source='manual', updated_at=? WHERE id=?",
                (body.text, now, chunk_id),
            )
        if body.metadata is not None:
            conn.execute(
                "UPDATE chunks SET metadata=?, updated_at=? WHERE id=?",
                (json.dumps(body.metadata, ensure_ascii=False), now, chunk_id),
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
    return ChunkOut(
        id=d["id"], file_id=d["file_id"], page=d["page"], bbox=BBox(**bbox),
        rotation=d.get("rotation", 0), crop_path=d.get("crop_path"), crop_url=crop_url,
        text=d.get("text"), text_source=d["text_source"],
        metadata=json.loads(d.get("metadata") or "{}"),
        metadata_llm=json.loads(d.get("metadata_llm") or "{}"),
        status=d["status"],
        ocr_status=ocr_job["status"] if ocr_job else None,
        ocr_error=ocr_job["error"] if ocr_job else None,
        ocr_job_id=ocr_job["id"] if ocr_job else None,
        created_at=d["created_at"], updated_at=d["updated_at"],
    )
