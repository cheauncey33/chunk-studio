"""Chunk CRUD + region cropping + metadata editing."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

from .. import artifacts, chunk_schema, config, current_user, db, extractors, jobs, pdf
from ..models import BBox, ChunkCreate, ChunkOut, ChunkUpdate
from ..storage.object_store import get_object_store
from ..storage.repositories import get_content_repository, get_content_write_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chunks", tags=["chunks"])

_ALLOWED_STATUS_TRANSITIONS = {
    # Editor status dropdown and list enable toggle may jump between any
    # review state; identical status is a no-op in the validator.
    "pending": {"reviewed", "approved", "rejected"},
    "reviewed": {"pending", "approved", "rejected"},
    "approved": {"pending", "reviewed", "rejected"},
    "rejected": {"pending", "reviewed", "approved"},
}

_REVIEW_SENSITIVE_JSON_FIELDS = (
    "metadata",
    "business_metadata",
    "metadata_llm",
    "source_trace",
    "chunk_logic",
    "relations",
)


def _workspace_id() -> str:
    return current_user.get_current_user().workspace_id


def _sidecar_authorized(authorization: str | None) -> bool:
    expected = str(config.AGENT_SIDECAR_TOKEN or "").strip()
    return bool(expected) and str(authorization or "").strip() == f"Bearer {expected}"


def _workspace_for_chunk_read(
    authorization: str | None,
    workspace_id: str | None,
) -> str:
    """Sidecar may read a bound originating workspace; users cannot hop."""
    requested = str(workspace_id or "").strip()
    if _sidecar_authorized(authorization) and requested:
        return requested
    return current_user.get_current_user().workspace_id


@router.post("")
async def create_chunk(body: ChunkCreate):
    """Box-select endpoint: crop region at 300DPI, extract embedded text, store chunk."""
    content = get_content_write_repository()
    f = (
        content.get_file(body.file_id)
        if content is not None
        else db.get_conn().execute(
            "SELECT * FROM files WHERE id=? AND workspace_id=?",
            (body.file_id, _workspace_id()),
        ).fetchone()
    )
    if not f:
        raise HTTPException(404, "file not found")
    f = dict(f)
    if body.page < 1 or body.page > f["page_count"]:
        raise HTTPException(404, "page out of range")

    # crop + extract in a thread so we don't block the loop
    pdf_rel = str(f.get("path") or "")
    if not pdf_rel or not config.from_rel(pdf_rel).is_file():
        materialized = artifacts.materialize_artifact(
            f.get("path"),
            f.get("object_key"),
            cache_name=f"{body.file_id}-source",
            suffix=".pdf",
        )
        if materialized is None:
            raise HTTPException(404, "stored file missing")
        pdf_rel = config.to_rel(materialized)
    result = await asyncio.to_thread(
        pdf.crop_region, pdf_rel, body.page - 1, body.bbox.model_dump()
    )

    cid = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    raw_metadata = f.get("metadata")
    if isinstance(raw_metadata, dict):
        meta = dict(raw_metadata)
    else:
        try:
            meta = json.loads(raw_metadata or "{}")
        except Exception:
            meta = {}
    # Auto-extract deterministic metadata (standard_no from filename, plus
    # content_type/table_* from any embedded <table>). Merged into an empty
    # file-level metadata copy, so all derived keys fill only empty slots;
    # re-runs (e.g. after OCR) won't clobber file or user edits.
    meta = extractors.merge_auto_metadata(meta, result.text, f["name"])
    source_trace = chunk_schema.source_trace_for_region(body.page, body.bbox.model_dump())
    chunk_logic = chunk_schema.chunk_logic_for_manual()
    crop_info = None
    if content is not None:
        crop_path = config.from_rel(result.crop_rel)
        crop_info = get_object_store().put_bytes(
            artifacts.crop_artifact_key(_workspace_id(), body.file_id, cid),
            crop_path.read_bytes(),
            content_type="image/png",
        )
        content.create_chunk({
            "id": cid,
            "workspace_id": _workspace_id(),
            "file_id": body.file_id,
            "page": body.page,
            "bbox": body.bbox.model_dump(),
            "rotation": 0,
            "crop_path": result.crop_rel,
            "crop_object_key": crop_info.key,
            "crop_sha256": crop_info.sha256,
            "crop_size": crop_info.size,
            "text": result.text,
            "text_source": result.text_source,
            "metadata": {},
            "business_metadata": meta,
            "metadata_llm": {},
            "source_trace": source_trace,
            "chunk_logic": chunk_logic,
            "relations": {},
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        })
    else:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO chunks
                   (id, workspace_id, file_id, page, bbox, rotation, crop_path, text, text_source,
                    metadata, business_metadata, metadata_llm, source_trace, chunk_logic, relations,
                    status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, _workspace_id(), body.file_id, body.page,
                    json.dumps(body.bbox.model_dump()),
                    0, result.crop_rel, result.text, result.text_source,
                    "{}", json.dumps(meta, ensure_ascii=False), "{}",
                    json.dumps(source_trace, ensure_ascii=False),
                    json.dumps(chunk_logic, ensure_ascii=False), "{}",
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
    repository = get_content_repository()
    if repository is not None:
        return [
            _row_to_out(row)
            for row in repository.list_chunks(
                file_id=file_id,
                page=page,
                has_llm_suggestions=has_llm_suggestions,
            )
        ]
    sql = "SELECT * FROM chunks"
    args: list = [_workspace_id()]
    clauses = ["workspace_id=?"]
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
def get_chunk(
    chunk_id: str,
    workspace_id: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    return _get_chunk(
        chunk_id,
        workspace_id=_workspace_for_chunk_read(authorization, workspace_id),
    )


@router.get("/{chunk_id}/crop")
def get_chunk_crop(chunk_id: str):
    """Serve a crop from shared object storage, with local rollback fallback."""
    repository = get_content_repository()
    row = repository.get_chunk(chunk_id) if repository is not None else None
    if row is None:
        row = db.get_conn().execute(
            "SELECT * FROM chunks WHERE id=? AND workspace_id=?",
            (chunk_id, _workspace_id()),
        ).fetchone()
        row = dict(row) if row else None
    if not row:
        raise HTTPException(404, "chunk not found")
    object_key = str(row.get("crop_object_key") or "").strip()
    if object_key:
        try:
            return Response(get_object_store().get_bytes(object_key), media_type="image/png")
        except Exception as exc:
            raise HTTPException(404, "crop object missing") from exc
    crop_path = str(row.get("crop_path") or "").strip()
    if crop_path:
        path = config.from_rel(crop_path)
        if path.is_file():
            return Response(path.read_bytes(), media_type="image/png")
    raise HTTPException(404, "crop image missing")


@router.patch("/{chunk_id}")
def update_chunk(chunk_id: str, body: ChunkUpdate):
    content = get_content_write_repository()
    if content is not None:
        current = content.get_chunk(chunk_id)
        if not current:
            raise HTTPException(404, "chunk not found")
        content_changed = _review_sensitive_change(current, body)
        if body.status is not None:
            if content_changed:
                raise HTTPException(409, "content and review status must be updated separately")
            _validate_status_transition(current["status"], body.status)
        values: dict[str, Any] = {}
        if body.text is not None and body.text != current.get("text"):
            values.update({"text": body.text, "text_source": "manual"})
        if body.metadata is not None:
            business_metadata, source_trace, chunk_logic, relations = (
                chunk_schema.split_flat_metadata_for_write(body.metadata)
            )
            values.update({
                "metadata": body.metadata,
                "business_metadata": business_metadata,
                "source_trace": source_trace,
                "chunk_logic": chunk_logic,
                "relations": relations,
            })
        for field in (
            "business_metadata", "metadata_llm", "source_trace", "chunk_logic", "relations",
            "ui_state", "indexing", "text_source",
        ):
            value = getattr(body, field)
            if value is not None:
                values[field] = value
        if body.status is not None:
            values["status"] = body.status
        elif content_changed and current["status"] != "pending":
            values["status"] = "pending"
        content.update_chunk(chunk_id, values)
        if body.status == "approved":
            try:
                jobs.enqueue_build_embeddings()
            except Exception:
                logger.exception("failed to queue embedding build after approval")
        return _get_chunk(chunk_id)
    cur = db.get_conn().execute(
        "SELECT * FROM chunks WHERE id=? AND workspace_id=?",
        (chunk_id, _workspace_id()),
    ).fetchone()
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
                "UPDATE chunks SET text=?, text_source='manual', updated_at=? WHERE id=? AND workspace_id=?",
                (body.text, now, chunk_id, _workspace_id()),
            )
        if body.metadata is not None:
            business_metadata, source_trace, chunk_logic, relations = chunk_schema.split_flat_metadata_for_write(body.metadata)
            conn.execute(
                """UPDATE chunks
                   SET metadata=?, business_metadata=?, source_trace=?, chunk_logic=?, relations=?, updated_at=?
                   WHERE id=? AND workspace_id=?""",
                (
                    json.dumps(body.metadata, ensure_ascii=False),
                    json.dumps(business_metadata, ensure_ascii=False),
                    json.dumps(source_trace, ensure_ascii=False),
                    json.dumps(chunk_logic, ensure_ascii=False),
                    json.dumps(relations, ensure_ascii=False),
                    now,
                    chunk_id,
                    _workspace_id(),
                ),
            )
        if body.business_metadata is not None:
            conn.execute(
                "UPDATE chunks SET business_metadata=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.business_metadata, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.metadata_llm is not None:
            conn.execute(
                "UPDATE chunks SET metadata_llm=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.metadata_llm, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.source_trace is not None:
            conn.execute(
                "UPDATE chunks SET source_trace=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.source_trace, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.chunk_logic is not None:
            conn.execute(
                "UPDATE chunks SET chunk_logic=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.chunk_logic, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.relations is not None:
            conn.execute(
                "UPDATE chunks SET relations=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.relations, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.ui_state is not None:
            conn.execute(
                "UPDATE chunks SET ui_state=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.ui_state, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.indexing is not None:
            conn.execute(
                "UPDATE chunks SET indexing=?, updated_at=? WHERE id=? AND workspace_id=?",
                (json.dumps(body.indexing, ensure_ascii=False), now, chunk_id, _workspace_id()),
            )
        if body.text_source is not None:
            conn.execute(
                "UPDATE chunks SET text_source=?, updated_at=? WHERE id=? AND workspace_id=?",
                (body.text_source, now, chunk_id, _workspace_id()),
            )
        if body.status is not None:
            conn.execute(
                "UPDATE chunks SET status=?, updated_at=? WHERE id=? AND workspace_id=?",
                (body.status, now, chunk_id, _workspace_id()),
            )
        elif content_changed and cur["status"] != "pending":
            conn.execute(
                "UPDATE chunks SET status='pending', updated_at=? WHERE id=? AND workspace_id=?",
                (now, chunk_id, _workspace_id()),
            )
    if body.status == "approved":
        # Approved chunks should become retrievable without a manual CLI step.
        # Failure to queue must not roll back the approval itself.
        try:
            jobs.enqueue_build_embeddings()
        except Exception:
            logger.exception("failed to enqueue embedding build after approval")
    return _get_chunk(chunk_id)


@router.delete("/{chunk_id}")
def delete_chunk(chunk_id: str):
    content = get_content_write_repository()
    if content is not None:
        deleted = content.delete_chunk(chunk_id)
        if not deleted:
            raise HTTPException(404, "chunk not found")
        if deleted.get("crop_path"):
            try:
                config.from_rel(deleted["crop_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        if deleted.get("crop_object_key"):
            try:
                get_object_store().delete(deleted["crop_object_key"])
            except Exception:
                logger.exception("failed to delete object %s", deleted["crop_object_key"])
        return {"ok": True}
    cur = db.get_conn().execute(
        "SELECT crop_path FROM chunks WHERE id=? AND workspace_id=?",
        (chunk_id, _workspace_id()),
    ).fetchone()
    if not cur:
        raise HTTPException(404, "chunk not found")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM chunks WHERE id=? AND workspace_id=?",
            (chunk_id, _workspace_id()),
        )
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


def _get_chunk(cid: str, workspace_id: str | None = None) -> ChunkOut:
    workspace = str(workspace_id or _workspace_id()).strip()
    repository = get_content_repository()
    if repository is not None:
        row = repository.get_chunk(cid, workspace_id=workspace)
        if not row:
            raise HTTPException(404, "chunk not found")
        return _row_to_out(row)
    row = db.get_conn().execute(
        "SELECT * FROM chunks WHERE id=? AND workspace_id=?",
        (cid, workspace),
    ).fetchone()
    if not row:
        raise HTTPException(404, "chunk not found")
    return _row_to_out(row)


def _row_to_out(r) -> ChunkOut:
    d = dict(r)
    bbox = d["bbox"] if isinstance(d["bbox"], dict) else json.loads(d["bbox"])
    crop_url = (
        f"/api/chunks/{d['id']}/crop"
        if d.get("crop_object_key")
        else (f"/crops/{d['crop_path'].split('/')[-1]}" if d.get("crop_path") else None)
    )
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
