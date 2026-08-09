from __future__ import annotations

from app import chat_agent


def test_chat_agent_runs_native_tool_loop_and_collects_citations(monkeypatch) -> None:
    calls: list[list[dict]] = []
    events: list[dict] = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_search",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"query":"绝缘电阻"}',
                },
            }],
        },
        {
            "role": "assistant",
            "content": "要求见 GB/T 1094.1，第 3 页。",
            "tool_calls": [],
        },
    ])

    def fake_chat_tools(messages, tools, **kwargs):
        calls.append([dict(message) for message in messages])
        assert tools[0]["function"]["name"] == "search_knowledge_base"
        assert tools[0]["function"]["parameters"]["required"] == ["query"]
        return next(responses)

    monkeypatch.setattr(chat_agent.llm, "chat_tools", fake_chat_tools)
    monkeypatch.setattr(
        chat_agent.retrieval,
        "hybrid_search",
        lambda query, **kwargs: {
            "hits": [{
                "chunk_id": "c1",
                "file_id": "f1",
                "file_name": "GB.pdf",
                "page": 3,
                "text": "绝缘电阻不低于 1000 MΩ",
                "score": 0.9,
            }],
            "degraded": [],
        },
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "绝缘电阻要求是什么？"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
        event_sink=events.append,
    )

    assert result["stop_reason"] == "finished"
    assert result["answer"] == "要求见 GB/T 1094.1，第 3 页。"
    assert result["tool_calls"] == 1
    assert result["citations"][0]["file_name"] == "GB.pdf"
    assert calls[1][-1]["role"] == "tool"
    assert calls[1][-1]["tool_call_id"] == "call_search"
    assert [event["type"] for event in events] == [
        "turn_started", "assistant_message", "tool_call", "tool_result",
        "turn_started", "assistant_message",
    ]


def test_chat_agent_returns_chart_from_business_tool(monkeypatch) -> None:
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_business",
                "type": "function",
                "function": {
                    "name": "query_business_data",
                    "arguments": '{"question":"审查状态分布"}',
                },
            }],
        },
        {"role": "assistant", "content": "已生成状态分布饼图。", "tool_calls": []},
    ])
    monkeypatch.setattr(chat_agent.llm, "chat_tools", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        chat_agent.business_analytics,
        "query_business_data",
        lambda *args, **kwargs: {
            "answer": "状态分布",
            "rows": [{"label": "supported", "value": 3}],
            "chart": {"type": "pie", "data": [{"label": "supported", "value": 3}]},
        },
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "画审查状态分布"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "已生成状态分布饼图。"
    assert result["charts"] == [{"type": "pie", "data": [{"label": "supported", "value": 3}]}]


def test_chat_agent_forwards_provider_token_events(monkeypatch) -> None:
    seen = []

    def fake_stream(messages, tools, **kwargs):
        assert tools
        kwargs["event_sink"]({"type": "token", "content": "流"})
        kwargs["event_sink"]({"type": "token", "content": "式"})
        return {"role": "assistant", "content": "流式", "tool_calls": []}

    monkeypatch.setattr(chat_agent.llm, "chat_tools_stream", fake_stream)
    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "开始"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
        stream_tokens=True,
        event_sink=seen.append,
    )

    assert result["answer"] == "流式"
    assert [item["content"] for item in seen if item["type"] == "token"] == ["流", "式"]
