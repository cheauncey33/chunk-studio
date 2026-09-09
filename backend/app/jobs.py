"""Persistent background jobs for OCR and future async tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from . import artifacts, chunk_schema, config, current_user, db, extractors, observability
from .adapters import ocr as ocr_adapter
from .job_errors import (
    NON_RETRYABLE,
    RETRYABLE,
    JobFailure,
)
from .storage.repositories import get_content_write_repository, get_job_repository
from .storage.object_store import get_object_store

logger = logging.getLogger(__name__)
JOB_POLL_SECONDS = 1.0
JOB_DEPENDENCY_BACKOFF_SECONDS = 5.0
JOB_TIMEOUT_SECONDS = 15 * 60
AUDIT_JOB_MAX_ATTEMPTS = 3  # L2: same run_id/checkpoint; business errors skip retry
AUDIT_JOB_TIMEOUT_GRACE_SECONDS = 60  # wait_for buffer after subprocess kill
NIGHT_BATCH_PRIORITY = -5


class ActiveAuditConflict(Exception):
    """The assistant already has a queued or running audit for this report."""

    def __init__(self, report_file_ids: list[str], *, job_ids: list[str] | None = None):
        ids = [str(item).strip() for item in report_file_ids if str(item).strip()]
        self.report_file_ids = ids
        self.job_ids = [str(item).strip() for item in (job_ids or []) if str(item).strip()]
        if len(ids) == 1:
            message = f"report {ids[0]} already has an active audit job"
        else:
            message = "reports already have an active audit job: " + ", ".join(ids)
        super().__init__(message)


def _timeout_for_job(job: dict[str, Any] | None) -> int:
    """Audit jobs need a longer wall clock than OCR/parse/chunk.

    The workflow subprocess is killed at AUDIT_JOB_TIMEOUT_SECONDS. The worker
    wait_for is that cap plus a short grace so the thread can surface TimeoutExpired
    before asyncio cancels around a still-running process.
    """
    if str((job or {}).get("type") or "") == "audit":
        return max(JOB_TIMEOUT_SECONDS, int(config.AUDIT_JOB_TIMEOUT_SECONDS)) + (
            AUDIT_JOB_TIMEOUT_GRACE_SECONDS
        )
    return JOB_TIMEOUT_SECONDS


def _refresh_job_lease(job: dict[str, Any] | None) -> None:
    """Stretch the claim lease when the job type outlives the default timeout."""
    if not job or not job.get("id"):
        return
    timeout = _timeout_for_job(job)
    if timeout <= JOB_TIMEOUT_SECONDS:
        return
    job_id = str(job["id"])
    repository = _job_repository()
    extender = getattr(repository, "extend_lease", None) if repository is not None else None
    if callable(extender):
        extender(job_id, timeout)
        return
    until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + timeout))
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET locked_until=? WHERE id=? AND status='running'",
            (until, job_id),
        )


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    aware = value.astimezone(timezone.utc).replace(microsecond=0)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso() -> str:
    return format_utc(utc_now())


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_job_schedule_time(value: str | datetime | None) -> datetime | None:
    """Parse a job schedule timestamp into UTC.

    Aware values (Z / offset) are converted to UTC. Naive values are treated as
    the host's local timezone so existing retry ``available_at`` rows keep working.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z") or raw.endswith("z"):
            raw = raw[:-1] + "+00:00"
        elif "T" not in raw[:19] and " " in raw:
            raw = raw.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("invalid scheduled_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tzinfo())
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def normalize_job_schedule_time(value: str | None) -> str:
    """Normalize API schedule times onto a canonical UTC ISO string.

    ``2026-09-09T23:00:00+08:00`` and ``2026-09-09T15:00:00Z`` both become
    ``2026-09-09T15:00:00Z``. Naive values are interpreted as local time.
    """
    parsed = parse_job_schedule_time(value)
    if parsed is None:
        raise ValueError("scheduled_at is required")
    return format_utc(parsed)


def available_at_reached(value: str | datetime | None, *, now: datetime | None = None) -> bool:
    """Return True when a job's ``available_at`` is due.

    Empty / NULL means immediately claimable. Comparisons always happen in UTC.
    """
    parsed = parse_job_schedule_time(value)
    if parsed is None:
        return True
    clock = now or utc_now()
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_local_tzinfo())
    return parsed <= clock.astimezone(timezone.utc).replace(microsecond=0)


