from __future__ import annotations

import asyncio

from starlette.requests import Request

from app import db
from app import current_user, runtime
from app.routers import assistants


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_agent_chat_persists_conversation_and_reuses_it(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','std.pdf','files/std.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,enabled,created_at)
               VALUES ('kb_uncategorized','f1',1,'now')"""
        )
    seen_messages: list[list[dict]] = []

    def fake_run(**kwargs):
        seen_messages.append(kwargs["messages"])
        return {
            "answer": "已回答",
            "new_messages": [{"role": "assistant", "content": "已回答", "tool_calls": []}],
            "citations": [],
            "charts": [],
            "stop_reason": "finished",
            "turns": 1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(assistants.chat_agent, "run_chat_agent", fake_run)
    first = assistants.assistant_agent_chat(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(message="第一个问题"),
    )
    second = assistants.assistant_agent_chat(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(
            message="第二个问题",
            conversation_id=first["conversation_id"],
        ),
    )

    assert second["conversation_id"] == first["conversation_id"]
    assert [item["role"] for item in seen_messages[1]] == [
        "user", "assistant", "user",
    ]
    events = db.get_conn().execute(
        "SELECT event_type FROM chat_events WHERE conversation_id=? ORDER BY sequence",
        (first["conversation_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events] == [
        "user_message", "assistant_message", "user_message", "assistant_message",
    ]

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_agent_chat_stream_emits_sse_lifecycle_events(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','std.pdf','files/std.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,enabled,created_at)
               VALUES ('kb_uncategorized','f1',1,'now')"""
        )

    def fake_run(**kwargs):
        kwargs["event_sink"]({"type": "turn_started", "turn": 1})
        return {
            "answer": "流式回答",
            "new_messages": [{"role": "assistant", "content": "流式回答", "tool_calls": []}],
            "citations": [],
            "charts": [],
            "stop_reason": "finished",
            "turns": 1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(assistants.chat_agent, "run_chat_agent", fake_run)
    response = assistants.assistant_agent_chat_stream(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(message="流式问题"),
    )

    async def collect() -> str:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    payload = asyncio.run(collect())
    assert "event: conversation" in payload
    assert "event: agent" in payload
    assert "event: final" in payload
    assert "event: done" in payload

    conversation_id = db.get_conn().execute(
        "SELECT id FROM chat_conversations ORDER BY created_at DESC LIMIT 1"
    ).fetchone()["id"]
    replay_request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"last-event-id", b"0-0")],
        "query_string": b"",
    })
    replay = assistants.replay_agent_events(
        "assistant_oil_transformer_audit",
        conversation_id,
        replay_request,
    )

    async def collect_replay() -> str:
        chunks = []
        async for chunk in replay.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    replay_payload = asyncio.run(collect_replay())
    assert "event: conversation" in replay_payload
    assert "id: " in replay_payload

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_agent_chat_idempotency_key_replays_completed_response(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    calls = 0

    def fake_run(**kwargs):
        nonlocal calls
        calls += 1
        return {
            "answer": "same response",
            "new_messages": [{"role": "assistant", "content": "same response"}],
            "citations": [],
            "charts": [],
            "stop_reason": "finished",
            "turns": 1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(assistants.chat_agent, "run_chat_agent", fake_run)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"idempotency-key", b"request-1")],
        "query_string": b"",
    })
    first = assistants.assistant_agent_chat(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(message="one"),
        request,
    )
    second = assistants.assistant_agent_chat(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(message="one"),
        request,
    )

    assert first == second
    assert calls == 1
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_agent_chat_stream_finishes_when_lock_cleanup_fails(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)

    class BrokenReleaseLock:
        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            raise RuntimeError("redis lock cleanup failed")

    monkeypatch.setattr(
        assistants,
        "_conversation_lock",
        lambda *_args, **_kwargs: BrokenReleaseLock(),
    )
    monkeypatch.setattr(
        assistants.chat_agent,
        "run_chat_agent",
        lambda **_kwargs: {
            "answer": "ok",
            "new_messages": [{"role": "assistant", "content": "ok"}],
            "citations": [],
            "charts": [],
            "stop_reason": "finished",
            "turns": 1,
            "tool_calls": 0,
        },
    )
    response = assistants.assistant_agent_chat_stream(
        "assistant_oil_transformer_audit",
        assistants.AgentChatRequest(message="test"),
    )

    async def collect() -> str:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    payload = asyncio.run(asyncio.wait_for(collect(), timeout=2))
    assert "event: final" in payload
    assert "event: done" in payload

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_new_conversations_do_not_share_a_global_lock(monkeypatch) -> None:
    monkeypatch.setattr(runtime.config, "REDIS_URL", "")
    runtime.reset_runtime_for_tests()
    identity = current_user.local_current_user()

    first = assistants._conversation_lock(identity, "assistant-1", None)
    second = assistants._conversation_lock(identity, "assistant-1", None)
    assert first.acquire()
    assert second.acquire()
    second.release()
    first.release()

    existing = assistants._conversation_lock(identity, "assistant-1", "conversation-1")
    duplicate = assistants._conversation_lock(identity, "assistant-1", "conversation-1")
    assert existing.acquire()
    assert not duplicate.acquire()
    existing.release()
    runtime.reset_runtime_for_tests()
