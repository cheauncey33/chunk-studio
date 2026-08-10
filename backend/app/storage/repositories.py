"""Database repository contracts and PostgreSQL adapters.

SQLite remains the development implementation behind the existing service
functions.  These adapters isolate PostgreSQL SQL, transaction semantics and
row locking so the application can switch after an explicit migration and
shadow-comparison step.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
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


class WorkspaceRepository(Protocol):
    def is_active_member(self, *, workspace_id: str, user_id: str) -> bool: ...

    def current_workspace(self, *, workspace_id: str, user_id: str) -> dict[str, Any] | None: ...

    def list_members(self, *, workspace_id: str) -> list[dict[str, Any]]: ...


class SettingsRepository(Protocol):
    def get(self, key: str, default: str = "") -> str: ...

    def set(self, key: str, value: str) -> None: ...

    def all(self) -> dict[str, str]: ...


class ContentRepository(Protocol):
    """Read-side contract for the first business-content migration slice."""

    def list_knowledge_bases(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def create_knowledge_base(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def update_knowledge_base(
        self, knowledge_base_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def delete_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any] | None: ...

    def ensure_assistant_for_knowledge_base(
        self,
        knowledge_base_id: str,
        *,
        name: str,
        description: str,
    ) -> dict[str, Any]: ...

    def get_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any] | None: ...

    def list_files(self, *, limit: int = 500) -> list[dict[str, Any]]: ...

    def get_file(self, file_id: str) -> dict[str, Any] | None: ...

    def create_file(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def update_file(self, file_id: str, values: dict[str, Any]) -> dict[str, Any] | None: ...

    def delete_file(self, file_id: str) -> dict[str, Any] | None: ...

    def attach_file_to_knowledge_base(
        self,
        knowledge_base_id: str,
        file_id: str,
        *,
        role: str,
        corpus_kind: str,
        enabled: bool = True,
    ) -> None: ...

    def update_knowledge_base_file(
        self, knowledge_base_id: str, file_id: str, values: dict[str, Any]
    ) -> bool: ...

    def remove_file_from_knowledge_base(self, knowledge_base_id: str, file_id: str) -> bool: ...

    def set_default_naming_file(self, knowledge_base_id: str, file_id: str | None) -> None: ...

    def create_chunk(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def update_chunk(self, chunk_id: str, values: dict[str, Any]) -> dict[str, Any] | None: ...

    def delete_chunk(self, chunk_id: str) -> dict[str, Any] | None: ...

    def latest_parse(self, file_id: str) -> dict[str, Any] | None: ...

    def list_file_parses(self, file_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def list_knowledge_base_files(
        self,
        knowledge_base_id: str,
        *,
        file_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def list_knowledge_base_chunks(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def list_chunks(
        self,
        *,
        file_id: str | None = None,
        page: int | None = None,
        has_llm_suggestions: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None: ...

    def primary_parser_config(self, file_id: str) -> dict[str, Any]: ...

    def create_parse(self, parse_id: str, file_id: str, *, provider: str = "mineru") -> dict[str, Any]: ...

    def update_parse(self, parse_id: str, **fields: Any) -> dict[str, Any] | None: ...

    def delete_file_chunks(self, file_id: str) -> list[dict[str, Any]]: ...

    def insert_chunk(self, **fields: Any) -> dict[str, Any]: ...

    def list_chunk_texts(self, file_id: str, page: int) -> list[str]: ...

    def update_chunk_ocr(self, chunk_id: str, *, text: str, business_metadata: dict[str, Any]) -> dict[str, Any] | None: ...

    def list_embedding_rows(self, *, model: str, dimension: int) -> list[dict[str, Any]]: ...

    def list_lexical_rows(
        self,
        *,
        content_type: str,
        file_ids: list[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]: ...

    def upsert_embeddings(
        self,
        documents: list[Any],
        vectors: list[list[float]],
        *,
        model: str,
        dimension: int,
        token_count: int = 0,
    ) -> None: ...

    def get_active_assistant_version(self, assistant_id: str) -> dict[str, Any] | None: ...

    def get_assistant_name(self, assistant_id: str) -> str | None: ...

    def list_assistants(self) -> list[dict[str, Any]]: ...

    def get_assistant(self, assistant_id: str) -> dict[str, Any] | None: ...

    def get_assistant_template(self) -> dict[str, Any] | None: ...

    def create_assistant(
        self,
        *,
        assistant_id: str,
        name: str,
        description: str,
        template: dict[str, Any],
        parameter_schema: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]: ...

    def update_assistant(self, assistant_id: str, values: dict[str, Any]) -> dict[str, Any] | None: ...

    def update_active_assistant_version(
        self,
        assistant_id: str,
        *,
        model_config: dict[str, Any],
        node_prompts: dict[str, Any],
        rules: dict[str, Any],
        retrieval_config: dict[str, Any],
        parameter_schema: dict[str, Any],
        initialization_provenance: dict[str, Any],
        updated_at: str,
    ) -> dict[str, Any]: ...

    def set_assistant_knowledge_bases(self, assistant_id: str, knowledge_base_ids: list[str]) -> None: ...

    def assistant_scoped_file_ids(self, assistant_id: str) -> list[str]: ...

    def assistant_bound_knowledge_bases(self, assistant_id: str) -> list[dict[str, Any]]: ...

    def assistant_default_naming_file_id(self, assistant_id: str) -> str | None: ...

    def assistant_evidence_file_ids(
        self, assistant_id: str, *, excluded_file_ids: set[str] | None = None
    ) -> list[str]: ...

    def list_standard_corpus_file_ids(self, assistant_id: str) -> list[str]: ...

    def get_init_draft(self, assistant_id: str) -> dict[str, Any] | None: ...

    def upsert_init_draft(
        self, assistant_id: str, *, status: str, payload: dict[str, Any], job_id: str | None = None
    ) -> dict[str, Any]: ...

    def apply_init_draft(
        self,
        assistant_id: str,
        *,
        model_config: dict[str, Any],
        node_prompts: dict[str, Any],
        rules: dict[str, Any],
        retrieval_config: dict[str, Any],
        parameter_schema: dict[str, Any],
        initialization_provenance: dict[str, Any],
        applied_at: str,
    ) -> dict[str, Any]: ...


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


@dataclass(frozen=True)
class PostgresWorkspaceRepository:
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

    def is_active_member(self, *, workspace_id: str, user_id: str) -> bool:
        return self.current_workspace(workspace_id=workspace_id, user_id=user_id) is not None

    def current_workspace(
        self, *, workspace_id: str, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT w.id, w.name, w.slug, w.status,
                          wm.role, wm.status AS member_status
                   FROM workspaces w
                   JOIN workspace_members wm ON wm.workspace_id=w.id
                   JOIN users u ON u.id=wm.user_id
                   WHERE w.id=%s AND wm.user_id=%s
                     AND w.status='active' AND wm.status='active'
                     AND u.status='active'""",
                (workspace_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list_members(self, *, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT u.id, u.display_name, wm.role, wm.status
                   FROM workspace_members wm
                   JOIN users u ON u.id=wm.user_id
                   WHERE wm.workspace_id=%s
                   ORDER BY u.display_name, u.id""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]


@dataclass(frozen=True)
class PostgresSettingsRepository:
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

    def get(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=%s",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO settings(key, value) VALUES (%s, %s)
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""",
                (key, value),
            )

    def all(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}


@dataclass(frozen=True)
class PostgresContentRepository:
    """Workspace-scoped read adapter for migrated business content."""

    dsn: str

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL content reads require the optional psycopg dependency"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _scope(workspace_id: str | None = None) -> str:
        value = str(workspace_id or current_user.get_current_user().workspace_id).strip()
        if not value:
            raise ValueError("workspace identity is required")
        return value

    def list_knowledge_bases(
        self,
        *,
        limit: int = 100,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT kb.*, nf.name AS default_naming_file_name,
                          (
                            SELECT akb.assistant_id
                            FROM assistant_knowledge_bases akb
                            WHERE akb.knowledge_base_id=kb.id
                              AND akb.workspace_id=kb.workspace_id AND akb.enabled
                            ORDER BY akb.priority ASC, akb.assistant_id ASC
                            LIMIT 1
                          ) AS assistant_id,
                          COUNT(DISTINCT kbf.file_id) FILTER (WHERE kbf.enabled) AS file_count,
                          COUNT(DISTINCT c.id) FILTER (WHERE kbf.enabled) AS chunk_count
                   FROM knowledge_bases kb
                   LEFT JOIN files nf
                     ON nf.id=kb.default_naming_file_id AND nf.workspace_id=kb.workspace_id
                   LEFT JOIN knowledge_base_files kbf
                     ON kbf.knowledge_base_id=kb.id AND kbf.workspace_id=kb.workspace_id
                   LEFT JOIN chunks c
                     ON c.file_id=kbf.file_id AND c.workspace_id=kb.workspace_id
                   WHERE kb.status <> 'archived' AND kb.workspace_id=%s
                   GROUP BY kb.id, nf.name
                   ORDER BY kb.is_default DESC, kb.updated_at DESC, kb.name
                   LIMIT %s""",
                (workspace, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_knowledge_base(
        self,
        knowledge_base_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        workspace = self._scope(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT kb.*, nf.name AS default_naming_file_name,
                          (
                            SELECT akb.assistant_id
                            FROM assistant_knowledge_bases akb
                            WHERE akb.knowledge_base_id=kb.id
                              AND akb.workspace_id=kb.workspace_id AND akb.enabled
                            ORDER BY akb.priority ASC, akb.assistant_id ASC
                            LIMIT 1
                          ) AS assistant_id,
                          COUNT(DISTINCT kbf.file_id) FILTER (WHERE kbf.enabled) AS file_count,
                          COUNT(DISTINCT c.id) FILTER (WHERE kbf.enabled) AS chunk_count
                   FROM knowledge_bases kb
                   LEFT JOIN files nf
                     ON nf.id=kb.default_naming_file_id AND nf.workspace_id=kb.workspace_id
                   LEFT JOIN knowledge_base_files kbf
                     ON kbf.knowledge_base_id=kb.id AND kbf.workspace_id=kb.workspace_id
                   LEFT JOIN chunks c
                     ON c.file_id=kbf.file_id AND c.workspace_id=kb.workspace_id
                   WHERE kb.id=%s AND kb.workspace_id=%s
                   GROUP BY kb.id, nf.name""",
                (knowledge_base_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def create_knowledge_base(self, values: dict[str, Any]) -> dict[str, Any]:
        workspace = self._scope(values.get("workspace_id"))
        encoded = {
            key: json.dumps(values.get(key) or {}, ensure_ascii=False)
            for key in ("parser_config", "retrieval_config", "manual_rules", "few_shot_rules")
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO knowledge_bases
                   (id, workspace_id, name, description, status, is_default,
                    parser_config, retrieval_config, manual_rules, few_shot_rules,
                    default_naming_file_id, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,'active',%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s)""",
                (
                    values["id"], workspace, values["name"], values.get("description") or "",
                    bool(values.get("is_default", False)), encoded["parser_config"],
                    encoded["retrieval_config"], encoded["manual_rules"], encoded["few_shot_rules"],
                    values.get("default_naming_file_id"), values.get("created_at"),
                    values.get("updated_at"),
                ),
            )
        return self.get_knowledge_base(str(values["id"])) or {}

    def update_knowledge_base(
        self, knowledge_base_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        workspace = self._scope()
        allowed = {
            "name", "description", "retrieval_config", "parser_config", "manual_rules",
            "few_shot_rules", "default_naming_file_id", "is_default", "status",
        }
        json_fields = {"retrieval_config", "parser_config", "manual_rules", "few_shot_rules"}
        fields = {key: value for key, value in values.items() if key in allowed}
        if fields:
            assignments: list[str] = []
            params: list[Any] = []
            for key, value in fields.items():
                assignments.append(f"{key}=%s" + ("::jsonb" if key in json_fields else ""))
                params.append(json.dumps(value or {}, ensure_ascii=False) if key in json_fields else value)
            assignments.append("updated_at=now()")
            params.extend([knowledge_base_id, workspace])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE knowledge_bases SET " + ", ".join(assignments)
                    + " WHERE id=%s AND workspace_id=%s",
                    params,
                )
        return self.get_knowledge_base(knowledge_base_id)

    def delete_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            kb = conn.execute(
                "SELECT id, is_default FROM knowledge_bases WHERE id=%s AND workspace_id=%s",
                (knowledge_base_id, workspace),
            ).fetchone()
            if not kb:
                return None
            if kb["is_default"]:
                raise ValueError("default knowledge base cannot be deleted")
            owned = conn.execute(
                """SELECT f.id, f.path, f.object_key,
                          (SELECT COUNT(*) FROM knowledge_base_files other
                           WHERE other.file_id=f.id AND other.workspace_id=%s
                             AND other.knowledge_base_id<>%s) AS other_kbs,
                          COALESCE((SELECT array_agg(c.crop_path) FROM chunks c
                                    WHERE c.file_id=f.id AND c.workspace_id=%s), ARRAY[]::TEXT[]) AS crop_paths,
                          COALESCE((SELECT array_agg(c.crop_object_key) FROM chunks c
                                    WHERE c.file_id=f.id AND c.workspace_id=%s), ARRAY[]::TEXT[]) AS crop_object_keys,
                          (SELECT COUNT(*) FROM chunks c WHERE c.file_id=f.id AND c.workspace_id=%s) AS chunk_count
                   FROM knowledge_base_files kbf
                   JOIN files f ON f.id=kbf.file_id AND f.workspace_id=kbf.workspace_id
                   WHERE kbf.knowledge_base_id=%s AND kbf.workspace_id=%s""",
                (workspace, knowledge_base_id, workspace, workspace, workspace, knowledge_base_id, workspace),
            ).fetchall()
            exclusive = [dict(item) for item in owned if int(item["other_kbs"] or 0) == 0]
            assistant = conn.execute(
                """SELECT akb.assistant_id FROM assistant_knowledge_bases akb
                   WHERE akb.knowledge_base_id=%s AND akb.workspace_id=%s AND akb.enabled
                   LIMIT 1""",
                (knowledge_base_id, workspace),
            ).fetchone()
            for item in exclusive:
                conn.execute(
                    "DELETE FROM chunk_vector_index WHERE file_id=%s AND workspace_id=%s",
                    (item["id"], workspace),
                )
                conn.execute(
                    "DELETE FROM files WHERE id=%s AND workspace_id=%s",
                    (item["id"], workspace),
                )
            conn.execute(
                "DELETE FROM knowledge_bases WHERE id=%s AND workspace_id=%s",
                (knowledge_base_id, workspace),
            )
            assistant_id = str(assistant["assistant_id"]) if assistant else ""
            if assistant_id and assistant_id != "assistant_audit_template":
                conn.execute(
                    "UPDATE audit_assistants SET active_version_id=NULL WHERE id=%s AND workspace_id=%s",
                    (assistant_id, workspace),
                )
                conn.execute(
                    "DELETE FROM audit_assistants WHERE id=%s AND workspace_id=%s",
                    (assistant_id, workspace),
                )
        return {
            "deleted_file_count": len(exclusive),
            "deleted_chunk_count": sum(int(item["chunk_count"] or 0) for item in exclusive),
            "artifacts": exclusive,
        }

    def ensure_assistant_for_knowledge_base(
        self,
        knowledge_base_id: str,
        *,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        workspace = self._scope()
        with self._connect() as conn:
            kb = conn.execute(
                "SELECT id, name, description FROM knowledge_bases WHERE id=%s AND workspace_id=%s",
                (knowledge_base_id, workspace),
            ).fetchone()
            if not kb:
                raise KeyError(f"knowledge base not found: {knowledge_base_id}")
            existing = conn.execute(
                """SELECT a.*, v.version AS active_version
                   FROM assistant_knowledge_bases akb
                   JOIN audit_assistants a ON a.id=akb.assistant_id
                   LEFT JOIN assistant_versions v ON v.id=a.active_version_id
                   WHERE akb.knowledge_base_id=%s AND akb.workspace_id=%s AND akb.enabled
                   LIMIT 1""",
                (knowledge_base_id, workspace),
            ).fetchone()
            if existing:
                return dict(existing)
            template = conn.execute(
                """SELECT model_config, node_prompts, rules, retrieval_config, parameter_schema
                   FROM assistant_versions WHERE id='assistant_audit_template_v1'"""
            ).fetchone()
            if not template:
                raise RuntimeError("generic assistant version template is missing")
            assistant_id = f"assistant_{uuid.uuid4().hex}"
            version_id = f"{assistant_id}_v1"
            assistant_name = name.strip() or f"{kb['name']} audit"
            conflict = conn.execute(
                "SELECT 1 FROM audit_assistants WHERE workspace_id=%s AND name=%s",
                (workspace, assistant_name),
            ).fetchone()
            if conflict:
                assistant_name = f"{assistant_name} ({uuid.uuid4().hex[:8]})"
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            provenance = json.dumps(
                {
                    "source": "template_snapshot",
                    "template_version_id": "assistant_audit_template_v1",
                    "copied_at": now,
                },
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT INTO audit_assistants
                   (id, workspace_id, name, description, status, active_version_id, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,'active',NULL,%s,%s)""",
                (assistant_id, workspace, assistant_name, description or kb["description"] or "", now, now),
            )
            conn.execute(
                """INSERT INTO assistant_versions
                   (id, assistant_id, version, name, status, model_config, node_prompts, rules,
                    retrieval_config, parameter_schema, category_profile, initialization_provenance,
                    created_at, activated_at)
                   VALUES (%s,%s,1,'','active',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'{}'::jsonb,%s::jsonb,%s,%s)""",
                (
                    version_id, assistant_id,
                    json.dumps(template["model_config"] or {}, ensure_ascii=False),
                    json.dumps(template["node_prompts"] or {}, ensure_ascii=False),
                    json.dumps(template["rules"] or {}, ensure_ascii=False),
                    json.dumps(template["retrieval_config"] or {}, ensure_ascii=False),
                    json.dumps(template["parameter_schema"] or {}, ensure_ascii=False),
                    provenance, now, now,
                ),
            )
            conn.execute(
                "UPDATE audit_assistants SET active_version_id=%s WHERE id=%s",
                (version_id, assistant_id),
            )
            conn.execute(
                """INSERT INTO assistant_knowledge_bases
                   (assistant_id, knowledge_base_id, workspace_id, priority, enabled)
                   VALUES (%s,%s,%s,0,TRUE)""",
                (assistant_id, knowledge_base_id, workspace),
            )
            row = conn.execute(
                """SELECT a.*, v.version AS active_version
                   FROM audit_assistants a JOIN assistant_versions v ON v.id=a.active_version_id
                   WHERE a.id=%s AND a.workspace_id=%s""",
                (assistant_id, workspace),
            ).fetchone()
        return dict(row)

    @staticmethod
    def _file_select() -> str:
        return """SELECT f.*,
                          latest.status AS parse_status,
                          latest.error AS parse_error,
                          latest.markdown_path AS parse_markdown_path,
                          latest.markdown_object_key AS parse_markdown_object_key,
                          latest.raw_zip_path AS parse_raw_zip_path,
                          latest.raw_zip_object_key AS parse_raw_zip_object_key,
                          latest.result AS parse_result
                   FROM files f
                   LEFT JOIN LATERAL (
                     SELECT p.status, p.error, p.markdown_path,
                            p.markdown_object_key, p.raw_zip_path,
                            p.raw_zip_object_key, p.result
                     FROM document_parses p
                     WHERE p.file_id=f.id AND p.workspace_id=f.workspace_id
                     ORDER BY p.created_at DESC, p.id DESC
                     LIMIT 1
                   ) latest ON TRUE"""

    def list_files(
        self,
        *,
        limit: int = 500,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        bounded_limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                self._file_select()
                + " WHERE f.workspace_id=%s ORDER BY f.created_at DESC, f.id DESC LIMIT %s",
                (workspace, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_file(
        self,
        file_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        workspace = self._scope(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                self._file_select() + " WHERE f.id=%s AND f.workspace_id=%s",
                (file_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def create_file(self, values: dict[str, Any]) -> dict[str, Any]:
        workspace = self._scope(values.get("workspace_id"))
        metadata = json.dumps(values.get("metadata") or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO files
                   (id, workspace_id, name, path, sha, object_key, object_sha256,
                    object_size, page_count, metadata, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    values["id"], workspace, values["name"], values["path"],
                    values.get("sha"), values.get("object_key") or "",
                    values.get("object_sha256") or "", int(values.get("object_size") or 0),
                    values.get("page_count"), metadata, values.get("created_at"),
                ),
            )
        return self.get_file(str(values["id"])) or {}

    def update_file(self, file_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        workspace = self._scope()
        allowed = {"name", "metadata", "object_key", "object_sha256", "object_size", "page_count"}
        fields = {key: value for key, value in values.items() if key in allowed}
        if fields:
            assignments: list[str] = []
            params: list[Any] = []
            json_fields = {"metadata"}
            for key, value in fields.items():
                assignments.append(f"{key}=%s" + ("::jsonb" if key in json_fields else ""))
                params.append(
                    json.dumps(value or {}, ensure_ascii=False) if key in json_fields else value
                )
            assignments.append("updated_at=now()")
            params.extend([file_id, workspace])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE files SET " + ", ".join(assignments)
                    + " WHERE id=%s AND workspace_id=%s",
                    params,
                )
        return self.get_file(file_id)

    def delete_file(self, file_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE id=%s AND workspace_id=%s",
                (file_id, workspace),
            ).fetchone()
            if not row:
                return None
            crops = conn.execute(
                "SELECT crop_path FROM chunks WHERE file_id=%s AND workspace_id=%s",
                (file_id, workspace),
            ).fetchall()
            conn.execute(
                "UPDATE knowledge_bases SET default_naming_file_id=NULL, updated_at=now()"
                " WHERE default_naming_file_id=%s AND workspace_id=%s",
                (file_id, workspace),
            )
            conn.execute(
                "DELETE FROM chunk_vector_index WHERE file_id=%s AND workspace_id=%s",
                (file_id, workspace),
            )
            conn.execute(
                "DELETE FROM files WHERE id=%s AND workspace_id=%s",
                (file_id, workspace),
            )
        result = dict(row)
        result["crop_paths"] = [str(item["crop_path"] or "") for item in crops]
        return result

    def attach_file_to_knowledge_base(
        self,
        knowledge_base_id: str,
        file_id: str,
        *,
        role: str,
        corpus_kind: str,
        enabled: bool = True,
    ) -> None:
        workspace = self._scope()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO knowledge_base_files
                   (knowledge_base_id, file_id, workspace_id, role, corpus_kind, enabled)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (knowledge_base_id, file_id) DO UPDATE SET
                     role=EXCLUDED.role, corpus_kind=EXCLUDED.corpus_kind,
                     enabled=EXCLUDED.enabled""",
                (knowledge_base_id, file_id, workspace, role, corpus_kind, enabled),
            )

    def update_knowledge_base_file(
        self, knowledge_base_id: str, file_id: str, values: dict[str, Any]
    ) -> bool:
        workspace = self._scope()
        allowed = {"enabled", "role", "corpus_kind"}
        fields = {key: value for key, value in values.items() if key in allowed}
        with self._connect() as conn:
            exists = conn.execute(
                """SELECT 1 FROM knowledge_base_files
                   WHERE knowledge_base_id=%s AND file_id=%s AND workspace_id=%s""",
                (knowledge_base_id, file_id, workspace),
            ).fetchone()
            if not exists:
                return False
            if fields:
                assignments = [f"{key}=%s" for key in fields]
                params = list(fields.values()) + [knowledge_base_id, file_id, workspace]
                conn.execute(
                    "UPDATE knowledge_base_files SET " + ", ".join(assignments)
                    + " WHERE knowledge_base_id=%s AND file_id=%s AND workspace_id=%s",
                    params,
                )
            return True

    def remove_file_from_knowledge_base(self, knowledge_base_id: str, file_id: str) -> bool:
        workspace = self._scope()
        with self._connect() as conn:
            result = conn.execute(
                """DELETE FROM knowledge_base_files
                   WHERE knowledge_base_id=%s AND file_id=%s AND workspace_id=%s""",
                (knowledge_base_id, file_id, workspace),
            )
            if result.rowcount:
                remaining = conn.execute(
                    """SELECT 1 FROM knowledge_base_files
                       WHERE file_id=%s AND workspace_id=%s LIMIT 1""",
                    (file_id, workspace),
                ).fetchone()
                fallback = conn.execute(
                    """SELECT id FROM knowledge_bases
                       WHERE workspace_id=%s AND is_default AND status='active'
                       ORDER BY created_at LIMIT 1""",
                    (workspace,),
                ).fetchone()
                if not remaining and fallback:
                    conn.execute(
                        """INSERT INTO knowledge_base_files
                           (knowledge_base_id, file_id, workspace_id, role, corpus_kind,
                            enabled, created_at)
                           VALUES (%s,%s,%s,'source','standard',TRUE,now())
                           ON CONFLICT (knowledge_base_id, file_id) DO UPDATE SET enabled=TRUE""",
                        (fallback["id"], file_id, workspace),
                    )
        return result.rowcount > 0

    def set_default_naming_file(self, knowledge_base_id: str, file_id: str | None) -> None:
        workspace = self._scope()
        with self._connect() as conn:
            conn.execute(
                "UPDATE knowledge_bases SET default_naming_file_id=%s, updated_at=now()"
                " WHERE id=%s AND workspace_id=%s",
                (file_id, knowledge_base_id, workspace),
            )

    def create_chunk(self, values: dict[str, Any]) -> dict[str, Any]:
        workspace = self._scope(values.get("workspace_id"))
        json_fields = {
            "bbox", "metadata", "business_metadata", "metadata_llm", "source_trace",
            "chunk_logic", "relations", "ui_state", "indexing",
        }
        columns = [
            "id", "workspace_id", "file_id", "page", "bbox", "rotation", "crop_path",
            "crop_object_key", "crop_sha256", "crop_size", "text", "text_source",
            "metadata", "business_metadata", "metadata_llm", "source_trace", "chunk_logic",
            "relations", "status", "created_at", "updated_at",
        ]
        params: list[Any] = []
        placeholders: list[str] = []
        for column in columns:
            value = workspace if column == "workspace_id" else values.get(column)
            if column in json_fields:
                placeholders.append("%s::jsonb")
                params.append(json.dumps(value or {}, ensure_ascii=False))
            else:
                placeholders.append("%s")
                params.append(value)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chunks (" + ",".join(columns) + ") VALUES ("
                + ",".join(placeholders) + ")",
                params,
            )
        return self.get_chunk(str(values["id"])) or {}

    def update_chunk(self, chunk_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        workspace = self._scope()
        allowed = {
            "text", "text_source", "metadata", "business_metadata", "metadata_llm",
            "source_trace", "chunk_logic", "relations", "ui_state", "indexing", "status",
            "crop_object_key", "crop_sha256", "crop_size",
        }
        json_fields = {
            "metadata", "business_metadata", "metadata_llm", "source_trace", "chunk_logic",
            "relations", "ui_state", "indexing",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        if fields:
            assignments: list[str] = []
            params: list[Any] = []
            for key, value in fields.items():
                assignments.append(f"{key}=%s" + ("::jsonb" if key in json_fields else ""))
                params.append(json.dumps(value or {}, ensure_ascii=False) if key in json_fields else value)
            assignments.append("updated_at=now()")
            params.extend([chunk_id, workspace])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE chunks SET " + ", ".join(assignments)
                    + " WHERE id=%s AND workspace_id=%s",
                    params,
                )
        return self.get_chunk(chunk_id)

    def delete_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chunks WHERE id=%s AND workspace_id=%s",
                (chunk_id, workspace),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "DELETE FROM chunk_vector_index WHERE chunk_id=%s AND workspace_id=%s",
                (chunk_id, workspace),
            )
            conn.execute(
                "DELETE FROM chunks WHERE id=%s AND workspace_id=%s",
                (chunk_id, workspace),
            )
        return dict(row)

    def latest_parse(
        self,
        file_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        workspace = self._scope(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT p.* FROM document_parses p
                   WHERE p.file_id=%s AND p.workspace_id=%s
                   ORDER BY p.created_at DESC, p.id DESC LIMIT 1""",
                (file_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def list_file_parses(
        self,
        file_id: str,
        *,
        limit: int = 100,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT p.* FROM document_parses p
                   WHERE p.file_id=%s AND p.workspace_id=%s
                   ORDER BY p.created_at DESC, p.id DESC LIMIT %s""",
                (file_id, workspace, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_knowledge_base_files(
        self,
        knowledge_base_id: str,
        *,
        file_id: str | None = None,
        limit: int = 500,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        bounded_limit = max(1, min(int(limit), 1000))
        clauses = [
            "kbf.knowledge_base_id=%s",
            "kbf.workspace_id=%s",
            "f.workspace_id=%s",
            "kb.workspace_id=%s",
            "kb.default_naming_file_id IS DISTINCT FROM f.id",
            "LOWER(COALESCE(f.metadata->>'doc_role', '')) NOT IN ('report', 'naming', 'sample_report')",
            "LOWER(COALESCE(f.metadata->>'doc_type', '')) NOT IN ('report', 'naming', 'sample_report')",
        ]
        params: list[Any] = [knowledge_base_id, workspace, workspace, workspace]
        if file_id:
            clauses.append("kbf.file_id=%s")
            params.append(file_id)
        params.append(bounded_limit)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT f.id, f.name, f.page_count, f.metadata, f.created_at,
                          kbf.role, kbf.corpus_kind, kbf.enabled,
                          (SELECT count(*) FROM chunks c
                           WHERE c.file_id=f.id AND c.workspace_id=f.workspace_id) AS chunk_count,
                          (SELECT count(*) FROM chunks c
                           WHERE c.file_id=f.id AND c.workspace_id=f.workspace_id
                             AND c.status='approved') AS approved_count,
                          latest.status AS parse_status, latest.error AS parse_error,
                          latest.markdown_path AS parse_markdown_path,
                          latest.markdown_object_key AS parse_markdown_object_key,
                          latest.raw_zip_path AS parse_raw_zip_path,
                          latest.raw_zip_object_key AS parse_raw_zip_object_key,
                          latest.result AS parse_result
                   FROM knowledge_base_files kbf
                   JOIN knowledge_bases kb ON kb.id=kbf.knowledge_base_id
                   JOIN files f ON f.id=kbf.file_id
                   LEFT JOIN LATERAL (
                     SELECT p.status, p.error, p.markdown_path, p.markdown_object_key,
                            p.raw_zip_path, p.raw_zip_object_key, p.result
                     FROM document_parses p
                     WHERE p.file_id=f.id AND p.workspace_id=f.workspace_id
                     ORDER BY p.created_at DESC, p.id DESC LIMIT 1
                   ) latest ON TRUE
                   WHERE """ + " AND ".join(clauses)
                + " ORDER BY f.created_at DESC, f.id DESC LIMIT %s",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_knowledge_base_chunks(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.text,
                          c.status, c.business_metadata, c.source_trace, c.updated_at
                   FROM knowledge_base_files kbf
                   JOIN knowledge_bases kb ON kb.id=kbf.knowledge_base_id
                   JOIN chunks c ON c.file_id=kbf.file_id
                   JOIN files f ON f.id=c.file_id
                   WHERE kbf.knowledge_base_id=%s AND kbf.workspace_id=%s
                     AND kb.workspace_id=%s AND c.workspace_id=%s AND f.workspace_id=%s
                     AND kbf.enabled
                   ORDER BY c.updated_at DESC, c.id
                   LIMIT %s OFFSET %s""",
                (knowledge_base_id, workspace, workspace, workspace, workspace,
                 bounded_limit, bounded_offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_chunks(
        self,
        *,
        file_id: str | None = None,
        page: int | None = None,
        has_llm_suggestions: bool = False,
        limit: int = 500,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = self._scope(workspace_id)
        clauses = ["workspace_id=%s"]
        params: list[Any] = [workspace]
        if file_id:
            clauses.append("file_id=%s")
            params.append(file_id)
        if page is not None:
            clauses.append("page=%s")
            params.append(page)
        if has_llm_suggestions:
            clauses.append(
                """(
                    (
                        jsonb_typeof(metadata_llm->'keywords'->'value') = 'array'
                        AND jsonb_array_length(metadata_llm->'keywords'->'value') > 0
                    )
                    OR (
                        jsonb_typeof(metadata_llm->'questions'->'value') = 'array'
                        AND jsonb_array_length(metadata_llm->'questions'->'value') > 0
                    )
                )"""
            )
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE " + " AND ".join(clauses)
                + " ORDER BY page, created_at, id LIMIT %s",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chunk(
        self,
        chunk_id: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        workspace = self._scope(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chunks WHERE id=%s AND workspace_id=%s",
                (chunk_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def primary_parser_config(self, file_id: str) -> dict[str, Any]:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT kb.parser_config
                   FROM knowledge_base_files kbf
                   JOIN knowledge_bases kb ON kb.id=kbf.knowledge_base_id
                   WHERE kbf.file_id=%s AND kbf.workspace_id=%s
                     AND kb.workspace_id=%s AND kb.status='active' AND kbf.enabled
                   ORDER BY kb.is_default DESC, kbf.created_at ASC
                   LIMIT 1""",
                (file_id, workspace, workspace),
            ).fetchone()
        return _decode(row["parser_config"]) if row else {}

    def create_parse(
        self,
        parse_id: str,
        file_id: str,
        *,
        provider: str = "mineru",
    ) -> dict[str, Any]:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO document_parses
                   (id, workspace_id, file_id, provider, status, result, error)
                   SELECT %s, workspace_id, %s, %s, 'queued', '{}'::jsonb, ''
                   FROM files WHERE id=%s AND workspace_id=%s
                   RETURNING *""",
                (parse_id, file_id, provider, file_id, workspace),
            ).fetchone()
            if not row:
                raise KeyError("file not found")
        return dict(row)

    def update_parse(self, parse_id: str, **fields: Any) -> dict[str, Any] | None:
        workspace = self._scope()
        allowed = {
            "status", "error", "markdown_path", "raw_zip_path",
            "markdown_object_key", "markdown_sha256", "markdown_size",
            "raw_zip_object_key", "raw_zip_sha256", "raw_zip_size", "result",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported parse fields: {sorted(unknown)}")
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key}=%s::jsonb" if key == "result" else f"{key}=%s")
            params.append(json.dumps(value or {}, ensure_ascii=False) if key == "result" else value)
        params.extend([parse_id, workspace])
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE document_parses SET " + ", ".join(assignments)
                + " WHERE id=%s AND workspace_id=%s RETURNING *",
                params,
            ).fetchone()
        return dict(row) if row else None

    def delete_file_chunks(self, file_id: str) -> list[dict[str, Any]]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, crop_path, crop_object_key FROM chunks WHERE file_id=%s AND workspace_id=%s",
                (file_id, workspace),
            ).fetchall()
            conn.execute(
                "DELETE FROM chunks WHERE file_id=%s AND workspace_id=%s",
                (file_id, workspace),
            )
        return [dict(row) for row in rows]

    def insert_chunk(self, **fields: Any) -> dict[str, Any]:
        workspace = self._scope(fields.get("workspace_id"))
        values = {
            "id": str(fields["id"]),
            "file_id": str(fields["file_id"]),
            "page": int(fields["page"]),
            "bbox": fields.get("bbox") or {},
            "rotation": int(fields.get("rotation") or 0),
            "crop_path": fields.get("crop_path"),
            "crop_object_key": str(fields.get("crop_object_key") or ""),
            "crop_sha256": str(fields.get("crop_sha256") or ""),
            "crop_size": int(fields.get("crop_size") or 0),
            "text": fields.get("text"),
            "text_source": fields.get("text_source") or "pending",
            "metadata": fields.get("metadata") or {},
            "business_metadata": fields.get("business_metadata") or {},
            "metadata_llm": fields.get("metadata_llm") or {},
            "source_trace": fields.get("source_trace") or {},
            "chunk_logic": fields.get("chunk_logic") or {},
            "relations": fields.get("relations") or {},
            "status": fields.get("status") or "pending",
        }
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO chunks
                   (id, workspace_id, file_id, page, bbox, rotation, crop_path,
                    crop_object_key, crop_sha256, crop_size, text, text_source,
                    metadata, business_metadata, metadata_llm, source_trace,
                    chunk_logic, relations, status)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,
                           %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                   RETURNING *""",
                (
                    values["id"], workspace, values["file_id"], values["page"],
                    json.dumps(values["bbox"], ensure_ascii=False), values["rotation"],
                    values["crop_path"], values["crop_object_key"], values["crop_sha256"],
                    values["crop_size"], values["text"], values["text_source"],
                    json.dumps(values["metadata"], ensure_ascii=False),
                    json.dumps(values["business_metadata"], ensure_ascii=False),
                    json.dumps(values["metadata_llm"], ensure_ascii=False),
                    json.dumps(values["source_trace"], ensure_ascii=False),
                    json.dumps(values["chunk_logic"], ensure_ascii=False),
                    json.dumps(values["relations"], ensure_ascii=False), values["status"],
                ),
            ).fetchone()
        return dict(row)

    def list_chunk_texts(self, file_id: str, page: int) -> list[str]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT text FROM chunks WHERE file_id=%s AND page=%s AND workspace_id=%s",
                (file_id, page, workspace),
            ).fetchall()
        return [str(row["text"] or "") for row in rows]

    def update_chunk_ocr(
        self,
        chunk_id: str,
        *,
        text: str,
        business_metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """UPDATE chunks
                   SET text=%s, text_source='ocr', status='pending',
                       business_metadata=%s::jsonb, updated_at=now()
                   WHERE id=%s AND workspace_id=%s
                   RETURNING *""",
                (text, json.dumps(business_metadata, ensure_ascii=False), chunk_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def list_embedding_rows(self, *, model: str, dimension: int) -> list[dict[str, Any]]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.text, c.business_metadata,
                          vi.text_sha256 AS indexed_text_sha256
                   FROM chunks c
                   LEFT JOIN chunk_vector_index vi
                     ON vi.workspace_id=c.workspace_id AND vi.chunk_id=c.id
                    AND vi.model=%s AND vi.dimension=%s
                   WHERE c.workspace_id=%s AND c.status='approved'
                   ORDER BY c.file_id, c.page, c.created_at, c.id""",
                (model, dimension, workspace),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_lexical_rows(
        self,
        *,
        content_type: str,
        file_ids: list[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        workspace = self._scope()
        bounded_limit = max(1, min(int(limit), 50000))
        clauses = [
            "c.workspace_id=%s",
            "f.workspace_id=%s",
            "c.status='approved'",
            "COALESCE(c.business_metadata->>'content_type', 'text')=%s",
        ]
        params: list[Any] = [workspace, workspace, content_type]
        if file_ids is not None:
            if not file_ids:
                return []
            clauses.append("c.file_id = ANY(%s)")
            params.append(file_ids)
        params.append(bounded_limit)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.file_id, f.name AS file_name, c.page,
                          c.crop_path, c.crop_object_key, c.text,
                          c.business_metadata, c.source_trace
                   FROM chunks c
                   JOIN files f ON f.id=c.file_id
                   WHERE """ + " AND ".join(clauses) + """
                   ORDER BY c.file_id, c.page, c.created_at, c.id LIMIT %s""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_embeddings(
        self,
        documents: list[Any],
        vectors: list[list[float]],
        *,
        model: str,
        dimension: int,
        token_count: int = 0,
    ) -> None:
        if len(documents) != len(vectors):
            raise ValueError("document and vector counts differ")
        workspace = self._scope()
        rows: list[tuple[Any, ...]] = []
        with self._connect() as conn:
            for document, vector in zip(documents, vectors, strict=True):
                if len(vector) != dimension:
                    raise ValueError(f"unexpected vector dimension for {document.chunk_id}")
                chunk = conn.execute(
                    """SELECT c.id, c.file_id, c.page, c.crop_path, c.crop_object_key,
                              c.crop_sha256, c.crop_size, c.text, c.business_metadata,
                              c.source_trace, f.name AS file_name
                       FROM chunks c JOIN files f ON f.id=c.file_id
                       WHERE c.id=%s AND c.workspace_id=%s AND f.workspace_id=%s""",
                    (str(document.chunk_id), workspace, workspace),
                ).fetchone()
                if not chunk:
                    raise KeyError(f"chunk not found: {document.chunk_id}")
                vector_literal = "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"
                rows.append(
                    (
                        workspace,
                        chunk["id"],
                        model,
                        dimension,
                        document.text_sha256,
                        vector_literal,
                        chunk["file_id"],
                        chunk["file_name"] or "",
                        chunk["page"],
                        chunk["crop_path"],
                        chunk["crop_object_key"] or "",
                        chunk["crop_sha256"] or "",
                        chunk["crop_size"] or 0,
                        chunk["text"] or "",
                        json.dumps(chunk["business_metadata"] or {}, ensure_ascii=False),
                        json.dumps(chunk["source_trace"] or {}, ensure_ascii=False),
                    )
                )
            if rows:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO chunk_vector_index
                           (workspace_id, chunk_id, model, dimension, text_sha256, embedding,
                            file_id, file_name, page, crop_path, crop_object_key, crop_sha256,
                            crop_size, text, business_metadata, source_trace, status)
                           VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,'approved')
                           ON CONFLICT (workspace_id, chunk_id, model, dimension) DO UPDATE SET
                             text_sha256=EXCLUDED.text_sha256, embedding=EXCLUDED.embedding,
                             file_id=EXCLUDED.file_id, file_name=EXCLUDED.file_name, page=EXCLUDED.page,
                             crop_path=EXCLUDED.crop_path, crop_object_key=EXCLUDED.crop_object_key,
                             crop_sha256=EXCLUDED.crop_sha256, crop_size=EXCLUDED.crop_size,
                             text=EXCLUDED.text, business_metadata=EXCLUDED.business_metadata,
                             source_trace=EXCLUDED.source_trace, updated_at=now()""",
                        rows,
                    )

    def get_active_assistant_version(self, assistant_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT v.*
                   FROM audit_assistants a
                   JOIN assistant_versions v ON v.id=a.active_version_id
                   WHERE a.id=%s AND a.workspace_id=%s AND a.status='active'""",
                (assistant_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def get_assistant_name(self, assistant_id: str) -> str | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM audit_assistants WHERE id=%s AND workspace_id=%s",
                (assistant_id, workspace),
            ).fetchone()
        return str(row["name"]) if row and row["name"] else None

    def list_assistants(self) -> list[dict[str, Any]]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT a.*, v.version AS active_version
                   FROM audit_assistants a
                   LEFT JOIN assistant_versions v ON v.id=a.active_version_id
                   WHERE a.workspace_id=%s AND a.status <> 'archived'
                   ORDER BY a.updated_at DESC, a.name""",
                (workspace,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_assistant(self, assistant_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT a.*, v.version AS active_version
                   FROM audit_assistants a
                   LEFT JOIN assistant_versions v ON v.id=a.active_version_id
                   WHERE a.id=%s AND a.workspace_id=%s""",
                (assistant_id, workspace),
            ).fetchone()
        return dict(row) if row else None

    def get_assistant_template(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT model_config, node_prompts, rules, retrieval_config, parameter_schema
                   FROM assistant_versions WHERE id='assistant_audit_template_v1'"""
            ).fetchone()
        return dict(row) if row else None

    def create_assistant(
        self,
        *,
        assistant_id: str,
        name: str,
        description: str,
        template: dict[str, Any],
        parameter_schema: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        workspace = self._scope()
        version_id = f"{assistant_id}_v1"
        provenance = json.dumps(
            {
                "source": "template_snapshot",
                "template_version_id": "assistant_audit_template_v1",
                "copied_at": created_at,
            },
            ensure_ascii=False,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_assistants
                   (id, workspace_id, name, description, status, active_version_id, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,'active',NULL,%s,%s)""",
                (assistant_id, workspace, name, description, created_at, created_at),
            )
            conn.execute(
                """INSERT INTO assistant_versions
                   (id, assistant_id, version, name, status, model_config, node_prompts, rules,
                    retrieval_config, parameter_schema, category_profile, initialization_provenance,
                    created_at, activated_at)
                   VALUES (%s,%s,1,'','active',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'{}'::jsonb,%s::jsonb,%s,%s)""",
                (
                    version_id, assistant_id,
                    json.dumps(template.get("model_config") or {}, ensure_ascii=False),
                    json.dumps(template.get("node_prompts") or {}, ensure_ascii=False),
                    json.dumps(template.get("rules") or {}, ensure_ascii=False),
                    json.dumps(template.get("retrieval_config") or {}, ensure_ascii=False),
                    json.dumps(parameter_schema or {}, ensure_ascii=False),
                    provenance, created_at, created_at,
                ),
            )
            conn.execute(
                "UPDATE audit_assistants SET active_version_id=%s WHERE id=%s AND workspace_id=%s",
                (version_id, assistant_id, workspace),
            )
        return self.get_assistant(assistant_id) or {}

    def update_assistant(self, assistant_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        workspace = self._scope()
        fields = {key: value for key, value in values.items() if key in {"name", "description", "status"}}
        if fields:
            params = list(fields.values()) + [assistant_id, workspace]
            with self._connect() as conn:
                conn.execute(
                    "UPDATE audit_assistants SET "
                    + ", ".join(f"{key}=%s" for key in fields)
                    + ", updated_at=now() WHERE id=%s AND workspace_id=%s",
                    params,
                )
        return self.get_assistant(assistant_id)

    def update_active_assistant_version(
        self,
        assistant_id: str,
        *,
        model_config: dict[str, Any],
        node_prompts: dict[str, Any],
        rules: dict[str, Any],
        retrieval_config: dict[str, Any],
        parameter_schema: dict[str, Any],
        initialization_provenance: dict[str, Any],
        updated_at: str,
    ) -> dict[str, Any]:
        workspace = self._scope()
        encoded = {
            "model_config": json.dumps(model_config or {}, ensure_ascii=False),
            "node_prompts": json.dumps(node_prompts or {}, ensure_ascii=False),
            "rules": json.dumps(rules or {}, ensure_ascii=False),
            "retrieval_config": json.dumps(retrieval_config or {}, ensure_ascii=False),
            "parameter_schema": json.dumps(parameter_schema or {}, ensure_ascii=False),
            "initialization_provenance": json.dumps(initialization_provenance or {}, ensure_ascii=False),
        }
        with self._connect() as conn:
            assistant = conn.execute(
                "SELECT active_version_id FROM audit_assistants WHERE id=%s AND workspace_id=%s FOR UPDATE",
                (assistant_id, workspace),
            ).fetchone()
            if not assistant:
                raise KeyError("assistant not found")
            version_id = assistant["active_version_id"]
            if version_id and not conn.execute(
                "SELECT 1 FROM assistant_versions WHERE id=%s AND assistant_id=%s",
                (version_id, assistant_id),
            ).fetchone():
                version_id = None
            if not version_id:
                latest = conn.execute(
                    "SELECT id FROM assistant_versions WHERE assistant_id=%s ORDER BY version DESC LIMIT 1",
                    (assistant_id,),
                ).fetchone()
                version_id = latest["id"] if latest else None
            if version_id:
                conn.execute(
                    """UPDATE assistant_versions
                       SET status='active', model_config=%s::jsonb, node_prompts=%s::jsonb,
                           rules=%s::jsonb, retrieval_config=%s::jsonb, parameter_schema=%s::jsonb,
                           category_profile='{}'::jsonb, initialization_provenance=%s::jsonb,
                           activated_at=COALESCE(activated_at,%s)
                       WHERE id=%s AND assistant_id=%s""",
                    (
                        encoded["model_config"], encoded["node_prompts"], encoded["rules"],
                        encoded["retrieval_config"], encoded["parameter_schema"],
                        encoded["initialization_provenance"], updated_at, version_id, assistant_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM assistant_versions WHERE assistant_id=%s AND id<>%s",
                    (assistant_id, version_id),
                )
            else:
                version_id = f"{assistant_id}_v1_{uuid.uuid4().hex[:8]}"
                conn.execute(
                    """INSERT INTO assistant_versions
                       (id,assistant_id,version,name,status,model_config,node_prompts,rules,
                        retrieval_config,parameter_schema,category_profile,initialization_provenance,
                        created_at,activated_at)
                       VALUES (%s,%s,1,'','active',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'{}'::jsonb,%s::jsonb,%s,%s)""",
                    (
                        version_id, assistant_id, encoded["model_config"], encoded["node_prompts"],
                        encoded["rules"], encoded["retrieval_config"], encoded["parameter_schema"],
                        encoded["initialization_provenance"], updated_at, updated_at,
                    ),
                )
            conn.execute(
                "UPDATE audit_assistants SET active_version_id=%s, status='active', updated_at=%s WHERE id=%s AND workspace_id=%s",
                (version_id, updated_at, assistant_id, workspace),
            )
        row = self.get_active_assistant_version(assistant_id)
        if not row:
            raise RuntimeError("active assistant version missing after update")
        return row

    def set_assistant_knowledge_bases(self, assistant_id: str, knowledge_base_ids: list[str]) -> None:
        workspace = self._scope()
        unique_ids = list(dict.fromkeys(knowledge_base_ids))
        if len(unique_ids) > 1:
            raise ValueError("assistant can bind at most one knowledge base")
        with self._connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM audit_assistants WHERE id=%s AND workspace_id=%s",
                (assistant_id, workspace),
            ).fetchone():
                raise KeyError("assistant not found")
            if unique_ids:
                kb_id = unique_ids[0]
                if not conn.execute(
                    "SELECT 1 FROM knowledge_bases WHERE id=%s AND workspace_id=%s",
                    (kb_id, workspace),
                ).fetchone():
                    raise ValueError("knowledge base does not exist")
                conflict = conn.execute(
                    """SELECT assistant_id FROM assistant_knowledge_bases
                       WHERE knowledge_base_id=%s AND workspace_id=%s AND enabled AND assistant_id<>%s""",
                    (kb_id, workspace, assistant_id),
                ).fetchone()
                if conflict:
                    raise ValueError("knowledge base is already bound to another assistant")
            conn.execute(
                "DELETE FROM assistant_knowledge_bases WHERE assistant_id=%s AND workspace_id=%s",
                (assistant_id, workspace),
            )
            if unique_ids:
                kb_id = unique_ids[0]
                conn.execute(
                    "DELETE FROM assistant_knowledge_bases WHERE knowledge_base_id=%s AND workspace_id=%s",
                    (kb_id, workspace),
                )
                conn.execute(
                    """INSERT INTO assistant_knowledge_bases
                       (assistant_id, knowledge_base_id, workspace_id, priority, enabled)
                       VALUES (%s,%s,%s,0,TRUE)""",
                    (assistant_id, kb_id, workspace),
                )

    def assistant_scoped_file_ids(self, assistant_id: str) -> list[str]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT kbf.file_id
                   FROM assistant_knowledge_bases akb
                   JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
                   JOIN knowledge_base_files kbf
                     ON kbf.knowledge_base_id=akb.knowledge_base_id
                    AND kbf.workspace_id=akb.workspace_id
                   WHERE akb.assistant_id=%s AND akb.workspace_id=%s
                     AND akb.enabled AND kb.status='active' AND kbf.enabled
                   ORDER BY kbf.file_id""",
                (assistant_id, workspace),
            ).fetchall()
        return [str(row["file_id"]) for row in rows]

    def assistant_bound_knowledge_bases(self, assistant_id: str) -> list[dict[str, Any]]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT kb.*, akb.priority AS bind_priority
                   FROM assistant_knowledge_bases akb
                   JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
                   WHERE akb.assistant_id=%s AND akb.workspace_id=%s
                     AND akb.enabled AND kb.status='active'
                   ORDER BY akb.priority ASC, kb.name ASC""",
                (assistant_id, workspace),
            ).fetchall()
        return [dict(row) for row in rows]

    def assistant_default_naming_file_id(self, assistant_id: str) -> str | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT kb.default_naming_file_id
                   FROM assistant_knowledge_bases akb
                   JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
                   WHERE akb.assistant_id=%s AND akb.workspace_id=%s AND akb.enabled
                     AND kb.status='active' AND kb.default_naming_file_id IS NOT NULL
                     AND TRIM(kb.default_naming_file_id) <> ''
                   ORDER BY akb.priority DESC, kb.name ASC
                   LIMIT 1""",
                (assistant_id, workspace),
            ).fetchone()
        return str(row["default_naming_file_id"]) if row else None

    def assistant_evidence_file_ids(
        self,
        assistant_id: str,
        *,
        excluded_file_ids: set[str] | None = None,
    ) -> list[str]:
        excluded = {str(item) for item in (excluded_file_ids or set())}
        file_ids = [
            file_id for file_id in self.assistant_scoped_file_ids(assistant_id)
            if file_id not in excluded
        ]
        if not file_ids:
            raise ValueError("assistant has no evidence files after excluding runtime inputs")
        return file_ids

    def list_standard_corpus_file_ids(self, assistant_id: str) -> list[str]:
        workspace = self._scope()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT kbf.file_id, kbf.corpus_kind, f.name
                   FROM assistant_knowledge_bases akb
                   JOIN knowledge_base_files kbf
                     ON kbf.knowledge_base_id=akb.knowledge_base_id
                    AND kbf.workspace_id=akb.workspace_id AND kbf.enabled
                   JOIN files f ON f.id=kbf.file_id AND f.workspace_id=akb.workspace_id
                   WHERE akb.assistant_id=%s AND akb.workspace_id=%s AND akb.enabled
                     AND EXISTS (
                       SELECT 1 FROM document_parses dp
                       WHERE dp.file_id=f.id AND dp.workspace_id=f.workspace_id
                         AND dp.status='done'
                         AND (dp.markdown_path IS NOT NULL AND TRIM(dp.markdown_path) <> ''
                              OR dp.markdown_object_key IS NOT NULL AND TRIM(dp.markdown_object_key) <> '')
                     )
                   ORDER BY CASE WHEN LOWER(COALESCE(kbf.corpus_kind, ''))='standard'
                                      THEN 0 ELSE 1 END, f.name ASC""",
                (assistant_id, workspace),
            ).fetchall()
        return [str(row["file_id"]) for row in rows]

    def get_init_draft(self, assistant_id: str) -> dict[str, Any] | None:
        workspace = self._scope()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM assistant_init_drafts WHERE assistant_id=%s AND workspace_id=%s",
                (assistant_id, workspace),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _decode(item.get("payload"))
        return item

    def upsert_init_draft(
        self,
        assistant_id: str,
        *,
        status: str,
        payload: dict[str, Any],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._scope()
        encoded = json.dumps(payload or {}, ensure_ascii=False)
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO assistant_init_drafts
                   (assistant_id, workspace_id, status, payload, job_id)
                   VALUES (%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (assistant_id) DO UPDATE SET
                     workspace_id=EXCLUDED.workspace_id, status=EXCLUDED.status,
                     payload=EXCLUDED.payload, job_id=COALESCE(EXCLUDED.job_id, assistant_init_drafts.job_id),
                     updated_at=now()
                   RETURNING *""",
                (assistant_id, workspace, status, encoded, job_id),
            ).fetchone()
        item = dict(row)
        item["payload"] = _decode(item.get("payload"))
        return item

    def apply_init_draft(
        self,
        assistant_id: str,
        *,
        model_config: dict[str, Any],
        node_prompts: dict[str, Any],
        rules: dict[str, Any],
        retrieval_config: dict[str, Any],
        parameter_schema: dict[str, Any],
        initialization_provenance: dict[str, Any],
        applied_at: str,
    ) -> dict[str, Any]:
        workspace = self._scope()
        encoded = {
            "model_config": json.dumps(model_config or {}, ensure_ascii=False),
            "node_prompts": json.dumps(node_prompts or {}, ensure_ascii=False),
            "rules": json.dumps(rules or {}, ensure_ascii=False),
            "retrieval_config": json.dumps(retrieval_config or {}, ensure_ascii=False),
            "parameter_schema": json.dumps(parameter_schema or {}, ensure_ascii=False),
            "initialization_provenance": json.dumps(
                initialization_provenance or {}, ensure_ascii=False
            ),
        }
        with self._connect() as conn:
            assistant = conn.execute(
                """SELECT active_version_id
                   FROM audit_assistants
                   WHERE id=%s AND workspace_id=%s
                   FOR UPDATE""",
                (assistant_id, workspace),
            ).fetchone()
            if not assistant:
                raise ValueError("assistant not found")
            version_id = assistant["active_version_id"]
            version_row = (
                conn.execute(
                    "SELECT version FROM assistant_versions WHERE id=%s AND assistant_id=%s",
                    (version_id, assistant_id),
                ).fetchone()
                if version_id
                else None
            )
            version_no = int(version_row["version"] or 1) if version_row else 1
            if version_id:
                conn.execute(
                    """UPDATE assistant_versions
                       SET status='active', model_config=%s::jsonb,
                           node_prompts=%s::jsonb, rules=%s::jsonb,
                           retrieval_config=%s::jsonb, parameter_schema=%s::jsonb,
                           category_profile='{}'::jsonb,
                           initialization_provenance=%s::jsonb,
                           activated_at=%s
                       WHERE id=%s AND assistant_id=%s""",
                    (
                        encoded["model_config"],
                        encoded["node_prompts"],
                        encoded["rules"],
                        encoded["retrieval_config"],
                        encoded["parameter_schema"],
                        encoded["initialization_provenance"],
                        applied_at,
                        version_id,
                        assistant_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM assistant_versions WHERE assistant_id=%s AND id<>%s",
                    (assistant_id, version_id),
                )
            else:
                version_id = f"{assistant_id}_v1_{uuid.uuid4().hex[:8]}"
                version_no = 1
                conn.execute(
                    """INSERT INTO assistant_versions
                       (id, assistant_id, version, name, status, model_config,
                        node_prompts, rules, retrieval_config, parameter_schema,
                        category_profile, initialization_provenance, created_at, activated_at)
                       VALUES (%s,%s,%s,'','active',%s::jsonb,%s::jsonb,%s::jsonb,
                               %s::jsonb,%s::jsonb,'{}'::jsonb,%s::jsonb,%s,%s)""",
                    (
                        version_id,
                        assistant_id,
                        version_no,
                        encoded["model_config"],
                        encoded["node_prompts"],
                        encoded["rules"],
                        encoded["retrieval_config"],
                        encoded["parameter_schema"],
                        encoded["initialization_provenance"],
                        applied_at,
                        applied_at,
                    ),
                )
            conn.execute(
                """UPDATE audit_assistants
                   SET active_version_id=%s, status='active', updated_at=%s
                   WHERE id=%s AND workspace_id=%s""",
                (version_id, applied_at, assistant_id, workspace),
            )
            conn.execute(
                """UPDATE assistant_init_drafts
                   SET status='applied', updated_at=%s
                   WHERE assistant_id=%s AND workspace_id=%s""",
                (applied_at, assistant_id, workspace),
            )
        return {"version_id": version_id, "version": version_no}

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
        """CREATE TABLE IF NOT EXISTS settings (
             key TEXT PRIMARY KEY,
             value TEXT NOT NULL
        )""",
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
             object_sha256 TEXT NOT NULL DEFAULT '',
             object_size INTEGER NOT NULL DEFAULT 0,
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
             crop_object_key TEXT NOT NULL DEFAULT '',
             crop_sha256 TEXT NOT NULL DEFAULT '',
             crop_size INTEGER NOT NULL DEFAULT 0,
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
             markdown_sha256 TEXT NOT NULL DEFAULT '',
             markdown_size INTEGER NOT NULL DEFAULT 0,
             raw_zip_object_key TEXT NOT NULL DEFAULT '',
             raw_zip_sha256 TEXT NOT NULL DEFAULT '',
             raw_zip_size INTEGER NOT NULL DEFAULT 0,
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
        "ALTER TABLE files ADD COLUMN IF NOT EXISTS object_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE files ADD COLUMN IF NOT EXISTS object_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS crop_object_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS crop_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS crop_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE document_parses ADD COLUMN IF NOT EXISTS markdown_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE document_parses ADD COLUMN IF NOT EXISTS markdown_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE document_parses ADD COLUMN IF NOT EXISTS raw_zip_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE document_parses ADD COLUMN IF NOT EXISTS raw_zip_size INTEGER NOT NULL DEFAULT 0",
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


def get_workspace_repository() -> PostgresWorkspaceRepository | None:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL repositories")
        return PostgresWorkspaceRepository(config.DATABASE_URL)
    return None


def get_settings_repository() -> PostgresSettingsRepository | None:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL repositories")
        return PostgresSettingsRepository(config.DATABASE_URL)
    return None


def get_content_repository() -> PostgresContentRepository | None:
    """Return the opt-in PostgreSQL read adapter for migrated content."""
    if config.CONTENT_READ_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL content reads")
        return PostgresContentRepository(config.DATABASE_URL)
    return None


def get_content_write_repository() -> PostgresContentRepository | None:
    """Return the PostgreSQL content writer for worker-owned mutations."""
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL content writes")
        return PostgresContentRepository(config.DATABASE_URL)
    return None
