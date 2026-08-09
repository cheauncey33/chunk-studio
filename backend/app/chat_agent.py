"""Bounded native tool-call Agent for knowledge-base conversations."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import business_analytics, config, db, llm, retrieval
from .agent_runtime.models import ToolDefinition


CHAT_SYSTEM_PROMPT = """You are the knowledge-base question answering agent.
Use search_knowledge_base for claims that need document evidence. Do not invent
standards, parameters, or citations when the search result is insufficient.
Use query_business_data only for workspace metrics, audit distributions, tables,
or charts. It is read-only and may return a pie, bar, metric, or table chart.
Answer in the user's language. Keep the final answer concise and distinguish
document evidence from business analytics results. This is not an audit run.
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


def _build_tools(
    *,
    assistant_id: str,
    file_ids: list[str],
    retrieval_config: dict[str, Any],
    model: str,
) -> list[ToolDefinition]:
    top_k = max(1, min(int(retrieval_config.get("top_k") or 10), 20))
    route_top_k = max(1, min(int(retrieval_config.get("route_top_k") or 30), 60))
    candidates_per_type = max(
        1, min(int(retrieval_config.get("candidate_count_per_type") or 20), 50)
    )
    threshold_value = retrieval_config.get("similarity_threshold")
    threshold = float(threshold_value) if threshold_value is not None else 0.2

    def search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        _require_question({"query": query})
        result = retrieval.hybrid_search(
            query,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=candidates_per_type,
            similarity_threshold=threshold,
            aggregate_continuation_tables=bool(
                retrieval_config.get("aggregate_continuation_tables", False)
            ),
            expand_references=bool(retrieval_config.get("expand_references", False)),
            file_ids=file_ids,
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
            "hits": bounded_hits,
            "citations": [_citation(hit) for hit in hits],
            "degraded": list(result.get("degraded") or []),
        }

    def query_business(arguments: dict[str, Any]) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        _require_question({"question": question})
        result = business_analytics.query_business_data(
            question,
            source=db.get_conn(),
            reports_dir=config.DATA_DIR / "reports",
            model=model,
        )
        # Keep the model context bounded while returning the full chart/table
        # result to the API caller through the tool event.
        return {
            **result,
            "rows": list(result.get("rows") or [])[:50],
            "summary": str(result.get("answer") or "business query completed"),
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
    ]


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
) -> dict[str, Any]:
    """Run model -> native tool call -> tool result until final text."""
    tools = _build_tools(
        assistant_id=assistant_id,
        file_ids=file_ids,
        retrieval_config=retrieval_config,
        model=model,
    )
    by_name = {tool.name: tool for tool in tools}
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

    for turn in range(1, max_turns + 1):
        if time.monotonic() - started >= timeout_seconds:
            return {
                "answer": "本次对话超出运行时限，请缩小问题范围后重试。",
                "stop_reason": "timeout",
                "new_messages": new_messages,
                "tool_events": tool_events,
                "citations": citations,
                "charts": charts,
                "turns": turn - 1,
                "tool_calls": tool_calls,
            }
        response = llm.chat_tools(
            request_messages,
            [_tool_schema(tool) for tool in tools],
            model=model,
            temperature=temperature,
        )
        assistant_message = {
            "role": "assistant",
            "content": str(response.get("content") or ""),
            "tool_calls": list(response.get("tool_calls") or []),
        }
        request_messages.append(assistant_message)
        new_messages.append(assistant_message)
        raw_calls = assistant_message["tool_calls"]
        if not raw_calls:
            answer = str(assistant_message["content"] or "").strip()
            if not answer:
                answer = "模型没有返回可展示的答案。"
            return {
                "answer": answer,
                "stop_reason": "finished",
                "new_messages": new_messages,
                "tool_events": tool_events,
                "citations": citations,
                "charts": charts,
                "turns": turn,
                "tool_calls": tool_calls,
            }

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
            if invalid_tool_call:
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
            if isinstance(result.get("citations"), list):
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

    return {
        "answer": "工具调用次数达到上限，暂时无法完成这次回答。",
        "stop_reason": "max_turns",
        "new_messages": new_messages,
        "tool_events": tool_events,
        "citations": citations,
        "charts": charts,
        "turns": max_turns,
        "tool_calls": tool_calls,
    }
