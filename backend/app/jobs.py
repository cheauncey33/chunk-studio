"""Persistent background jobs for OCR and future async tasks."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from . import chunk_schema, config, db, extractors
from .adapters import ocr as ocr_adapter

JOB_POLL_SECONDS = 1.0


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def enqueue_ocr_chunk(chunk_id: str, *, priority: int = 0, force: bool = False) -> dict[str, Any]:
    """Create or return a queued/running OCR job for a chunk."""
    row = db.get_conn().execute("SELECT id FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if not row:
        raise KeyError("chunk not found")

    if not force:
        existing = db.get_conn().execute(
            """SELECT * FROM jobs
               WHERE type='ocr' AND target_type='chunk' AND target_id=?
                 AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 1""",
            (chunk_id,),
        ).fetchone()
        if existing:
            return dict(existing)

    jid = uuid.uuid4().hex
    created = now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at)
               VALUES (?, 'ocr', 'chunk', ?, 'queued', ?, 0, 2, '', '{}', ?)""",
            (jid, chunk_id, priority, created),
        )
    return get_job(jid)


def enqueue_ocr_for_file(
    file_id: str,
    *,
    page: int | None = None,
    pending_only: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["file_id=?"]
    args: list[Any] = [file_id]
    if page is not None:
        clauses.append("page=?")
        args.append(page)
    if pending_only:
        clauses.append("text_source='pending'")
    rows = db.get_conn().execute(
        f"SELECT id FROM chunks WHERE {' AND '.join(clauses)} ORDER BY page, created_at",
        args,
    ).fetchall()
    return [enqueue_ocr_chunk(r["id"]) for r in rows]


def enqueue_parse_file(file_id: str, *, priority: int = -10, force: bool = False) -> dict[str, Any]:
    """Create or return a MinerU full-document parse job for a PDF file."""
    row = db.get_conn().execute("SELECT id FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise KeyError("file not found")

    if not force:
        existing = db.get_conn().execute(
            """SELECT * FROM jobs
               WHERE type='parse' AND target_type='file' AND target_id=?
                 AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        if existing:
            return dict(existing)

    jid = uuid.uuid4().hex
    parse_id = uuid.uuid4().hex
    created = now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO document_parses
               (id, file_id, provider, status, result, error, created_at, updated_at)
               VALUES (?, ?, 'mineru', 'queued', '{}', '', ?, ?)""",
            (parse_id, file_id, created, created),
        )
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at)
               VALUES (?, 'parse', 'file', ?, 'queued', ?, 0, 1, '', ?, ?)""",
            (
                jid,
                file_id,
                priority,
                json.dumps({"parse_id": parse_id}, ensure_ascii=False),
                created,
            ),
        )
    return get_job(jid)


def get_job(job_id: str) -> dict[str, Any]:
    row = db.get_conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError("job not found")
    return _row_to_job(row)


def list_jobs(
    *,
    target_id: str | None = None,
    status: str | None = None,
    type_: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = []
    args: list[Any] = []
    if target_id:
        clauses.append("target_id=?")
        args.append(target_id)
    if status:
        clauses.append("status=?")
        args.append(status)
    if type_:
        clauses.append("type=?")
        args.append(type_)
    sql = "SELECT * FROM jobs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [_row_to_job(r) for r in db.get_conn().execute(sql, args).fetchall()]


def latest_job_for_target(target_id: str, type_: str = "ocr") -> dict[str, Any] | None:
    row = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE target_type='chunk' AND target_id=? AND type=?
           ORDER BY created_at DESC LIMIT 1""",
        (target_id, type_),
    ).fetchone()
    return _row_to_job(row) if row else None


async def worker_loop() -> None:
    while True:
        job = _claim_next_job()
        if not job:
            await asyncio.sleep(JOB_POLL_SECONDS)
            continue
        if job["type"] == "ocr" and job["target_type"] == "chunk":
            await _run_ocr_job(job)
        elif job["type"] == "parse" and job["target_type"] == "file":
            await _run_parse_job(job)
        else:
            _fail_job(job, f"unknown job type {job['type']}")


