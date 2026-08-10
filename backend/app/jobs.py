"""Persistent background jobs for OCR and future async tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from . import chunk_schema, config, current_user, db, extractors
from .adapters import ocr as ocr_adapter
from .storage.repositories import get_job_repository

logger = logging.getLogger(__name__)
JOB_POLL_SECONDS = 1.0
JOB_TIMEOUT_SECONDS = 15 * 60


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def workspace_id() -> str:
    return current_user.get_current_user().workspace_id


def _job_repository():
    """Return the opt-in PostgreSQL queue adapter, or the SQLite path."""
    return get_job_repository()


def _create_job_record(
    *,
    job_id: str,
    workspace: str,
    type_: str,
    target_type: str,
    target_id: str,
    priority: int,
    max_attempts: int,
    result: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    repository = _job_repository()
    if repository is not None:
        return repository.create_job(
            job_id=job_id,
            workspace_id=workspace,
            type_=type_,
            target_type=target_type,
            target_id=target_id,
            priority=priority,
            max_attempts=max_attempts,
            result=result,
        )
    timestamp = created_at or now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, workspace_id, type, target_type, target_id, status, priority,
                attempts, max_attempts, error, result, created_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?, '', ?, ?)""",
            (
                job_id,
                workspace,
                type_,
                target_type,
                target_id,
                priority,
                max_attempts,
                json.dumps(result or {}, ensure_ascii=False),
                timestamp,
            ),
        )
    return get_job(job_id, workspace_id_value=workspace)


def _find_active_job(
    *,
    workspace: str,
    type_: str,
    target_type: str,
    target_id: str,
) -> dict[str, Any] | None:
    repository = _job_repository()
    if repository is not None:
        rows = repository.list_jobs(
            workspace_id=workspace,
            target_id=target_id,
            type_=type_,
            limit=100,
        )
        return next(
            (
                item
                for item in rows
                if item.get("target_type") == target_type
                and item.get("status") in {"queued", "running"}
            ),
            None,
        )
    row = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE workspace_id=? AND type=? AND target_type=? AND target_id=?
             AND status IN ('queued','running')
           ORDER BY created_at DESC LIMIT 1""",
        (workspace, type_, target_type, target_id),
    ).fetchone()
    return _row_to_job(row) if row else None


def enqueue_ocr_chunk(chunk_id: str, *, priority: int = 0, force: bool = False) -> dict[str, Any]:
    """Create or return a queued/running OCR job for a chunk."""
    row = db.get_conn().execute(
        "SELECT id, workspace_id FROM chunks WHERE id=? AND workspace_id=?",
        (chunk_id, workspace_id()),
    ).fetchone()
    if not row:
        raise KeyError("chunk not found")

    if not force:
        existing = _find_active_job(
            workspace=workspace_id(),
            type_="ocr",
            target_type="chunk",
            target_id=chunk_id,
        )
        if existing:
            return existing

    jid = uuid.uuid4().hex
    created = now_iso()
    return _create_job_record(
        job_id=jid,
        workspace=row["workspace_id"],
        type_="ocr",
        target_type="chunk",
        target_id=chunk_id,
        priority=priority,
        max_attempts=2,
        created_at=created,
    )


def enqueue_ocr_for_file(
    file_id: str,
    *,
    page: int | None = None,
    pending_only: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["file_id=?", "workspace_id=?"]
    args: list[Any] = [file_id, workspace_id()]
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
    row = db.get_conn().execute(
        "SELECT id, workspace_id FROM files WHERE id=? AND workspace_id=?",
        (file_id, workspace_id()),
    ).fetchone()
    if not row:
        raise KeyError("file not found")

    if not force:
        existing = _find_active_job(
            workspace=workspace_id(),
            type_="parse",
            target_type="file",
            target_id=file_id,
        )
        if existing:
            return existing

    deleted = 0
    if delete_chunks:
        deleted = _delete_file_chunks(file_id)

    jid = uuid.uuid4().hex
    parse_id = uuid.uuid4().hex
    created = now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO document_parses
               (id, workspace_id, file_id, provider, status, result, error, created_at, updated_at)
               VALUES (?, ?, ?, 'mineru', 'queued', '{}', '', ?, ?)""",
            (parse_id, row["workspace_id"], file_id, created, created),
        )
    return _create_job_record(
        job_id=jid,
        workspace=row["workspace_id"],
        type_="parse",
        target_type="file",
        target_id=file_id,
        priority=priority,
        max_attempts=1,
        result={
            "parse_id": parse_id,
            "delete_chunks": bool(delete_chunks),
            "deleted_chunk_count": deleted,
        },
        created_at=created,
    )


