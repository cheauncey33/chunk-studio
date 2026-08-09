"""Extensible registry for tools exposed to the conversation Agent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .agent_runtime.models import ToolDefinition


@dataclass(frozen=True)
class ToolContext:
    assistant_id: str
    file_ids: list[str]
    retrieval_config: dict[str, Any]
    model: str


ToolFactory = Callable[[ToolContext], Iterable[ToolDefinition]]


class ToolRegistry:
    """Named factory registry with duplicate checks at registration and build."""

    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(
        self,
        name: str,
        factory: ToolFactory,
        *,
        replace: bool = False,
    ) -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("tool factory name must not be blank")
        if key in self._factories and not replace:
            raise ValueError(f"tool factory already registered: {key}")
        self._factories[key] = factory

    def unregister(self, name: str) -> None:
        self._factories.pop(str(name or "").strip(), None)

    def build(self, context: ToolContext) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        names: set[str] = set()
        for factory_name, factory in self._factories.items():
            for tool in factory(context):
                if tool.name in names:
                    raise ValueError(
                        f"duplicate tool name from registry: {tool.name} "
                        f"(factory={factory_name})"
                    )
                names.add(tool.name)
                tools.append(tool)
        return tools

    def names(self) -> list[str]:
        return list(self._factories)
