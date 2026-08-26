import pytest

from app.agent.memory import AgentMemory, MemoryManager, WorkingMemory
from app.agent.service import AgentService, ResolvedIntent
from app.domain.metrics import MetricDefinition


GMV = MetricDefinition(
    name="gmv",
    version=1,
    label="GMV",
    description="Gross merchandise value",
    model="analytics.fct_order_items",
    expression="price",
    aggregation="sum",
    time_dimension="order_purchase_at",
    grain="order_item",
    allowed_dimensions=["customer_state", "product_category", "seller_state", "month"],
    filters=["order_status != 'canceled'"],
    owner="finance",
    status="published",
)


@pytest.mark.asyncio
async def test_dimension_question_uses_allowed_dimensions_only():
    service = AgentService(metrics=[GMV], query_gateway=None, chat_provider=None)
    memory = AgentMemory(
        working=WorkingMemory(
            entities={
                "metric": {"name": "gmv", "label": "GMV", "model": GMV.model},
                "time_window": {"start": "2018-01-01", "end": "2019-01-01"},
            }
        )
    )
    intent = await service.understand("gmv有哪些维度，拆解一下", memory=memory)
    assert intent.route == "direct"
    assert intent.pending_action["type"] == "metric_breakdown"
    assert "customer_state" in intent.direct_answer
    assert "product_id" not in (intent.direct_answer or "")


@pytest.mark.asyncio
async def test_affirm_followup_runs_metric_with_pending_dimension():
    service = AgentService(metrics=[GMV], query_gateway=None, chat_provider=None)
    memory = AgentMemory(
        working=WorkingMemory(
            entities={
                "metric": {"name": "gmv", "label": "GMV", "model": GMV.model},
                "time_window": {"start": "2018-01-01", "end": "2019-01-01"},
                "pending_action": {
                    "type": "metric_breakdown",
                    "metric": "gmv",
                    "suggested_dimensions": ["product_category", "customer_state"],
                    "start": "2018-01-01",
                    "end": "2019-01-01",
                },
            }
        )
    )
    intent = await service.understand("好", memory=memory)
    assert intent.route == "metric"
    assert intent.is_metric_query
    assert intent.metric and intent.metric.name == "gmv"
    assert intent.dimensions == ["product_category"]
    assert intent.start == "2018-01-01"
    assert intent.end == "2019-01-01"
