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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import config, current_user, db, jobs, pdf
from ..storage.object_store import get_object_store
from ..storage.repositories import get_content_repository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])

_PDF_MAGIC = b"%PDF-"
_CORPUS_KINDS = frozenset({"standard", "spec"})
_NON_CORPUS_ROLES = frozenset({"report", "naming", "sample_report"})


def _workspace_id() -> str:
    return current_user.get_current_user().workspace_id


class FileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, Any] | None = None


def _normalize_doc_role(file_meta: dict[str, Any]) -> str:
    role = str(file_meta.get("doc_role") or file_meta.get("doc_type") or "").strip().lower()
    return role


def _resolve_corpus_kind(file_meta: dict[str, Any]) -> str:
    raw = str(file_meta.get("corpus_kind") or "").strip().lower()
    if raw in _CORPUS_KINDS:
        return raw
    role = _normalize_doc_role(file_meta)
    if role == "spec":
        return "spec"
    if role in {"standard", "source", "reference"}:
        return "standard" if role != "reference" else "spec"
    return "standard"


@router.post("")
async def upload(
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
    knowledge_base_id: str | None = Form(None),
):
    """Upload a PDF. Validates magic bytes, content-addressed copy into data/files.

    Corpus files (standard/spec) attach to a knowledge base. Report, sample_report
    and naming uploads stay outside knowledge_base_files; naming updates
    default_naming_file_id.
    """
    target_kb = None
    if knowledge_base_id:
        target_kb = db.get_conn().execute(
            """SELECT id FROM knowledge_bases
               WHERE id=? AND workspace_id=? AND status='active'""",
            (knowledge_base_id, _workspace_id()),
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
    doc_role = _normalize_doc_role(file_meta)
    if doc_role in _NON_CORPUS_ROLES:
        file_meta["doc_role"] = doc_role
        file_meta.setdefault("doc_type", doc_role)
    elif doc_role not in _NON_CORPUS_ROLES:
        corpus_kind = _resolve_corpus_kind(file_meta)
        file_meta["corpus_kind"] = corpus_kind
        file_meta.setdefault("doc_role", corpus_kind)
        file_meta.setdefault("doc_type", corpus_kind)

    sha = hashlib.sha256(raw).hexdigest()[:16]
    safe_name = Path(file.filename or "upload.pdf").name
    stored = f"{sha}_{safe_name}"
    dest = config.FILES_DIR / stored
    if not dest.exists():
        dest.write_bytes(raw)
    file_id = uuid.uuid4().hex
    object_key = f"workspaces/{_workspace_id()}/files/{file_id}/content.pdf"
    object_info = get_object_store().put_bytes(
        object_key,
        raw,
        content_type="application/pdf",
    )
    file_rel = config.to_rel(dest)
    n_pages = await asyncio.to_thread(pdf.page_count, file_rel)
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files
               (id,workspace_id,name,path,sha,object_key,page_count,metadata,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                file_id, _workspace_id(), safe_name, file_rel, sha, object_info.key, n_pages,
                json.dumps(file_meta, ensure_ascii=False), created,
            ),
        )
        if doc_role == "naming":
            if not target_kb:
                raise HTTPException(400, "naming upload requires knowledge_base_id")
            conn.execute(
                """UPDATE knowledge_bases
                   SET default_naming_file_id=?, updated_at=?
                   WHERE id=? AND workspace_id=?""",
                (file_id, created, target_kb["id"], _workspace_id()),
            )
        elif doc_role in {"report", "sample_report"}:
            # Audit / init inputs only; do not attach to a knowledge base.
            pass
        else:
            relation_kb = target_kb or conn.execute(
                """SELECT id FROM knowledge_bases
                   WHERE is_default=1 AND workspace_id=? ORDER BY created_at LIMIT 1""",
                (_workspace_id(),),
            ).fetchone()
            if relation_kb:
                corpus_kind = _resolve_corpus_kind(file_meta)
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_base_files
                       (knowledge_base_id, file_id, workspace_id, role, corpus_kind,
                        enabled, created_at)
                       VALUES (?,?,?, 'source', ?, 1, ?)""",
                    (relation_kb["id"], file_id, _workspace_id(), corpus_kind, created),
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
        "object_key": object_info.key,
    })


@router.get("")
def list_files():
    repository = get_content_repository()
    if repository is not None:
        return [_file_out(row, include_parse=True) for row in repository.list_files()]
    rows = db.get_conn().execute(
        "SELECT * FROM files WHERE workspace_id=? ORDER BY created_at DESC",
        (_workspace_id(),),
    ).fetchall()
    return [_file_out(dict(r), include_parse=True) for r in rows]


@router.get("/{file_id}")
def get_file(file_id: str):
    return _file_out(_get_file(file_id), include_parse=True)


@router.get("/{file_id}/content")
def get_file_content(file_id: str):
    """Serve the stored PDF for inline viewing in a new browser tab."""
    row = _get_file(file_id)
    path = config.from_rel(row["path"])
    if path.is_file():
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=row["name"],
            content_disposition_type="inline",
        )
    object_key = str(row.get("object_key") or "").strip()
    if object_key:
        try:
            content = get_object_store().get_bytes(object_key)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(404, "stored file missing") from exc
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{row["name"]}"'},
        )
    raise HTTPException(404, "stored file missing")


@router.patch("/{file_id}")
def update_file(file_id: str, body: FileUpdate):
    row = _get_file(file_id)
    updates: list[str] = []
    params: list[Any] = []
    if body.name is not None:
        safe_name = Path(body.name.strip()).name
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(422, "invalid file name")
        if Path(safe_name).suffix.lower() != ".pdf":
            raise HTTPException(422, "file name must end with .pdf")
        updates.append("name=?")
        params.append(safe_name)
    if body.metadata is not None:
        metadata = {k: v for k, v in body.metadata.items() if v not in ("", None, [], {})}
        updates.append("metadata=?")
        params.append(json.dumps(metadata, ensure_ascii=False))
    if updates:
        params.extend([file_id, _workspace_id()])
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE files SET {', '.join(updates)} WHERE id=? AND workspace_id=?",
                params,
            )
    return _file_out(_get_file(file_id), include_parse=True)


@router.get("/{file_id}/parses")
def list_file_parses(file_id: str):
    _get_file(file_id)
    repository = get_content_repository()
    if repository is not None:
        return repository.list_file_parses(file_id)
    rows = db.get_conn().execute(
        """SELECT * FROM document_parses
           WHERE file_id=? AND workspace_id=?
           ORDER BY created_at DESC""",
        (file_id, _workspace_id()),
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


class ParseEnqueueBody(BaseModel):
    delete_chunks: bool = False
    force: bool = True


@router.post("/{file_id}/parse")
def enqueue_file_parse(file_id: str, body: ParseEnqueueBody = ParseEnqueueBody()):
    """Enqueue MinerU parse. Optionally wipe existing chunks first (re-parse)."""
    _get_file(file_id)
    try:
        return jobs.enqueue_parse_file(
            file_id,
            force=body.force,
            delete_chunks=body.delete_chunks,
        )
    except KeyError:
        raise HTTPException(404, "file not found")


class AutoChunkBody(BaseModel):
    chunk_config: dict[str, Any] | None = None
    skip_existing: bool = False
    persist_override: bool = False


@router.post("/{file_id}/auto-chunk")
async def auto_chunk_file(file_id: str, body: AutoChunkBody | None = None):
    """Generate chunks from the latest completed parse using KB/file rules."""
    from .. import chunk_pipeline

    _get_file(file_id)
    body = body or AutoChunkBody()
    parse = db.get_conn().execute(
        """SELECT id FROM document_parses
           WHERE file_id=? AND workspace_id=? AND status='done'
             AND ((raw_zip_path IS NOT NULL AND TRIM(raw_zip_path) != '')
                  OR (raw_zip_object_key IS NOT NULL AND TRIM(raw_zip_object_key) != ''))
           ORDER BY created_at DESC LIMIT 1""",
        (file_id, _workspace_id()),
    ).fetchone()
    if not parse:
        raise HTTPException(400, "file has no completed parse with layout zip")

    if body.persist_override and body.chunk_config is not None:
        row = db.get_conn().execute(
            "SELECT metadata FROM files WHERE id=? AND workspace_id=?",
            (file_id, _workspace_id()),
        ).fetchone()
        try:
            metadata = json.loads((row["metadata"] if row else None) or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["chunk_config"] = body.chunk_config
        with db.transaction() as conn:
            conn.execute(
                "UPDATE files SET metadata=? WHERE id=? AND workspace_id=?",
                (json.dumps(metadata, ensure_ascii=False), file_id, _workspace_id()),
            )

    config = (
        chunk_pipeline.normalize_parser_config(body.chunk_config)
        if body.chunk_config is not None
        else chunk_pipeline.resolve_file_chunk_config(file_id)
    )
    try:
        summary = await chunk_pipeline.run_auto_chunk_pipeline(
            file_id,
            parse["id"],
            chunk_config=config,
            skip_existing=body.skip_existing,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"auto-chunk failed: {exc}") from exc
    return summary


@router.get("/{file_id}/pages/{page_no}")
async def page_image(file_id: str, page_no: int, dpi: int = 150):
    f = _get_file(file_id)
    if page_no < 1 or page_no > f["page_count"]:
        raise HTTPException(404, "page out of range")
    # Clamp: audit preview may request high DPI; keep an upper bound for cache size.
    render_dpi = max(96, min(int(dpi or 150), 400))
    png = await asyncio.to_thread(pdf.render_page_png, f["path"], page_no - 1, render_dpi)
    return Response(content=png, media_type="image/png")


@router.get("/{file_id}/locate")
def locate_text(
    file_id: str,
    q: str = "",
    project: str = "",
    requirement: str = "",
):
    """Locate a requirement string on the file's MinerU layout (1-based page)."""
    _get_file(file_id)
    from .. import report_locate

    query = (q or "").strip()
    project_text = (project or "").strip()
    requirement_text = (requirement or "").strip()
    if not (query or project_text or requirement_text):
        raise HTTPException(400, "q or project/requirement is required")
    try:
        return report_locate.locate_text_page(
            file_id,
            query,
            project=project_text,
            requirement=requirement_text,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{file_id}")
def delete_file(file_id: str):
    f = _get_file(file_id)
    with db.transaction() as conn:
        conn.execute(
            """UPDATE knowledge_bases
               SET default_naming_file_id=NULL, updated_at=?
               WHERE default_naming_file_id=? AND workspace_id=?""",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), file_id, _workspace_id()),
        )
        conn.execute(
            "DELETE FROM chunks WHERE file_id=? AND workspace_id=?",
            (file_id, _workspace_id()),
        )
        conn.execute(
            "DELETE FROM files WHERE id=? AND workspace_id=?",
            (file_id, _workspace_id()),
        )
    try:
        config.from_rel(f["path"]).unlink(missing_ok=True)
    except Exception:
        pass
    if f.get("object_key"):
        try:
            get_object_store().delete(f["object_key"])
        except Exception:
            logger.exception("failed to delete object %s", f["object_key"])
    return {"ok": True}


