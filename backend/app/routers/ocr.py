"""OCR queue endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import jobs as job_service
from ..routers.chunks import _get_chunk

router = APIRouter(prefix="/ocr", tags=["ocr"])


@router.post("/bulk")
def enqueue_bulk_ocr(
    file_id: str,
    page: int | None = None,
    pending_only: bool = True,
):
    jobs = job_service.enqueue_ocr_for_file(
        file_id,
        page=page,
        pending_only=pending_only,
    )
    return {"queued": len(jobs), "jobs": jobs}


@router.post("/{chunk_id}")
def enqueue_ocr_chunk(chunk_id: str, force: bool = False):
    _get_chunk(chunk_id)
    try:
        job_service.enqueue_ocr_chunk(chunk_id, force=force)
    except KeyError:
        raise HTTPException(404, "chunk not found")
    return _get_chunk(chunk_id)
