"""Background job inspection endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import current_user, jobs as job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    target_id: str | None = None,
    status: str | None = None,
    type: str | None = None,
    limit: int = 100,
):
    return job_service.list_jobs(
        target_id=target_id,
        status=status,
        type_=type,
        limit=min(max(limit, 1), 500),
    )


@router.get("/{job_id}")
def get_job(job_id: str):
    try:
        return job_service.get_job(
            job_id,
            workspace_id_value=current_user.get_current_user().workspace_id,
        )
    except KeyError:
        raise HTTPException(404, "job not found")
