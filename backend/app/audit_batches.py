"""Night Batch orchestration over existing assistant audit jobs.

A batch is an aggregation and scheduling layer. Each report still maps to one
Audit Job. The worker continues to claim and execute ordinary ``type=audit``
jobs; it never iterates reports itself.

``max_concurrency`` is stored for a later batch-slot design. Phase 1 does not
enforce it: report concurrency stays 1 only with a single ``worker_loop``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from . import current_user, jobs, observability
from .storage.batch_repository import (
    BATCH_MAX_CONCURRENCY,
    BATCH_MODE_NIGHT,
    BATCH_STATUS_COMPLETED,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_PARTIAL_FAILED,
    BATCH_STATUS_RUNNING,
    BATCH_STATUS_SCHEDULED,
    NIGHT_BATCH_MAX_REPORTS,
    NIGHT_BATCH_PRIORITY,
    get_batch_repository,
)
from .storage.usage_repository import compact_usage_summary, get_usage_repository


logger = logging.getLogger(__name__)

_TERMINAL_JOB_STATUSES = frozenset({"done", "failed"})


def _workspace_id() -> str:
    return current_user.get_current_user().workspace_id


def _clock(now: datetime | str | None = None) -> datetime:
    if now is None:
        return jobs.utc_now()
    if isinstance(now, datetime):
        parsed = jobs.parse_job_schedule_time(now)
        return parsed or jobs.utc_now()
    parsed = jobs.parse_job_schedule_time(now)
    return parsed or jobs.utc_now()


def _job_schedule_bucket(job: dict[str, Any], *, now: datetime) -> str:
    status = str(job.get("status") or "")
    if status in {"running", "done", "failed"}:
        return status
    if status != "queued":
        return status or "queued"
    if not jobs.available_at_reached(job.get("available_at"), now=now):
        return "scheduled"
    return "queued"


def derive_batch_status(
    child_jobs: list[dict[str, Any]],
    *,
    scheduled_at: str,
    now: datetime | str | None = None,
) -> str:
    """Derive batch status from child audit jobs. Jobs are the source of truth."""
    clock = _clock(now)
    if not child_jobs:
        return BATCH_STATUS_FAILED
    buckets = [_job_schedule_bucket(job, now=clock) for job in child_jobs]
    n_done = buckets.count("done")
    n_failed = buckets.count("failed")
    n_running = buckets.count("running")
    n_queued = buckets.count("queued")
    n_scheduled = buckets.count("scheduled")
    n_terminal = n_done + n_failed
    if n_terminal == len(child_jobs):
        if n_failed == len(child_jobs):
            return BATCH_STATUS_FAILED
        if n_failed > 0:
            return BATCH_STATUS_PARTIAL_FAILED
        return BATCH_STATUS_COMPLETED
    if n_running or n_queued or jobs.available_at_reached(scheduled_at, now=clock):
        return BATCH_STATUS_RUNNING
    if n_scheduled == len(child_jobs) - n_terminal:
        return BATCH_STATUS_SCHEDULED
    return BATCH_STATUS_RUNNING


def _count_buckets(
    child_jobs: list[dict[str, Any]], *, now: datetime | str | None = None
) -> dict[str, int]:
    clock = _clock(now)
    counts = {
        "scheduled": 0,
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
    }
    for job in child_jobs:
        bucket = _job_schedule_bucket(job, now=clock)
        if bucket == "done":
            counts["completed"] += 1
        elif bucket in counts:
            counts[bucket] += 1
    return counts


def _progress(finished: int, total: int) -> dict[str, Any]:
    percent = round((finished / total) * 100, 2) if total else 0.0
    return {"finished": finished, "total": total, "percent": percent}


def _load_child_jobs(
    items: list[dict[str, Any]], *, workspace_id: str
) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for item in items:
        job_id = str(item.get("audit_job_id") or "")
        if not job_id:
            continue
        try:
            loaded.append(jobs.get_job(job_id, workspace_id_value=workspace_id))
        except KeyError:
            logger.warning(
                "audit batch item %s missing job %s", item.get("id"), job_id
            )
    return loaded


def _item_view(item: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    result = (job or {}).get("result") or {}
    if not isinstance(result, dict):
        result = {}
    status = str((job or {}).get("status") or "")
    return {
        "id": item.get("id"),
        "report_file_id": item.get("report_file_id"),
        "audit_job_id": item.get("audit_job_id"),
        "ordinal": item.get("ordinal"),
        "status": status,
        "attempts": int((job or {}).get("attempts") or 0),
        "resumed": bool(result.get("resumed")),
        "resumed_case_count": int(result.get("resumed_case_count") or 0),
        "run_id": str(result.get("run_id") or (job or {}).get("id") or ""),
        "batch_id": item.get("batch_id"),
        "batch_item_id": item.get("id"),
    }


def _observe_status_transition(previous: str | None, current: str) -> None:
    if previous == current:
        return
    if current == BATCH_STATUS_COMPLETED:
        observability.metrics.increment("audit_batches_completed_total", status=current)
    elif current in {BATCH_STATUS_FAILED, BATCH_STATUS_PARTIAL_FAILED}:
        observability.metrics.increment("audit_batches_failed_total", status=current)


def refresh_batch_status(
    batch_id: str,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    workspace = str(workspace_id or _workspace_id()).strip()
    repo = get_batch_repository()
    batch = repo.get_batch(batch_id, workspace_id=workspace)
    if batch is None:
        return None
    items = repo.list_items(batch_id, workspace_id=workspace)
    child_jobs = _load_child_jobs(items, workspace_id=workspace)
    now = jobs.utc_now()
    status = derive_batch_status(
        child_jobs,
        scheduled_at=str(batch.get("scheduled_at") or ""),
        now=now,
    )
    previous = str(batch.get("status") or "")
    started_at = None
    finished_at = batch.get("finished_at")
    stamp = jobs.utc_now_iso()
    if status == BATCH_STATUS_RUNNING and not batch.get("started_at"):
        started_at = stamp
    if status in {
        BATCH_STATUS_COMPLETED,
        BATCH_STATUS_PARTIAL_FAILED,
        BATCH_STATUS_FAILED,
    }:
        finished_at = batch.get("finished_at") or stamp
    else:
        finished_at = None
    if status != previous or started_at or finished_at != batch.get("finished_at"):
        repo.update_batch_cache(
            batch_id,
            workspace_id=workspace,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            updated_at=stamp,
        )
        _observe_status_transition(previous, status)
        batch = repo.get_batch(batch_id, workspace_id=workspace) or batch
        batch["status"] = status
    else:
        batch["status"] = status
    return batch


def refresh_batch_for_job(
    job_id: str, *, workspace_id: str | None = None
) -> dict[str, Any] | None:
    link = get_batch_repository().lookup_batch_for_job(job_id)
    if not link:
        return None
    return refresh_batch_status(
        link["batch_id"],
        workspace_id=workspace_id or link.get("workspace_id"),
    )


def _preflight_reports(
    assistant_id: str,
    report_file_ids: list[str],
    naming_rule_file_id: str | None,
) -> str | None:
    """Validate every report before writing batch rows. Returns resolved naming id."""
    from . import audit_run, db

    workspace = _workspace_id()
    content = jobs._content_write_repository()
    if content is not None:
        active = content.get_active_assistant_version(assistant_id)
        row = (
            {
                "id": assistant_id,
                "workspace_id": workspace,
                "active_version_id": active.get("id") if active else None,
            }
            if active
            else None
        )
    else:
        row = db.get_conn().execute(
            """SELECT id, workspace_id, active_version_id
               FROM audit_assistants WHERE id=? AND workspace_id=?""",
            (assistant_id, workspace),
        ).fetchone()
    if not row:
        raise KeyError("assistant not found")
    if not row["active_version_id"]:
        raise ValueError("assistant has no active version")

    scoped_file_ids = set(
        content.assistant_scoped_file_ids(assistant_id)
        if content is not None
        else db.assistant_scoped_file_ids(assistant_id)
    )
    if not scoped_file_ids:
        raise ValueError("assistant has no enabled files in its knowledge bases")

    resolved_naming_id = audit_run.resolve_naming_rule_file_id(
        assistant_id, naming_rule_file_id
    )
    if resolved_naming_id:
        naming_row = (
            content.get_file(resolved_naming_id)
            if content is not None
            else db.get_conn().execute(
                "SELECT id FROM files WHERE id=? AND workspace_id=?",
                (resolved_naming_id, row["workspace_id"]),
            ).fetchone()
        )
        if not naming_row:
            raise ValueError("naming-rule file not found")
        audit_run.resolve_naming_rule_path(
            resolved_naming_id, assistant_id=assistant_id
        )

    conflicts: list[str] = []
    conflict_jobs: list[str] = []
    for report_file_id in report_file_ids:
        report_row = (
            content.get_file(report_file_id)
            if content is not None
            else db.get_conn().execute(
                "SELECT id FROM files WHERE id=? AND workspace_id=?",
                (report_file_id, row["workspace_id"]),
            ).fetchone()
        )
        if not report_row:
            raise ValueError(f"report file not found: {report_file_id}")
        excluded = {report_file_id}
        if resolved_naming_id:
            excluded.add(resolved_naming_id)
        if content is not None:
            content.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)
        else:
            db.assistant_evidence_file_ids(assistant_id, excluded_file_ids=excluded)
        audit_run.resolve_markdown_path(report_file_id)
        existing = jobs.find_active_assistant_audit(
            workspace=row["workspace_id"],
            assistant_id=assistant_id,
            report_file_id=report_file_id,
        )
        if existing is not None:
            conflicts.append(report_file_id)
            conflict_jobs.append(str(existing.get("id") or ""))

    if conflicts:
        raise jobs.ActiveAuditConflict(conflicts, job_ids=conflict_jobs)
    return resolved_naming_id


def create_night_batch(
    *,
    assistant_id: str,
    report_file_ids: list[str],
    naming_rule_file_id: str | None = None,
    scheduled_at: str,
    max_concurrency: int = BATCH_MAX_CONCURRENCY,
) -> dict[str, Any]:
    ids = [str(item).strip() for item in report_file_ids if str(item).strip()]
    if not ids or len(ids) > NIGHT_BATCH_MAX_REPORTS:
        raise ValueError(
            f"report_file_ids must contain between 1 and {NIGHT_BATCH_MAX_REPORTS} reports"
        )
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate report_file_id")
    if int(max_concurrency) != BATCH_MAX_CONCURRENCY:
        raise ValueError("max_concurrency must be 1")

    scheduled = jobs.normalize_job_schedule_time(scheduled_at)
    resolved_naming = _preflight_reports(assistant_id, ids, naming_rule_file_id)

    workspace = _workspace_id()
    created = jobs.now_iso()
    batch_id = uuid.uuid4().hex
    children: list[dict[str, Any]] = []
    for ordinal, report_file_id in enumerate(ids, start=1):
        item_id = uuid.uuid4().hex
        spec = jobs.build_assistant_audit_job_spec(
            assistant_id=assistant_id,
            report_file_id=report_file_id,
            naming_rule_file_id=resolved_naming,
            workspace=workspace,
            priority=NIGHT_BATCH_PRIORITY,
            available_at=scheduled,
            batch_id=batch_id,
            batch_item_id=item_id,
            created_at=created,
        )
        children.append(
            {
                "job": spec,
                "item": {
                    "id": item_id,
                    "workspace_id": workspace,
                    "batch_id": batch_id,
                    "ordinal": ordinal,
                    "report_file_id": report_file_id,
                    "audit_job_id": spec["id"],
                    "created_at": created,
                    "updated_at": created,
                },
            }
        )
    repo = get_batch_repository()
    repo.create_bundle(
        {
            "id": batch_id,
            "workspace_id": workspace,
            "assistant_id": assistant_id,
            "naming_rule_file_id": resolved_naming,
            "mode": BATCH_MODE_NIGHT,
            "status": BATCH_STATUS_SCHEDULED,
            "scheduled_at": scheduled,
            "max_concurrency": BATCH_MAX_CONCURRENCY,
            "created_by": current_user.get_current_user().user_id,
            "created_at": created,
            "updated_at": created,
        },
        children,
    )
    created_jobs = [
        jobs.get_job(child["job"]["id"], workspace_id_value=workspace)
        for child in children
    ]

    observability.metrics.increment("audit_batches_created_total", mode=BATCH_MODE_NIGHT)
    observability.metrics.increment("audit_batch_items_total", amount=len(ids))
    refreshed = refresh_batch_status(batch_id, workspace_id=workspace) or {}
    return {
        "id": batch_id,
        "mode": BATCH_MODE_NIGHT,
        "status": refreshed.get("status") or BATCH_STATUS_SCHEDULED,
        "scheduled_at": scheduled,
        "max_concurrency": BATCH_MAX_CONCURRENCY,
        "assistant_id": assistant_id,
        "naming_rule_file_id": resolved_naming,
        "total": len(ids),
        "jobs": created_jobs,
    }


def get_batch_detail(batch_id: str) -> dict[str, Any]:
    workspace = _workspace_id()
    batch = refresh_batch_status(batch_id, workspace_id=workspace)
    if batch is None:
        raise KeyError("batch not found")
    repo = get_batch_repository()
    items = repo.list_items(batch_id, workspace_id=workspace)
    jobs_by_id = {
        str(job.get("id")): job
        for job in _load_child_jobs(items, workspace_id=workspace)
    }
    now = jobs.utc_now()
    child_jobs = [jobs_by_id[str(item["audit_job_id"])] for item in items if str(item.get("audit_job_id")) in jobs_by_id]
    counts = _count_buckets(child_jobs, now=now)
    total = len(items)
    finished = counts["completed"] + counts["failed"]
    usage = compact_usage_summary(
        get_usage_repository().get_batch_usage_summary(
            workspace_id=workspace, batch_id=batch_id
        )
    )
    return {
        "id": batch["id"],
        "workspace_id": workspace,
        "assistant_id": batch.get("assistant_id"),
        "naming_rule_file_id": batch.get("naming_rule_file_id"),
        "mode": batch.get("mode") or BATCH_MODE_NIGHT,
        "status": batch.get("status"),
        "scheduled_at": batch.get("scheduled_at"),
        "max_concurrency": int(batch.get("max_concurrency") or BATCH_MAX_CONCURRENCY),
        "created_by": batch.get("created_by"),
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "started_at": batch.get("started_at"),
        "finished_at": batch.get("finished_at"),
        "total": total,
        "scheduled": counts["scheduled"],
        "queued": counts["queued"],
        "running": counts["running"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "progress": _progress(finished, total),
        "usage": usage,
        "items": [
            _item_view(item, jobs_by_id.get(str(item.get("audit_job_id") or "")))
            for item in items
        ],
    }


def list_batches(*, limit: int = 50) -> list[dict[str, Any]]:
    workspace = _workspace_id()
    repo = get_batch_repository()
    batches = repo.list_batches(workspace_id=workspace, limit=limit)
    summaries: list[dict[str, Any]] = []
    usage_by_batch = get_usage_repository().get_batch_usage_summaries(
        workspace_id=workspace,
        batch_ids=[str(batch["id"]) for batch in batches],
    )
    for batch in batches:
        batch_id = str(batch["id"])
        refreshed = refresh_batch_status(batch_id, workspace_id=workspace) or batch
        items = repo.list_items(batch_id, workspace_id=workspace)
        child_jobs = _load_child_jobs(items, workspace_id=workspace)
        counts = _count_buckets(child_jobs, now=jobs.utc_now())
        usage = compact_usage_summary(usage_by_batch.get(batch_id) or {})
        summaries.append(
            {
                "id": batch_id,
                "status": refreshed.get("status"),
                "scheduled_at": refreshed.get("scheduled_at"),
                "created_at": refreshed.get("created_at"),
                "mode": refreshed.get("mode") or BATCH_MODE_NIGHT,
                "total": len(items),
                "completed": counts["completed"],
                "failed": counts["failed"],
                "total_tokens": usage.get("total_tokens") or 0,
                "known_cost_microunits": usage.get("known_cost_microunits") or 0,
                "cost_microunits": usage.get("cost_microunits"),
                "cost_complete": bool(usage.get("cost_complete")),
            }
        )
    return summaries
