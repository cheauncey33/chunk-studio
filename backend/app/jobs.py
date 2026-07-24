"""Persistent background jobs for OCR and future async tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from . import chunk_schema, config, db, extractors
from .adapters import ocr as ocr_adapter

logger = logging.getLogger(__name__)
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


def enqueue_parse_file(
    file_id: str,
    *,
    priority: int = -10,
    force: bool = False,
    delete_chunks: bool = False,
) -> dict[str, Any]:
    """Create or return a MinerU full-document parse job for a PDF file.

    When delete_chunks=True, remove existing chunks for this file first so the
    post-parse auto-chunk pipeline can rebuild from a clean slate (RAGFlow-like).
    """
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

    deleted = 0
    if delete_chunks:
        deleted = _delete_file_chunks(file_id)

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
                json.dumps(
                    {
                        "parse_id": parse_id,
                        "delete_chunks": bool(delete_chunks),
                        "deleted_chunk_count": deleted,
                    },
                    ensure_ascii=False,
                ),
                created,
            ),
        )
    return get_job(jid)


def _delete_file_chunks(file_id: str) -> int:
    """Delete all chunks for a file and best-effort remove crop images."""
    rows = db.get_conn().execute(
        "SELECT id, crop_path FROM chunks WHERE file_id=?",
        (file_id,),
    ).fetchall()
    with db.transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    for row in rows:
        crop = (row["crop_path"] or "").strip()
        if not crop:
            continue
        try:
            config.from_rel(crop).unlink(missing_ok=True)
        except OSError:
            pass
    return len(rows)


def enqueue_assistant_audit(
    assistant_id: str,
    *,
    report_file_id: str,
    naming_rule_file_id: str | None = None,
    report_id: str | None = None,
    priority: int = 5,
) -> dict[str, Any]:
    """Queue an end-to-end assistant audit run.

    ``report_id`` is an evaluation-only option: when set, the run audits the
    frozen case pool for that report instead of the full extracted report.

    The report is an audit input (not KB corpus). Naming PDF is a KB attribute
    and also need not appear in knowledge_base_files. Evidence still comes from
    enabled files in the assistant's bound knowledge bases.
    """
    row = db.get_conn().execute(
        "SELECT id, active_version_id FROM audit_assistants WHERE id=?",
        (assistant_id,),
    ).fetchone()
    if not row:
        raise KeyError("assistant not found")
    if not row["active_version_id"]:
        raise ValueError("assistant has no active version")

    report_row = db.get_conn().execute(
        "SELECT id FROM files WHERE id=?",
        (report_file_id,),
    ).fetchone()
    if not report_row:
        raise ValueError("report file not found")

    scoped_file_ids = set(db.assistant_scoped_file_ids(assistant_id))
    if not scoped_file_ids:
        raise ValueError("assistant has no enabled files in its knowledge bases")

    from . import audit_run

    resolved_naming_id = audit_run.resolve_naming_rule_file_id(
        assistant_id, naming_rule_file_id
    )
    if resolved_naming_id:
        naming_row = db.get_conn().execute(
            "SELECT id FROM files WHERE id=?",
            (resolved_naming_id,),
        ).fetchone()
        if not naming_row:
            raise ValueError("naming-rule file not found")

    excluded = {report_file_id}
    if resolved_naming_id:
        excluded.add(resolved_naming_id)
    # Fail fast: after excluding runtime inputs, corpus evidence must remain.
    db.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)

    existing = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE type='audit' AND target_type='assistant' AND target_id=?
             AND status IN ('queued','running')
           ORDER BY created_at DESC LIMIT 1""",
        (assistant_id,),
    ).fetchone()
    if existing:
        return get_job(existing["id"])

    # Validate inputs early so the API can fail fast.
    audit_run.resolve_markdown_path(report_file_id)
    audit_run.resolve_naming_rule_path(resolved_naming_id, assistant_id=assistant_id)

    jid = uuid.uuid4().hex
    created = now_iso()
    payload = {
        "assistant_id": assistant_id,
        "report_file_id": report_file_id,
        "naming_rule_file_id": resolved_naming_id,
    }
    if report_id:
        payload["report_id"] = report_id
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at)
               VALUES (?, 'audit', 'assistant', ?, 'queued', ?, 0, 1, '', ?, ?)""",
            (jid, assistant_id, priority, json.dumps(payload, ensure_ascii=False), created),
        )
    return get_job(jid)


def enqueue_build_embeddings(*, priority: int = 3) -> dict[str, Any]:
    """Queue an incremental dense-embedding build for approved chunks.

    The build itself is idempotent (skips chunks whose text hash already has a
    stored vector), so a single queued/running job is reused for any number of
    approvals that happen in the meantime.
    """
    existing = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE type='embed' AND target_type='corpus' AND target_id='approved_chunks'
             AND status IN ('queued','running')
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if existing:
        return get_job(existing["id"])

    jid = uuid.uuid4().hex
    created = now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at)
               VALUES (?, 'embed', 'corpus', 'approved_chunks', 'queued', ?, 0, 1, '', '{}', ?)""",
            (jid, priority, created),
        )
    return get_job(jid)


