from __future__ import annotations

from app import chat_agent


def test_chat_agent_uses_current_user_question_as_primary_search_query(monkeypatch) -> None:
    payloads: list[dict] = []

    def fake_sidecar(payload, **kwargs):
        payloads.append(payload)
        del kwargs
        return {
            "ok": True,
            "answer": "grounded answer",
            "citations": [{"chunk_id": "c1", "file_name": "std.pdf", "page": 1}],
            "charts": [],
            "stats": {
                "tool_calls": 2,
                "search_calls": 1,
                "read_chunks": 1,
                "turns": 2,
                "knowledge_grounded": True,
            },
        }

    monkeypatch.setattr(chat_agent, "_call_chat_sidecar", fake_sidecar)

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "original user question"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "grounded answer"
    assert payloads[0]["current_question"] == "original user question"


def test_chat_agent_abstains_when_knowledge_search_has_no_hits(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_agent,
        "_call_chat_sidecar",
        lambda payload, **kwargs: {
            "ok": True,
            "answer": "unsupported answer",
            "citations": [],
            "charts": [],
            "stats": {
                "tool_calls": 1,
                "search_calls": 1,
                "read_chunks": 0,
                "turns": 2,
                "knowledge_grounded": False,
            },
        },
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "original user question"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "知识库中没有检索到足够证据，暂时无法可靠回答。"


def test_chat_agent_abstains_when_fixed_judge_rejects_structured_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_agent,
        "_call_chat_sidecar",
        lambda payload, **kwargs: {
            "ok": True,
            "answer": "unsupported answer",
            "citations": [],
            "charts": [],
            "stats": {
                "tool_calls": 1,
                "search_calls": 1,
                "read_chunks": 0,
                "turns": 2,
                "knowledge_grounded": False,
            },
        },
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "最小空气间隙表格"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "知识库中没有检索到足够证据，暂时无法可靠回答。"


def test_chat_agent_finalizes_after_one_bounded_knowledge_search(monkeypatch) -> None:
    calls = 0

    def fake_sidecar(payload, **kwargs):
        nonlocal calls
        calls += 1
        del payload, kwargs
        return {
            "ok": True,
            "answer": "声级测定应按标准规定的方法进行。",
            "citations": [{
                "chunk_id": "sound-level",
                "file_id": "f1",
                "file_name": "GB.pdf",
                "page": 13,
            }],
            "charts": [],
            "stats": {
                "tool_calls": 2,
                "search_calls": 1,
                "read_chunks": 1,
                "turns": 2,
                "knowledge_grounded": True,
            },
        }

    monkeypatch.setattr(chat_agent, "_call_chat_sidecar", fake_sidecar)
    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "声级测定具体内容是什么？"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "声级测定应按标准规定的方法进行。"
    assert calls == 1
    assert "进一步检索" not in result["answer"]
