from __future__ import annotations

from app import chat_agent
import pytest


def test_knowledge_tool_passes_split_retrieval_thresholds(monkeypatch) -> None:
    captured: dict = {}

    monkeypatch.setattr(
        chat_agent.retrieval,
        "hybrid_search",
        lambda query, **kwargs: captured.update(kwargs) or {"hits": [], "degraded": []},
    )
    tools = chat_agent._knowledge_base_tools(
        chat_agent.ToolContext(
            assistant_id="assistant-1",
            file_ids=["file-1"],
            retrieval_config={
                "dense_threshold": 0.35,
                "rerank_threshold": 0.72,
            },
            model="test-model",
            current_question="原始问题",
        )
    )

    tools[0].execute({"query": "候选查询"})

    assert captured["dense_threshold"] == 0.35
    assert captured["rerank_threshold"] == 0.72
    assert captured["query_routes"] == {"production": "原始问题"}
    assert "similarity_threshold" not in captured
    assert "agentic_rag" not in captured


def test_pack_chat_turn_keeps_summary_and_recent_text_only() -> None:
    packed = chat_agent.pack_chat_turn(
        [
            {
                "role": "system",
                "content": "Conversation summary from earlier turns:\n用户在问绝缘电阻",
            },
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
            {
                "role": "tool",
                "name": "search_knowledge_base",
                "content": "{\"hits\":[]}",
            },
            {"role": "user", "content": "当前问题"},
        ]
    )
    assert packed["conversation_summary"] == "用户在问绝缘电阻"
    assert packed["current_question"] == "当前问题"
    assert packed["recent_turns"] == [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
    ]


def test_chat_agent_runs_sidecar_turn_and_collects_citations(monkeypatch) -> None:
    events: list[dict] = []
    payloads: list[dict] = []

    def fake_sidecar(payload, **kwargs):
        payloads.append(payload)
        del kwargs
        return {
            "ok": True,
            "answer": "要求见 GB/T 1094.1，第 3 页。",
            "citations": [{
                "chunk_id": "c1",
                "file_id": "f1",
                "file_name": "GB.pdf",
                "page": 3,
                "snippet": "绝缘电阻不低于 1000 MΩ",
            }],
            "charts": [],
            "stats": {
                "tool_calls": 2,
                "search_calls": 1,
                "read_chunks": 1,
                "turns": 3,
                "knowledge_grounded": True,
            },
        }

    monkeypatch.setattr(chat_agent, "_call_chat_sidecar", fake_sidecar)

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "绝缘电阻要求是什么？"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
        event_sink=events.append,
        conversation_id="chat_1",
    )

    assert result["stop_reason"] == "finished"
    assert result["answer"] == "要求见 GB/T 1094.1，第 3 页。"
    assert result["tool_calls"] == 2
    assert result["citations"][0]["file_name"] == "GB.pdf"
    assert "tool_calls" not in result["new_messages"][-1]
    assert payloads[0]["current_question"] == "绝缘电阻要求是什么？"
    assert payloads[0]["file_ids"] == ["f1"]
    assert payloads[0]["conversation_id"] == "chat_1"
    assert [event["type"] for event in events if event["type"] != "node_timing"] == [
        "turn_started", "first_token", "assistant_message",
    ]
    assert [event["node"] for event in events if event["type"] == "node_timing"] == ["sidecar"]


def test_chat_agent_returns_chart_from_business_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_agent,
        "_call_chat_sidecar",
        lambda payload, **kwargs: {
            "ok": True,
            "answer": "已生成状态分布饼图。",
            "citations": [],
            "charts": [{"type": "pie", "data": [{"label": "supported", "value": 3}]}],
            "stats": {
                "tool_calls": 1,
                "search_calls": 0,
                "read_chunks": 0,
                "turns": 2,
                "knowledge_grounded": False,
            },
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


def test_business_only_context_does_not_expose_document_search() -> None:
    tools = chat_agent._build_tools(
        assistant_id="assistant-1",
        file_ids=[],
        retrieval_config={},
        model="test-model",
    )

    names = {tool.name for tool in tools}
    assert "search_knowledge_base" not in names
    assert {"query_business_data", "get_business_schema", "get_business_overview"} <= names


def test_business_intent_gate_hides_knowledge_search() -> None:
    tools = chat_agent._build_tools(
        assistant_id="assistant-1",
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
        current_question="我们现在有几个数据库？SQL 是怎么写的？",
    )

    selected = chat_agent._select_tools(tools, "我们现在有几个数据库？SQL 是怎么写的？")
    assert {tool.name for tool in selected} == {
        "query_business_data", "get_business_schema", "get_business_overview",
    }


def test_document_table_intent_keeps_knowledge_search() -> None:
    tools = chat_agent._build_tools(
        assistant_id="assistant-1",
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
        current_question="最小空气间隙（外绝缘空气间隙）表格",
    )

    selected = chat_agent._select_tools(tools, "最小空气间隙（外绝缘空气间隙）表格")
    assert {tool.name for tool in selected} == {"search_knowledge_base"}


def test_chat_agent_forwards_provider_token_events(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        chat_agent,
        "_call_chat_sidecar",
        lambda payload, **kwargs: {
            "ok": True,
            "answer": "流式",
            "citations": [],
            "charts": [],
            "stats": {"tool_calls": 0, "search_calls": 0, "read_chunks": 0, "turns": 1},
        },
    )
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
    assert [item["content"] for item in seen if item["type"] == "token"] == ["流式"]
    assert result["performance"]["ttft_ms"] is not None
    assert result["performance"]["total_ms"] >= result["performance"]["ttft_ms"]
    assert result["performance"]["nodes"][0]["node"] == "sidecar"
    assert len([item for item in seen if item["type"] == "first_token"]) == 1
    assert any(item["type"] == "node_timing" for item in seen)
    assert any(item["type"] == "turn_started" for item in seen)
    assert any(item["type"] == "assistant_message" for item in seen)


def test_chat_agent_raises_when_sidecar_fails(monkeypatch) -> None:
    def boom(payload, **kwargs):
        del payload, kwargs
        raise RuntimeError("agent sidecar 5xx (502): sidecar down")

    monkeypatch.setattr(chat_agent, "_call_chat_sidecar", boom)
    with pytest.raises(RuntimeError, match="sidecar"):
        chat_agent.run_chat_agent(
            assistant_id="assistant-1",
            messages=[{"role": "user", "content": "开始"}],
            file_ids=["f1"],
            retrieval_config={},
            model="test-model",
        )
