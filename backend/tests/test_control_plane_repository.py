from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, KnowledgeChunk, MetricRecord, QueryAudit
from app.infrastructure.control_plane import ControlPlaneRepository, cosine_similarity


def make_repo():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return ControlPlaneRepository(Session, tenant_id="tenant-a"), Session


def test_repository_loads_published_metrics_from_registry():
    repo, Session = make_repo()
    definition = {
        "name": "delivered_revenue",
        "version": 1,
        "label": "已交付收入",
        "description": "Delivered order item price plus freight value.",
        "model": "analytics.fct_order_items",
        "expression": "price + freight_value",
        "aggregation": "sum",
        "time_dimension": "order_purchase_at",
        "grain": "order_item",
        "allowed_dimensions": ["customer_state"],
        "filters": ["order_status = 'delivered'"],
        "owner": "analytics",
        "status": "published",
    }
    with Session.begin() as session:
        session.add(
            MetricRecord(
                name="delivered_revenue",
                version=1,
                status="published",
                definition=definition,
                definition_hash="hash",
            )
        )

    metrics = repo.list_published_metrics()

    assert [metric.name for metric in metrics] == ["delivered_revenue"]


def test_conversations_and_memories_are_scoped_by_user():
    repo, _ = make_repo()
    repo.get_or_create_conversation("analyst-1", "workspace", "Workspace")
    repo.append_conversation_event(
        "analyst-1",
        "workspace",
        {"type": "user", "content": "2018 年收入"},
    )
    repo.get_or_create_conversation("admin-1", "workspace", "Admin workspace")

    assert len(repo.list_conversations("analyst-1")) == 1
    assert len(repo.list_conversations("admin-1")) == 1
    assert repo.list_conversations("analyst-1")[0].state["events"][0]["content"] == "2018 年收入"

    memory = repo.create_user_memory(
        "analyst-1",
        kind="semantic",
        value={"term": "收入", "metric": "delivered_revenue"},
        status="pending",
    )
    assert repo.list_user_memories("analyst-1", confirmed_only=True) == []
    repo.confirm_user_memory("analyst-1", str(memory.id))
    assert len(repo.list_user_memories("analyst-1", confirmed_only=True)) == 1
    assert repo.list_user_memories("admin-1") == []


def test_keyword_search_and_audit_persistence():
    repo, Session = make_repo()
    with Session.begin() as session:
        session.add(
            KnowledgeChunk(
                tenant_id="tenant-a",
                source_type="knowledge",
                source_ref="knowledge/business_glossary.md",
                content="已交付收入 means delivered order item price plus freight.",
                content_hash="hash",
                embedding_model="test",
                embedding_dimensions=1024,
                embedding=None,
                metadata_json={"schema_snapshot": "snapshot-1"},
            )
        )

    results = repo.search_knowledge_keyword("2018年各州已交付收入是多少", limit=5)
    assert len(results) == 1
    assert results[0].source_ref == "knowledge/business_glossary.md"

    audit = repo.write_query_audit(
        user_id="analyst-1",
        conversation_id="workspace",
        trace_id="trace-1",
        normalized_sql="SELECT 1",
        evidence={"row_count": 1},
    )
    with Session() as session:
        assert session.get(QueryAudit, audit.id).user_id == "analyst-1"


def test_vector_search_ranks_embedded_knowledge_chunks():
    repo, Session = make_repo()
    with Session.begin() as session:
        session.add_all(
            [
                KnowledgeChunk(
                    tenant_id="tenant-a",
                    source_type="knowledge",
                    source_ref="knowledge/revenue.md",
                    content="Revenue definition",
                    content_hash="hash-a",
                    embedding_model="test",
                    embedding_dimensions=3,
                    embedding=[1.0, 0.0, 0.0],
                    metadata_json={},
                ),
                KnowledgeChunk(
                    tenant_id="tenant-a",
                    source_type="knowledge",
                    source_ref="knowledge/reviews.md",
                    content="Review score definition",
                    content_hash="hash-b",
                    embedding_model="test",
                    embedding_dimensions=3,
                    embedding=[0.0, 1.0, 0.0],
                    metadata_json={},
                ),
            ]
        )

    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0
    results = repo.search_knowledge_vector([0.9, 0.1, 0.0], limit=2)
    assert [item.source_ref for item in results] == [
        "knowledge/revenue.md",
        "knowledge/reviews.md",
    ]


def test_data_sources_are_tenant_scoped_and_single_active():
    repo, _ = make_repo()
    first = repo.create_data_source(
        name="ClickHouse A",
        provider="clickhouse",
        config={"host": "clickhouse-a", "database": "analytics"},
        created_by="admin-1",
        is_active=True,
    )
    second = repo.create_data_source(
        name="ClickHouse B",
        provider="clickhouse",
        config={"host": "clickhouse-b", "database": "analytics"},
        created_by="admin-1",
        is_active=False,
    )

    assert repo.active_data_source().id == first.id
    repo.activate_data_source(str(second.id))
    sources = repo.list_data_sources()
    assert repo.active_data_source().id == second.id
    assert sum(1 for source in sources if source.is_active) == 1
