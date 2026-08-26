from typing import Any

from app.agent.tools.base import ToolContext, ToolResult

# Governed analytics models + ClickHouse system catalogs for exploration.
DEFAULT_ALLOWED_TABLES = {
    "system.tables",
    "system.columns",
    "analytics.fct_order_items",
    "analytics.fct_reviews",
    "analytics.fct_orders",
    "analytics.customer_order_summary",
    "raw_olist.orders",
    "raw_olist.customers",
    "raw_olist.order_items",
    "raw_olist.products",
    "raw_olist.sellers",
    "raw_olist.payments",
    "raw_olist.reviews",
    "raw_olist.geolocation",
    "raw_olist.category_translation",
}


class ListTablesTool:
    name = "list_tables"
    description = (
        "List available analytics/raw tables and optional row counts. "
        "Use this when the user asks what tables exist or which table is largest."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_row_counts": {
                "type": "boolean",
                "description": "If true, include total_rows ordered descending.",
                "default": True,
            },
            "databases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Databases to include. Defaults to analytics and raw_olist.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        include_counts = bool(arguments.get("include_row_counts", True))
        databases = arguments.get("databases") or ["analytics", "raw_olist"]
        db_list = ", ".join(f"'{db}'" for db in databases)
        if include_counts:
            sql = (
                "SELECT database, name, coalesce(total_rows, 0) AS total_rows "
                "FROM system.tables "
                f"WHERE database IN ({db_list}) ORDER BY total_rows DESC"
            )
        else:
            sql = (
                "SELECT database, name FROM system.tables "
                f"WHERE database IN ({db_list}) ORDER BY database, name"
            )
        from app.agent.tools.run_sql import RunSqlTool

        return await RunSqlTool().execute(context, {"sql": sql})


class DescribeTableTool:
    name = "describe_table"
    description = (
        "Describe columns for a table (name, type, position). "
        "Use when the user asks about fields/schema of a table. "
        "Prefer memory.working.entities.table when the user uses pronouns."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "description": "Table name, optionally database-qualified like raw_olist.geolocation",
            },
            "database": {
                "type": "string",
                "description": "Optional database if table is unqualified.",
            },
        },
        "required": ["table"],
        "additionalProperties": False,
    }

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        raw_table = str(arguments.get("table") or "").strip()
        database = arguments.get("database")
        if "." in raw_table:
            database, table = raw_table.split(".", 1)
        else:
            table = raw_table
        if not table:
            return ToolResult(ok=False, name=self.name, content="", error="table is required")

        if database:
            sql = (
                "SELECT database, table, name, type, position FROM system.columns "
                f"WHERE database = '{database}' AND table = '{table}' "
                "ORDER BY database, table, position"
            )
        else:
            sql = (
                "SELECT database, table, name, type, position FROM system.columns "
                "WHERE database IN ('analytics', 'raw_olist') "
                f"AND table = '{table}' ORDER BY database, table, position"
            )
        from app.agent.tools.run_sql import RunSqlTool

        return await RunSqlTool().execute(context, {"sql": sql})
