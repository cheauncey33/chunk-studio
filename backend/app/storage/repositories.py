"""Database repository contracts and PostgreSQL adapters.

SQLite remains the development implementation behind the existing service
functions.  These adapters isolate PostgreSQL SQL, transaction semantics and
row locking so the application can switch after an explicit migration and
shadow-comparison step.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Protocol
import uuid

from .. import config, current_user


class ChatRepository(Protocol):
    def create_conversation(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def get_conversation(self, conversation_id: str, **kwargs: Any) -> dict[str, Any] | None: ...

    def list_conversations(self, assistant_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...

    def set_config_snapshot_if_empty(self, conversation_id: str, snapshot: dict[str, Any]) -> bool: ...

    def append_events(self, conversation_id: str, events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]: ...

    def list_events(self, conversation_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_events_since(self, conversation_id: str, sequence: int, **kwargs: Any) -> list[dict[str, Any]]: ...

    def compact_conversation(self, conversation_id: str, **kwargs: Any) -> bool: ...

    def load_messages(self, conversation_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...


class JobRepository(Protocol):
    def create_job(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_job(self, job_id: str, *, workspace_id: str | None = None) -> dict[str, Any] | None: ...

    def list_jobs(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def merge_result(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None: ...

    def claim_pending(self, worker_id: str, **kwargs: Any) -> list[dict[str, Any]]: ...

    def mark_done(self, job_id: str, result: dict[str, Any] | None = None) -> None: ...

    def requeue(self, job_id: str, error: str, retry_delay_seconds: int = 30) -> None: ...

    def mark_failed(self, job_id: str, error: str, *, retry_delay_seconds: int = 30) -> None: ...


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _messages_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for event in events:
        payload = event["payload"]
        event_type = event["event_type"]
        if event_type == "user_message":
            content = str(payload.get("content") or "").strip()
            if content:
                messages.append({"role": "user", "content": content})
        elif event_type == "assistant_message":
            message = payload.get("message")
            if isinstance(message, dict):
                normalized = dict(message)
                if not normalized.get("tool_calls"):
                    normalized.pop("tool_calls", None)
                messages.append(normalized)
        elif event_type == "tool_result":
            tool_call_id = str(payload.get("tool_call_id") or "")
            if tool_call_id:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": str(payload.get("name") or ""),
                    "content": str(payload.get("content") or ""),
                })
    return messages


@dataclass(frozen=True)
class PostgresChatRepository:
    dsn: str

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL repositories require the optional psycopg dependency"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _scope(
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[str, str]:
        identity = current_user.get_current_user()
        workspace = str(workspace_id or identity.workspace_id).strip()
        user = str(user_id or identity.user_id).strip()
        if not workspace or not user:
            raise ValueError("workspace and user identity are required")
        return workspace, user

    def create_conversation(
        self,
        assistant_id: str,
        *,
        title: str = "",
        config_snapshot: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        workspace, user = self._scope(workspace_id, user_id)
        conversation_id = f"chat_{uuid.uuid4().hex}"
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(config_snapshot or {}, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO chat_conversations
                   (id, assistant_id, workspace_id, user_id, title, config_snapshot,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                   RETURNING *""",
                (conversation_id, assistant_id, workspace, user, title.strip()[:120], encoded, timestamp, timestamp),
            ).fetchone()
        return dict(row)

    def get_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        workspace, user = self._scope(workspace_id, user_id)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM chat_conversations
                   WHERE id=%s AND workspace_id=%s AND user_id=%s""",
                (conversation_id, workspace, user),
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(
        self,
        assistant_id: str,
        *,
        limit: int = 50,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace, user = self._scope(workspace_id, user_id)
        bounded_limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM chat_conversations
                   WHERE assistant_id=%s AND workspace_id=%s AND user_id=%s
                   ORDER BY updated_at DESC LIMIT %s""",
                (assistant_id, workspace, user, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_config_snapshot_if_empty(
        self,
        conversation_id: str,
        snapshot: dict[str, Any],
    ) -> bool:
        workspace, user = self._scope()
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE chat_conversations
                   SET config_snapshot=%s::jsonb
                   WHERE id=%s AND workspace_id=%s AND user_id=%s
                     AND (config_snapshot IS NULL OR config_snapshot='{}'::jsonb)""",
                (encoded, conversation_id, workspace, user),
            )
        return result.rowcount > 0

    def append_events(
        self,
        conversation_id: str,
        events: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        if not events:
            return []
        workspace, user = self._scope()
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        persisted: list[dict[str, Any]] = []
        with self._connect() as conn:
            conversation = conn.execute(
                """SELECT id FROM chat_conversations
                   WHERE id=%s AND workspace_id=%s AND user_id=%s FOR UPDATE""",
                (conversation_id, workspace, user),
            ).fetchone()
            if not conversation:
                raise ValueError("conversation not found")
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM chat_events WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()
            sequence = int(row["sequence"] or 0)
            for event_type, payload in events:
                sequence += 1
                event_id = f"event_{uuid.uuid4().hex}"
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                conn.execute(
                    """INSERT INTO chat_events
                       (id, conversation_id, sequence, event_type, payload, created_at)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
                    (event_id, conversation_id, sequence, event_type, encoded, timestamp),
                )
                persisted.append({
                    "id": event_id,
                    "conversation_id": conversation_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "payload": payload,
                    "created_at": timestamp,
                })
            conn.execute(
                "UPDATE chat_conversations SET updated_at=%s WHERE id=%s",
                (timestamp, conversation_id),
            )
        return persisted

    @staticmethod
    def _event(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = _decode(item.get("payload"))
        return item

    def list_events(
        self,
        conversation_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        workspace, user = self._scope()
        params: list[Any] = [conversation_id, workspace, user]
        if limit is None:
            sql = """SELECT ce.* FROM chat_events ce
                     JOIN chat_conversations cc ON cc.id=ce.conversation_id
                     WHERE ce.conversation_id=%s AND cc.workspace_id=%s AND cc.user_id=%s
                     ORDER BY ce.sequence"""
        else:
            params.append(max(1, min(int(limit), 500)))
            sql = """SELECT * FROM (
                       SELECT ce.* FROM chat_events ce
                       JOIN chat_conversations cc ON cc.id=ce.conversation_id
                       WHERE ce.conversation_id=%s AND cc.workspace_id=%s AND cc.user_id=%s
                       ORDER BY ce.sequence DESC LIMIT %s
                     ) recent ORDER BY sequence"""
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._event(row) for row in rows]

    def list_events_since(
        self,
        conversation_id: str,
        sequence: int,
        *,
        through_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        workspace, user = self._scope()
        clauses = [
            "ce.conversation_id=%s",
            "cc.workspace_id=%s",
            "cc.user_id=%s",
            "ce.sequence>%s",
        ]
        params: list[Any] = [conversation_id, workspace, user, int(sequence)]
        if through_sequence is not None:
            clauses.append("ce.sequence<=%s")
            params.append(int(through_sequence))
        sql = (
            "SELECT ce.* FROM chat_events ce JOIN chat_conversations cc "
            "ON cc.id=ce.conversation_id WHERE " + " AND ".join(clauses)
            + " ORDER BY ce.sequence"
        )
        if limit is not None:
            sql += " LIMIT %s"
            params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._event(row) for row in rows]

    def compact_conversation(
        self,
        conversation_id: str,
        *,
        summarize: Callable[[str, list[dict[str, Any]]], str],
        min_events: int = 48,
        keep_recent_events: int = 24,
    ) -> bool:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError("conversation not found")
        workspace, user = self._scope()
        with self._connect() as conn:
            stats = conn.execute(
                """SELECT COUNT(*) AS count, COALESCE(MAX(ce.sequence), 0) AS maximum
                   FROM chat_events ce JOIN chat_conversations cc ON cc.id=ce.conversation_id
                   WHERE ce.conversation_id=%s AND cc.workspace_id=%s AND cc.user_id=%s""",
                (conversation_id, workspace, user),
            ).fetchone()
        event_count = int(stats["count"] or 0)
        maximum = int(stats["maximum"] or 0)
        if event_count < max(1, int(min_events)):
            return False
        current_cursor = int(conversation.get("summary_sequence") or 0)
        cutoff = max(current_cursor, maximum - max(1, int(keep_recent_events)))
        if cutoff <= current_cursor:
            return False
        older_messages = _messages_from_events(
            self.list_events_since(conversation_id, current_cursor, through_sequence=cutoff)
        )
        if not older_messages:
            return False
        try:
            summary = summarize(str(conversation.get("summary") or ""), older_messages).strip()
        except Exception:
            return False
        if not summary:
            return False
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE chat_conversations
                   SET summary=%s, summary_version=summary_version+1,
                       summary_sequence=%s, updated_at=%s
                   WHERE id=%s AND workspace_id=%s AND user_id=%s""",
                (summary[:6000], cutoff, timestamp, conversation_id, workspace, user),
            )
        return result.rowcount > 0

    def load_messages(self, conversation_id: str, *, max_messages: int = 40) -> list[dict[str, Any]]:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError("conversation not found")
        messages: list[dict[str, Any]] = []
        summary = str(conversation.get("summary") or "").strip()
        if summary:
            messages.append({"role": "system", "content": "Conversation summary from earlier turns:\n" + summary})
        events = self.list_events_since(
            conversation_id,
            int(conversation.get("summary_sequence") or 0),
            limit=max_messages,
        )
        while events and events[0]["event_type"] != "user_message":
            events.pop(0)
        return messages + _messages_from_events(events)


@dataclass(frozen=True)
class PostgresJobRepository:
    """Queue adapter using row locks that are safe across worker processes."""

    dsn: str

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError("PostgreSQL jobs require the optional psycopg dependency") from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _job(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["result"] = _decode(item.get("result"))
        return item

    def create_job(
        self,
        *,
        job_id: str,
        workspace_id: str,
        type_: str,
        target_type: str,
        target_id: str,
        priority: int = 0,
        max_attempts: int = 2,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO jobs
                   (id, workspace_id, type, target_type, target_id, status,
                    priority, attempts, max_attempts, error, result)
                   VALUES (%s, %s, %s, %s, %s, 'queued', %s, 0, %s, '', %s::jsonb)
                   RETURNING *""",
                (
                    job_id,
                    workspace_id,
                    type_,
                    target_type,
                    target_id,
                    int(priority),
                    max(1, int(max_attempts)),
                    json.dumps(result or {}, ensure_ascii=False),
                ),
            ).fetchone()
        return self._job(row)

    def get_job(
        self,
        job_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        identity = current_user.get_current_user()
        scoped_workspace = str(workspace_id or identity.workspace_id).strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND workspace_id=%s",
                (job_id, scoped_workspace),
            ).fetchone()
        return self._job(row) if row else None

    def list_jobs(
        self,
        *,
        workspace_id: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        type_: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        identity = current_user.get_current_user()
        clauses = ["workspace_id=%s"]
        params: list[Any] = [str(workspace_id or identity.workspace_id).strip()]
        if target_id:
            clauses.append("target_id=%s")
            params.append(target_id)
        if status:
            clauses.append("status=%s")
            params.append(status)
        if type_:
            clauses.append("type=%s")
            params.append(type_)
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT %s",
                params,
            ).fetchall()
        return [self._job(row) for row in rows]

    def merge_result(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        if not patch:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result, status FROM jobs WHERE id=%s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if not row or row["status"] not in {"queued", "running"}:
                return None
            current = _decode(row.get("result"))
            merged = {**current, **patch}
            if isinstance(patch.get("progress"), dict):
                previous = current.get("progress") if isinstance(current.get("progress"), dict) else {}
                merged["progress"] = {**previous, **patch["progress"]}
            updated = conn.execute(
                "UPDATE jobs SET result=%s::jsonb WHERE id=%s RETURNING *",
                (json.dumps(merged, ensure_ascii=False), job_id),
            ).fetchone()
        return self._job(updated) if updated else None

    def claim_pending(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        lease_seconds: int = 300,
        job_types: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 100))
        clauses = [
            "((status='queued' AND (available_at IS NULL OR available_at <= now())) "
            "OR (status='running' AND locked_until < now()))",
            "attempts < max_attempts",
        ]
        params: list[Any] = []
        if job_types:
            clauses.append("type = ANY(%s)")
            params.append(sorted(job_types))
        params.append(bounded_limit)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM jobs
                   WHERE """ + " AND ".join(clauses) + """
                   ORDER BY priority DESC, created_at
                   LIMIT %s FOR UPDATE SKIP LOCKED""",
                params,
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                updated = conn.execute(
                    """UPDATE jobs
                       SET status='running', attempts=attempts+1,
                           started_at=COALESCE(started_at, now()),
                           locked_by=%s,
                           locked_until=now() + (%s * interval '1 second')
                       WHERE id=%s AND (status='queued' OR (status='running' AND locked_until < now()))
                       RETURNING *""",
                    (worker_id, max(1, int(lease_seconds)), row["id"]),
                ).fetchone()
                if updated:
                    claimed.append(self._job(updated))
        return claimed

    def mark_done(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status='done', result=%s::jsonb,
                   finished_at=now(), locked_by=NULL, locked_until=NULL
                   WHERE id=%s""",
                (json.dumps(result or {}, ensure_ascii=False), job_id),
            )

    def requeue(self, job_id: str, error: str, retry_delay_seconds: int = 30) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status='queued', error=%s,
                   available_at=now() + (%s * interval '1 second'),
                   locked_by=NULL, locked_until=NULL
                   WHERE id=%s""",
                (error[:4000] or "retrying", max(1, int(retry_delay_seconds)), job_id),
            )

    def mark_succeeded(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        self.mark_done(job_id, result)

    def mark_failed(self, job_id: str, error: str, *, retry_delay_seconds: int = 30) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs
                       SET status=CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                       error=%s,
                       dead_letter=(attempts >= max_attempts),
                       available_at=CASE WHEN attempts < max_attempts
                                          THEN now() + (%s * interval '1 second') ELSE NULL END,
                       finished_at=CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                       locked_by=NULL, locked_until=NULL
                   WHERE id=%s""",
                (error[:4000], max(1, int(retry_delay_seconds)), job_id),
            )


