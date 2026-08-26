from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.catalog import DEFAULT_METRICS
from app.db import Base
from app.domain.knowledge_service import KnowledgeIndexService, content_hash
from app.infrastructure.control_plane import ControlPlaneRepository


class EmbeddingProvider:
    async def embed(self, texts: list[str]):
        return [[1.0, 0.0, 0.0] for _ in texts]


def make_repo():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return ControlPlaneRepository(Session, tenant_id="tenant-a")


def test_content_hash_is_stable():
    assert content_hash("revenue") == content_hash("revenue")


@pytest.mark.asyncio
async def test_knowledge_index_service_replaces_chunks(tmp_path: Path):
    repo = make_repo()
    repo.upsert_schema_snapshot(
        source="clickhouse",
        snapshot_hash="snapshot-1",
        payload=[
            {
                "database": "analytics",
                "table": "fct_order_items",
                "column": "customer_state",
                "type": "String",
            }
        ],
    )
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "business_glossary.md").write_text("净流水 means delivered_revenue.")
    service = KnowledgeIndexService(
        repository=repo,
        metrics=DEFAULT_METRICS,
        knowledge_root=knowledge_root,
        embedding_provider=EmbeddingProvider(),
        embedding_model="test-embedding",
        embedding_dimensions=3,
    )

    result = await service.reindex()

    assert result["status"] == "completed"
    assert result["documents"] >= 3
    refs = [chunk.source_ref for chunk in repo.search_knowledge_keyword("净流水", limit=10)]
    assert "knowledge/business_glossary.md" in refs
    assert not any(chunk.source_type == "schema" for chunk in repo.search_knowledge_keyword("", limit=100))
    assert repo.search_knowledge_vector([1.0, 0.0, 0.0], limit=1)
