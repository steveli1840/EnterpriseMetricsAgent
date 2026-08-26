from __future__ import annotations

import json
from typing import Any

from app.agent.tools.base import ToolContext, ToolResult


class SearchMemoryTool:
    name = "search_memory"
    description = (
        "Search session working memory and recent successful tool uses for this conversation. "
        "Use before guessing table/column names when the user refers to previous results."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional free-text filter; empty returns current working memory.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        memory = context.memory
        payload = memory.to_prompt_dict() if memory is not None and hasattr(memory, "to_prompt_dict") else {}
        tool_uses: list[dict[str, Any]] = []
        if context.repository is not None:
            snapshot = context.repository.get_conversation_snapshot(
                context.user_id,
                context.conversation_id,
            )
            for event in snapshot.get("events") or []:
                if event.get("type") == "tool_use":
                    tool_uses.append(
                        {
                            "tool_name": event.get("tool_name"),
                            "args": event.get("args") or {},
                            "note": event.get("note") or "",
                        }
                    )
            tool_uses = tool_uses[-8:]
        query = str(arguments.get("query") or "").lower().strip()
        if query:
            tool_uses = [
                item
                for item in tool_uses
                if query in json.dumps(item, ensure_ascii=False).lower()
            ]
        content = {
            "working": payload.get("working", {}),
            "recent_turns": payload.get("recent_turns", [])[-4:],
            "tool_uses": tool_uses,
        }
        return ToolResult(
            ok=True,
            name=self.name,
            content=json.dumps(content, ensure_ascii=False),
            data=content,
        )


class SaveMemoryTool:
    name = "save_memory"
    description = (
        "Save a successful exploratory tool use (tool name + args + short note) "
        "for future turns in this conversation."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "args": {"type": "object"},
            "note": {"type": "string"},
        },
        "required": ["tool_name", "args"],
        "additionalProperties": False,
    }

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        entry = {
            "tool_name": arguments.get("tool_name"),
            "args": arguments.get("args") or {},
            "note": arguments.get("note") or "",
        }
        if context.repository is None:
            return ToolResult(ok=True, name=self.name, content="memory skipped (no repository)", data=entry)
        context.repository.append_conversation_event(
            context.user_id,
            context.conversation_id,
            {
                "type": "tool_use",
                "content": json.dumps(entry, ensure_ascii=False),
                **entry,
            },
        )
        return ToolResult(ok=True, name=self.name, content="saved", data=entry)
