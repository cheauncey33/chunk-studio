from __future__ import annotations

import pytest

from app.agent_runtime.models import ToolDefinition
from app.tool_registry import ToolContext, ToolRegistry


def test_tool_registry_builds_scoped_tools_and_rejects_duplicates() -> None:
    registry = ToolRegistry()
    registry.register(
        "custom",
        lambda context: [ToolDefinition(
            name=f"custom_{context.assistant_id}",
            description="custom",
            input_schema={"type": "object"},
            execute=lambda _args: {"ok": True},
        )],
    )
    context = ToolContext(
        assistant_id="a1",
        file_ids=["f1"],
        retrieval_config={},
        model="m1",
    )
    assert [tool.name for tool in registry.build(context)] == ["custom_a1"]

    with pytest.raises(ValueError, match="already registered"):
        registry.register("custom", lambda _context: [])

    registry.register("custom", lambda _context: [], replace=True)
    assert registry.build(context) == []
