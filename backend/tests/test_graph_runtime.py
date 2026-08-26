import pytest

from app.agent.graph import GovernedAgentRuntime
from app.agent.service import AgentService
from app.catalog import DEFAULT_METRICS
from app.domain.retrieval import RetrievedContext, RetrievedKnowledge


class FakeQueryGateway:
    async def explain(self, sql: str, parameters: dict):
        return {"estimated_rows": 2}

    async def execute(self, sql: str, parameters: dict):
        return {
            "query_id": "query-1",
            "columns": ["customer_state", "delivered_revenue"],
            "rows": [["SP", 1200.0], ["RJ", 800.0]],
            "elapsed_ms": 12,
        }


class FakeRetriever:
    async def retrieve(self, *, question: str, user_id: str, metrics: list, limit: int = 8):
        return RetrievedContext(
            question=question,
            metrics=[metric for metric in metrics if metric.name == "delivered_revenue"],
            schema=None,
            knowledge=[
                RetrievedKnowledge(
                    source_type="knowledge",
                    source_ref="knowledge/business_glossary.md",
                    content=(
                        "metric.delivered_revenue: 已交付收入\n"
                        "dimension.customer_state: 州"
                    ),
                    score=1.0,
                )
            ],
            memories=[],
        )


class FakeSchemaService:
    def describe_model(self, model: str):
        assert model == "analytics.fct_order_items"
        return {
            "snapshot": "snapshot-tool",
            "source": "test",
            "model": model,
            "columns": [
                {"name": "customer_state", "type": "String"},
                {"name": "price", "type": "Float64"},
            ],
        }


@pytest.mark.asyncio
async def test_graph_runtime_executes_retrieval_before_governed_query():
    service = AgentService(
        metrics=DEFAULT_METRICS,
        query_gateway=FakeQueryGateway(),
        retriever=FakeRetriever(),
        schema_service=FakeSchemaService(),
    )
    runtime = GovernedAgentRuntime(service)

    result = await runtime.analyze(
        question="2018年1月各州已交付收入是多少？",
        user_id="analyst-1",
        conversation_id="workspace",
    )

    assert result.evidence.metrics[0].name == "delivered_revenue"
    assert result.sql.statement.startswith("SELECT")
    assert "analytics.fct_order_items.customer_state" in result.evidence.schema_refs
    assert result.evidence.schema_snapshot == "snapshot-tool"