def enqueue_assistant_init(
    assistant_id: str,
    *,
    sample_report_file_ids: list[str] | None = None,
    model: str | None = None,
    priority: int = 4,
) -> dict[str, Any]:
    """Queue category-init draft generation for an assistant."""
    from . import assistant_init

    row = db.get_conn().execute(
        "SELECT id, active_version_id FROM audit_assistants WHERE id=?",
        (assistant_id,),
    ).fetchone()
    if not row:
        raise KeyError("assistant not found")
    if assistant_id == "assistant_audit_template":
        raise ValueError("不能对通用审查模板运行品类初始化")
    if not row["active_version_id"]:
        raise ValueError("assistant has no active version")
    if not db.knowledge_base_id_for_assistant(assistant_id):
        raise ValueError("assistant is not bound to a knowledge base")

    samples = list(dict.fromkeys(sample_report_file_ids or []))[:3]
    for file_id in samples:
        file_row = db.get_conn().execute(
            "SELECT id FROM files WHERE id=?",
            (file_id,),
        ).fetchone()
        if not file_row:
            raise ValueError(f"sample report file not found: {file_id}")

    standard_ids = assistant_init.list_standard_corpus_file_ids(assistant_id)
    if not standard_ids and not samples:
        raise ValueError("需要至少一个已解析的标准语料或样例报告")

    existing = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE type='assistant_init' AND target_type='assistant' AND target_id=?
             AND status IN ('queued','running')
           ORDER BY created_at DESC LIMIT 1""",
        (assistant_id,),
    ).fetchone()
    if existing:
        return get_job(existing["id"])

    jid = uuid.uuid4().hex
    created = now_iso()
    payload = {
        "assistant_id": assistant_id,
        "sample_report_file_ids": samples,
        "model": model,
    }
    assistant_init.upsert_init_draft(
        assistant_id,
        status="generating",
        payload={
            "category_profile": {},
            "parameter_schema": {},
            "report_parameters_prompt": "",
            "source_file_ids": {
                "standard": standard_ids,
                "sample_reports": samples,
            },
            "model": model or "",
            "error": "",
        },
        job_id=jid,
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at)
               VALUES (?, 'assistant_init', 'assistant', ?, 'queued', ?, 0, 1, '', ?, ?)""",
            (jid, assistant_id, priority, json.dumps(payload, ensure_ascii=False), created),
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
        elif job["type"] == "audit" and job["target_type"] == "assistant":
            await _run_audit_job(job)
        elif job["type"] == "assistant_init" and job["target_type"] == "assistant":
            await _run_assistant_init_job(job)
        elif job["type"] == "embed" and job["target_type"] == "corpus":
            await _run_embed_job(job)
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

    chunk_summary: dict[str, Any] = {}
    try:
        from . import chunk_pipeline

        chunk_config = chunk_pipeline.resolve_file_chunk_config(file_id)
        if chunk_config.get("auto_chunk_after_parse", True):
            # Fresh rebuild after delete_chunks; otherwise skip near-duplicates.
            job_payload = job.get("result") or {}
            skip_existing = not bool(job_payload.get("delete_chunks"))
            chunk_summary = await chunk_pipeline.run_auto_chunk_pipeline(
                file_id,
                parse_id,
                chunk_config=chunk_config,
                skip_existing=skip_existing,
            )
            parse_result["auto_chunk"] = {
                "total": chunk_summary.get("total", 0),
                "sections": chunk_summary.get("sections", 0),
                "tables": chunk_summary.get("tables", 0),
                "images": chunk_summary.get("images", 0),
            }
    except Exception as exc:
        logger.exception("auto-chunk after parse failed for file %s", file_id)
        parse_result["auto_chunk_error"] = str(exc)

    with db.transaction() as conn:
        conn.execute(
            """UPDATE document_parses
               SET result=?, updated_at=?
               WHERE id=?""",
            (json.dumps(parse_result, ensure_ascii=False), now_iso(), parse_id),
        )
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (
                json.dumps({"parse_id": parse_id, **parse_result}, ensure_ascii=False),
                now_iso(),
                job["id"],
            ),
        )


