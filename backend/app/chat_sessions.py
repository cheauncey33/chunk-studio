"""Persistent conversation and append-only event storage for Agent chat."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from . import config, db
from .storage.repositories import get_chat_repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _repository():
    return get_chat_repository()


def create_conversation(
    assistant_id: str,
    *,
    title: str = "",
    config_snapshot: dict[str, Any] | None = None,
    workspace_id: str = config.DEFAULT_WORKSPACE_ID,
    user_id: str = config.DEFAULT_USER_ID,
) -> dict[str, Any]:
    repository = _repository()
    if repository is not None:
        return repository.create_conversation(
            assistant_id,
            title=title,
            config_snapshot=config_snapshot,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    conversation_id = f"chat_{uuid.uuid4().hex}"
    timestamp = _now()
    encoded_snapshot = json.dumps(
        config_snapshot if isinstance(config_snapshot, dict) else {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chat_conversations
               (id, assistant_id, workspace_id, user_id, title, config_snapshot,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                assistant_id,
                workspace_id.strip() or config.DEFAULT_WORKSPACE_ID,
                user_id.strip() or config.DEFAULT_USER_ID,
                title.strip()[:120],
                encoded_snapshot,
                timestamp,
                timestamp,
            ),
        )
    return get_conversation(conversation_id) or {
        "id": conversation_id,
        "assistant_id": assistant_id,
        "workspace_id": workspace_id.strip() or config.DEFAULT_WORKSPACE_ID,
        "user_id": user_id.strip() or config.DEFAULT_USER_ID,
        "title": title.strip()[:120],
        "summary": "",
        "summary_version": 0,
        "summary_sequence": 0,
        "config_snapshot": encoded_snapshot,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def get_conversation(
    conversation_id: str,
    *,
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    repository = _repository()
    if repository is not None:
        return repository.get_conversation(
            conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    clauses = ["id=?"]
    params: list[Any] = [conversation_id]
    if workspace_id is not None:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    if user_id is not None:
        clauses.append("user_id=?")
        params.append(user_id)
    row = db.get_conn().execute(
        "SELECT * FROM chat_conversations WHERE " + " AND ".join(clauses),
        params,
    ).fetchone()
    return dict(row) if row else None


def list_conversations(
    assistant_id: str,
    *,
    limit: int = 50,
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    repository = _repository()
    if repository is not None:
        return repository.list_conversations(
            assistant_id,
            limit=limit,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    bounded_limit = max(1, min(int(limit), 100))
    clauses = ["assistant_id=?"]
    params: list[Any] = [assistant_id]
    if workspace_id is not None:
        clauses.append("workspace_id=?")
        params.append(workspace_id)
    if user_id is not None:
        clauses.append("user_id=?")
        params.append(user_id)
    params.append(bounded_limit)
    rows = db.get_conn().execute(
        """SELECT * FROM chat_conversations
           WHERE """ + " AND ".join(clauses) + """
           ORDER BY updated_at DESC
           LIMIT ?""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def set_config_snapshot_if_empty(
    conversation_id: str,
    config_snapshot: dict[str, Any],
) -> bool:
    """Backfill snapshots for legacy conversations without overwriting them."""
    repository = _repository()
    if repository is not None:
        return repository.set_config_snapshot_if_empty(conversation_id, config_snapshot)
    encoded_snapshot = json.dumps(
        config_snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with db.transaction() as conn:
        cursor = conn.execute(
            """UPDATE chat_conversations
               SET config_snapshot=?
               WHERE id=? AND (config_snapshot IS NULL OR config_snapshot IN ('', '{}'))""",
            (encoded_snapshot, conversation_id),
        )
    return cursor.rowcount > 0


def append_events(
    conversation_id: str,
    events: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Append events in order and return their persisted representations."""
    repository = _repository()
    if repository is not None:
        return repository.append_events(conversation_id, events)
    if not events:
        return []
    timestamp = _now()
    persisted: list[dict[str, Any]] = []
    with db.transaction() as conn:
        conversation = conn.execute(
            "SELECT id FROM chat_conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise ValueError("conversation not found")
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence "
            "FROM chat_events WHERE conversation_id=?",
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
                   VALUES (?, ?, ?, ?, ?, ?)""",
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
            "UPDATE chat_conversations SET updated_at=? WHERE id=?",
            (timestamp, conversation_id),
        )
    return persisted


def list_events(conversation_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    repository = _repository()
    if repository is not None:
        return repository.list_events(conversation_id, limit=limit)
    if limit is None:
        rows = db.get_conn().execute(
            """SELECT * FROM chat_events
               WHERE conversation_id=? ORDER BY sequence""",
            (conversation_id,),
        ).fetchall()
    else:
        bounded_limit = max(1, min(int(limit), 500))
        rows = db.get_conn().execute(
            """SELECT * FROM (
                 SELECT * FROM chat_events
                  WHERE conversation_id=? ORDER BY sequence DESC LIMIT ?
               ) ORDER BY sequence""",
            (conversation_id, bounded_limit),
        ).fetchall()
    return [
        {
            **dict(row),
            "payload": _decode(row["payload"]),
        }
        for row in rows
    ]


def list_events_since(
    conversation_id: str,
    sequence: int,
    *,
    through_sequence: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    repository = _repository()
    if repository is not None:
        return repository.list_events_since(
            conversation_id,
            sequence,
            through_sequence=through_sequence,
            limit=limit,
        )
    clauses = ["conversation_id=?", "sequence>?"]
    params: list[Any] = [conversation_id, int(sequence)]
    if through_sequence is not None:
        clauses.append("sequence<=?")
        params.append(int(through_sequence))
    limit_clause = ""
    if limit is not None:
        limit_clause = " LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
    rows = db.get_conn().execute(
        "SELECT * FROM chat_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY sequence"
        + limit_clause,
        params,
    ).fetchall()
    return [
        {**dict(row), "payload": _decode(row["payload"])}
        for row in rows
    ]


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
                # Older sessions may contain `tool_calls: []` from the
                # streaming Agent. DeepSeek rejects that empty field when the
                # session is sent again, so normalize it at the load boundary.
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


def compact_conversation(
    conversation_id: str,
    *,
    summarize: Callable[[str, list[dict[str, Any]]], str],
    min_events: int = 48,
    keep_recent_events: int = 24,
) -> bool:
    """Summarize an old event prefix while retaining the append-only log."""
    repository = _repository()
    if repository is not None:
        return repository.compact_conversation(
            conversation_id,
            summarize=summarize,
            min_events=min_events,
            keep_recent_events=keep_recent_events,
        )
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise ValueError("conversation not found")
    row = db.get_conn().execute(
        """SELECT COUNT(*) AS count, COALESCE(MAX(sequence), 0) AS maximum
           FROM chat_events WHERE conversation_id=?""",
        (conversation_id,),
    ).fetchone()
    event_count = int(row["count"] or 0)
    maximum = int(row["maximum"] or 0)
    if event_count < max(1, int(min_events)):
        return False
    current_cursor = int(conversation.get("summary_sequence") or 0)
    cutoff = max(current_cursor, maximum - max(1, int(keep_recent_events)))
    if cutoff <= current_cursor:
        return False
    older_events = list_events_since(
        conversation_id,
        current_cursor,
        through_sequence=cutoff,
    )
    older_messages = _messages_from_events(older_events)
    if not older_messages:
        return False
    try:
        summary = summarize(
            str(conversation.get("summary") or ""),
            older_messages,
        ).strip()
    except Exception:
        # Compaction is an optimization. A provider failure must not make the
        # user's next chat turn fail; the bounded event window remains usable.
        return False
    if not summary:
        return False
    timestamp = _now()
    with db.transaction() as conn:
        conn.execute(
            """UPDATE chat_conversations
               SET summary=?, summary_version=summary_version+1,
                   summary_sequence=?, updated_at=?
               WHERE id=?""",
            (summary[:6000], cutoff, timestamp, conversation_id),
        )
    return True


def load_messages(conversation_id: str, *, max_messages: int = 40) -> list[dict[str, Any]]:
    """Translate persisted events into native chat-completion messages."""
    repository = _repository()
    if repository is not None:
        return repository.load_messages(conversation_id, max_messages=max_messages)
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise ValueError("conversation not found")
    messages: list[dict[str, Any]] = []
    summary = str(conversation.get("summary") or "").strip()
    if summary:
        messages.append({
            "role": "system",
            "content": "Conversation summary from earlier turns:\n" + summary,
        })
    events = list_events_since(
        conversation_id,
        int(conversation.get("summary_sequence") or 0),
        limit=max_messages,
    )
    # A bounded tail may begin with a tool result or an assistant tool call.
    # Drop that incomplete prefix so the provider always receives a valid
    # user -> assistant -> tool-result sequence.
    while events and events[0]["event_type"] != "user_message":
        events.pop(0)
    return messages + _messages_from_events(events)