def workspace_id() -> str:
    return current_user.get_current_user().workspace_id


def _job_repository():
    """Return the PostgreSQL queue adapter, or the SQLite local-profile path."""
    return get_job_repository()


def _content_write_repository():
    """Return the PostgreSQL writer only when the primary DB is PostgreSQL."""
    return get_content_write_repository()


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
    available_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or now_iso()
    scheduled = str(available_at or "").strip() or None
    if scheduled:
        scheduled = normalize_job_schedule_time(scheduled)
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
            available_at=scheduled,
        )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, workspace_id, type, target_type, target_id, status, priority,
                attempts, max_attempts, error, result, created_at, available_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?, '', ?, ?, ?)""",
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
                scheduled,
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
    content = _content_write_repository()
    row = content.get_chunk(chunk_id) if content is not None else db.get_conn().execute(
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
    content = _content_write_repository()
    if content is not None:
        rows = content.list_chunks(file_id=file_id, limit=1000)
        if page is not None:
            rows = [row for row in rows if int(row.get("page") or 0) == page]
        if pending_only:
            rows = [row for row in rows if row.get("text_source") == "pending"]
        return [enqueue_ocr_chunk(str(row["id"])) for row in rows]
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
    content = _content_write_repository()
    row = content.get_file(file_id) if content is not None else db.get_conn().execute(
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
    if content is not None:
        content.create_parse(parse_id, file_id)
    else:
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
    content = _content_write_repository()
    row = content.get_file(file_id) if content is not None else db.get_conn().execute(
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
    content = _content_write_repository()
    if content is not None:
        rows = content.delete_file_chunks(file_id)
    else:
        rows = db.get_conn().execute(
            """SELECT id, crop_path, crop_object_key FROM chunks
               WHERE file_id=? AND workspace_id=?""",
            (file_id, workspace_id()),
        ).fetchall()
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE file_id=? AND workspace_id=?)",
                (file_id, workspace_id()),
            )
            conn.execute(
                "DELETE FROM chunks WHERE file_id=? AND workspace_id=?",
                (file_id, workspace_id()),
            )
    for row in rows:
        crop = (row["crop_path"] or "").strip()
        if crop:
            try:
                config.from_rel(crop).unlink(missing_ok=True)
            except OSError:
                pass
        object_key = str(row["crop_object_key"] or "").strip()
        if object_key:
            try:
                get_object_store().delete(object_key)
            except Exception:
                logger.exception("failed to delete object %s", object_key)
    return len(rows)


def list_active_assistant_audits(
    *,
    workspace: str,
    assistant_id: str,
) -> list[dict[str, Any]]:
    """Queued or running assistant audit jobs for one assistant in a workspace."""
    repository = _job_repository()
    if repository is not None:
        rows: list[dict[str, Any]] = []
        for status in ("queued", "running"):
            rows.extend(
                repository.list_jobs(
                    workspace_id=workspace,
                    target_id=assistant_id,
                    type_="audit",
                    status=status,
                    limit=500,
                )
            )
        return [job for job in rows if job.get("target_type") == "assistant"]
    return [
        _row_to_job(item)
        for item in db.get_conn().execute(
            """SELECT * FROM jobs
               WHERE workspace_id=? AND type='audit' AND target_type='assistant'
                 AND target_id=? AND status IN ('queued','running')
               ORDER BY created_at DESC""",
            (workspace, assistant_id),
        ).fetchall()
    ]


def find_active_assistant_audit(
    *,
    workspace: str,
    assistant_id: str,
    report_file_id: str,
) -> dict[str, Any] | None:
    wanted = str(report_file_id or "").strip()
    if not wanted:
        return None
    for existing_job in list_active_assistant_audits(
        workspace=workspace, assistant_id=assistant_id
    ):
        existing_report = str((existing_job.get("result") or {}).get("report_file_id") or "")
        if existing_report == wanted:
            return existing_job
    return None


def build_assistant_audit_job_spec(
    *,
    assistant_id: str,
    report_file_id: str,
    naming_rule_file_id: str | None,
    workspace: str,
    priority: int,
    available_at: str | None = None,
    batch_id: str | None = None,
    batch_item_id: str | None = None,
    job_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the job row Night Batch inserts in the same transaction as items."""
    from . import audit_run

    jid = str(job_id or uuid.uuid4().hex)
    created = created_at or now_iso()
    identity = audit_run.audit_run_identity(assistant_id=assistant_id, run_id=jid)
    payload = {
        "assistant_id": assistant_id,
        "report_file_id": report_file_id,
        "naming_rule_file_id": naming_rule_file_id,
        **identity,
    }
    batch_key = str(batch_id or "").strip()
    item_key = str(batch_item_id or "").strip()
    if batch_key:
        payload["batch_id"] = batch_key
    if item_key:
        payload["batch_item_id"] = item_key
    scheduled = str(available_at or "").strip() or None
    if scheduled:
        scheduled = normalize_job_schedule_time(scheduled)
    return {
        "id": jid,
        "workspace_id": workspace,
        "type": "audit",
        "target_type": "assistant",
        "target_id": assistant_id,
        "priority": int(priority),
        "max_attempts": AUDIT_JOB_MAX_ATTEMPTS,
        "result": payload,
        "created_at": created,
        "available_at": scheduled,
    }


