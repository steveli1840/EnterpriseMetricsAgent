from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolContext:
    user_id: str
    conversation_id: str
    memory: Any | None = None
    query_gateway: Any | None = None
    repository: Any | None = None
    schema_service: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    name: str
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_message_content(self) -> str:
        if self.ok:
            return self.content
        return f"ERROR ({self.name}): {self.error or self.content}"


class AgentTool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult: ...


def openai_tool_schema(tool: AgentTool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
