import pytest

from app.agent.service import AgentService
from app.domain.metrics import MetricDefinition
from app.domain.retrieval import RetrievedContext, RetrievedKnowledge


class FakeQueryGateway:
    async def explain(self, sql: str, parameters: dict):
        return {"estimated_rows": 2}

    async def execute(self, sql: str, parameters: dict):
        lowered = sql.lower()
        if "min(" in lowered and "max(" in lowered:
            return {
                "query_id": "query-date",
                "columns": ["min_at", "max_at"],
                "rows": [["2018-01-01 00:00:00", "2018-02-01 00:00:00"]],
                "elapsed_ms": 1,
            }
        return {
            "query_id": "query-1",
            "columns": ["customer_state", "delivered_revenue"],
            "rows": [["SP", 1200.0], ["RJ", 800.0]],
            "elapsed_ms": 12,
        }


@pytest.mark.asyncio
async def test_agent_returns_sql_and_complete_evidence():
    metric = MetricDefinition(
        name="delivered_revenue",
        version=1,
        label="已交付收入",
        description="Revenue from delivered orders",
        model="analytics.fct_order_items",
        expression="price + freight_value",
        aggregation="sum",
        time_dimension="order_purchase_at",
        grain="order_item",
        allowed_dimensions=["customer_state"],
        filters=["order_status = 'delivered'"],
        owner="analytics",
        status="published",
    )
    service = AgentService(metrics=[metric], query_gateway=FakeQueryGateway())
    context = RetrievedContext(
        question="2018年1月各州已交付收入是多少？",
        metrics=[metric],
        schema=None,
        knowledge=[
            RetrievedKnowledge(
                source_type="knowledge",
                source_ref="knowledge/business_aliases.md",
                content=(
                    "metric.delivered_revenue: 已交付收入\n"
                    "dimension.customer_state: 州"
                ),
                score=1.0,
            )
        ],
        memories=[],
    )
    result = await service.analyze(
        question="2018年1月各州已交付收入是多少？",
        user_id="analyst-1",
        conversation_id="conversation-1",
        context=context,
    )
    assert result.sql.statement.startswith("SELECT")
    assert result.evidence.metrics[0].name == "delivered_revenue"
    assert result.evidence.row_count == 2
    assert result.result_preview[0][0] == "SP"
    assert result.chart_hint is not None
    assert result.chart_hint.enabled is True
    assert result.chart_hint.x == "customer_state"


@pytest.mark.asyncio
async def test_agent_resolves_metric_from_retrieved_business_knowledge():
    metric = MetricDefinition(
        name="delivered_revenue",
        version=1,
        label="已交付收入",
        description="Revenue from delivered orders",
        model="analytics.fct_order_items",
        expression="price + freight_value",
        aggregation="sum",
        time_dimension="order_purchase_at",
        grain="order_item",
        allowed_dimensions=["customer_state"],
        filters=["order_status = 'delivered'"],
        owner="analytics",
        status="published",
    )
    context = RetrievedContext(
        question="按州净流水是多少？",
        metrics=[metric],
        schema=None,
        knowledge=[
            RetrievedKnowledge(
                source_type="knowledge",
                source_ref="knowledge/business_aliases.md",
                content=(
                    "metric.delivered_revenue: 净流水\n"
                    "dimension.customer_state: 州"
                ),
                score=1.0,
            )
        ],
        memories=[],
    )
    service = AgentService(metrics=[metric], query_gateway=FakeQueryGateway())

    result = await service.analyze(
        question="按州净流水是多少？",
        user_id="analyst-1",
        conversation_id="conversation-1",
        context=context,
    )

    assert result.evidence.metrics[0].name == "delivered_revenue"
    assert result.evidence.knowledge_refs == ["knowledge/business_aliases.md"]