def _get_file(file_id: str):
    repository = get_content_repository()
    if repository is not None:
        row = repository.get_file(file_id)
        if not row:
            raise HTTPException(404, "file not found")
        return row
    row = db.get_conn().execute(
        "SELECT * FROM files WHERE id=? AND workspace_id=?",
        (file_id, _workspace_id()),
    ).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    return dict(row)


def _latest_parse(file_id: str) -> dict[str, Any] | None:
    repository = get_content_repository()
    if repository is not None:
        row = repository.latest_parse(file_id)
        return row
    row = db.get_conn().execute(
        """SELECT status, error, markdown_path, result FROM document_parses
           WHERE file_id=? AND workspace_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (file_id, _workspace_id()),
    ).fetchone()
    return dict(row) if row else None


def _auto_chunk_error(parse_result: Any) -> str:
    if isinstance(parse_result, dict):
        result = parse_result
    else:
        try:
            result = json.loads(parse_result or "{}")
        except Exception:
            return ""
    return str(result.get("auto_chunk_error") or "") if isinstance(result, dict) else ""


def _file_out(row: dict, *, include_parse: bool = False):
    raw_metadata = row.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata = raw_metadata
    else:
        try:
            metadata = json.loads(raw_metadata or "{}")
        except Exception:
            metadata = {}
    out: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "page_count": row["page_count"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "object_key": row.get("object_key") or None,
    }
    if include_parse:
        parse = _latest_parse(row["id"])
        out["parse_status"] = parse["status"] if parse else None
        out["parse_error"] = (parse.get("error") or "") if parse else ""
        out["parse_ready"] = bool(
            parse
            and parse.get("status") == "done"
            and (parse.get("markdown_path") or parse.get("markdown_object_key"))
        )
        # Parse jobs succeed even when post-parse auto-chunking fails; surface
        # that error so the UI can prompt a manual re-chunk.
        out["auto_chunk_error"] = _auto_chunk_error(parse.get("result")) if parse else ""
    return out
