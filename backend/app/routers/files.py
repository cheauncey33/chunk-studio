"""File upload / list / page-image / delete."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel

from .. import config, db, jobs, pdf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])

_PDF_MAGIC = b"%PDF-"


class FileUpdate(BaseModel):
    metadata: dict[str, Any] | None = None


@router.post("")
async def upload(
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
    knowledge_base_id: str | None = Form(None),
):
    """Upload a PDF. Validates magic bytes, content-addressed copy into data/files."""
    target_kb = None
    if knowledge_base_id:
        target_kb = db.get_conn().execute(
            "SELECT id FROM knowledge_bases WHERE id=? AND status='active'",
            (knowledge_base_id,),
        ).fetchone()
        if not target_kb:
            raise HTTPException(404, "knowledge base not found")

    raw = await file.read()
    if not raw.startswith(_PDF_MAGIC):
        raise HTTPException(422, "not a valid PDF (bad magic bytes)")
    try:
        file_meta = json.loads(metadata or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"invalid metadata JSON: {exc.msg}") from exc
    if not isinstance(file_meta, dict):
        raise HTTPException(422, "metadata must be a JSON object")
    file_meta = {k: v for k, v in file_meta.items() if v not in ("", None, [], {})}
    sha = hashlib.sha256(raw).hexdigest()[:16]
    safe_name = Path(file.filename or "upload.pdf").name
    stored = f"{sha}_{safe_name}"
    dest = config.FILES_DIR / stored
    if not dest.exists():
        dest.write_bytes(raw)
    file_id = uuid.uuid4().hex
    file_rel = config.to_rel(dest)
    # page count in a thread
    n_pages = await asyncio.to_thread(pdf.page_count, file_rel)
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO files(id,name,path,sha,page_count,metadata,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                file_id, safe_name, file_rel, sha, n_pages,
                json.dumps(file_meta, ensure_ascii=False), created,
            ),
        )
        relation_kb = target_kb or conn.execute(
            "SELECT id FROM knowledge_bases WHERE is_default=1 ORDER BY created_at LIMIT 1"
        ).fetchone()
        if relation_kb:
            conn.execute(
                """INSERT OR IGNORE INTO knowledge_base_files
                   (knowledge_base_id, file_id, role, enabled, created_at)
                   VALUES (?,?, 'source', 1, ?)""",
                (relation_kb["id"], file_id, created),
            )
    try:
        jobs.enqueue_parse_file(file_id)
    except Exception:
        logger.exception("failed to enqueue parse for file %s", file_id)
    return _file_out({
        "id": file_id,
        "name": safe_name,
        "path": file_rel,
        "sha": sha,
        "page_count": n_pages,
        "metadata": json.dumps(file_meta, ensure_ascii=False),
        "created_at": created,
    })


@router.get("")
def list_files():
    rows = db.get_conn().execute("SELECT * FROM files ORDER BY created_at DESC").fetchall()
    return [_file_out(dict(r)) for r in rows]


@router.get("/{file_id}")
def get_file(file_id: str):
    row = db.get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    return _file_out(dict(row))


@router.patch("/{file_id}")
def update_file(file_id: str, body: FileUpdate):
    row = db.get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    if body.metadata is not None:
        metadata = {k: v for k, v in body.metadata.items() if v not in ("", None, [], {})}
        with db.transaction() as conn:
            conn.execute(
                "UPDATE files SET metadata=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), file_id),
            )
    updated = db.get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    return _file_out(dict(updated))


@router.get("/{file_id}/parses")
def list_file_parses(file_id: str):
    _get_file(file_id)
    rows = db.get_conn().execute(
        """SELECT * FROM document_parses
           WHERE file_id=?
           ORDER BY created_at DESC""",
        (file_id,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["result"] = json.loads(item.get("result") or "{}")
        except Exception:
            item["result"] = {}
        out.append(item)
    return out


@router.post("/{file_id}/parse")
def enqueue_file_parse(file_id: str):
    _get_file(file_id)
    try:
        return jobs.enqueue_parse_file(file_id)
    except KeyError:
        raise HTTPException(404, "file not found")


@router.get("/{file_id}/pages/{page_no}")
async def page_image(file_id: str, page_no: int):
    f = _get_file(file_id)
    if page_no < 1 or page_no > f["page_count"]:
        raise HTTPException(404, "page out of range")
    png = await asyncio.to_thread(pdf.render_page_png, f["path"], page_no - 1)
    return Response(content=png, media_type="image/png")


@router.delete("/{file_id}")
def delete_file(file_id: str):
    f = _get_file(file_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    # best-effort delete the stored PDF
    try:
        config.from_rel(f["path"]).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


def _get_file(file_id: str):
    row = db.get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    return dict(row)


def _file_out(row: dict):
    try:
        metadata = json.loads(row.get("metadata") or "{}")
    except Exception:
        metadata = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "page_count": row["page_count"],
        "metadata": metadata,
        "created_at": row["created_at"],
    }
