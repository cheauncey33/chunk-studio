from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .context import build_agent_context
from .models import (
    AgentPolicy,
    AgentProvider,
    AgentRunResult,
    StateReducer,
    ToolDefinition,
)


def _signature(tool_name: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result(
    *,
    stop_reason: str,
    value: dict[str, Any],
    mutable_state: dict[str, Any],
    events: list[dict[str, Any]],
    turns: int,
    tool_calls: int,
    search_calls: int,
    started: float,
) -> AgentRunResult:
    return AgentRunResult(
        stop_reason=stop_reason,  # type: ignore[arg-type]
        result=value,
        mutable_state=mutable_state,
        events=events,
        turns=turns,
        tool_calls=tool_calls,
        search_calls=search_calls,
        elapsed_seconds=round(time.monotonic() - started, 6),
    )


def run_agent(
    *,
    provider: AgentProvider,
    tools: list[ToolDefinition],
    immutable_state: dict[str, Any],
    mutable_state: dict[str, Any] | None = None,
    policy: AgentPolicy | None = None,
    reduce_state: StateReducer | None = None,
) -> AgentRunResult:
    """Run a bounded model/tool loop with deterministic state injection."""
    active_policy = policy or AgentPolicy()
    state = dict(mutable_state or {})
    by_name = {tool.name: tool for tool in tools}
    if len(by_name) != len(tools):
        raise ValueError("tool names must be unique")

    events: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    started = time.monotonic()
    tool_calls = 0
    search_calls = 0

    for turn in range(1, active_policy.max_turns + 1):
        if time.monotonic() - started >= active_policy.timeout_seconds:
            return _result(
                stop_reason="timeout",
                value={"outcome": "runtime_failed", "reason": "timeout"},
                mutable_state=state,
                events=events,
                turns=turn - 1,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )

        context = build_agent_context(
            immutable_state=immutable_state,
            mutable_state=state,
            events=events,
            remaining_budget={
                "turns": active_policy.max_turns - turn + 1,
                "tool_calls": active_policy.max_tool_calls - tool_calls,
                "search_calls": active_policy.max_search_calls - search_calls,
                "seconds": max(
                    0.0,
                    active_policy.timeout_seconds - (time.monotonic() - started),
                ),
            },
            max_chars=active_policy.max_context_chars,
            keep_recent_events=active_policy.keep_recent_events,
        )
        try:
            action = provider(context, [tool.model_schema() for tool in tools])
        except Exception as exc:
            events.append({
                "type": "provider_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            return _result(
                stop_reason="provider_error",
                value={"outcome": "runtime_failed", "reason": "provider_error"},
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )

        if not isinstance(action, dict):
            return _result(
                stop_reason="invalid_action",
                value={"outcome": "runtime_failed", "reason": "action must be an object"},
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )
        events.append({"type": "model_action", "turn": turn, "action": action})

        action_type = str(action.get("type") or "")
        if action_type == "finish":
            value = action.get("result")
            if not isinstance(value, dict):
                value = {"outcome": "runtime_failed", "reason": "finish result must be an object"}
            return _result(
                stop_reason="finished",
                value=value,
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )
        if action_type != "tool_call":
            return _result(
                stop_reason="invalid_action",
                value={"outcome": "runtime_failed", "reason": "unknown action type"},
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )

        tool_name = str(action.get("tool") or "")
        arguments = action.get("arguments")
        tool = by_name.get(tool_name)
        if tool is None or not isinstance(arguments, dict):
            events.append({
                "type": "tool_result",
                "tool": tool_name,
                "result": {"ok": False, "error": "unknown tool or invalid arguments"},
            })
            continue
        if tool_calls >= active_policy.max_tool_calls:
            return _result(
                stop_reason="max_tool_calls",
                value={"outcome": "exhausted", "reason": "max_tool_calls"},
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )
        if tool.category == "search" and search_calls >= active_policy.max_search_calls:
            events.append({
                "type": "tool_result",
                "tool": tool_name,
                "result": {"ok": False, "error": "search budget exhausted"},
            })
            continue

        signature = _signature(tool_name, arguments)
        if signature in seen_calls:
            events.append({
                "type": "tool_result",
                "tool": tool_name,
                "result": {"ok": False, "error": "duplicate tool call rejected"},
            })
            continue
        seen_calls.add(signature)
        events.append({"type": "tool_call", "tool": tool_name, "arguments": arguments})

        try:
            if tool.validate:
                tool.validate(arguments)
            tool_result = tool.execute(arguments)
            if not isinstance(tool_result, dict):
                raise TypeError("tool result must be an object")
            tool_result = {"ok": True, **tool_result}
        except Exception as exc:
            tool_result = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        tool_calls += 1
        if tool.category == "search":
            search_calls += 1
        events.append({"type": "tool_result", "tool": tool_name, "result": tool_result})
        if reduce_state:
            reduce_state(state, tool_name, tool_result)

        if tool.category == "finish" and tool_result.get("ok"):
            return _result(
                stop_reason="finished",
                value=tool_result,
                mutable_state=state,
                events=events,
                turns=turn,
                tool_calls=tool_calls,
                search_calls=search_calls,
                started=started,
            )

    return _result(
        stop_reason="max_turns",
        value={"outcome": "exhausted", "reason": "max_turns"},
        mutable_state=state,
        events=events,
        turns=active_policy.max_turns,
        tool_calls=tool_calls,
        search_calls=search_calls,
        started=started,
    )