def _claim_next_job() -> dict[str, Any] | None:
    with db.transaction() as conn:
        row = conn.execute(
            """SELECT * FROM jobs
               WHERE status='queued'
               ORDER BY priority DESC, created_at
               LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        started = now_iso()
        conn.execute(
            """UPDATE jobs
               SET status='running', attempts=attempts+1, started_at=?, error=''
               WHERE id=?""",
            (started, row["id"]),
        )
    return get_job(row["id"])


async def _run_parse_job(job: dict[str, Any]) -> None:
    file_id = job["target_id"]
    parse_id = (job.get("result") or {}).get("parse_id") or uuid.uuid4().hex
    started = now_iso()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE document_parses SET status='running', error='', updated_at=? WHERE id=?",
            (started, parse_id),
        )

    result = await ocr_adapter.parse_file(file_id)
    finished = now_iso()
    if not result.ok:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE document_parses SET status='failed', error=?, updated_at=? WHERE id=?",
                (result.error or "parse failed", finished, parse_id),
            )
        _fail_job(job, result.error)
        return

    markdown_rel, zip_rel = _write_parse_outputs(file_id, parse_id, result.text, result.zip_bytes)
    parse_result = {
        "markdown_length": len(result.text),
        "has_zip": bool(result.zip_bytes),
    }
    with db.transaction() as conn:
        conn.execute(
            """UPDATE document_parses
               SET status='done', markdown_path=?, raw_zip_path=?, result=?, error='', updated_at=?
               WHERE id=?""",
            (
                markdown_rel,
                zip_rel,
                json.dumps(parse_result, ensure_ascii=False),
                finished,
                parse_id,
            ),
        )
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (
                json.dumps({"parse_id": parse_id, **parse_result}, ensure_ascii=False),
                finished,
                job["id"],
            ),
        )


def _write_parse_outputs(
    file_id: str, parse_id: str, markdown: str, zip_bytes: bytes | None
) -> tuple[str, str | None]:
    config.PARSES_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{file_id}_{parse_id}"
    md_path = config.PARSES_DIR / f"{stem}.md"
    md_path.write_text(markdown, encoding="utf-8")
    zip_rel = None
    if zip_bytes:
        zip_path = config.PARSES_DIR / f"{stem}.zip"
        zip_path.write_bytes(zip_bytes)
        zip_rel = config.to_rel(zip_path)
    return config.to_rel(md_path), zip_rel


async def ocr_chunk_sync(chunk_id: str) -> tuple[bool, str]:
    """Run MinerU OCR synchronously and update the chunk. Returns (ok, error)."""
    row = db.get_conn().execute("SELECT id FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if not row:
        raise KeyError("chunk not found")

    result = await ocr_adapter.ocr_chunk(chunk_id)
    if not result.ok:
        finished = now_iso()
        jid = uuid.uuid4().hex
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, type, target_type, target_id, status, priority, attempts,
                    max_attempts, error, result, created_at, started_at, finished_at)
                   VALUES (?, 'ocr', 'chunk', ?, 'failed', 0, 1, 1, ?, '{}', ?, ?, ?)""",
                (jid, chunk_id, result.error or "ocr failed", finished, finished, finished),
            )
        return False, result.error or "ocr failed"

    finished = now_iso()
    jid = uuid.uuid4().hex
    with db.transaction() as conn:
        conn.execute(
            "UPDATE chunks SET text=?, text_source='ocr', status='pending', updated_at=? WHERE id=?",
            (result.text, finished, chunk_id),
        )
        row = conn.execute(
            "SELECT text, metadata, metadata_v2, file_id FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        fname_row = conn.execute(
            "SELECT name FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()
        fname = fname_row["name"] if fname_row else ""
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row["metadata"],
            metadata_v2=row["metadata_v2"],
        )
        meta = layers["metadata_v2"]
        extractors.merge_auto_metadata(meta, row["text"], fname)
        conn.execute(
            "UPDATE chunks SET metadata_v2=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), chunk_id),
        )
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at, started_at, finished_at)
               VALUES (?, 'ocr', 'chunk', ?, 'done', 0, 1, 1, '', ?, ?, ?, ?)""",
            (
                jid,
                chunk_id,
                json.dumps({"text_length": len(result.text), "sync": True}, ensure_ascii=False),
                finished,
                finished,
                finished,
            ),
        )
    return True, ""


async def _run_ocr_job(job: dict[str, Any]) -> None:
    chunk_id = job["target_id"]
    result = await ocr_adapter.ocr_chunk(chunk_id)
    if not result.ok:
        if job["attempts"] < job["max_attempts"]:
            _requeue_job(job, result.error)
        else:
            _fail_job(job, result.error)
        return

    finished = now_iso()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE chunks SET text=?, text_source='ocr', status='pending', updated_at=? WHERE id=?",
            (result.text, finished, chunk_id),
        )
        # OCR produced new text (often a <table> for spec pages); backfill the
        # auto fields that depend on it, without overwriting any user edits.
        row = conn.execute(
            "SELECT text, metadata, metadata_v2, file_id FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        fname_row = conn.execute(
            "SELECT name FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()
        fname = fname_row["name"] if fname_row else ""
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row["metadata"],
            metadata_v2=row["metadata_v2"],
        )
        meta = layers["metadata_v2"]
        extractors.merge_auto_metadata(meta, row["text"], fname)
        conn.execute(
            "UPDATE chunks SET metadata_v2=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), chunk_id),
        )
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (json.dumps({"text_length": len(result.text)}, ensure_ascii=False), finished, job["id"]),
        )


def _requeue_job(job: dict[str, Any], error: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status='queued', error=? WHERE id=?",
            (error or "retrying", job["id"]),
        )


def _fail_job(job: dict[str, Any], error: str) -> None:
    finished = now_iso()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error or "job failed", finished, job["id"]),
        )


def _row_to_job(row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["result"] = json.loads(d.get("result") or "{}")
    except Exception:
        d["result"] = {}
    return d
