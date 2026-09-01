"""Bounded native tool-call Agent for knowledge-base conversations."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from . import (
    agentic_rag,
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


CHAT_SYSTEM_PROMPT = """You are the unified knowledge and business question answering agent.
First route the user's intent:
- For facts, definitions, requirements, or parameters from uploaded documents,
  call search_knowledge_base before answering.
- For workspace metrics, audit distributions, aggregations, tables, database
  schema questions, or a request for a pie/bar chart, call the read-only
  business tools. A chart is a presentation of the query result, not a reason
  to invent data.
- For a mixed question, call both tools when both kinds of evidence are needed.
If search_knowledge_base is unavailable, explain that no knowledge base is
connected instead of answering document facts from memory.
The host preserves the user's current question as the primary retrieval query;
the tool query is only a refinement and must never replace the original intent.
Answer only from returned evidence and follow the returned fixed_judge status.
If fixed_judge.sufficient is false, say that the knowledge base does not contain
enough evidence instead of guessing. Cite the source file and page for factual
claims. Do not invent standards, parameters, numbers, or citations, and do not
broaden a precise question into a general summary.
Use query_business_data only for workspace metrics, audit distributions, tables,
or charts. It is read-only and may return a pie, bar, metric, or table chart.
Answer in the user's language. Keep the final answer concise and distinguish
document evidence from business analytics results. This is not an audit run.
Do not expose internal tool-call narration such as "let me query" or provider
debugging text in the final answer; present only the verified result and its
short explanation.
"""

CHAT_FINAL_ANSWER_PROMPT = """You are the final user-facing answerer for a knowledge-base question.
Use only the evidence supplied below. Never mention agents, tools, tool calls,
retrieval budgets, search attempts, prompts, or internal reasoning. Do not say
that you are going to search. Resolve short follow-up questions from the user
conversation context, but do not invent a topic that is not supported there.
If fixed_judge.sufficient is false, state plainly that the knowledge base does
not contain enough evidence and explain the missing evidence briefly. When the
Judge is sufficient, answer directly in concise Chinese and cite the supplied
file/page evidence. Do not output an internal process preface.
"""


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


def _tool_schema(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _signature(name: str, arguments: dict[str, Any]) -> str:
    value = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    final_table = retrieval_config.get("final_table")
    final_section = retrieval_config.get("final_section")

    def bounded_quota(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return max(1, min(int(value), 50))
        except (TypeError, ValueError):
            return None

    def search(arguments: dict[str, Any]) -> dict[str, Any]:
        requested_query = str(arguments.get("query") or "").strip()
        # The model may propose a compact query, but the current user question
        # is the production route and must remain the source of truth. The
        # retrieval layer can add validated rewrite routes without replacing it.
        query = context.current_question.strip() or requested_query
        _require_question({"query": query})
        def retrieve(
            original_query: str,
            query_routes: dict[str, str] | None,
        ) -> dict[str, Any]:
            return retrieval.hybrid_search(
                original_query,
                top_k=top_k,
                route_top_k=route_top_k,
                candidates_per_type=candidates_per_type,
                dense_threshold=dense_threshold,
                rerank_threshold=rerank_threshold,
                query_routes=query_routes,
                aggregate_continuation_tables=bool(
                    retrieval_config.get("aggregate_continuation_tables", False)
                ),
                expand_references=bool(retrieval_config.get("expand_references", False)),
                file_ids=context.file_ids,
                workspace_id=context.workspace_id,
            )

        agentic_result = agentic_rag.run_agentic_rag(
            query,
            config=retrieval_config,
            searcher=retrieve,
            merger=retrieval.merge_and_rerank_candidate_pools,
            top_k=top_k,
            rerank_threshold=rerank_threshold,
            final_table=bounded_quota(final_table),
            final_section=bounded_quota(final_section),
            aggregate_continuation_tables=bool(
                retrieval_config.get("aggregate_continuation_tables", False)
            ),
            expand_references=bool(retrieval_config.get("expand_references", False)),
        )
        result = agentic_result["retrieval"]
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
                "agentic_rag": agentic_result["trace"],
            },
            "fixed_judge": result.get("fixed_judge") or agentic_result["trace"].get("final_judgment"),
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


def _has_knowledge_evidence(tool_events: list[dict[str, Any]]) -> bool:
    return any(
        event.get("name") == "search_knowledge_base"
        and isinstance(event.get("result"), dict)
        and bool(event["result"].get("hits"))
        and isinstance(event["result"].get("fixed_judge"), dict)
        and bool(event["result"]["fixed_judge"].get("sufficient"))
        for event in tool_events
    )


def _build_final_knowledge_messages(
    messages: list[dict[str, Any]],
    current_question: str,
    tool_result: dict[str, Any],
) -> list[dict[str, str]]:
    user_context: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            user_context.append(content.strip()[:2000])
    evidence = {
        "fixed_judge": tool_result.get("fixed_judge"),
        "hits": tool_result.get("hits") or [],
        "citations": tool_result.get("citations") or [],
        "degraded": tool_result.get("degraded") or [],
    }
    return [
        {"role": "system", "content": CHAT_FINAL_ANSWER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "conversation_user_context": user_context[-6:],
                    "current_question": current_question,
                    "evidence": evidence,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


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


def _parse_tool_call(raw: Any) -> tuple[str, str, dict[str, Any]] | None:
    if not isinstance(raw, dict):
        return None
    function = raw.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "").strip()
    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = (
            json.loads(raw_arguments)
            if isinstance(raw_arguments, str)
            else raw_arguments
        )
    except json.JSONDecodeError:
        return None
    if not name or not isinstance(arguments, dict):
        return None
    return str(raw.get("id") or ""), name, arguments


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
    timeout_seconds: float = 90,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    stream_tokens: bool = False,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run model -> native tool call -> tool result until final text."""
    request_messages: list[dict[str, Any]] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        *messages,
    ]
    new_messages: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    seen: set[str] = set()
    tool_calls = 0
    search_calls = 0
    started = time.monotonic()
    first_token_ms: float | None = None
    node_timings: list[dict[str, Any]] = []

    def forward_llm_event(event: dict[str, Any]) -> None:
        nonlocal first_token_ms
        if event.get("type") == "token" and first_token_ms is None:
            first_token_ms = round((time.monotonic() - started) * 1000, 3)
            observability.metrics.observe(
                "chunk_studio_agent_ttft_seconds",
                first_token_ms / 1000,
                model=model,
            )
            _emit(event_sink, {"type": "first_token", "ttft_ms": first_token_ms})
        _emit(event_sink, event)

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

    current_question = _latest_user_question(messages)
    knowledge_result: dict[str, Any] | None = None
    business_tool_used = False
    tools = _select_tools(_build_tools(
        assistant_id=assistant_id,
        file_ids=file_ids,
        retrieval_config=retrieval_config,
        model=model,
        current_question=current_question,
        workspace_id=workspace_id,
    ), current_question)
    by_name = {tool.name: tool for tool in tools}

    for turn in range(1, max_turns + 1):
        if time.monotonic() - started >= timeout_seconds:
            return finish({
                "answer": "本次对话超出运行时限，请缩小问题范围后重试。",
                "stop_reason": "timeout",
                "new_messages": new_messages,
                "tool_events": tool_events,
                "citations": citations,
                "charts": charts,
                "turns": turn - 1,
                "tool_calls": tool_calls,
            })
        _emit(event_sink, {"type": "turn_started", "turn": turn})
        tool_schemas = [_tool_schema(tool) for tool in tools]
        model_started = time.monotonic()
        try:
            if stream_tokens:
                response = llm.chat_tools_stream(
                    request_messages,
                    tool_schemas,
                    model=model,
                    temperature=temperature,
                    event_sink=forward_llm_event,
                )
            else:
                response = llm.chat_tools(
                    request_messages,
                    tool_schemas,
                    model=model,
                    temperature=temperature,
                )
        except Exception:
            record_node("llm", model_started, status="error", turn=turn, phase="agent_decision")
            record_run("error")
            raise
        record_node("llm", model_started, turn=turn, phase="agent_decision")
        assistant_message = {
            "role": "assistant",
            "content": str(response.get("content") or ""),
        }
        raw_calls = list(response.get("tool_calls") or [])
        # OpenAI-compatible providers reject a final assistant message with
        # `tool_calls: []`. Only tool-call turns should carry this field.
        if raw_calls:
            assistant_message["tool_calls"] = raw_calls
        request_messages.append(assistant_message)
        new_messages.append(assistant_message)
        _emit(event_sink, {
            "type": "assistant_message",
            "turn": turn,
            "message": assistant_message,
        })
        raw_calls = list(assistant_message.get("tool_calls") or [])
        if not raw_calls:
            answer = str(assistant_message["content"] or "").strip()
            if search_calls and not _has_knowledge_evidence(tool_events):
                answer = "知识库中没有检索到足够证据，暂时无法可靠回答。"
            elif not answer:
                answer = "The model returned no displayable answer."
            return finish({
                "answer": answer,
                "stop_reason": "finished",
                "new_messages": new_messages,
                "tool_events": tool_events,
                "citations": citations,
                "charts": charts,
                "turns": turn,
                "tool_calls": tool_calls,
            })

        for raw_call in raw_calls:
            parsed = _parse_tool_call(raw_call)
            if parsed is None:
                invalid_tool_call = True
                call_id = str(raw_call.get("id") or "") if isinstance(raw_call, dict) else ""
                function = raw_call.get("function") if isinstance(raw_call, dict) else {}
                tool_name = str(function.get("name") or "") if isinstance(function, dict) else ""
                arguments: dict[str, Any] = {}
                result: dict[str, Any] = {"ok": False, "error": "invalid tool call payload"}
            else:
                invalid_tool_call = False
                call_id, tool_name, arguments = parsed
                result = {}
            tool = by_name.get(tool_name)
            if not call_id:
                call_id = f"call_{turn}_{tool_calls + 1}"
            reused_knowledge_result = False
            _emit(event_sink, {
                "type": "tool_call",
                "turn": turn,
                "tool_call_id": call_id,
                "name": tool_name,
                "arguments": arguments,
            })
            if (
                not invalid_tool_call
                and tool_name == "search_knowledge_base"
                and knowledge_result is not None
            ):
                # Agentic RAG already owns the bounded recovery loop. Reuse
                # its result for any additional model-requested search calls
                # in the same response instead of exposing a second budget
                # or another uncontrolled retrieval path.
                result = dict(knowledge_result)
                reused_knowledge_result = True
            elif invalid_tool_call:
                pass
            elif tool is None:
                result = {"ok": False, "error": "unknown tool"}
            elif tool_calls >= max_tool_calls:
                result = {"ok": False, "error": "tool budget exhausted"}
            elif tool.category == "search" and search_calls >= max_search_calls:
                result = {"ok": False, "error": "search budget exhausted"}
            elif _signature(tool_name, arguments) in seen:
                result = {"ok": False, "error": "duplicate tool call rejected"}
            else:
                seen.add(_signature(tool_name, arguments))
                tool_started = time.monotonic()
                try:
                    if tool.validate:
                        tool.validate(arguments)
                    result = {"ok": True, **tool.execute(arguments)}
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                tool_calls += 1
                if tool.category == "search":
                    search_calls += 1
                if tool_name == "search_knowledge_base":
                    knowledge_result = dict(result)
                else:
                    business_tool_used = True
                record_node(
                    "tool",
                    tool_started,
                    status="ok" if result.get("ok") else "error",
                    turn=turn,
                    tool=tool_name,
                )
            _emit(event_sink, {
                "type": "tool_result",
                "turn": turn,
                "tool_call_id": call_id,
                "name": tool_name,
                "result": result,
            })
            if not reused_knowledge_result and isinstance(result.get("citations"), list):
                citations.extend(item for item in result["citations"] if isinstance(item, dict))
            chart = result.get("chart")
            if isinstance(chart, dict):
                charts.append(chart)
            content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            tool_message = {
                "role": "tool",
                "tool_call_id": call_id,
                "name": tool_name,
                "content": content,
            }
            request_messages.append(tool_message)
            new_messages.append(tool_message)
            tool_events.append({
                "tool_call_id": call_id,
                "name": tool_name,
                "arguments": arguments,
                "result": result,
            })

        if knowledge_result is not None and not business_tool_used:
            final_messages = _build_final_knowledge_messages(
                messages,
                current_question,
                knowledge_result,
            )
            model_started = time.monotonic()
            try:
                final_response = (
                    llm.chat_tools_stream(
                        final_messages,
                        tool_schemas,
                        model=model,
                        temperature=temperature,
                        tool_choice="none",
                        event_sink=forward_llm_event,
                    )
                    if stream_tokens
                    else llm.chat_tools(
                        final_messages,
                        tool_schemas,
                        model=model,
                        temperature=temperature,
                        tool_choice="none",
                    )
                )
            except Exception:
                record_node("llm", model_started, status="error", turn=turn, phase="final_answer")
                record_run("error")
                raise
            record_node("llm", model_started, turn=turn, phase="final_answer")
            final_message = {
                "role": "assistant",
                "content": str(final_response.get("content") or "").strip(),
            }
            request_messages.append(final_message)
            new_messages.append(final_message)
            _emit(event_sink, {
                "type": "assistant_message",
                "turn": turn,
                "message": final_message,
            })
            answer = final_message["content"]
            if not _has_knowledge_evidence(tool_events):
                answer = "知识库中没有检索到足够证据，暂时无法可靠回答。"
            elif not answer:
                answer = "The model returned no displayable answer."
            return finish({
                "answer": answer,
                "stop_reason": "finished",
                "new_messages": new_messages,
                "tool_events": tool_events,
                "citations": citations,
                "charts": charts,
                "turns": turn,
                "tool_calls": tool_calls,
            })

    return finish({
        "answer": "工具调用次数达到上限，暂时无法完成这次回答。",
        "stop_reason": "max_turns",
        "new_messages": new_messages,
        "tool_events": tool_events,
        "citations": citations,
        "charts": charts,
        "turns": max_turns,
        "tool_calls": tool_calls,
    })