def enqueue_assistant_audit(
    assistant_id: str,
    *,
    report_file_id: str,
    naming_rule_file_id: str | None = None,
    priority: int = 5,
    available_at: str | None = None,
    batch_id: str | None = None,
    batch_item_id: str | None = None,
    reuse_active: bool = True,
) -> dict[str, Any]:
    """Queue an end-to-end assistant audit run.

    The report is an audit input (not KB corpus). Naming PDF is a KB attribute
    and also need not appear in knowledge_base_files. Evidence still comes from
    enabled files in the assistant's bound knowledge bases.
    """
    content = _content_write_repository()
    if content is not None:
        active = content.get_active_assistant_version(assistant_id)
        row = (
            {
                "id": assistant_id,
                "workspace_id": workspace_id(),
                "active_version_id": active.get("id") if active else None,
            }
            if active
            else None
        )
    else:
        row = db.get_conn().execute(
            """SELECT id, workspace_id, active_version_id
               FROM audit_assistants WHERE id=? AND workspace_id=?""",
            (assistant_id, workspace_id()),
        ).fetchone()
    if not row:
        raise KeyError("assistant not found")
    if not row["active_version_id"]:
        raise ValueError("assistant has no active version")

    report_row = content.get_file(report_file_id) if content is not None else db.get_conn().execute(
        "SELECT id FROM files WHERE id=? AND workspace_id=?",
        (report_file_id, row["workspace_id"]),
    ).fetchone()
    if not report_row:
        raise ValueError("report file not found")

    scoped_file_ids = set(
        content.assistant_scoped_file_ids(assistant_id)
        if content is not None
        else db.assistant_scoped_file_ids(assistant_id)
    )
    if not scoped_file_ids:
        raise ValueError("assistant has no enabled files in its knowledge bases")

    from . import audit_run

    resolved_naming_id = audit_run.resolve_naming_rule_file_id(
        assistant_id, naming_rule_file_id
    )
    if resolved_naming_id:
        naming_row = content.get_file(resolved_naming_id) if content is not None else db.get_conn().execute(
            "SELECT id FROM files WHERE id=? AND workspace_id=?",
            (resolved_naming_id, row["workspace_id"]),
        ).fetchone()
        if not naming_row:
            raise ValueError("naming-rule file not found")

    excluded = {report_file_id}
    if resolved_naming_id:
        excluded.add(resolved_naming_id)
    # Fail fast: after excluding runtime inputs, corpus evidence must remain.
    if content is not None:
        content.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)
    else:
        db.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)

    # Reuse only an in-flight job for the same assistant + report (resume after
    # client disconnect). Night Batch must not reuse or silently retarget that
    # job, because scheduled_at would lose its meaning.
    existing_job = find_active_assistant_audit(
        workspace=row["workspace_id"],
        assistant_id=assistant_id,
        report_file_id=report_file_id,
    )
    if existing_job is not None:
        if reuse_active:
            return existing_job
        raise ActiveAuditConflict(
            [report_file_id],
            job_ids=[str(existing_job.get("id") or "")],
        )

    # Validate inputs early so the API can fail fast.
    audit_run.resolve_markdown_path(report_file_id)
    audit_run.resolve_naming_rule_path(resolved_naming_id, assistant_id=assistant_id)

    spec = build_assistant_audit_job_spec(
        assistant_id=assistant_id,
        report_file_id=report_file_id,
        naming_rule_file_id=resolved_naming_id,
        workspace=row["workspace_id"],
        priority=priority,
        available_at=available_at,
        batch_id=batch_id,
        batch_item_id=batch_item_id,
    )
    return _create_job_record(
        job_id=spec["id"],
        workspace=spec["workspace_id"],
        type_="audit",
        target_type="assistant",
        target_id=assistant_id,
        priority=spec["priority"],
        max_attempts=spec["max_attempts"],
        result=spec["result"],
        created_at=spec["created_at"],
        available_at=spec["available_at"],
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

    content = _content_write_repository()
    if content is not None:
        active = content.get_active_assistant_version(assistant_id)
        row = (
            {
                "id": assistant_id,
                "workspace_id": workspace_id(),
                "active_version_id": active.get("id") if active else None,
            }
            if active
            else None
        )
    else:
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
    if content is not None:
        if not content.assistant_bound_knowledge_bases(assistant_id):
            raise ValueError("assistant is not bound to a knowledge base")
    elif not db.knowledge_base_id_for_assistant(assistant_id):
        raise ValueError("assistant is not bound to a knowledge base")

    samples = list(dict.fromkeys(sample_report_file_ids or []))[:3]
    for file_id in samples:
        file_row = content.get_file(file_id) if content is not None else db.get_conn().execute(
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


def get_job_usage(job_id: str) -> dict[str, Any]:
    """Workspace-scoped usage aggregate. Ledger rows are the source of truth."""
    job = get_job(job_id, workspace_id_value=workspace_id())
    from .storage.usage_repository import get_usage_repository

    summary = get_usage_repository().get_usage_summary(
        workspace_id=str(job.get("workspace_id") or workspace_id()),
        job_id=str(job.get("id") or job_id),
    )
    summary["job_id"] = str(job.get("id") or job_id)
    return summary


def _usage_summary_snapshot(job_id: str, workspace: str | None = None) -> dict[str, Any] | None:
    try:
        from .storage.usage_repository import compact_usage_summary, get_usage_repository

        summary = get_usage_repository().get_usage_summary(
            workspace_id=str(workspace or workspace_id()),
            job_id=str(job_id),
        )
        return compact_usage_summary(summary)
    except Exception:
        logger.exception("usage summary snapshot failed for job %s", job_id)
        return None


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


def _refresh_parent_batch_for_job(
    job_id: str,
    workspace_id_value: str | None = None,
) -> None:
    """Recompute cached batch status from child jobs after a job changes."""
    jid = str(job_id or "").strip()
    if not jid:
        return
    try:
        from . import audit_batches

        audit_batches.refresh_batch_for_job(
            jid, workspace_id=str(workspace_id_value or "").strip() or None
        )
    except Exception:
        logger.exception("failed to refresh audit batch for job %s", jid)


def _mark_job_done(job_id: str, result: dict[str, Any] | None = None) -> None:
    repository = _job_repository()
    if repository is not None:
        repository.mark_done(job_id, result)
    else:
        with db.transaction() as conn:
            conn.execute(
                """UPDATE jobs
                   SET status='done', error='', result=?, finished_at=?,
                       locked_by=NULL, locked_until=NULL
                   WHERE id=?""",
                (json.dumps(result or {}, ensure_ascii=False), now_iso(), job_id),
            )
    workspace = None
    if isinstance(result, dict):
        workspace = str(result.get("workspace_id") or "").strip() or None
    _refresh_parent_batch_for_job(job_id, workspace)


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
        try:
            job = _claim_next_job(job_types)
        except Exception as exc:
            logger.exception("worker could not claim a job; retrying after dependency backoff")
            observability.metrics.increment(
                "chunk_studio_worker_claim_failures_total",
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(JOB_DEPENDENCY_BACKOFF_SECONDS)
            continue
        if not job:
            await asyncio.sleep(JOB_POLL_SECONDS)
            continue
        timeout = _timeout_for_job(job)
        try:
            await asyncio.wait_for(_dispatch_job(job), timeout=timeout)
        except asyncio.TimeoutError:
            await _record_job_failure(
                job,
                f"job timed out after {timeout} seconds",
                retryable=True,
                error_code="timeout",
            )
        except Exception as exc:
            logger.exception("job %s failed outside handler", job.get("id"))
            await _record_job_failure(job, str(exc), retryable=True, error_code="runtime")


async def _record_job_failure(
    job: dict[str, Any],
    error: str,
    *,
    retryable: bool | None = None,
    error_code: str = "",
) -> None:
    """Keep the worker alive if persisting a job failure also loses its dependency."""
    try:
        _fail_job(job, error, retryable=retryable, error_code=error_code)
    except Exception as exc:
        logger.exception("worker could not persist failure for job %s", job.get("id"))
        observability.metrics.increment(
            "chunk_studio_worker_failure_persist_errors_total",
            error_type=type(exc).__name__,
        )
        await asyncio.sleep(JOB_DEPENDENCY_BACKOFF_SECONDS)


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
            _fail_job(job, f"unknown job type {job['type']}", retryable=False, error_code="unknown_type")
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
        job = claimed[0] if claimed else None
        _refresh_job_lease(job)
        return job
    type_clause = ""
    params: list[Any] = []
    if job_types:
        ordered = sorted(job_types)
        type_clause = " AND type IN (" + ",".join("?" for _ in ordered) + ")"
        params.extend(ordered)
    clock = utc_now()
    with db.transaction() as conn:
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE status='queued'"""
            + type_clause
            + " ORDER BY priority DESC, created_at",
            params,
        ).fetchall()
        row = None
        for candidate in rows:
            if available_at_reached(candidate["available_at"], now=clock):
                row = candidate
                break
        if not row:
            return None
        started = now_iso()
        timeout = _timeout_for_job({"type": row["type"]})
        conn.execute(
            """UPDATE jobs
               SET status='running', attempts=attempts+1, started_at=?,
                   locked_by=?, locked_until=?, error='', available_at=NULL
               WHERE id=?""",
            (started, f"local-worker-{uuid.uuid4().hex[:8]}",
             time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + timeout)),
             row["id"]),
        )
    return get_job(row["id"])


async def _run_parse_job(job: dict[str, Any]) -> None:
    file_id = job["target_id"]
    parse_id = (job.get("result") or {}).get("parse_id") or uuid.uuid4().hex
    started = now_iso()
    content = _content_write_repository()
    if content is not None:
        content.update_parse(parse_id, status="running", error="")
    else:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE document_parses SET status='running', error='', updated_at=? WHERE id=?",
                (started, parse_id),
            )

    result = await ocr_adapter.parse_file(file_id)
    finished = now_iso()
    if not result.ok:
        if content is not None:
            content.update_parse(parse_id, status="failed", error=result.error or "parse failed")
        else:
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE document_parses SET status='failed', error=?, updated_at=? WHERE id=?",
                    (result.error or "parse failed", finished, parse_id),
                )
        _fail_job(job, result.error)
        return

    outputs = _write_parse_outputs(file_id, parse_id, result.text, result.zip_bytes)
    markdown_rel = outputs["markdown_path"]
    zip_rel = outputs["raw_zip_path"]
    parse_result = {
        "markdown_length": len(result.text),
        "has_zip": bool(result.zip_bytes),
        "markdown_object_key": outputs["markdown_object_key"],
        "raw_zip_object_key": outputs["raw_zip_object_key"],
    }
    parse_fields = {
        "status": "done",
        "markdown_path": markdown_rel,
        "raw_zip_path": zip_rel,
        "markdown_object_key": outputs["markdown_object_key"],
        "markdown_sha256": outputs["markdown_sha256"],
        "markdown_size": outputs["markdown_size"],
        "raw_zip_object_key": outputs["raw_zip_object_key"],
        "raw_zip_sha256": outputs["raw_zip_sha256"],
        "raw_zip_size": outputs["raw_zip_size"],
        "result": parse_result,
        "error": "",
    }
    if content is not None:
        content.update_parse(parse_id, **parse_fields)
    else:
        with db.transaction() as conn:
            conn.execute(
                """UPDATE document_parses
                   SET status='done', markdown_path=?, raw_zip_path=?,
                       markdown_object_key=?, markdown_sha256=?, markdown_size=?,
                       raw_zip_object_key=?, raw_zip_sha256=?, raw_zip_size=?,
                       result=?, error='', updated_at=?
                   WHERE id=?""",
                (
                    markdown_rel,
                    zip_rel,
                    outputs["markdown_object_key"],
                    outputs["markdown_sha256"],
                    outputs["markdown_size"],
                    outputs["raw_zip_object_key"],
                    outputs["raw_zip_sha256"],
                    outputs["raw_zip_size"],
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

    if content is not None:
        content.update_parse(parse_id, result=parse_result)
    else:
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
        _fail_job(job, "audit job missing report_file_id", retryable=False, error_code="invalid_input")
        return

    run_id = str(payload.get("run_id") or job.get("id") or "").strip()
    stored_name = str(payload.get("report_name") or "").strip()
    identity = (
        audit_run.audit_output_paths(stored_name, run_id=run_id)
        if stored_name
        else audit_run.audit_run_identity(assistant_id=assistant_id, run_id=run_id)
    )
    merge_job_result(
        str(job.get("id") or ""),
        {
            **identity,
            "attempt": int(job.get("attempts") or 1),
            "progress": {
                "stage": "starting",
                "stage_label": "启动审查",
                "percent": 1,
                "message": "正在启动审查工作流…",
                "updated_at": now_iso(),
                "attempt": int(job.get("attempts") or 1),
            },
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
            run_id=identity["run_id"],
            report_name=identity["report_name"],
            job_attempt=int(job.get("attempts") or 1),
        )
    except JobFailure as exc:
        _fail_job(job, str(exc), retryable=exc.retryable, error_code=exc.code)
        return
    except ValueError as exc:
        _fail_job(job, str(exc), retryable=False, error_code="invalid_input")
        return
    except (RuntimeError, OSError) as exc:
        _fail_job(job, str(exc), retryable=True, error_code="runtime")
        return

    finished = now_iso()
    current: dict[str, Any] = {}
    try:
        current = get_job(str(job.get("id") or "")).get("result") or {}
    except KeyError:
        current = {}
    if not isinstance(current, dict):
        current = {}
    result = {
        **payload,
        **current,
        **identity,
        **outcome,
        "job_id": job.get("id"),
        "progress": {
            "stage": "done",
            "stage_label": "已完成",
            "percent": 100,
            "message": "审查完成",
            "updated_at": finished,
            "attempt": current.get("attempt") or payload.get("attempt") or int(job.get("attempts") or 1),
            "resumed": bool(current.get("resumed")),
            "resumed_case_count": int(current.get("resumed_case_count") or 0),
        },
    }
    result["attempt"] = result["progress"]["attempt"]
    result["resumed"] = result["progress"]["resumed"]
    result["resumed_case_count"] = result["progress"]["resumed_case_count"]
    for key in ("batch_id", "batch_item_id"):
        value = payload.get(key) or current.get(key)
        if value:
            result[key] = value
    result.pop("error_class", None)
    result.pop("error_code", None)
    snapshot = _usage_summary_snapshot(str(job.get("id") or ""), str(job.get("workspace_id") or "") or None)
    if snapshot is not None:
        result["usage_summary"] = snapshot
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
) -> dict[str, Any]:
    config.PARSES_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{file_id}_{parse_id}"
    md_path = config.PARSES_DIR / f"{stem}.md"
    markdown_bytes = markdown.encode("utf-8")
    md_path.write_bytes(markdown_bytes)
    store = get_object_store()
    workspace = workspace_id()
    markdown_key = artifacts.parse_artifact_key(workspace, file_id, parse_id, "markdown")
    markdown_info = store.put_bytes(
        markdown_key,
        markdown_bytes,
        content_type="text/markdown; charset=utf-8",
    )
    zip_rel = None
    zip_key = ""
    zip_info: Any = None
    if zip_bytes:
        zip_path = config.PARSES_DIR / f"{stem}.zip"
        zip_path.write_bytes(zip_bytes)
        zip_rel = config.to_rel(zip_path)
        zip_key = artifacts.parse_artifact_key(workspace, file_id, parse_id, "layout_zip")
        zip_info = store.put_bytes(zip_key, zip_bytes, content_type="application/zip")
    return {
        "markdown_path": config.to_rel(md_path),
        "markdown_object_key": markdown_info.key,
        "markdown_sha256": markdown_info.sha256,
        "markdown_size": markdown_info.size,
        "raw_zip_path": zip_rel,
        "raw_zip_object_key": zip_info.key if zip_info else "",
        "raw_zip_sha256": zip_info.sha256 if zip_info else "",
        "raw_zip_size": zip_info.size if zip_info else 0,
    }


async def ocr_chunk_sync(chunk_id: str) -> tuple[bool, str]:
    """Run MinerU OCR synchronously and update the chunk. Returns (ok, error)."""
    content = _content_write_repository()
    if content is not None:
        row = content.get_chunk(chunk_id)
        if not row:
            raise KeyError("chunk not found")
        result = await ocr_adapter.ocr_chunk(chunk_id)
        job = _create_job_record(
            job_id=uuid.uuid4().hex,
            workspace=workspace_id(),
            type_="ocr",
            target_type="chunk",
            target_id=chunk_id,
            priority=0,
            max_attempts=1,
            result={"sync": True},
        )
        if not result.ok:
            _fail_job(job, result.error or "ocr failed")
            return False, result.error or "ocr failed"
        file_row = content.get_file(str(row.get("file_id") or ""))
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row.get("metadata"),
            business_metadata=row.get("business_metadata"),
        )
        meta = layers["business_metadata"]
        extractors.merge_auto_metadata(
            meta,
            result.text,
            str((file_row or {}).get("name") or ""),
        )
        if not content.update_chunk_ocr(
            chunk_id,
            text=result.text,
            business_metadata=meta,
        ):
            _fail_job(job, "chunk not found")
            return False, "chunk not found"
        _mark_job_done(job["id"], {"text_length": len(result.text), "sync": True})
        return True, ""
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

    content = _content_write_repository()
    if content is not None:
        row = content.get_chunk(chunk_id)
        if not row:
            _fail_job(job, "chunk not found")
            return
        file_row = content.get_file(str(row.get("file_id") or ""))
        fname = str((file_row or {}).get("name") or "")
        layers = chunk_schema.ensure_layered_chunk(
            metadata=row.get("metadata"),
            business_metadata=row.get("business_metadata"),
        )
        meta = layers["business_metadata"]
        extractors.merge_auto_metadata(meta, result.text, fname)
        if not content.update_chunk_ocr(
            chunk_id,
            text=result.text,
            business_metadata=meta,
        ):
            _fail_job(job, "chunk not found")
            return
        _mark_job_done(job["id"], {"text_length": len(result.text)})
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


def _fail_job(
    job: dict[str, Any],
    error: str,
    *,
    retryable: bool | None = None,
    error_code: str = "",
) -> None:
    finished = now_iso()
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 1)
    allow_retry = True if retryable is None else bool(retryable)
    retry = allow_retry and attempts < max_attempts
    available = None
    retry_delay = 30
    if retry:
        retry_delay = min(300, 2 ** max(0, attempts - 1) * 5)
        available = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() + retry_delay),
        )
    job_id = str(job.get("id") or "")
    if job_id:
        merge_job_result(
            job_id,
            {
                "error_class": RETRYABLE if allow_retry else NON_RETRYABLE,
                "error_code": error_code
                or ("transient" if allow_retry else "business"),
            },
        )
    repository = _job_repository()
    if repository is not None:
        repository.mark_failed(
            job["id"],
            error or "job failed",
            retry_delay_seconds=(retry_delay if retry else 30),
            retryable=retry,
        )
    else:
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
    _refresh_parent_batch_for_job(job_id, str(job.get("workspace_id") or "") or None)


def _row_to_job(row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["result"] = json.loads(d.get("result") or "{}")
    except Exception:
        d["result"] = {}
    result = d.get("result") or {}
    if isinstance(result, dict):
        if result.get("batch_id"):
            d["batch_id"] = result["batch_id"]
        if result.get("batch_item_id"):
            d["batch_item_id"] = result["batch_item_id"]
    return d
