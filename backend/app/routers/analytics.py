from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .. import business_analytics, config, current_user, db, llm


router = APIRouter(prefix="/analytics", tags=["analytics"])


class BusinessQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    workspace_id: str | None = None


def _sidecar_authorized(authorization: str | None) -> bool:
    expected = str(config.AGENT_SIDECAR_TOKEN or "").strip()
    return bool(expected) and str(authorization or "").strip() == f"Bearer {expected}"


def _workspace_for_analytics(
    authorization: str | None,
    workspace_id: str | None,
) -> str:
    requested = str(workspace_id or "").strip()
    if _sidecar_authorized(authorization) and requested:
        return requested
    return current_user.get_current_user().workspace_id


@router.get("/schema")
def schema() -> dict[str, Any]:
    return business_analytics.describe_business_schema()


@router.get("/overview")
def overview(
    workspace_id: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    snapshot = business_analytics.build_business_snapshot(
        source=db.get_conn(),
        reports_dir=config.DATA_DIR / "reports",
        workspace_id=_workspace_for_analytics(authorization, workspace_id),
    )
    try:
        return business_analytics.get_business_overview(snapshot)
    finally:
        snapshot.close()


@router.post("/query")
def query(
    body: BusinessQueryRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return business_analytics.query_business_data(
            body.question,
            source=db.get_conn(),
            reports_dir=config.DATA_DIR / "reports",
            model=llm.DEFAULT_MODEL,
            workspace_id=_workspace_for_analytics(authorization, body.workspace_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
