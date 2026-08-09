from __future__ import annotations

import json
from typing import Any


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    if event_type == "tool_result":
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        return {
            "type": event_type,
            "tool": event.get("tool"),
            "ok": bool(result.get("ok", True)),
            "summary": str(result.get("summary") or result.get("outcome") or "")[:500],
        }
    if event_type == "tool_call":
        return {
            "type": event_type,
            "tool": event.get("tool"),
            "arguments": event.get("arguments"),
        }
    return {"type": event_type, "summary": str(event.get("message") or "")[:500]}


def build_agent_context(
    *,
    immutable_state: dict[str, Any],
    mutable_state: dict[str, Any],
    events: list[dict[str, Any]],
    remaining_budget: dict[str, Any],
    max_chars: int,
    keep_recent_events: int,
) -> dict[str, Any]:
    """Build deterministic context while keeping pinned state lossless."""
    recent_count = max(0, keep_recent_events)
    recent = events[-recent_count:] if recent_count else []
    older = events[:-recent_count] if recent_count else events
    context = {
        "immutable_state": immutable_state,
        "mutable_state": mutable_state,
        "compacted_history": [_event_summary(event) for event in older],
        "recent_events": recent,
        "remaining_budget": remaining_budget,
    }
    if _json_size(context) <= max_chars:
        return context

    # Drop verbose old summaries first. Pinned state and the latest events are
    # never truncated; large evidence bodies belong in an external registry.
    compacted = context["compacted_history"]
    while compacted and _json_size(context) > max_chars:
        compacted.pop(0)
    return context
