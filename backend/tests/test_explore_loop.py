import json

import pytest

from app.agent.explore_loop import ExploreLoop
from app.agent.tools import build_explore_registry
from app.agent.tools.base import ToolContext
from app.agent.tools.catalog import DEFAULT_ALLOWED_TABLES
from app.agent.tools.run_sql import RunSqlTool


class FakeGateway:
    async def explain(self, sql: str, parameters: dict):
        return {"status": "ok"}

    async def execute(self, sql: str, parameters: dict):
        if "system.tables" in sql and "total_rows" in sql:
            return {
                "query_id": "q1",
                "columns": ["database", "name", "total_rows"],
                "rows": [["raw_olist", "geolocation", 1000163], ["raw_olist", "orders", 99441]],
                "elapsed_ms": 3,
            }
        if "system.columns" in sql:
            return {
                "query_id": "q2",
                "columns": ["database", "table", "name", "type", "position"],
                "rows": [
                    ["raw_olist", "geolocation", "geolocation_city", "String", 4],
                    ["raw_olist", "geolocation", "geolocation_state", "String", 5],
                ],
                "elapsed_ms": 2,
            }
        return {
            "query_id": "q3",
            "columns": ["geolocation_city"],
            "rows": [["sao paulo"], ["rio de janeiro"]],
            "elapsed_ms": 4,
        }


@pytest.mark.asyncio
async def test_run_sql_tool_returns_metadata():
    tool = RunSqlTool(allowed_tables=DEFAULT_ALLOWED_TABLES)
    context = ToolContext(
        user_id="u1",
        conversation_id="c1",
        query_gateway=FakeGateway(),
    )
    result = await tool.execute(
        context,
        {
            "sql": (
                "SELECT database, name, total_rows FROM system.tables "
                "WHERE database IN ('analytics','raw_olist') ORDER BY total_rows DESC"
            )
        },
    )
    assert result.ok
    assert result.data["returned_rows"] == 2
    assert result.data["columns"][0] == "database"


@pytest.mark.asyncio
async def test_explore_loop_fallback_without_tool_calling():
    loop = ExploreLoop(
        chat_provider=None,
        query_gateway=FakeGateway(),
        registry=build_explore_registry(),
    )
    result = await loop.run(
        question="你有哪些表？",
        user_id="u1",
        conversation_id="c1",
    )
    assert result.iterations == 1
    assert result.returned_rows == 2
    assert "geolocation" in result.answer or "raw_olist" in result.answer


class ScriptedChat:
    def __init__(self):
        self.calls = 0

    async def complete_with_tools(self, messages, tools, tool_choice="auto"):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "describe_table",
                            "arguments": json.dumps({"table": "raw_olist.geolocation"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "geolocation 有城市和州等字段。", "tool_calls": []}

    async def complete_json(self, messages):
        return json.dumps({"summary": "geolocation 有城市和州等字段。"}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_explore_loop_tool_calling_describe_table():
    loop = ExploreLoop(
        chat_provider=ScriptedChat(),
        query_gateway=FakeGateway(),
        registry=build_explore_registry(),
    )
    result = await loop.run(
        question="geolocation 有什么字段",
        user_id="u1",
        conversation_id="c1",
    )
    assert any(item["tool"] == "describe_table" and item["ok"] for item in result.tool_trace)
    assert "字段" in result.answer or "geolocation" in result.answer.lower()


def test_explore_result_disables_chart_hint():
    from app.agent.explore_loop import ExploreLoopResult, explore_result_to_analysis

    analysis = explore_result_to_analysis(
        ExploreLoopResult(
            answer="有 2 张表",
            columns=["database", "name", "total_rows"],
            rows=[["raw_olist", "geolocation", 1000163]],
            sql="SELECT ...",
        ),
        trace_id="t1",
    )
    assert analysis.chart_hint is not None
    assert analysis.chart_hint.enabled is False
