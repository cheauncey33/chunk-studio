"""Persistent conversation and append-only event storage for Agent chat."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

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


def load_messages(conversation_id: str, *, max_messages: int = 40) -> list[dict[str, Any]]:
    """Translate persisted events into native chat-completion messages."""
    messages: list[dict[str, Any]] = []
    events = list_events(conversation_id, limit=max_messages)
    # A bounded tail may begin with a tool result or an assistant tool call.
    # Drop that incomplete prefix so the provider always receives a valid
    # user -> assistant -> tool-result sequence.
    while events and events[0]["event_type"] != "user_message":
        events.pop(0)
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
