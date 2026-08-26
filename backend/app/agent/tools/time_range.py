from typing import Any

from app.agent.tools.base import ToolContext, ToolResult
from app.domain.time_semantics import (
    load_time_semantics,
    range_to_dict,
    resolve_relative_time_range,
)


class ResolveTimeRangeTool:
    name = "resolve_time_range"
    description = (
        "Resolve relative time expressions (上周/本月/过去7天) into exclusive "
        "[start, end) ISO dates using organization time_semantics config and server clock. "
        "Use this before metric queries when the user says relative time."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Relative expression such as 上周, 本月, 过去7天",
            },
            "as_of": {
                "type": "string",
                "description": "Optional anchor date YYYY-MM-DD (defaults to today in org timezone)",
            },
        },
        "required": ["expression"],
        "additionalProperties": False,
    }

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        expression = str(arguments.get("expression") or "").strip()
        as_of = arguments.get("as_of")
        if not expression:
            return ToolResult(ok=False, name=self.name, content="", error="expression is required")
        try:
            cfg = load_time_semantics()
            resolved = resolve_relative_time_range(
                expression,
                as_of=str(as_of) if as_of else None,
                config=cfg,
            )
        except Exception as exc:
            return ToolResult(ok=False, name=self.name, content="", error=str(exc))
        payload = range_to_dict(resolved)
        import json

        return ToolResult(
            ok=True,
            name=self.name,
            content=json.dumps(payload, ensure_ascii=False),
            data=payload,
        )