def enqueue_chunk_file(
    file_id: str,
    parse_id: str,
    *,
    priority: int = 0,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Queue chunking separately from the document parse job."""
    row = db.get_conn().execute(
        "SELECT id, workspace_id FROM files WHERE id=? AND workspace_id=?",
        (file_id, workspace_id()),
    ).fetchone()
    if not row:
        raise KeyError("file not found")
    existing = _find_active_job(
        workspace=row["workspace_id"],
        type_="chunk",
        target_type="file",
        target_id=file_id,
    )
    if existing:
        return existing
    return _create_job_record(
        job_id=uuid.uuid4().hex,
        workspace=row["workspace_id"],
        type_="chunk",
        target_type="file",
        target_id=file_id,
        priority=priority,
        max_attempts=2,
        result={
            "parse_id": parse_id,
            "skip_existing": bool(skip_existing),
        },
    )


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
    priority: int = 5,
) -> dict[str, Any]:
    """Queue an end-to-end assistant audit run.

    The report is an audit input (not KB corpus). Naming PDF is a KB attribute
    and also need not appear in knowledge_base_files. Evidence still comes from
    enabled files in the assistant's bound knowledge bases.
    """
    row = db.get_conn().execute(
        """SELECT id, workspace_id, active_version_id
           FROM audit_assistants WHERE id=? AND workspace_id=?""",
        (assistant_id, workspace_id()),
    ).fetchone()
    if not row:
        raise KeyError("assistant not found")
    if not row["active_version_id"]:
        raise ValueError("assistant has no active version")

    report_row = db.get_conn().execute(
        "SELECT id FROM files WHERE id=? AND workspace_id=?",
        (report_file_id, row["workspace_id"]),
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
            "SELECT id FROM files WHERE id=? AND workspace_id=?",
            (resolved_naming_id, row["workspace_id"]),
        ).fetchone()
        if not naming_row:
            raise ValueError("naming-rule file not found")

    excluded = {report_file_id}
    if resolved_naming_id:
        excluded.add(resolved_naming_id)
    # Fail fast: after excluding runtime inputs, corpus evidence must remain.
    db.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)

    # Reuse only an in-flight job for the same assistant + report (resume after
    # client disconnect). Different reports must not share one job row.
    repository = _job_repository()
    if repository is not None:
        existing_rows = repository.list_jobs(
            workspace_id=row["workspace_id"],
            target_id=assistant_id,
            type_="audit",
            limit=100,
        )
    else:
        existing_rows = [
            _row_to_job(item)
            for item in db.get_conn().execute(
                """SELECT * FROM jobs
                   WHERE workspace_id=? AND type='audit' AND target_type='assistant' AND target_id=?
                     AND status IN ('queued','running')
                   ORDER BY created_at DESC""",
                (row["workspace_id"], assistant_id),
            ).fetchall()
        ]
    for existing_job in existing_rows:
        if existing_job.get("target_type") != "assistant":
            continue
        existing_report = str((existing_job.get("result") or {}).get("report_file_id") or "")
        if existing_report == report_file_id:
            return existing_job

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
    return _create_job_record(
        job_id=jid,
        workspace=row["workspace_id"],
        type_="audit",
        target_type="assistant",
        target_id=assistant_id,
        priority=priority,
        max_attempts=1,
        result=payload,
        created_at=created,
    )


def enqueue_build_embeddings(*, priority: int = 3) -> dict[str, Any]:
    """Queue an incremental dense-embedding build for approved chunks.

    The build itself is idempotent (skips chunks whose text hash already has a
    stored vector), so a single queued/running job is reused for any number of
    approvals that happen in the meantime.
    """
    existing = _find_active_job(
        workspace=workspace_id(),
        type_="embed",
        target_type="corpus",
        target_id="approved_chunks",
    )
    if existing:
        return existing

    jid = uuid.uuid4().hex
    created = now_iso()
    return _create_job_record(
        job_id=jid,
        workspace=workspace_id(),
        type_="embed",
        target_type="corpus",
        target_id="approved_chunks",
        priority=priority,
        max_attempts=1,
        created_at=created,
    )


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
        """SELECT id, workspace_id, active_version_id
           FROM audit_assistants WHERE id=? AND workspace_id=?""",
        (assistant_id, workspace_id()),
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
            "SELECT id FROM files WHERE id=? AND workspace_id=?",
            (file_id, row["workspace_id"]),
        ).fetchone()
        if not file_row:
            raise ValueError(f"sample report file not found: {file_id}")

    standard_ids = assistant_init.list_standard_corpus_file_ids(assistant_id)
    if not standard_ids and not samples:
        raise ValueError("需要至少一个已解析的标准语料或样例报告")

    existing = _find_active_job(
        workspace=row["workspace_id"],
        type_="assistant_init",
        target_type="assistant",
        target_id=assistant_id,
    )
    if existing:
        return existing

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
    return _create_job_record(
        job_id=jid,
        workspace=row["workspace_id"],
        type_="assistant_init",
        target_type="assistant",
        target_id=assistant_id,
        priority=priority,
        max_attempts=1,
        result=payload,
        created_at=created,
    )


def get_job(
    job_id: str,
    *,
    workspace_id_value: str | None = None,
) -> dict[str, Any]:
    repository = _job_repository()
    if repository is not None:
        row = repository.get_job(job_id, workspace_id=workspace_id_value)
        if not row:
            raise KeyError("job not found")
        return row
    clauses = ["id=?"]
    args: list[Any] = [job_id]
    if workspace_id_value is not None:
        clauses.append("workspace_id=?")
        args.append(workspace_id_value)
    row = db.get_conn().execute(
        "SELECT * FROM jobs WHERE " + " AND ".join(clauses),
        args,
    ).fetchone()
    if not row:
        raise KeyError("job not found")
    return _row_to_job(row)


def merge_job_result(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Merge fields into a queued/running job's result JSON (e.g. live progress).

    Returns the updated job dict, or None if the job is missing / already terminal.
    Nested ``progress`` is shallow-merged so callers can patch individual keys.
    """
    jid = str(job_id or "").strip()
    if not jid or not isinstance(patch, dict) or not patch:
        return None
    repository = _job_repository()
    if repository is not None:
        return repository.merge_result(jid, patch)
    # Read+write under one lock so concurrent audit progress updates are safe.
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT result, status FROM jobs WHERE id=?",
            (jid,),
        ).fetchone()
        if not row or row["status"] not in ("queued", "running"):
            return None
        try:
            current = json.loads(row["result"] or "{}")
        except Exception:
            current = {}
        if not isinstance(current, dict):
            current = {}
        merged = {**current, **patch}
        if isinstance(patch.get("progress"), dict):
            prev_progress = (
                current.get("progress")
                if isinstance(current.get("progress"), dict)
                else {}
            )
            merged["progress"] = {**prev_progress, **patch["progress"]}
        conn.execute(
            """UPDATE jobs
               SET result=?
               WHERE id=? AND status IN ('queued', 'running')""",
            (json.dumps(merged, ensure_ascii=False), jid),
        )
    try:
        return get_job(jid)
    except KeyError:
        return None


