from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import business_analytics, config, db, llm


router = APIRouter(prefix="/analytics", tags=["analytics"])


class BusinessQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.get("/schema")
def schema() -> dict[str, Any]:
    return business_analytics.describe_business_schema()


@router.get("/overview")
def overview() -> dict[str, Any]:
    snapshot = business_analytics.build_business_snapshot(
        source=db.get_conn(),
        reports_dir=config.DATA_DIR / "reports",
    )
    try:
        return business_analytics.get_business_overview(snapshot)
    finally:
        snapshot.close()


@router.post("/query")
def query(body: BusinessQueryRequest) -> dict[str, Any]:
    try:
        return business_analytics.query_business_data(
            body.question,
            source=db.get_conn(),
            reports_dir=config.DATA_DIR / "reports",
            model=llm.DEFAULT_MODEL,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
