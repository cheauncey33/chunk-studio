"""LLM usage events: normalization, context, and best-effort ledger writes.

Usage events are the source of truth. Job/case/stage totals are SUM() over
this ledger. Retry creates new events; it never overwrites earlier attempts.

Token field convention
----------------------
When ``usage_source`` is ``provider`` or ``sdk`` and a provider omitted an
optional counter (reasoning / cache), store **0**. The request completed and
the field is known-absent, not unknown.

When ``usage_source`` is ``unknown``, store **NULL** for every token field
and for ``cost_microunits``. Do not invent tokens from character counts.

``total_tokens`` prefers the provider/SDK total. If that is missing but
input and output are both known, ``total = input + output``. Reasoning is
**not** added: Zhipu / Qwen / OpenAI-compatible APIs typically already fold
reasoning into completion/output tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import uuid
from typing import Any

from . import llm_pricing, observability
from .storage.usage_repository import UsageEvent, get_usage_repository


logger = logging.getLogger(__name__)

USAGE_SOURCE_PROVIDER = "provider"
USAGE_SOURCE_SDK = "sdk"
USAGE_SOURCE_UNKNOWN = "unknown"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

STAGE_REPORT_PARAMETERS = "report_parameters"
STAGE_TEST_ITEMS = "test_items"
STAGE_MODEL_DECODE = "model_decode"
STAGE_AUDIT_AGENT = "audit_agent"
STAGE_QUERY_REWRITE = "query_rewrite"
STAGE_RERANK = "rerank"
STAGE_OTHER = "other"

KNOWN_STAGES = frozenset(
    {
        STAGE_REPORT_PARAMETERS,
        STAGE_TEST_ITEMS,
        STAGE_MODEL_DECODE,
        STAGE_AUDIT_AGENT,
        STAGE_QUERY_REWRITE,
        STAGE_RERANK,
        STAGE_OTHER,
    }
)


@dataclass(frozen=True)
class UsageContext:
    """Explicit metering identity. Pass this across ThreadPoolExecutor workers.

    contextvars are not used: ThreadPoolExecutor does not copy them unless a
    copy_context wrapper is installed, which this codebase does not do.
    """

    workspace_id: str = ""
    job_id: str = ""
    run_id: str = ""
    case_id: str = ""
    job_attempt: int | None = None
    request_attempt: int = 1
    stage: str = STAGE_OTHER
    provider: str = ""
    model: str = ""


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    total_tokens: int | None
    usage_source: str
    raw: dict[str, Any] = field(default_factory=dict)


def new_request_id() -> str:
    return str(uuid.uuid4())


def normalize_stage(value: Any) -> str:
    stage = str(value or "").strip() or STAGE_OTHER
    return stage if stage in KNOWN_STAGES else STAGE_OTHER


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _details(container: Any, *names: str) -> dict[str, Any]:
    if not isinstance(container, dict):
        return {}
    for name in names:
        nested = container.get(name)
        if isinstance(nested, dict):
            return nested
    return {}


def _present_or_zero(value: int | None, *, known: bool) -> int | None:
    if not known:
        return None
    return 0 if value is None else value


def _total_tokens(
    *,
    reported: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    known: bool,
) -> int | None:
    if reported is not None:
        return reported
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens
    if not known:
        return None
    return None


def unknown_usage() -> NormalizedUsage:
    return NormalizedUsage(
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        total_tokens=None,
        usage_source=USAGE_SOURCE_UNKNOWN,
        raw={},
    )


def normalize_openai_usage(response_json: Any) -> NormalizedUsage:
    """Normalize an OpenAI-compatible chat.completions JSON body.

    Known shapes:
    - OpenAI / DeepSeek: ``usage.prompt_tokens`` / ``completion_tokens`` /
      ``total_tokens``
    - Zhipu GLM / DashScope Qwen: the same, plus
      ``prompt_tokens_details.cached_tokens`` and
      ``completion_tokens_details.reasoning_tokens``
    """
    payload = response_json if isinstance(response_json, dict) else {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return unknown_usage()
    input_tokens = _int_or_none(
        usage.get("prompt_tokens", usage.get("input_tokens"))
    )
    output_tokens = _int_or_none(
        usage.get("completion_tokens", usage.get("output_tokens"))
    )
    if input_tokens is None and output_tokens is None and usage.get("total_tokens") is None:
        return NormalizedUsage(
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            total_tokens=_int_or_none(usage.get("total_tokens")),
            usage_source=USAGE_SOURCE_UNKNOWN,
            raw=dict(usage),
        )
    prompt_details = _details(
        usage, "prompt_tokens_details", "input_tokens_details", "prompt_details"
    )
    completion_details = _details(
        usage,
        "completion_tokens_details",
        "output_tokens_details",
        "completion_details",
    )
    reasoning = _int_or_none(
        completion_details.get("reasoning_tokens", usage.get("reasoning_tokens"))
    )
    cache_read = _int_or_none(
        prompt_details.get(
            "cached_tokens",
            prompt_details.get("cache_read_tokens", usage.get("cache_read_tokens")),
        )
    )
    cache_write = _int_or_none(
        prompt_details.get(
            "cache_write_tokens",
            prompt_details.get(
                "cache_creation_tokens",
                usage.get("cache_write_tokens", usage.get("cache_creation_input_tokens")),
            ),
        )
    )
    known = True
    return NormalizedUsage(
        input_tokens=_present_or_zero(input_tokens, known=known),
        output_tokens=_present_or_zero(output_tokens, known=known),
        reasoning_tokens=_present_or_zero(reasoning, known=known),
        cache_read_tokens=_present_or_zero(cache_read, known=known),
        cache_write_tokens=_present_or_zero(cache_write, known=known),
        total_tokens=_total_tokens(
            reported=_int_or_none(usage.get("total_tokens")),
            input_tokens=_present_or_zero(input_tokens, known=known),
            output_tokens=_present_or_zero(output_tokens, known=known),
            known=known,
        ),
        usage_source=USAGE_SOURCE_PROVIDER,
        raw=dict(usage),
    )


def normalize_pi_sdk_usage(message_or_usage: Any) -> NormalizedUsage:
    """Normalize Pi coding-agent assistant usage.

    Real fields (pi-ai ``Usage``, copied onto ``message.usage`` and summed by
    ``@earendil-works/pi-coding-agent`` ``usage-totals`` / ``compaction``):

    - ``input``
    - ``output``
    - ``cacheRead``
    - ``cacheWrite``
    - ``reasoning`` (optional)
    - ``totalTokens`` (optional; native total when the provider supplies it)
    - ``cost`` (SDK-estimated dollars from its own rate table — ignored here)

    Collected from ``message_end`` → ``event.message.usage`` when
    ``event.message.role === "assistant"``.
    """
    payload = message_or_usage
    if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
        payload = payload["usage"]
    if not isinstance(payload, dict):
        return unknown_usage()
    input_tokens = _int_or_none(payload.get("input", payload.get("input_tokens")))
    output_tokens = _int_or_none(payload.get("output", payload.get("output_tokens")))
    if (
        input_tokens is None
        and output_tokens is None
        and payload.get("totalTokens") is None
        and payload.get("total_tokens") is None
    ):
        return unknown_usage()
    known = True
    reasoning = _int_or_none(payload.get("reasoning", payload.get("reasoning_tokens")))
    cache_read = _int_or_none(
        payload.get("cacheRead", payload.get("cache_read_tokens"))
    )
    cache_write = _int_or_none(
        payload.get("cacheWrite", payload.get("cache_write_tokens"))
    )
    reported_total = _int_or_none(
        payload.get("totalTokens", payload.get("total_tokens"))
    )
    return NormalizedUsage(
        input_tokens=_present_or_zero(input_tokens, known=known),
        output_tokens=_present_or_zero(output_tokens, known=known),
        reasoning_tokens=_present_or_zero(reasoning, known=known),
        cache_read_tokens=_present_or_zero(cache_read, known=known),
        cache_write_tokens=_present_or_zero(cache_write, known=known),
        total_tokens=_total_tokens(
            reported=reported_total,
            input_tokens=_present_or_zero(input_tokens, known=known),
            output_tokens=_present_or_zero(output_tokens, known=known),
            known=known,
        ),
        usage_source=USAGE_SOURCE_SDK,
        raw=dict(payload),
    )


def price_usage(usage: NormalizedUsage, *, provider: str, model: str) -> tuple[int | None, bool, dict[str, Any] | None]:
    """Return (cost_microunits, pricing_missing, snapshot).

    ``pricing_missing`` means the model has no unit price. Unknown token
    usage (timeout, missing provider counters) does **not** imply missing
    prices: those events are ``usage_source=unknown`` with cost NULL.
    """
    price = llm_pricing.lookup_price(provider, model)
    missing = price is None
    snapshot = price.snapshot() if price is not None else None
    if usage.usage_source == USAGE_SOURCE_UNKNOWN:
        return None, missing, snapshot
    if missing:
        return None, True, None
    try:
        cost = llm_pricing.compute_cost_microunits(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            price=price,
            usage_source=usage.usage_source,
        )
    except Exception:
        logger.exception("llm pricing calculation failed for %s/%s", provider, model)
        return None, True, None
    return cost, False, snapshot


def _metric_labels(event: UsageEvent) -> dict[str, str]:
    return {
        "provider": str(event.provider or "unknown")[:80],
        "model": str(event.model or "unknown")[:80],
        "stage": normalize_stage(event.stage),
        "status": str(event.status or STATUS_SUCCESS)[:32],
    }


def _observe_recorded(event: UsageEvent) -> None:
    labels = _metric_labels(event)
    observability.metrics.increment("llm_requests_total", **labels)
    if event.input_tokens:
        observability.metrics.increment(
            "llm_input_tokens_total", event.input_tokens, **labels
        )
    if event.output_tokens:
        observability.metrics.increment(
            "llm_output_tokens_total", event.output_tokens, **labels
        )
    if event.total_tokens:
        observability.metrics.increment(
            "llm_tokens_total", event.total_tokens, **labels
        )
    if event.cost_microunits:
        observability.metrics.increment(
            "llm_cost_microunits_total", event.cost_microunits, **labels
        )


def build_usage_event(
    *,
    context: UsageContext,
    request_id: str,
    usage: NormalizedUsage,
    status: str = STATUS_SUCCESS,
    provider: str = "",
    model: str = "",
) -> UsageEvent:
    provider_id = str(provider or context.provider or "").strip()
    model_id = str(model or context.model or "").strip()
    cost, missing, snapshot = price_usage(
        usage, provider=provider_id, model=model_id
    )
    return UsageEvent(
        id=str(uuid.uuid4()),
        workspace_id=str(context.workspace_id or "").strip(),
        job_id=str(context.job_id or "").strip() or None,
        run_id=str(context.run_id or "").strip() or None,
        case_id=str(context.case_id or "").strip() or None,
        job_attempt=context.job_attempt,
        request_attempt=max(1, int(context.request_attempt or 1)),
        stage=normalize_stage(context.stage),
        provider=provider_id,
        model=model_id,
        request_id=str(request_id).strip(),
        status=str(status or STATUS_SUCCESS).strip() or STATUS_SUCCESS,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        total_tokens=usage.total_tokens,
        cost_microunits=cost,
        pricing_missing=bool(missing),
        pricing_snapshot=snapshot,
        usage_source=usage.usage_source,
    )


def record_usage_event(event: UsageEvent) -> bool:
    """Insert one ledger row. Never raises — metering must not fail audits."""
    try:
        if not event.request_id:
            raise ValueError("request_id is required")
        inserted = get_usage_repository().record_usage(event)
        if inserted:
            _observe_recorded(event)
        return inserted
    except Exception:
        logger.exception(
            "llm usage record failed request_id=%s job_id=%s case_id=%s",
            event.request_id,
            event.job_id,
            event.case_id,
        )
        try:
            observability.metrics.increment(
                "llm_usage_record_failures_total",
                **_metric_labels(event),
            )
        except Exception:
            pass
        return False


def record_normalized_usage(
    *,
    context: UsageContext | None,
    request_id: str,
    usage: NormalizedUsage,
    status: str = STATUS_SUCCESS,
    provider: str = "",
    model: str = "",
) -> bool:
    if context is None:
        return False
    event = build_usage_event(
        context=context,
        request_id=request_id,
        usage=usage,
        status=status,
        provider=provider,
        model=model,
    )
    return record_usage_event(event)
