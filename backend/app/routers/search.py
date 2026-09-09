"""Hybrid retrieval API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from .. import config, current_user, lexical, llm_usage, retrieval
from ..audit_semantics import bind_table_row
from ..llm_usage import UsageContext
from ..models import VectorSearchRequest, VectorSearchResponse


router = APIRouter(prefix="/search", tags=["search"])


def _sidecar_authorized(authorization: str | None) -> bool:
    expected = str(config.AGENT_SIDECAR_TOKEN or "").strip()
    return bool(expected) and str(authorization or "").strip() == f"Bearer {expected}"


def _search_usage_context(
    body: VectorSearchRequest,
    authorization: str | None,
) -> UsageContext | None:
    """Meter search only when job identity cannot jump workspaces.

    Ordinary callers must own the job's workspace. A request with a valid
    sidecar token may resolve workspace from ``job_id`` because that secret
    already authorizes service-to-service metering.
    """
    expected_workspace_id = None
    if not _sidecar_authorized(authorization):
        expected_workspace_id = current_user.get_current_user().workspace_id
    return llm_usage.usage_context_from_job(
        job_id=body.job_id,
        run_id=body.run_id,
        case_id=body.case_id,
        job_attempt=body.job_attempt,
        expected_workspace_id=expected_workspace_id,
    )


def _annotate_row_filters(hits: list[dict[str, Any]], row_filter: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach table row bindings to table-type hits when a row filter is provided.

    The hit's ``text`` is the raw HTML table; bind_table_row resolves the exact
    matching row (by capacity / voltage / ...) and returns its column values so
    downstream LLM tools can display concrete cells instead of raw tables.
    """
    for hit in hits or []:
        candidate = {
            **hit,
            # hits use text_source as the storing field? keep text only for binding
            "business_metadata": hit.get("business_metadata") or {},
        }
        binding = bind_table_row(candidate, row_filter)
        if binding:
            hit["row_binding"] = binding
    return hits


@router.post("", response_model=VectorSearchResponse)
def search_chunks(
    body: VectorSearchRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    try:
        result = retrieval.hybrid_search(
            body.query,
            top_k=body.top_k,
            file_ids=body.file_ids or None,
            query_routes=body.query_routes or None,
            usage_context=_search_usage_context(body, authorization),
        )
        if body.row_filter:
            result["hits"] = _annotate_row_filters(result.get("hits") or [], body.row_filter)
        if not lexical.production_enabled() and lexical.shadow_enabled():
            background_tasks.add_task(
                lexical.run_shadow,
                body.query,
                result.get("query_routes") or {},
                [hit["chunk_id"] for hit in result.get("hits") or []],
            )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
