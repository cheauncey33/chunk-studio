from __future__ import annotations

from app import db
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
