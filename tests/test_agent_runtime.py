from __future__ import annotations

from app.agent_runtime import AgentPolicy, ToolDefinition, run_agent


def test_agent_loop_executes_tool_and_reinjects_mutable_state() -> None:
    seen_contexts = []

    def provider(context, tools):
        seen_contexts.append(context)
        if not context["mutable_state"].get("found"):
            return {"type": "tool_call", "tool": "search", "arguments": {"query": "x"}}
        return {"type": "finish", "result": {"outcome": "evidence_found"}}

    def reduce_state(state, _tool, result):
        state["found"] = result["items"]

    result = run_agent(
        provider=provider,
        tools=[ToolDefinition(
            name="search",
            description="search",
            input_schema={"type": "object"},
            execute=lambda args: {"items": [args["query"]]},
            category="search",
        )],
        immutable_state={"case_id": "case-1"},
        reduce_state=reduce_state,
    )

    assert result.stop_reason == "finished"
    assert result.result["outcome"] == "evidence_found"
    assert result.tool_calls == 1
    assert seen_contexts[1]["immutable_state"] == {"case_id": "case-1"}
    assert seen_contexts[1]["mutable_state"]["found"] == ["x"]


def test_agent_loop_rejects_duplicate_calls_without_consuming_tool_budget() -> None:
    calls = 0

    def provider(_context, _tools):
        return {"type": "tool_call", "tool": "search", "arguments": {"query": "same"}}

    def execute(_args):
        nonlocal calls
        calls += 1
        return {"items": []}

    result = run_agent(
        provider=provider,
        tools=[ToolDefinition(
            name="search",
            description="search",
            input_schema={"type": "object"},
            execute=execute,
            category="search",
        )],
        immutable_state={},
        policy=AgentPolicy(max_turns=3, max_tool_calls=3),
    )

    assert result.stop_reason == "max_turns"
    assert result.tool_calls == 1
    assert calls == 1
    assert any(
        event.get("result", {}).get("error") == "duplicate tool call rejected"
        for event in result.events
    )


def test_agent_loop_enforces_search_budget() -> None:
    queries = iter(("one", "two", "three"))

    def provider(_context, _tools):
        return {"type": "tool_call", "tool": "search", "arguments": {"query": next(queries)}}

    result = run_agent(
        provider=provider,
        tools=[ToolDefinition(
            name="search",
            description="search",
            input_schema={"type": "object"},
            execute=lambda args: {"items": [args["query"]]},
            category="search",
        )],
        immutable_state={},
        policy=AgentPolicy(max_turns=3, max_tool_calls=3, max_search_calls=1),
    )

    assert result.search_calls == 1
    assert any(
        event.get("result", {}).get("error") == "search budget exhausted"
        for event in result.events
    )


def test_context_compaction_never_drops_pinned_state() -> None:
    large = "x" * 5000

    def provider(context, _tools):
        assert context["immutable_state"] == {"case_id": "case-1", "requirement": large}
        return {"type": "finish", "result": {"outcome": "exhausted"}}

    result = run_agent(
        provider=provider,
        tools=[],
        immutable_state={"case_id": "case-1", "requirement": large},
        policy=AgentPolicy(max_context_chars=100),
    )
    assert result.stop_reason == "finished"
