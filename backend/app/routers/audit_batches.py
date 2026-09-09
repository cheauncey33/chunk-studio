"""Night Batch orchestration APIs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import audit_batches, jobs
from ..storage.batch_repository import BATCH_MAX_CONCURRENCY, NIGHT_BATCH_MAX_REPORTS


router = APIRouter(prefix="/audit-batches", tags=["audit-batches"])


class AuditBatchCreateRequest(BaseModel):
    assistant_id: str = Field(min_length=1)
    report_file_ids: list[str] = Field(min_length=1, max_length=NIGHT_BATCH_MAX_REPORTS)
    naming_rule_file_id: str | None = None
    scheduled_at: str = Field(
        min_length=1,
        description="Timezone-aware ISO-8601 instant, stored and compared as UTC.",
    )
    max_concurrency: int = Field(
        default=BATCH_MAX_CONCURRENCY,
        ge=1,
        description="Report-level Night Batch slot budget, enforced at job claim.",
    )


@router.post("")
def create_audit_batch(body: AuditBatchCreateRequest):
    try:
        return audit_batches.create_night_batch(
            assistant_id=body.assistant_id,
            report_file_ids=body.report_file_ids,
            naming_rule_file_id=body.naming_rule_file_id,
            scheduled_at=body.scheduled_at,
            max_concurrency=body.max_concurrency,
        )
    except jobs.ActiveAuditConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_audit_batches(limit: int = 50):
    return audit_batches.list_batches(limit=min(max(limit, 1), 200))


@router.get("/{batch_id}")
def get_audit_batch(batch_id: str):
    try:
        return audit_batches.get_batch_detail(batch_id)
    except KeyError:
        raise HTTPException(404, "batch not found")
