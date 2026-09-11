"""Internal service endpoints. Not part of the user-facing /api surface."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .. import config, llm_usage
from ..storage.usage_repository import get_usage_repository


router = APIRouter(prefix="/internal", tags=["internal"])


class InternalUsageIngest(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    job_id: str | None = None
    run_id: str | None = None
    case_id: str | None = None
    conversation_id: str | None = None
    job_attempt: int | None = None
    request_attempt: int | None = 1
    stage: str | None = None
    provider: str = ""
    model: str = ""
    status: str = llm_usage.STATUS_SUCCESS
    usage_source: str | None = None
    usage: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    # Sidecar-supplied workspace is ignored; resolved from job_id or conversation_id.
    workspace_id: str | None = None


def _require_sidecar_token(authorization: str | None) -> None:
    expected = str(config.AGENT_SIDECAR_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=401, detail="sidecar token is not configured")
    header = str(authorization or "").strip()
    if header != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _normalize_ingest(body: InternalUsageIngest) -> llm_usage.NormalizedUsage:
    source = str(body.usage_source or "").strip().lower()
    raw = body.usage if isinstance(body.usage, dict) else {}
    if source == llm_usage.USAGE_SOURCE_SDK or (
        "input" in raw or "cacheRead" in raw or "totalTokens" in raw
    ):
        usage = llm_usage.normalize_pi_sdk_usage(raw or {
            "input": body.input_tokens,
            "output": body.output_tokens,
            "reasoning": body.reasoning_tokens,
            "cacheRead": body.cache_read_tokens,
            "cacheWrite": body.cache_write_tokens,
            "totalTokens": body.total_tokens,
        })
        if usage.usage_source != llm_usage.USAGE_SOURCE_UNKNOWN:
            return usage
    if raw.get("prompt_tokens") is not None or raw.get("completion_tokens") is not None:
        return llm_usage.normalize_openai_usage({"usage": raw})
    if any(
        value is not None
        for value in (
            body.input_tokens,
            body.output_tokens,
            body.total_tokens,
            raw.get("input_tokens"),
            raw.get("output_tokens"),
            raw.get("total_tokens"),
        )
    ):
        merged = {
            "prompt_tokens": body.input_tokens if body.input_tokens is not None else raw.get("input_tokens"),
            "completion_tokens": body.output_tokens if body.output_tokens is not None else raw.get("output_tokens"),
            "total_tokens": body.total_tokens if body.total_tokens is not None else raw.get("total_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": body.cache_read_tokens
                if body.cache_read_tokens is not None
                else raw.get("cache_read_tokens"),
            },
            "completion_tokens_details": {
                "reasoning_tokens": body.reasoning_tokens
                if body.reasoning_tokens is not None
                else raw.get("reasoning_tokens"),
            },
        }
        if body.cache_write_tokens is not None or raw.get("cache_write_tokens") is not None:
            merged["cache_write_tokens"] = (
                body.cache_write_tokens
                if body.cache_write_tokens is not None
                else raw.get("cache_write_tokens")
            )
        return llm_usage.normalize_openai_usage({"usage": merged})
    return llm_usage.unknown_usage()


@router.post("/llm-usage")
def ingest_llm_usage(
    body: InternalUsageIngest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Idempotent usage ingest for the Pi sidecar. Best-effort from the caller's view.

    Metering errors return 2xx with recorded=false so a sidecar crash-retry
    cannot turn a ledger blip into an audit failure.
    """
    try:
        _require_sidecar_token(authorization)
    except HTTPException:
        raise
    job_id = str(body.job_id or "").strip()
    conversation_id = str(body.conversation_id or "").strip()
    try:
        repository = get_usage_repository()
        workspace_id = repository.lookup_job_workspace(job_id) if job_id else None
        if job_id and not workspace_id:
            return {"ok": True, "recorded": False, "reason": "job_not_found"}
        if not workspace_id and conversation_id:
            workspace_id = repository.lookup_conversation_workspace(conversation_id)
            if not workspace_id:
                return {"ok": True, "recorded": False, "reason": "conversation_not_found"}
        if not workspace_id:
            return {"ok": True, "recorded": False, "reason": "job_id_required"}
        usage = _normalize_ingest(body)
        default_stage = (
            llm_usage.STAGE_CHAT_AGENT if conversation_id and not job_id else llm_usage.STAGE_AUDIT_AGENT
        )
        context = llm_usage.UsageContext(
            workspace_id=workspace_id,
            job_id=job_id,
            run_id=str(body.run_id or "").strip(),
            case_id=str(body.case_id or "").strip(),
            conversation_id=conversation_id,
            job_attempt=body.job_attempt,
            request_attempt=max(1, int(body.request_attempt or 1)),
            stage=llm_usage.normalize_stage(body.stage or default_stage),
            provider=str(body.provider or "").strip(),
            model=str(body.model or "").strip(),
        )
        recorded = llm_usage.record_normalized_usage(
            context=context,
            request_id=str(body.request_id).strip(),
            usage=usage,
            status=str(body.status or llm_usage.STATUS_SUCCESS).strip()
            or llm_usage.STATUS_SUCCESS,
            provider=context.provider,
            model=context.model,
        )
        return {"ok": True, "recorded": recorded}
    except HTTPException:
        raise
    except Exception:
        return {"ok": True, "recorded": False, "reason": "metering_failed"}
