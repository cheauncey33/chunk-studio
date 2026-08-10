"""Dense embedding build trigger and readiness status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import current_user, db, embeddings
from .. import jobs as job_service

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.get("/status")
def embeddings_status() -> dict[str, Any]:
    approved = db.get_conn().execute(
        "SELECT COUNT(*) FROM chunks WHERE status='approved' AND workspace_id=?",
        (current_user.get_current_user().workspace_id,),
    ).fetchone()[0]
    pending = len(
        embeddings.pending_documents(
            model=embeddings.DEFAULT_MODEL,
            dimension=embeddings.DEFAULT_DIMENSION,
            workspace_id=current_user.get_current_user().workspace_id,
        )
    )
    latest = job_service.list_jobs(type_="embed", limit=1)
    return {
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "approved_chunks": approved,
        "pending_chunks": pending,
        "embedded_chunks": approved - pending,
        "latest_job": latest[0] if latest else None,
    }


@router.post("/build")
def build_embeddings() -> dict[str, Any]:
    return job_service.enqueue_build_embeddings()
