import json

import pytest

from app.agent.chart_planner import hard_gate, hard_veto, plan_chart
from app.agent.schemas import ChartHint


def test_hard_gate_blocks_explore_and_catalog():
    assert hard_gate(
        route="explore",
        columns=["database", "name", "total_rows"],
        rows=[["raw_olist", "orders", 1], ["analytics", "fct", 2]],
    ) == ChartHint(enabled=False)

    assert hard_gate(
        route="metric",
        columns=["database", "table", "name", "type", "position"],
        rows=[["raw_olist", "geolocation", "lat", "Float64", 1]] * 2,
        sql="SELECT database, table, name, type, position FROM system.columns",
        dimensions=["customer_state"],
    ) == ChartHint(enabled=False)


def test_hard_gate_blocks_metric_without_dimensions():
    assert hard_gate(
        route="metric",
        columns=["gmv"],
        rows=[[100.0], [200.0]],
        dimensions=[],
    ) == ChartHint(enabled=False)


@pytest.mark.asyncio
async def test_plan_chart_default_bar_without_llm():
    hint = await plan_chart(
        question="每个州的 gmv",
        route="metric",
        columns=["customer_state", "gmv"],
        rows=[["SP", 100], ["RJ", 80], ["MG", 70]],
        dimensions=["customer_state"],
        metric_name="gmv",
        chat_provider=None,
    )
    assert hint.enabled is True
    assert hint.type == "bar"
    assert hint.x == "customer_state"
    assert hint.y == "gmv"


@pytest.mark.asyncio
async def test_plan_chart_uses_llm_then_veto_meta_y():
    class FakeChat:
        async def complete_json(self, messages):
            return json.dumps(
                {"enabled": True, "type": "line", "x": "database", "y": "position"}
            )

    hint = await plan_chart(
        question="字段有哪些",
        route="metric",
        columns=["database", "position"],
        rows=[["raw_olist", 1], ["raw_olist", 2]],
        dimensions=["database"],
        chat_provider=FakeChat(),
    )
    # Catalog-like columns + meta y should be vetoed even if LLM says yes.
    # Actually hard_gate for metric with dimensions and numeric y=position:
    # columns[y].lower() in _META_Y → gated False before LLM.
    assert hint.enabled is False


def test_hard_veto_rejects_single_category():
    vetoed = hard_veto(
        ChartHint(enabled=True, type="bar", x="database", y="gmv"),
        columns=["database", "gmv"],
        rows=[["raw_olist", 1], ["raw_olist", 2]],
    )
    assert vetoed.enabled is False


@pytest.mark.asyncio
async def test_plan_chart_llm_picks_line_for_month():
    class FakeChat:
        async def complete_json(self, messages):
            return json.dumps(
                {"enabled": True, "type": "line", "x": "order_month", "y": "gmv"}
            )

    hint = await plan_chart(
        question="每月 gmv 趋势",
        route="metric",
        columns=["order_month", "gmv"],
        rows=[["2018-01", 10], ["2018-02", 20], ["2018-03", 15]],
        dimensions=["order_month"],
        metric_name="gmv",
        chat_provider=FakeChat(),
    )
    assert hint.enabled is True
    assert hint.type == "line"
    assert hint.x == "order_month"
