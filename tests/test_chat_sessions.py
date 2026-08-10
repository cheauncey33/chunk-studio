from __future__ import annotations

import json
import sqlite3

from app import chat_sessions, db


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_conversation_config_snapshot_is_persisted_and_not_overwritten(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    snapshot = {
        "assistant_version_id": "assistant_v1",
        "model_config": {"model": "deepseek-v4-flash", "temperature": 0},
        "retrieval_config": {"dense_threshold": 0.0, "rerank_threshold": 0.2},
        "file_ids": ["file-1"],
    }
    conversation = chat_sessions.create_conversation(
        "assistant_oil_transformer_audit",
        config_snapshot=snapshot,
    )
    persisted = chat_sessions.get_conversation(conversation["id"])
    assert persisted is not None
    assert json.loads(persisted["config_snapshot"]) == snapshot

    assert not chat_sessions.set_config_snapshot_if_empty(
        conversation["id"],
        {"retrieval_config": {"rerank_threshold": 0.9}},
    )
    unchanged = chat_sessions.get_conversation(conversation["id"])
    assert unchanged is not None
    assert json.loads(unchanged["config_snapshot"]) == snapshot


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


def test_chat_events_drop_empty_tool_calls_from_final_assistant_message(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    conversation = chat_sessions.create_conversation("assistant_oil_transformer_audit")
    chat_sessions.append_events(
        conversation["id"],
        [
            ("user_message", {"content": "question"}),
            (
                "assistant_message",
                {
                    "message": {
                        "role": "assistant",
                        "content": "answer",
                        "tool_calls": [],
                    }
                },
            ),
        ],
    )

    messages = chat_sessions.load_messages(conversation["id"])

    assert messages[-1] == {"role": "assistant", "content": "answer"}

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


def test_conversation_history_isolated_by_workspace_and_user(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    first = chat_sessions.create_conversation(
        "assistant_oil_transformer_audit",
        title="workspace one",
        workspace_id="workspace-1",
        user_id="user-1",
    )
    second = chat_sessions.create_conversation(
        "assistant_oil_transformer_audit",
        title="workspace two",
        workspace_id="workspace-2",
        user_id="user-2",
    )

    assert chat_sessions.get_conversation(
        first["id"], workspace_id="workspace-1", user_id="user-1"
    )["title"] == "workspace one"
    assert chat_sessions.get_conversation(
        first["id"], workspace_id="workspace-2", user_id="user-2"
    ) is None
    assert [item["id"] for item in chat_sessions.list_conversations(
        "assistant_oil_transformer_audit",
        workspace_id="workspace-2",
        user_id="user-2",
    )] == [second["id"]]

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_legacy_tenant_column_is_renamed_during_db_migration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    legacy = sqlite3.connect(db.config.DB_PATH)
    legacy.execute(
        """CREATE TABLE chat_conversations (
             id TEXT PRIMARY KEY,
             assistant_id TEXT NOT NULL,
             tenant_id TEXT NOT NULL DEFAULT 'legacy-workspace',
             user_id TEXT NOT NULL DEFAULT 'legacy-user',
             title TEXT NOT NULL DEFAULT '',
             created_at TEXT NOT NULL,
             updated_at TEXT NOT NULL
        )"""
    )
    legacy.commit()
    legacy.close()
    monkeypatch.setattr(db, "_conn", None)

    db.init_db()
    columns = {
        row["name"]
        for row in db.get_conn().execute("PRAGMA table_info(chat_conversations)")
    }
    assert "workspace_id" in columns
    assert "tenant_id" not in columns

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
