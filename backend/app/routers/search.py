"""Hybrid retrieval API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from .. import audit_run, config, current_user, db, lexical, llm_usage, retrieval
from ..audit_semantics import bind_table_row
from ..llm_usage import UsageContext
from ..models import VectorSearchRequest, VectorSearchResponse
from ..report_context import search_report_markdown
from ..storage.repositories import get_content_repository, get_content_write_repository
from ..storage.usage_repository import get_usage_repository


router = APIRouter(prefix="/search", tags=["search"])


class ReportContextSearchRequest(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=8)
    max_results: int = 8
    report_file_id: str = Field(min_length=1)
    job_id: str | None = None


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


def _workspace_for_report_search(authorization: str | None, job_id: str | None) -> str:
    if _sidecar_authorized(authorization) and str(job_id or "").strip():
        workspace_id = get_usage_repository().lookup_job_workspace(str(job_id).strip())
        if workspace_id:
            return str(workspace_id)
    return current_user.get_current_user().workspace_id


def _report_file_in_workspace(file_id: str, workspace_id: str) -> bool:
    wanted = str(file_id or "").strip()
    if not wanted:
        return False
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        row = repository.get_file(wanted)
        if not row:
            return False
        file_ws = str(row.get("workspace_id") or "").strip()
        return not file_ws or file_ws == workspace_id
    row = db.get_conn().execute(
        "SELECT id FROM files WHERE id=? AND workspace_id=?",
        (wanted, workspace_id),
    ).fetchone()
    return row is not None


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


@router.post("/report-context")
def search_report_context(
    body: ReportContextSearchRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Literal windows from the current inspection-report markdown."""
    workspace_id = _workspace_for_report_search(authorization, body.job_id)
    report_file_id = str(body.report_file_id or "").strip()
    if not _report_file_in_workspace(report_file_id, workspace_id):
        raise HTTPException(404, f"report file not found: {report_file_id}")
    try:
        markdown = audit_run.resolve_markdown_path(report_file_id)
        return search_report_markdown(
            markdown.read_text(encoding="utf-8"),
            list(body.terms),
            max_results=body.max_results,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("", response_model=VectorSearchResponse)
def search_chunks(
    body: VectorSearchRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    try:
        workspace_id = None
        if _sidecar_authorized(authorization):
            requested = str(body.workspace_id or "").strip()
            if requested:
                workspace_id = requested
            elif body.job_id:
                workspace_id = get_usage_repository().lookup_job_workspace(
                    str(body.job_id).strip()
                )
        result = retrieval.hybrid_search(
            body.query,
            top_k=body.top_k,
            file_ids=body.file_ids or None,
            query_routes=body.query_routes or None,
            workspace_id=workspace_id,
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
