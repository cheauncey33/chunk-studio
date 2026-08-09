"""Persistent conversation and append-only event storage for Agent chat."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from . import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def create_conversation(assistant_id: str, *, title: str = "") -> dict[str, Any]:
    conversation_id = f"chat_{uuid.uuid4().hex}"
    timestamp = _now()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chat_conversations
               (id, assistant_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, assistant_id, title.strip()[:120], timestamp, timestamp),
        )
    return get_conversation(conversation_id) or {
        "id": conversation_id,
        "assistant_id": assistant_id,
        "title": title.strip()[:120],
        "summary": "",
        "summary_version": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    row = db.get_conn().execute(
        "SELECT * FROM chat_conversations WHERE id=?",
        (conversation_id,),
    ).fetchone()
    return dict(row) if row else None


def list_conversations(assistant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    rows = db.get_conn().execute(
        """SELECT * FROM chat_conversations
           WHERE assistant_id=?
           ORDER BY updated_at DESC
           LIMIT ?""",
        (assistant_id, bounded_limit),
    ).fetchall()
    return [dict(row) for row in rows]


def append_events(
    conversation_id: str,
    events: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Append events in order and return their persisted representations."""
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
                messages.append(message)
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
