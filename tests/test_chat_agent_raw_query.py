from __future__ import annotations

from app import chat_agent


def test_chat_agent_uses_current_user_question_as_primary_search_query(monkeypatch) -> None:
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_search",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"query":"model rewrite"}',
                },
            }],
        },
        {"role": "assistant", "content": "grounded answer", "tool_calls": []},
    ])
    searched_queries: list[str] = []
    monkeypatch.setattr(chat_agent.llm, "chat_tools", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        chat_agent.retrieval,
        "hybrid_search",
        lambda query, **_kwargs: (
            searched_queries.append(query)
            or {
                "hits": [{
                    "chunk_id": "c1",
                    "file_id": "f1",
                    "file_name": "std.pdf",
                    "page": 1,
                    "text": "evidence",
                }],
                "degraded": [],
            }
        ),
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "original user question"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "grounded answer"
    assert searched_queries == ["original user question"]


def test_chat_agent_abstains_when_knowledge_search_has_no_hits(monkeypatch) -> None:
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_empty_search",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"query":"model rewrite"}',
                },
            }],
        },
        {"role": "assistant", "content": "unsupported answer", "tool_calls": []},
    ])
    monkeypatch.setattr(chat_agent.llm, "chat_tools", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        chat_agent.retrieval,
        "hybrid_search",
        lambda *_args, **_kwargs: {"hits": [], "degraded": []},
    )

    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "original user question"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "\u77e5\u8bc6\u5e93\u4e2d\u6ca1\u6709\u68c0\u7d22\u5230\u8db3\u591f\u8bc1\u636e\uff0c\u6682\u65f6\u65e0\u6cd5\u53ef\u9760\u56de\u7b54\u3002"


def test_chat_agent_abstains_when_fixed_judge_rejects_structured_gap(monkeypatch) -> None:
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_table_search",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"query":"表格"}',
                },
            }],
        },
        {"role": "assistant", "content": "unsupported answer", "tool_calls": []},
    ])
    monkeypatch.setattr(chat_agent.llm, "chat_tools", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        chat_agent.retrieval,
        "hybrid_search",
        lambda *_args, **_kwargs: {
            "hits": [{
                "chunk_id": "section-1",
                "content_type": "section",
                "text": "这里只是表格说明，没有目标表行。",
                "rerank_score": 0.9,
            }],
            "degraded": [],
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
    responses = iter([
        {
            "role": "assistant",
            "content": "我来进一步检索。",
            "tool_calls": [
                {
                    "id": "call_search_1",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": '{"query":"声级测定"}',
                    },
                },
                {
                    "id": "call_search_2",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge_base",
                        "arguments": '{"query":"声级测定具体内容"}',
                    },
                },
            ],
        },
        {"role": "assistant", "content": "声级测定应按标准规定的方法进行。", "tool_calls": []},
    ])
    search_count = 0
    choices: list[str] = []

    def fake_chat_tools(_messages, _tools, **kwargs):
        choices.append(kwargs.get("tool_choice", "auto"))
        return next(responses)

    monkeypatch.setattr(chat_agent.llm, "chat_tools", fake_chat_tools)

    def fake_search(_query, **_kwargs):
        nonlocal search_count
        search_count += 1
        return {
            "hits": [{
                "chunk_id": "sound-level",
                "file_id": "f1",
                "file_name": "GB.pdf",
                "page": 13,
                "text": "声级测量应通过 A 计权声功率级表示。",
                "score": 0.9,
            }],
            "degraded": [],
        }

    monkeypatch.setattr(chat_agent.retrieval, "hybrid_search", fake_search)
    result = chat_agent.run_chat_agent(
        assistant_id="assistant-1",
        messages=[{"role": "user", "content": "声级测定具体内容是什么？"}],
        file_ids=["f1"],
        retrieval_config={},
        model="test-model",
    )

    assert result["answer"] == "声级测定应按标准规定的方法进行。"
    assert search_count == 1
    assert choices == ["auto", "none"]
    assert "进一步检索" not in result["answer"]
