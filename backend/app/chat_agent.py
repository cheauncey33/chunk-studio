"""Pi sidecar client for knowledge-base Agent conversations."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from . import (
    business_analytics,
    config,
    db,
    llm,
    mcp_client,
    observability,
    retrieval,
)
from .agent_runtime.models import ToolDefinition
from .tool_registry import ToolContext, ToolFactory, ToolRegistry


_KNOWLEDGE_ABSTAIN = "知识库中没有检索到足够证据，暂时无法可靠回答。"
_SUMMARY_PREFIX = "Conversation summary from earlier turns:"
_RECENT_TEXT_TURNS = 8


def _citation(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": hit.get("chunk_id"),
        "file_id": hit.get("file_id") or hit.get("doc_id"),
        "file_name": hit.get("file_name") or hit.get("doc_id"),
        "page": hit.get("page"),
        "score": hit.get("rerank_score")
        if hit.get("rerank_score") is not None
        else hit.get("score"),
        "snippet": str(hit.get("text") or hit.get("content") or "")[:240],
    }


def _require_question(arguments: dict[str, Any]) -> None:
    question = str(arguments.get("question") or arguments.get("query") or "").strip()
    if not question:
        raise ValueError("question must not be blank")
    if len(question) > 2000:
        raise ValueError("question is too long")


def _knowledge_base_tools(context: ToolContext) -> list[ToolDefinition]:
    if not context.file_ids:
        return []
    retrieval_config = retrieval.normalize_retrieval_config(context.retrieval_config)
    top_k = max(1, min(int(retrieval_config.get("top_k") or 10), 20))
    route_top_k = max(1, min(int(retrieval_config.get("route_top_k") or 30), 60))
    candidates_per_type = max(
        1, min(int(retrieval_config.get("candidate_count_per_type") or 20), 50)
    )
    dense_threshold = float(retrieval_config.get("dense_threshold") or 0.0)
    rerank_threshold = float(
        retrieval_config.get("rerank_threshold")
        if retrieval_config.get("rerank_threshold") is not None
        else retrieval.DEFAULT_RERANK_THRESHOLD
    )

    def search(arguments: dict[str, Any]) -> dict[str, Any]:
        requested_query = str(arguments.get("query") or "").strip()
        # Host leftover path: production query stays the current user question.
        # Live Agent chat search runs in the sidecar, not here.
        query = context.current_question.strip() or requested_query
        _require_question({"query": query})
        result = retrieval.hybrid_search(
            query,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=candidates_per_type,
            dense_threshold=dense_threshold,
            rerank_threshold=rerank_threshold,
            query_routes={"production": query},
            aggregate_continuation_tables=bool(
                retrieval_config.get("aggregate_continuation_tables", False)
            ),
            expand_references=bool(retrieval_config.get("expand_references", False)),
            file_ids=context.file_ids,
            workspace_id=context.workspace_id,
        )
        hits = list(result.get("hits") or [])[:top_k]
        bounded_hits = []
        for hit in hits:
            bounded_hits.append({
                "file_name": hit.get("file_name") or hit.get("doc_id"),
                "file_id": hit.get("file_id") or hit.get("doc_id"),
                "page": hit.get("page"),
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("rerank_score")
                if hit.get("rerank_score") is not None
                else hit.get("score"),
                "text": str(hit.get("text") or hit.get("content") or "")[:1200],
            })
        return {
            "summary": f"found {len(bounded_hits)} evidence hits",
            "query": query,
            "hits": bounded_hits,
            "citations": [_citation(hit) for hit in hits],
            "retrieval_trace": {
                "dense_threshold": result.get("dense_threshold", dense_threshold),
                "rerank_threshold": result.get("rerank_threshold", rerank_threshold),
                "route_top_k": route_top_k,
                "candidates_per_type": candidates_per_type,
                "candidate_count": result.get("candidate_count"),
                "retrieval_mode": result.get("retrieval_mode"),
                "rerank_model": result.get("rerank_model"),
                "query_routes": result.get("query_routes") or {},
            },
            "fixed_judge": result.get("fixed_judge") or {},
            "degraded": list(result.get("degraded") or []),
        }

    return [
        ToolDefinition(
            name="search_knowledge_base",
            description=(
                "Search the assistant's bound knowledge-base files and return "
                "bounded evidence snippets with file/page citations."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
            execute=search,
            category="search",
            validate=_require_question,
        ),
    ]


def _business_tools(context: ToolContext) -> list[ToolDefinition]:
    def query_business(arguments: dict[str, Any]) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        _require_question({"question": question})
        result = business_analytics.query_business_data(
            question,
            source=db.get_conn(),
            reports_dir=config.DATA_DIR / "reports",
            model=context.model,
        )
        return {
            **result,
            "rows": list(result.get("rows") or [])[:50],
            "summary": str(result.get("answer") or "business query completed"),
        }

    def business_schema(_arguments: dict[str, Any]) -> dict[str, Any]:
        return {"schema": business_analytics.describe_business_schema()}

    def business_overview(_arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = business_analytics.build_business_snapshot(
            source=db.get_conn(),
            reports_dir=config.DATA_DIR / "reports",
        )
        try:
            return business_analytics.get_business_overview(snapshot)
        finally:
            snapshot.close()

    empty_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return [
        ToolDefinition(
            name="query_business_data",
            description=(
                "Run a read-only business analytics/Text2SQL query over the "
                "curated workspace schema and optionally produce a chart spec."
            ),
            input_schema={
                "type": "object",
                "properties": {"question": {"type": "string", "minLength": 1}},
                "required": ["question"],
                "additionalProperties": False,
            },
            execute=query_business,
            category="read",
            validate=_require_question,
        ),
        ToolDefinition(
            name="get_business_schema",
            description="Describe the curated read-only analytics schema and chart types.",
            input_schema=empty_schema,
            execute=business_schema,
            category="read",
        ),
        ToolDefinition(
            name="get_business_overview",
            description="Read current workspace metrics and audit status distribution.",
            input_schema=empty_schema,
            execute=business_overview,
            category="read",
        ),
    ]


tool_registry = ToolRegistry()
tool_registry.register("knowledge_base", _knowledge_base_tools)
tool_registry.register("business_analytics", _business_tools)
tool_registry.register("mcp", mcp_client.build_mcp_tools)


def register_chat_tool_factory(
    name: str,
    factory: ToolFactory,
    *,
    replace: bool = False,
) -> None:
    """Register another scoped tool factory for all Agent chat requests."""
    tool_registry.register(name, factory, replace=replace)


def _build_tools(
    *,
    assistant_id: str,
    file_ids: list[str],
    retrieval_config: dict[str, Any],
    model: str,
    current_question: str = "",
    workspace_id: str | None = None,
) -> list[ToolDefinition]:
    return tool_registry.build(ToolContext(
        assistant_id=assistant_id,
        file_ids=file_ids,
        retrieval_config=retrieval_config,
        model=model,
        current_question=current_question,
        workspace_id=workspace_id,
    ))


def _latest_user_question(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return " ".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ).strip()
        return str(content or "").strip()
    return ""


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    return str(content or "").strip()


def pack_chat_turn(
    messages: list[dict[str, Any]],
    *,
    current_question: str | None = None,
) -> dict[str, Any]:
    """Pack summary + recent user/assistant text for one sidecar turn."""
    question = (current_question or _latest_user_question(messages)).strip()
    summary = ""
    recent: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "")
        text = _message_text(message)
        if role == "system" and text.startswith(_SUMMARY_PREFIX):
            summary = text[len(_SUMMARY_PREFIX):].strip()
            continue
        if role == "tool":
            continue
        if role in {"user", "assistant"} and text:
            recent.append({"role": role, "content": text[:4000]})
    if recent and recent[-1]["role"] == "user" and recent[-1]["content"] == question:
        recent = recent[:-1]
    return {
        "conversation_summary": summary,
        "recent_turns": recent[-_RECENT_TEXT_TURNS:],
        "current_question": question,
    }


def _looks_like_abstain(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "没有检索到足够证据",
            "无法可靠回答",
            "知识库中没有",
            "未绑定知识库",
            "无法检索文档",
        )
    )


def _call_chat_sidecar(
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """POST one Q&A turn to the Pi sidecar; raise on transport/5xx errors."""
    base_url = str(config.AGENT_SIDECAR_URL or "").rstrip("/")
    token = str(config.AGENT_SIDECAR_TOKEN or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(
            f"{base_url}/chat/turn",
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"agent sidecar unreachable at {base_url} "
            f"(start it: cd services/pi-audit-sidecar && npm start): {exc}"
        ) from exc
    if response.status_code >= 500:
        raise RuntimeError(
            f"agent sidecar 5xx ({response.status_code}): {response.text[:300]}"
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"agent sidecar rejected request ({response.status_code}): {response.text[:300]}"
        )
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"agent sidecar error: {str(body.get('error'))[:300]}")
    return body


_BUSINESS_INTENT_MARKERS = (
    "数据库", "sql", "text2sql", "业务数据", "工作区", "统计", "分布", "占比",
    "饼图", "柱状图", "条形图", "schema", "字段", "多少个知识库",
    "审查记录", "审查状态", "按检测项目",
)
_KNOWLEDGE_INTENT_MARKERS = (
    "标准", "规范", "条款", "要求", "定义", "参数", "额定容量", "爬电",
    "试验", "标注", "表格", "空气间隙", "外绝缘", "gb/t", "gbt", "第几页", "依据哪一份",
)


def _tool_intent(question: str) -> str:
    normalized = question.casefold()
    business = any(marker in normalized for marker in _BUSINESS_INTENT_MARKERS)
    knowledge = any(marker in normalized for marker in _KNOWLEDGE_INTENT_MARKERS)
    if business and not knowledge:
        return "business_only"
    if knowledge and not business:
        return "knowledge_only"
    return "mixed"


def _select_tools(tools: list[ToolDefinition], question: str) -> list[ToolDefinition]:
    """Apply a host-side intent gate before exposing tools to the model."""
    intent = _tool_intent(question)
    if intent == "business_only":
        return [tool for tool in tools if tool.name != "search_knowledge_base"]
    if intent == "knowledge_only":
        knowledge_tools = [tool for tool in tools if tool.name == "search_knowledge_base"]
        return knowledge_tools or tools
    return tools


def summarize_context(
    existing_summary: str,
    messages: list[dict[str, Any]],
    *,
    model: str,
) -> str:
    """Compress older native messages into a bounded, model-readable summary."""
    transcript = "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))[:1600]
        for message in messages
    )[:12000]
    if not transcript:
        return existing_summary.strip()
    prompt = (
        "Summarize this conversation for a future knowledge-base Agent turn. "
        "Keep user goals, confirmed document facts, citations, business query "
        "results, unresolved questions, and important constraints. Do not add "
        "facts. Return plain text under 500 words.\n\n"
        f"Existing summary:\n{existing_summary.strip()[:5000]}\n\n"
        f"New transcript:\n{transcript}"
    )
    return llm.chat_text(
        [{"role": "system", "content": "You compact Agent context faithfully."},
         {"role": "user", "content": prompt}],
        model=model,
        temperature=0,
        timeout=90,
    ).strip()[:6000]


def _emit(event_sink: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if event_sink:
        event_sink(event)


def run_chat_agent(
    *,
    assistant_id: str,
    messages: list[dict[str, Any]],
    file_ids: list[str],
    retrieval_config: dict[str, Any],
    model: str,
    temperature: float = 0,
    max_turns: int = 6,
    max_tool_calls: int = 6,
    max_search_calls: int = 3,
    timeout_seconds: float = 120,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    stream_tokens: bool = False,
    workspace_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Run one Q&A turn via the Pi sidecar; Python owns compact/SSE persistence."""
    del temperature, max_turns, max_tool_calls, max_search_calls
    packed = pack_chat_turn(messages)
    current_question = packed["current_question"]
    tool_intent = _tool_intent(current_question)
    started = time.monotonic()
    first_token_ms: float | None = None
    node_timings: list[dict[str, Any]] = []

    def record_node(
        node: str,
        node_started: float,
        *,
        status: str = "ok",
        **details: Any,
    ) -> None:
        duration_ms = round((time.monotonic() - node_started) * 1000, 3)
        timing = {"node": node, "duration_ms": duration_ms, "status": status, **details}
        node_timings.append(timing)
        observability.metrics.observe(
            "chunk_studio_stage_duration_seconds",
            duration_ms / 1000,
            component="chat_agent",
            stage=node,
            status=status,
        )
        _emit(event_sink, {"type": "node_timing", **timing})

    def record_run(stop_reason: str) -> float:
        total_ms = round((time.monotonic() - started) * 1000, 3)
        observability.metrics.observe(
            "chunk_studio_agent_duration_seconds",
            total_ms / 1000,
            model=model,
            stop_reason=stop_reason,
        )
        observability.metrics.increment(
            "chunk_studio_agent_runs_total",
            model=model,
            stop_reason=stop_reason,
        )
        return total_ms

    def finish(value: dict[str, Any]) -> dict[str, Any]:
        total_ms = record_run(str(value.get("stop_reason") or "unknown"))
        value["performance"] = {
            "total_ms": total_ms,
            "ttft_ms": first_token_ms,
            "nodes": node_timings,
        }
        return value

    retrieval_config = retrieval.normalize_retrieval_config(retrieval_config)
    payload = {
        "conversation_id": conversation_id or "",
        "assistant_id": assistant_id,
        "current_question": current_question,
        "conversation_summary": packed["conversation_summary"],
        "recent_turns": packed["recent_turns"],
        "file_ids": list(file_ids or []),
        "workspace_id": workspace_id or "",
        "tool_intent": tool_intent,
        "retrieval_config": {"top_k": retrieval_config.get("top_k") or 8},
    }
    _emit(event_sink, {"type": "turn_started", "turn": 1})
    sidecar_started = time.monotonic()
    try:
        body = _call_chat_sidecar(payload, timeout_seconds=timeout_seconds)
    except Exception:
        record_node("sidecar", sidecar_started, status="error")
        record_run("error")
        raise
    record_node("sidecar", sidecar_started, status="ok")
    stats = body.get("stats") if isinstance(body.get("stats"), dict) else {}
    citations = [
        item for item in (body.get("citations") or []) if isinstance(item, dict)
    ]
    charts = [item for item in (body.get("charts") or []) if isinstance(item, dict)]
    answer = str(body.get("answer") or "").strip()
    search_calls = int(stats.get("search_calls") or 0)
    read_chunks = int(stats.get("read_chunks") or 0)
    grounded = bool(stats.get("knowledge_grounded")) or read_chunks > 0
    if search_calls and not grounded and not charts and not _looks_like_abstain(answer):
        answer = _KNOWLEDGE_ABSTAIN
    elif not answer:
        answer = "The model returned no displayable answer."
    assistant_message = {"role": "assistant", "content": answer}
    first_token_ms = round((time.monotonic() - started) * 1000, 3)
    observability.metrics.observe(
        "chunk_studio_agent_ttft_seconds",
        first_token_ms / 1000,
        model=model,
    )
    _emit(event_sink, {"type": "first_token", "ttft_ms": first_token_ms})
    if stream_tokens:
        _emit(event_sink, {"type": "token", "content": answer})
    _emit(event_sink, {
        "type": "assistant_message",
        "turn": int(stats.get("turns") or 1),
        "message": assistant_message,
    })
    return finish({
        "answer": answer,
        "stop_reason": "finished",
        "new_messages": [assistant_message],
        "tool_events": [],
        "citations": citations,
        "charts": charts,
        "turns": int(stats.get("turns") or 1),
        "tool_calls": int(stats.get("tool_calls") or 0),
        "stats": stats,
        "trace_file": body.get("trace_file"),
    })
