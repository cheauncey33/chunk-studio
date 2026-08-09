from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol


AgentStopReason = Literal[
    "finished",
    "max_turns",
    "max_tool_calls",
    "timeout",
    "invalid_action",
    "provider_error",
]


@dataclass(frozen=True)
class AgentPolicy:
    max_turns: int = 4
    max_tool_calls: int = 6
    max_search_calls: int = 5
    timeout_seconds: float = 90.0
    max_context_chars: int = 24000
    keep_recent_events: int = 4


ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]
ToolValidator = Callable[[dict[str, Any]], None]
StateReducer = Callable[[dict[str, Any], str, dict[str, Any]], None]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    execute: ToolExecutor
    category: Literal["search", "read", "finish"] = "read"
    validate: ToolValidator | None = None

    def model_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class AgentProvider(Protocol):
    def __call__(
        self,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


@dataclass
class AgentRunResult:
    stop_reason: AgentStopReason
    result: dict[str, Any]
    mutable_state: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    search_calls: int = 0
    elapsed_seconds: float = 0.0
