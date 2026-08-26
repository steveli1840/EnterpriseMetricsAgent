import pytest

from app.catalog import DEFAULT_METRICS
from app.domain.retrieval import HybridRetriever, reciprocal_rank_fusion


class Memory:
    kind = "semantic"
    value = {"term": "销售额", "metric": "delivered_revenue"}


class Knowledge:
    source_type = "knowledge"
    source_ref = "knowledge/business_glossary.md"
    content = "metric.delivered_revenue: 已交付收入\ndimension.customer_state: 州"
    metadata_json = {"schema_snapshot": "olist-v1"}


class VectorKnowledge:
    source_type = "knowledge"
    source_ref = "knowledge/semantic_aliases.md"
    content = "metric.delivered_revenue: 销售额"
    metadata_json = {"schema_snapshot": "olist-v1"}


class Repository:
    def latest_schema_snapshot(self):
        return None

    def search_knowledge_keyword(self, query: str, limit: int = 8):
        assert "已交付收入" in query
        return [Knowledge()]

    def search_knowledge_vector(self, vector: list[float], limit: int = 8):
        assert vector == [1.0, 0.0, 0.0]
        return [VectorKnowledge(), Knowledge()]

    def list_user_memories(self, user_id: str, confirmed_only: bool = False):
        assert user_id == "analyst-1"
        assert confirmed_only is True
        return [Memory()]


class EmbeddingProvider:
    async def embed(self, texts: list[str]):
        assert texts == ["2018年各州已交付收入是多少"]
        return [[1.0, 0.0, 0.0]]


def test_reciprocal_rank_fusion_orders_by_fused_score():
    fused = reciprocal_rank_fusion(
        keyword_ids=["metric:revenue", "schema:orders"],
        vector_ids=["schema:orders", "metric:revenue"],
    )
    assert fused[0][0] == "metric:revenue"
    assert fused[0][1] == fused[1][1]


@pytest.mark.asyncio
async def test_hybrid_retriever_returns_metrics_knowledge_and_memories():
    retriever = HybridRetriever(Repository(), embedding_provider=EmbeddingProvider())

    context = await retriever.retrieve(
        question="2018年各州已交付收入是多少",
        user_id="analyst-1",
        metrics=DEFAULT_METRICS,
    )

    assert context.metrics[0].name == "delivered_revenue"
    assert context.knowledge_refs == [
        "knowledge/business_glossary.md",
        "knowledge/semantic_aliases.md",
    ]
    assert context.memories[0].value["term"] == "销售额"
