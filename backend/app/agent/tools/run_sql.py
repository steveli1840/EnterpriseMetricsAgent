from __future__ import annotations

import json
from typing import Any

from app.agent.tools.base import ToolContext, ToolResult
from app.agent.tools.catalog import DEFAULT_ALLOWED_TABLES
from app.domain.sql_guard import SQLPolicyError, validate_read_only_sql


class RunSqlTool:
    name = "run_sql"
    description = (
        "Execute a single read-only SELECT/WITH query against the active warehouse. "
        "Only authorized analytics/raw/system catalog tables are allowed. "
        "Prefer list_tables/describe_table for schema discovery."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single read-only SQL statement.",
            },
        },
        "required": ["sql"],
        "additionalProperties": False,
    }

    def __init__(self, allowed_tables: set[str] | None = None, *, max_rows: int = 1000):
        self.allowed_tables = allowed_tables or set(DEFAULT_ALLOWED_TABLES)
        self.max_rows = max_rows

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        sql = str(arguments.get("sql") or "").strip()
        if not sql:
            return ToolResult(ok=False, name=self.name, content="", error="sql is required")
        if context.query_gateway is None:
            return ToolResult(ok=False, name=self.name, content="", error="query gateway unavailable")

        try:
            validated = validate_read_only_sql(sql, self.allowed_tables, max_rows=self.max_rows)
        except SQLPolicyError as exc:
            return ToolResult(ok=False, name=self.name, content="", error=str(exc))

        try:
            await context.query_gateway.explain(validated.sql, {})
            result = await context.query_gateway.execute(validated.sql, {})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, name=self.name, content="", error=str(exc))

        columns = list(result.get("columns") or [])
        rows = [list(row) for row in (result.get("rows") or [])]
        returned_rows = len(rows)
        truncated = returned_rows >= self.max_rows
        preview = rows[:20]
        payload = {
            "sql": validated.sql,
            "columns": columns,
            "returned_rows": returned_rows,
            "truncated": truncated,
            "elapsed_ms": result.get("elapsed_ms", 0),
            "query_id": result.get("query_id"),
            "preview": preview,
        }
        content = json.dumps(
            {
                "sql": validated.sql,
                "columns": columns,
                "returned_rows": returned_rows,
                "truncated": truncated,
                "preview": [[str(cell) for cell in row] for row in preview],
            },
            ensure_ascii=False,
        )
        return ToolResult(ok=True, name=self.name, content=content, data=payload)