def postgres_schema_sql() -> list[str]:
    """DDL for session/job tables; deployment runs it before switching adapters."""
    return [
        """CREATE TABLE IF NOT EXISTS users (
             id TEXT PRIMARY KEY,
             external_subject TEXT NOT NULL UNIQUE,
             display_name TEXT NOT NULL DEFAULT '',
             status TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'disabled')),
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS workspaces (
             id TEXT PRIMARY KEY,
             name TEXT NOT NULL,
             slug TEXT NOT NULL UNIQUE,
             status TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'archived')),
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS workspace_members (
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
             role TEXT NOT NULL DEFAULT 'member',
             status TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'invited', 'disabled')),
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             PRIMARY KEY (workspace_id, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_workspace_members_user ON workspace_members(user_id, status, workspace_id)",
        """CREATE TABLE IF NOT EXISTS chat_conversations (
             id TEXT PRIMARY KEY,
             assistant_id TEXT NOT NULL,
             workspace_id TEXT NOT NULL,
             user_id TEXT NOT NULL,
             title TEXT NOT NULL DEFAULT '',
             summary TEXT NOT NULL DEFAULT '',
             summary_version INTEGER NOT NULL DEFAULT 0,
             summary_sequence INTEGER NOT NULL DEFAULT 0,
             config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS chat_events (
             id TEXT PRIMARY KEY,
             conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
             sequence INTEGER NOT NULL,
             event_type TEXT NOT NULL,
             payload JSONB NOT NULL DEFAULT '{}'::jsonb,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             UNIQUE (conversation_id, sequence)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_chat_conversations_scope ON chat_conversations(workspace_id, user_id, assistant_id, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_chat_events_conversation ON chat_events(conversation_id, sequence)",
        """CREATE TABLE IF NOT EXISTS jobs (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL,
             type TEXT NOT NULL,
             target_type TEXT NOT NULL,
             target_id TEXT NOT NULL,
             status TEXT NOT NULL,
             priority INTEGER NOT NULL DEFAULT 0,
             attempts INTEGER NOT NULL DEFAULT 0,
             max_attempts INTEGER NOT NULL DEFAULT 2,
             error TEXT NOT NULL DEFAULT '',
             result JSONB NOT NULL DEFAULT '{}'::jsonb,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             started_at TIMESTAMPTZ,
             finished_at TIMESTAMPTZ,
             available_at TIMESTAMPTZ,
             locked_by TEXT,
             locked_until TIMESTAMPTZ,
             dead_letter BOOLEAN NOT NULL DEFAULT FALSE
        )""",
        "CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs(status, available_at, priority DESC, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_jobs_scope ON jobs(workspace_id, status, created_at DESC)",
    ]


def postgres_content_schema_sql() -> list[str]:
    """DDL for the workspace-owned business content migration slice.

    Chat/jobs and the vector index have their own migration boundaries.  This
    slice keeps the source-compatible path columns while adding object keys
    for the later MinIO/S3 cutover.  JSON text from SQLite is stored as JSONB
    so the PostgreSQL repository can filter structured metadata without
    reparsing every row in Python.
    """
    return [
        """CREATE TABLE IF NOT EXISTS files (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             name TEXT NOT NULL,
             path TEXT NOT NULL,
             sha TEXT,
             object_key TEXT NOT NULL DEFAULT '',
             page_count INTEGER,
             metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS chunks (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             page INTEGER NOT NULL,
             bbox JSONB NOT NULL DEFAULT '{}'::jsonb,
             rotation INTEGER NOT NULL DEFAULT 0,
             crop_path TEXT,
             text TEXT,
             text_source TEXT NOT NULL DEFAULT 'pending'
               CHECK (text_source IN ('digital', 'manual', 'ocr', 'pending')),
             metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
             business_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
             metadata_llm JSONB NOT NULL DEFAULT '{}'::jsonb,
             source_trace JSONB NOT NULL DEFAULT '{}'::jsonb,
             chunk_logic JSONB NOT NULL DEFAULT '{}'::jsonb,
             relations JSONB NOT NULL DEFAULT '{}'::jsonb,
             ui_state JSONB NOT NULL DEFAULT '{}'::jsonb,
             indexing JSONB NOT NULL DEFAULT '{}'::jsonb,
             status TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'reviewed', 'approved', 'rejected')),
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS document_parses (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             provider TEXT NOT NULL DEFAULT 'mineru',
             status TEXT NOT NULL DEFAULT 'queued',
             markdown_path TEXT,
             raw_zip_path TEXT,
             markdown_object_key TEXT NOT NULL DEFAULT '',
             raw_zip_object_key TEXT NOT NULL DEFAULT '',
             result JSONB NOT NULL DEFAULT '{}'::jsonb,
             error TEXT NOT NULL DEFAULT '',
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS knowledge_bases (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             name TEXT NOT NULL,
             description TEXT NOT NULL DEFAULT '',
             status TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'archived')),
             is_default BOOLEAN NOT NULL DEFAULT FALSE,
             parser_config JSONB NOT NULL DEFAULT '{}'::jsonb,
             retrieval_config JSONB NOT NULL DEFAULT '{}'::jsonb,
             manual_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
             few_shot_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
             default_naming_file_id TEXT,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             UNIQUE (workspace_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS knowledge_base_files (
             knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
             file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             role TEXT NOT NULL DEFAULT 'source'
               CHECK (role IN ('source', 'reference')),
             corpus_kind TEXT NOT NULL DEFAULT 'standard'
               CHECK (corpus_kind IN ('standard', 'spec')),
             enabled BOOLEAN NOT NULL DEFAULT TRUE,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             PRIMARY KEY (knowledge_base_id, file_id)
        )""",
        """CREATE TABLE IF NOT EXISTS audit_assistants (
             id TEXT PRIMARY KEY,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             name TEXT NOT NULL,
             description TEXT NOT NULL DEFAULT '',
             status TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('draft', 'active', 'archived')),
             active_version_id TEXT,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             UNIQUE (workspace_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS assistant_versions (
             id TEXT PRIMARY KEY,
             assistant_id TEXT NOT NULL REFERENCES audit_assistants(id) ON DELETE CASCADE,
             version INTEGER NOT NULL,
             name TEXT NOT NULL DEFAULT '',
             status TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'active', 'retired')),
             model_config JSONB NOT NULL DEFAULT '{}'::jsonb,
             node_prompts JSONB NOT NULL DEFAULT '{}'::jsonb,
             rules JSONB NOT NULL DEFAULT '{}'::jsonb,
             retrieval_config JSONB NOT NULL DEFAULT '{}'::jsonb,
             parameter_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
             category_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
             initialization_provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             activated_at TIMESTAMPTZ,
             UNIQUE (assistant_id, version)
        )""",
        """CREATE TABLE IF NOT EXISTS assistant_knowledge_bases (
             assistant_id TEXT NOT NULL REFERENCES audit_assistants(id) ON DELETE CASCADE,
             knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             priority INTEGER NOT NULL DEFAULT 0,
             enabled BOOLEAN NOT NULL DEFAULT TRUE,
             PRIMARY KEY (assistant_id, knowledge_base_id)
        )""",
        """CREATE TABLE IF NOT EXISTS assistant_init_drafts (
             assistant_id TEXT PRIMARY KEY REFERENCES audit_assistants(id) ON DELETE CASCADE,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             status TEXT NOT NULL DEFAULT 'ready'
               CHECK (status IN ('generating', 'ready', 'failed', 'applied', 'discarded')),
             payload JSONB NOT NULL DEFAULT '{}'::jsonb,
             job_id TEXT,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS audit_case_reviews (
             report_name TEXT NOT NULL,
             case_id TEXT NOT NULL,
             workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
             status TEXT NOT NULL CHECK (status IN ('confirmed', 'corrected')),
             corrected_status TEXT NOT NULL DEFAULT '',
             note TEXT NOT NULL DEFAULT '',
             reviewer TEXT NOT NULL DEFAULT '',
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             PRIMARY KEY (workspace_id, report_name, case_id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_files_scope_created ON files(workspace_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_chunks_scope_file_page ON chunks(workspace_id, file_id, page, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_chunks_scope_status_updated ON chunks(workspace_id, status, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_document_parses_scope_file ON document_parses(workspace_id, file_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_scope_status ON knowledge_bases(workspace_id, status, is_default DESC, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_kb_files_scope_kb ON knowledge_base_files(workspace_id, knowledge_base_id, enabled, file_id)",
        "CREATE INDEX IF NOT EXISTS ix_kb_files_scope_file ON knowledge_base_files(workspace_id, file_id, knowledge_base_id)",
        "CREATE INDEX IF NOT EXISTS ix_assistants_scope_status ON audit_assistants(workspace_id, status, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_assistant_versions_assistant ON assistant_versions(assistant_id, version DESC)",
        "CREATE INDEX IF NOT EXISTS ix_assistant_kbs_scope ON assistant_knowledge_bases(workspace_id, knowledge_base_id, enabled, priority)",
        "CREATE INDEX IF NOT EXISTS ix_reviews_scope_report ON audit_case_reviews(workspace_id, report_name, updated_at DESC)",
    ]


def get_chat_repository() -> PostgresChatRepository | None:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL repositories")
        return PostgresChatRepository(config.DATABASE_URL)
    return None


def get_job_repository() -> PostgresJobRepository | None:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL repositories")
        return PostgresJobRepository(config.DATABASE_URL)
    return None