async def _run_audit_job(job: dict[str, Any]) -> None:
    from . import audit_run

    payload = job.get("result") or {}
    assistant_id = str(payload.get("assistant_id") or job["target_id"])
    report_file_id = str(payload.get("report_file_id") or "")
    naming_rule_file_id = payload.get("naming_rule_file_id") or None
    report_id = str(payload.get("report_id") or "") or None
    if not report_file_id:
        _fail_job(job, "audit job missing report_file_id")
        return

    try:
        outcome = await asyncio.to_thread(
            audit_run.run_assistant_audit,
            assistant_id=assistant_id,
            report_file_id=report_file_id,
            naming_rule_file_id=naming_rule_file_id,
            report_id=report_id,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        _fail_job(job, str(exc))
        return

    finished = now_iso()
    result = {
        **payload,
        **outcome,
    }
    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (json.dumps(result, ensure_ascii=False), finished, job["id"]),
        )


async def _run_embed_job(job: dict[str, Any]) -> None:
    from . import embeddings

    try:
        summary = await asyncio.to_thread(embeddings.build_embeddings)
    except Exception as exc:
        logger.exception("embedding build failed")
        _fail_job(job, str(exc))
        return

    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (json.dumps(summary, ensure_ascii=False), now_iso(), job["id"]),
        )


async def _run_assistant_init_job(job: dict[str, Any]) -> None:
    from . import assistant_init

    payload = job.get("result") or {}
    assistant_id = str(payload.get("assistant_id") or job["target_id"])
    samples = payload.get("sample_report_file_ids") or []
    if not isinstance(samples, list):
        samples = []
    model = payload.get("model") or None
    try:
        result_payload = await asyncio.to_thread(
            assistant_init.generate_init_draft,
            assistant_id,
            sample_report_file_ids=[str(item) for item in samples],
            model=str(model) if model else None,
        )
    except Exception as exc:
        logger.exception("assistant init failed for %s", assistant_id)
        assistant_init.upsert_init_draft(
            assistant_id,
            status="failed",
            payload={
                "category_profile": {},
                "parameter_schema": {},
                "report_parameters_prompt": "",
                "source_file_ids": {
                    "standard": assistant_init.list_standard_corpus_file_ids(assistant_id),
                    "sample_reports": samples,
                },
                "model": model or "",
                "error": str(exc),
            },
            job_id=job["id"],
        )
        _fail_job(job, str(exc))
        return

    assistant_init.upsert_init_draft(
        assistant_id,
        status="ready",
        payload=result_payload,
        job_id=job["id"],
    )
    finished = now_iso()
    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?
               WHERE id=?""",
            (
                json.dumps({**payload, "draft_status": "ready"}, ensure_ascii=False),
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
            "SELECT text, metadata, business_metadata, file_id FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        fname_row = conn.execute(
            "SELECT name FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()
        fname = fname_row["name"] if fname_row else ""
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row["metadata"],
            business_metadata=row["business_metadata"],
        )
        meta = layers["business_metadata"]
        extractors.merge_auto_metadata(meta, row["text"], fname)
        conn.execute(
            "UPDATE chunks SET business_metadata=? WHERE id=?",
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
            "SELECT text, metadata, business_metadata, file_id FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        fname_row = conn.execute(
            "SELECT name FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()
        fname = fname_row["name"] if fname_row else ""
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row["metadata"],
            business_metadata=row["business_metadata"],
        )
        meta = layers["business_metadata"]
        extractors.merge_auto_metadata(meta, row["text"], fname)
        conn.execute(
            "UPDATE chunks SET business_metadata=? WHERE id=?",
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
