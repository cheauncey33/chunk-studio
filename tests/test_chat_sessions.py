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
