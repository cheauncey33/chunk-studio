from __future__ import annotations

from app import chat_sessions, db


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_chat_events_round_trip_to_native_messages(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    conversation = chat_sessions.create_conversation("assistant_oil_transformer_audit")
    chat_sessions.append_events(
        conversation["id"],
        [
            ("user_message", {"content": "查一下要求"}),
            (
                "assistant_message",
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_knowledge_base", "arguments": "{}"},
                        }],
                    }
                },
            ),
            (
                "tool_result",
                {
                    "tool_call_id": "call_1",
                    "name": "search_knowledge_base",
                    "content": "{\"ok\":true}",
                },
            ),
        ],
    )

    messages = chat_sessions.load_messages(conversation["id"])

    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "call_1"
    assert chat_sessions.list_events(conversation["id"])[1]["sequence"] == 2

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_chat_context_compaction_keeps_summary_and_recent_tail(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    conversation = chat_sessions.create_conversation("assistant_oil_transformer_audit")
    events = []
    for index in range(6):
        events.extend([
            ("user_message", {"content": f"问题 {index}"}),
            (
                "assistant_message",
                {"message": {"role": "assistant", "content": f"回答 {index}", "tool_calls": []}},
            ),
        ])
    chat_sessions.append_events(conversation["id"], events)

    compacted = chat_sessions.compact_conversation(
        conversation["id"],
        summarize=lambda existing, messages: f"摘要 {len(messages)} 条消息",
        min_events=4,
        keep_recent_events=2,
    )

    stored = chat_sessions.get_conversation(conversation["id"])
    messages = chat_sessions.load_messages(conversation["id"])
    assert compacted is True
    assert stored["summary"] == "摘要 10 条消息"
    assert stored["summary_sequence"] == 10
    assert messages[0]["role"] == "system"
    assert messages[-1]["content"] == "回答 5"

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