def _mark_job_done(job_id: str, result: dict[str, Any] | None = None) -> None:
    repository = _job_repository()
    if repository is not None:
        repository.mark_done(job_id, result)
        return
    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs
               SET status='done', error='', result=?, finished_at=?,
                   locked_by=NULL, locked_until=NULL
               WHERE id=?""",
            (json.dumps(result or {}, ensure_ascii=False), now_iso(), job_id),
        )


def list_jobs(
    *,
    target_id: str | None = None,
    status: str | None = None,
    type_: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    repository = _job_repository()
    if repository is not None:
        return repository.list_jobs(
            workspace_id=workspace_id(),
            target_id=target_id,
            status=status,
            type_=type_,
            limit=limit,
        )
    clauses = ["workspace_id=?"]
    args: list[Any] = [workspace_id()]
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
    repository = _job_repository()
    if repository is not None:
        rows = repository.list_jobs(
            workspace_id=workspace_id(),
            target_id=target_id,
            type_=type_,
            limit=100,
        )
        return next(
            (item for item in rows if item.get("target_type") == "chunk"),
            None,
        )
    row = db.get_conn().execute(
        """SELECT * FROM jobs
           WHERE workspace_id=? AND target_type='chunk' AND target_id=? AND type=?
           ORDER BY created_at DESC LIMIT 1""",
        (workspace_id(), target_id, type_),
    ).fetchone()
    return _row_to_job(row) if row else None


async def worker_loop(job_types: set[str] | None = None) -> None:
    while True:
        job = _claim_next_job(job_types)
        if not job:
            await asyncio.sleep(JOB_POLL_SECONDS)
            continue
        try:
            await asyncio.wait_for(_dispatch_job(job), timeout=JOB_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            _fail_job(job, f"job timed out after {JOB_TIMEOUT_SECONDS} seconds")
        except Exception as exc:
            logger.exception("job %s failed outside handler", job.get("id"))
            _fail_job(job, str(exc))


async def _dispatch_job(job: dict[str, Any]) -> None:
    # A worker handles jobs from all workspaces; bind the job scope while its
    # handler calls shared scoped helpers.
    base = current_user.get_current_user()
    identity = current_user.CurrentUser(
        user_id=base.user_id,
        workspace_id=str(job.get("workspace_id") or base.workspace_id),
        roles=base.roles,
        authenticated=base.authenticated,
    )
    token = current_user.set_current_user(identity)
    try:
        if job["type"] == "ocr" and job["target_type"] == "chunk":
            await _run_ocr_job(job)
        elif job["type"] == "parse" and job["target_type"] == "file":
            await _run_parse_job(job)
        elif job["type"] == "chunk" and job["target_type"] == "file":
            await _run_chunk_job(job)
        elif job["type"] == "audit" and job["target_type"] == "assistant":
            await _run_audit_job(job)
        elif job["type"] == "assistant_init" and job["target_type"] == "assistant":
            await _run_assistant_init_job(job)
        elif job["type"] == "embed" and job["target_type"] == "corpus":
            await _run_embed_job(job)
        else:
            _fail_job(job, f"unknown job type {job['type']}")
    finally:
        current_user.reset_current_user(token)


def _claim_next_job(job_types: set[str] | None = None) -> dict[str, Any] | None:
    repository = _job_repository()
    if repository is not None:
        worker_id = f"worker-{uuid.uuid4().hex[:12]}"
        claimed = repository.claim_pending(
            worker_id,
            limit=1,
            lease_seconds=JOB_TIMEOUT_SECONDS,
            job_types=job_types,
        )
        return claimed[0] if claimed else None
    type_clause = ""
    params: list[Any] = [now_iso()]
    if job_types:
        ordered = sorted(job_types)
        type_clause = " AND type IN (" + ",".join("?" for _ in ordered) + ")"
        params.extend(ordered)
    with db.transaction() as conn:
        row = conn.execute(
            """SELECT * FROM jobs
               WHERE status='queued'
                 AND (available_at IS NULL OR available_at<=?)"""
            + type_clause
            + " ORDER BY priority DESC, created_at LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return None
        started = now_iso()
        conn.execute(
            """UPDATE jobs
               SET status='running', attempts=attempts+1, started_at=?,
                   locked_by=?, locked_until=?, error='', available_at=NULL
               WHERE id=?""",
            (started, f"local-worker-{uuid.uuid4().hex[:8]}",
             time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + JOB_TIMEOUT_SECONDS)),
             row["id"]),
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

    chunk_job: dict[str, Any] | None = None
    try:
        from . import chunk_pipeline

        chunk_config = chunk_pipeline.resolve_file_chunk_config(file_id)
        if chunk_config.get("auto_chunk_after_parse", True):
            job_payload = job.get("result") or {}
            skip_existing = not bool(job_payload.get("delete_chunks"))
            chunk_job = enqueue_chunk_file(
                file_id,
                parse_id,
                skip_existing=skip_existing,
            )
            parse_result["chunk_job_id"] = chunk_job["id"]
    except Exception as exc:
        logger.exception("failed to enqueue chunk job for file %s", file_id)
        parse_result["chunk_enqueue_error"] = str(exc)

    with db.transaction() as conn:
        conn.execute(
            """UPDATE document_parses
               SET result=?, updated_at=?
               WHERE id=?""",
            (json.dumps(parse_result, ensure_ascii=False), now_iso(), parse_id),
        )
    _mark_job_done(job["id"], {"parse_id": parse_id, **parse_result})


async def _run_chunk_job(job: dict[str, Any]) -> None:
    """Build chunks in a separately scalable worker stage."""
    from . import chunk_pipeline

    file_id = str(job["target_id"])
    payload = job.get("result") or {}
    parse_id = str(payload.get("parse_id") or "")
    if not parse_id:
        _fail_job(job, "chunk job missing parse_id")
        return
    chunk_config = chunk_pipeline.resolve_file_chunk_config(file_id)
    summary = await chunk_pipeline.run_auto_chunk_pipeline(
        file_id,
        parse_id,
        chunk_config=chunk_config,
        skip_existing=bool(payload.get("skip_existing", True)),
    )
    _mark_job_done(
        job["id"],
        {
            "file_id": file_id,
            "parse_id": parse_id,
            "total": summary.get("total", 0),
            "sections": summary.get("sections", 0),
            "tables": summary.get("tables", 0),
            "images": summary.get("images", 0),
        },
    )


async def _run_audit_job(job: dict[str, Any]) -> None:
    from . import audit_run

    payload = job.get("result") or {}
    assistant_id = str(payload.get("assistant_id") or job["target_id"])
    report_file_id = str(payload.get("report_file_id") or "")
    naming_rule_file_id = payload.get("naming_rule_file_id") or None
    if not report_file_id:
        _fail_job(job, "audit job missing report_file_id")
        return

    merge_job_result(
        str(job.get("id") or ""),
        {
            "progress": {
                "stage": "starting",
                "stage_label": "启动审查",
                "percent": 1,
                "message": "正在启动审查工作流…",
                "updated_at": now_iso(),
            }
        },
    )

    try:
        outcome = await asyncio.to_thread(
            audit_run.run_assistant_audit,
            assistant_id=assistant_id,
            report_file_id=report_file_id,
            naming_rule_file_id=naming_rule_file_id,
            job_id=str(job.get("id") or "") or None,
            started_at=str(job.get("started_at") or "") or None,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        _fail_job(job, str(exc))
        return

    finished = now_iso()
    result = {
        **payload,
        **outcome,
        "job_id": job.get("id"),
        "progress": {
            "stage": "done",
            "stage_label": "已完成",
            "percent": 100,
            "message": "审查完成",
            "updated_at": finished,
        },
    }
    _mark_job_done(job["id"], result)


async def _run_embed_job(job: dict[str, Any]) -> None:
    from . import embeddings

    try:
        summary = await asyncio.to_thread(
            embeddings.build_embeddings,
            workspace_id=str(job.get("workspace_id") or workspace_id()),
        )
    except Exception as exc:
        logger.exception("embedding build failed")
        _fail_job(job, str(exc))
        return

    _mark_job_done(job["id"], summary)


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
    _mark_job_done(job["id"], {**payload, "draft_status": "ready"})


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
    _mark_job_done(job["id"], {"text_length": len(result.text)})


def _requeue_job(job: dict[str, Any], error: str) -> None:
    retry_delay = min(300, 2 ** max(0, int(job.get("attempts") or 1)) * 5)
    repository = _job_repository()
    if repository is not None:
        repository.requeue(job["id"], error or "retrying", retry_delay)
        return
    available = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() + retry_delay),
    )
    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs SET status='queued', error=?, available_at=?,
               locked_by=NULL, locked_until=NULL WHERE id=?""",
            (error or "retrying", available, job["id"]),
        )


def _fail_job(job: dict[str, Any], error: str) -> None:
    finished = now_iso()
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 1)
    retry = attempts < max_attempts
    available = None
    if retry:
        retry_delay = min(300, 2 ** max(0, attempts - 1) * 5)
        available = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() + retry_delay),
        )
    repository = _job_repository()
    if repository is not None:
        repository.mark_failed(
            job["id"],
            error or "job failed",
            retry_delay_seconds=(retry_delay if retry else 30),
        )
        return
    with db.transaction() as conn:
        conn.execute(
            """UPDATE jobs SET status=?, error=?, finished_at=?,
               available_at=?, locked_by=NULL, locked_until=NULL, dead_letter=?
               WHERE id=?""",
            (
                "queued" if retry else "failed",
                error or "job failed",
                None if retry else finished,
                available,
                0 if retry else 1,
                job["id"],
            ),
        )


def _row_to_job(row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["result"] = json.loads(d.get("result") or "{}")
    except Exception:
        d["result"] = {}
    return d
