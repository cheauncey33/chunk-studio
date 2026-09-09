"""Claim-time Night Batch concurrency budget.

Report-level limits are enforced in the job claim transaction, not after a
worker already holds a job. Interactive audits are not limited by this budget.
"""
from __future__ import annotations

from typing import Any

from . import config


# Page size for Postgres audit claim scans. Full batches are skipped and the
# next page is read until an eligible job is found or the queue is exhausted.
AUDIT_CLAIM_SCAN_LIMIT = 32
# Session-scoped Postgres lock so two workers cannot both observe running=2
# and claim the last global/batch slot.
AUDIT_CLAIM_ADVISORY_LOCK_KEY = 852774401

_NON_AUDIT_JOB_TYPES = frozenset(
    {"ocr", "parse", "chunk", "embed", "assistant_init"}
)


def non_audit_job_types() -> frozenset[str]:
    return _NON_AUDIT_JOB_TYPES


def claims_audit_jobs(job_types: set[str] | None) -> bool:
    return job_types is None or "audit" in job_types


def count_from_row(row: Any) -> int:
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row.get("n") or 0)
    try:
        return int(row["n"])
    except (KeyError, TypeError, IndexError):
        return int(row[0])


def _placeholder(postgres: bool) -> str:
    return "%s" if postgres else "?"


def running_for_batch_sql(*, postgres: bool) -> str:
    placeholder = _placeholder(postgres)
    return (
        "SELECT COUNT(*) AS n FROM audit_batch_items i "
        "JOIN jobs j ON j.id = i.audit_job_id "
        f"WHERE i.batch_id={placeholder} AND j.status='running' "
        f"AND j.type='audit' AND j.id <> {placeholder}"
    )


def running_batch_global_sql(*, postgres: bool) -> str:
    placeholder = _placeholder(postgres)
    return (
        "SELECT COUNT(*) AS n FROM audit_batch_items i "
        "JOIN jobs j ON j.id = i.audit_job_id "
        "WHERE j.status='running' AND j.type='audit' "
        f"AND j.id <> {placeholder}"
    )


def batch_max_concurrency_sql(*, postgres: bool, for_update: bool = False) -> str:
    sql = f"SELECT max_concurrency FROM audit_batches WHERE id={_placeholder(postgres)}"
    if for_update:
        sql += " FOR UPDATE"
    return sql


def job_batch_id(job: dict[str, Any] | None) -> str:
    payload = job or {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    return str(result.get("batch_id") or payload.get("batch_id") or "").strip()


def is_batch_audit_job(job: dict[str, Any] | None) -> bool:
    payload = job or {}
    return str(payload.get("type") or "") == "audit" and bool(job_batch_id(payload))


def batch_claim_allowed(
    *,
    is_batch: bool,
    running_for_batch: int,
    batch_max: int,
    running_batch_global: int,
) -> bool:
    """Return True when a Night Batch child may be claimed.

    Interactive / non-audit jobs always pass. A batch child needs a free slot
    both on its own ``max_concurrency`` and on the process-wide night-batch cap.
    """
    if not is_batch:
        return True
    limit = max(1, int(batch_max or 1))
    if int(running_for_batch) >= limit:
        return False
    if int(running_batch_global) >= int(config.AUDIT_BATCH_GLOBAL_SLOTS):
        return False
    return True


def validated_batch_max_concurrency(value: int) -> int:
    cap = int(config.AUDIT_BATCH_GLOBAL_SLOTS)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"max_concurrency must be between 1 and {cap}") from exc
    if parsed < 1 or parsed > cap:
        raise ValueError(f"max_concurrency must be between 1 and {cap}")
    return parsed
