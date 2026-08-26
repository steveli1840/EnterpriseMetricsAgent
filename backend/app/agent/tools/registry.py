from __future__ import annotations

from typing import Any

from app.agent.tools.base import AgentTool, ToolContext, ToolResult, openai_tool_schema


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[AgentTool]:
        return list(self._tools.values())

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [openai_tool_schema(tool) for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, name=name, content="", error=f"unknown tool: {name}")
        try:
            return await tool.execute(context, arguments or {})
        except Exception as exc:  # noqa: BLE001 - surface tool failures to the loop
            return ToolResult(ok=False, name=name, content="", error=str(exc))
