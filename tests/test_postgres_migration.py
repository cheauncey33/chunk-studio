from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db
import migrate_sessions_jobs_to_postgres as migration


def test_session_job_migration_collects_only_requested_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    workspace = db.config.DEFAULT_WORKSPACE_ID
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chat_conversations
               (id, assistant_id, workspace_id, user_id, title, created_at, updated_at)
               VALUES ('chat-1', 'assistant_oil_transformer_audit', ?, 'local-user', 'test', 'now', 'now')""",
            (workspace,),
        )
        conn.execute(
            """INSERT INTO chat_events
               (id, conversation_id, sequence, event_type, payload, created_at)
               VALUES ('event-1', 'chat-1', 1, 'user_message', '{"content":"hi"}', 'now')"""
        )
        conn.execute(
            """INSERT INTO jobs
               (id, workspace_id, type, target_type, target_id, status, priority,
                attempts, max_attempts, error, result, created_at)
               VALUES ('job-1', ?, 'parse', 'file', 'file-1', 'queued', 0,
                       0, 1, '', '{}', 'now')""",
            (workspace,),
        )

    rows = migration._rows(workspace)

    assert len(rows["chat_conversations"]) == 1
    assert len(rows["chat_events"]) == 1
    assert len(rows["jobs"]) == 1
