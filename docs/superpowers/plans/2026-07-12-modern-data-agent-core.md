# Modern Data Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-grade backend loop for EnterpriseMetricsAgent: persisted control-plane state, hybrid retrieval, LangGraph node boundaries, governed metric SQL execution, and query audit evidence.

**Architecture:** Keep LangGraph as the orchestration runtime and keep metric compilation as the SQL authority. Add PostgreSQL-backed repositories and a hybrid retriever so the agent uses metric registry, schema snapshots, business knowledge, and confirmed user memories instead of hard-coded aliases.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, pgvector, LangGraph, SQLGlot, ClickHouse Connect, Pydantic, pytest.

## Global Constraints

- Do not introduce Vanna as the core framework.
- Do not use Deep Agents for the governed query path.
- Do not build full OAuth/OIDC in this phase; keep demo JWTs.
- Do not add arbitrary SQL generation for non-metric questions.
- Keep final SQL governed by metric compilation and SQL guardrails.
- Preserve the current React visual shell unless response shapes require small API adaptations.
- Every persistent user-scoped operation must filter by `tenant_id` and `user_id`.
- Successful answers must include metric, schema, knowledge, filter, time, SQL, and execution evidence.

---

## File Structure

- Create `backend/app/infrastructure/control_plane.py`: SQLAlchemy-backed repository for metrics, schema snapshots, knowledge chunks, conversations, memories, and query audit.
- Expand `backend/app/domain/retrieval.py`: data classes and `HybridRetriever` that fuses exact, keyword, vector, and memory results.
- Modify `backend/app/agent/service.py`: split analysis into intent resolution, SQL compilation, execution, evidence construction, and audit persistence.
- Modify `backend/app/agent/graph.py`: make LangGraph nodes perform real workflow steps.
- Modify `backend/app/main.py`: remove in-memory dictionaries, wire repository/retriever/agent dependencies, and back governance endpoints with PostgreSQL when not testing.
- Modify `backend/app/agent/schemas.py`: add evidence fields for retrieval refs, warnings, and schema snapshot if not already present.
- Add/modify backend tests under `backend/tests/`: repository persistence, retrieval, graph flow, API scoping, audit, and SQL guard coverage.

---

### Task 1: Control Plane Repository

**Files:**
- Create: `backend/app/infrastructure/control_plane.py`
- Test: `backend/tests/test_control_plane_repository.py`

**Interfaces:**
- Produces: `ControlPlaneRepository(session_factory, tenant_id: str = "demo")`
- Produces: `list_published_metrics() -> list[MetricDefinition]`
- Produces: `latest_schema_snapshot() -> SchemaSnapshot | None`
- Produces: `search_knowledge_keyword(query: str, limit: int = 10) -> list[KnowledgeChunk]`
- Produces: `get_or_create_conversation(user_id: str, conversation_id: str, title: str) -> Conversation`
- Produces: `append_conversation_event(user_id: str, conversation_id: str, event: dict) -> Conversation`
- Produces: `list_conversations(user_id: str) -> list[Conversation]`
- Produces: `create_user_memory(user_id: str, kind: str, value: dict, status: str) -> UserMemory`
- Produces: `list_user_memories(user_id: str, confirmed_only: bool = False) -> list[UserMemory]`
- Produces: `confirm_user_memory(user_id: str, memory_id: str) -> UserMemory`
- Produces: `delete_user_memory(user_id: str, memory_id: str) -> bool`
- Produces: `write_query_audit(...) -> QueryAudit`

- [ ] **Step 1: Write failing repository tests**

Create `backend/tests/test_control_plane_repository.py`:

```python
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, KnowledgeChunk, MetricRecord, QueryAudit
from app.infrastructure.control_plane import ControlPlaneRepository


def make_repo():
    engine = create_engine("sqlite+pysqlite:///:memory:")
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
                content="Delivered revenue means delivered order item price plus freight.",
                content_hash="hash",
                embedding_model="test",
                embedding_dimensions=1024,
                embedding=None,
                metadata_json={"schema_snapshot": "snapshot-1"},
            )
        )

    results = repo.search_knowledge_keyword("delivered revenue", limit=5)
    assert len(results) == 1
    assert results[0].source_ref == "knowledge/business_glossary.md"

    audit = repo.write_query_audit(
        user_id="analyst-1",
        conversation_id="workspace",
        trace_id=str(uuid4()),
        normalized_sql="SELECT 1",
        sql_hash="hash",
        evidence={"row_count": 1},
    )
    with Session() as session:
        assert session.get(QueryAudit, audit.id).user_id == "analyst-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend pytest backend/tests/test_control_plane_repository.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.infrastructure.control_plane'`.

- [ ] **Step 3: Implement repository**

Create `backend/app/infrastructure/control_plane.py`:

```python
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import (
    Conversation,
    KnowledgeChunk,
    MetricRecord,
    QueryAudit,
    SchemaSnapshot,
    UserMemory,
)
from app.domain.metrics import MetricDefinition


class NotFoundError(LookupError):
    pass


def _hash_sql(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


class ControlPlaneRepository:
    def __init__(self, session_factory: sessionmaker, tenant_id: str = "demo"):
        self.session_factory = session_factory
        self.tenant_id = tenant_id

    def list_published_metrics(self) -> list[MetricDefinition]:
        with self.session_factory() as session:
            records = session.scalars(
                select(MetricRecord)
                .where(MetricRecord.status == "published")
                .order_by(MetricRecord.name, MetricRecord.version.desc())
            ).all()
        return [MetricDefinition.model_validate(record.definition) for record in records]

    def latest_schema_snapshot(self) -> SchemaSnapshot | None:
        with self.session_factory() as session:
            return session.scalar(
                select(SchemaSnapshot).order_by(SchemaSnapshot.created_at.desc())
            )

    def search_knowledge_keyword(self, query: str, limit: int = 10) -> list[KnowledgeChunk]:
        terms = [term.lower() for term in query.split() if term.strip()]
        with self.session_factory() as session:
            chunks = session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.tenant_id == self.tenant_id)
                .limit(200)
            ).all()
        scored: list[tuple[int, KnowledgeChunk]] = []
        for chunk in chunks:
            haystack = f"{chunk.source_ref}\n{chunk.content}".lower()
            score = sum(term in haystack for term in terms)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].source_ref))
        return [chunk for _, chunk in scored[:limit]]

    def get_or_create_conversation(
        self, user_id: str, conversation_id: str, title: str
    ) -> Conversation:
        conversation_uuid = UUID(conversation_id) if _is_uuid(conversation_id) else None
        with self.session_factory.begin() as session:
            conversation = None
            if conversation_uuid is not None:
                conversation = session.scalar(
                    select(Conversation).where(
                        Conversation.id == conversation_uuid,
                        Conversation.tenant_id == self.tenant_id,
                        Conversation.user_id == user_id,
                    )
                )
            if conversation is None:
                conversation = Conversation(
                    tenant_id=self.tenant_id,
                    user_id=user_id,
                    title=title,
                    state={"client_conversation_id": conversation_id, "events": []},
                )
                session.add(conversation)
                session.flush()
            session.expunge(conversation)
            return conversation

    def append_conversation_event(
        self, user_id: str, conversation_id: str, event: dict
    ) -> Conversation:
        with self.session_factory.begin() as session:
            conversation = self._find_conversation(session, user_id, conversation_id)
            state = dict(conversation.state or {})
            events = list(state.get("events", []))
            events.append({**event, "created_at": datetime.now(UTC).isoformat()})
            state["events"] = events
            conversation.state = state
            session.add(conversation)
            session.flush()
            session.expunge(conversation)
            return conversation

    def list_conversations(self, user_id: str) -> list[Conversation]:
        with self.session_factory() as session:
            conversations = session.scalars(
                select(Conversation)
                .where(
                    Conversation.tenant_id == self.tenant_id,
                    Conversation.user_id == user_id,
                )
                .order_by(Conversation.created_at.desc())
            ).all()
            for conversation in conversations:
                session.expunge(conversation)
            return list(conversations)

    def create_user_memory(
        self, user_id: str, kind: str, value: dict, status: str = "confirmed"
    ) -> UserMemory:
        with self.session_factory.begin() as session:
            memory = UserMemory(
                tenant_id=self.tenant_id,
                user_id=user_id,
                kind=kind,
                value=value,
                status=status,
            )
            session.add(memory)
            session.flush()
            session.expunge(memory)
            return memory

    def list_user_memories(
        self, user_id: str, confirmed_only: bool = False
    ) -> list[UserMemory]:
        statement = select(UserMemory).where(
            UserMemory.tenant_id == self.tenant_id,
            UserMemory.user_id == user_id,
        )
        if confirmed_only:
            statement = statement.where(UserMemory.status == "confirmed")
        with self.session_factory() as session:
            memories = session.scalars(statement.order_by(UserMemory.created_at.desc())).all()
            for memory in memories:
                session.expunge(memory)
            return list(memories)

    def confirm_user_memory(self, user_id: str, memory_id: str) -> UserMemory:
        with self.session_factory.begin() as session:
            memory = session.scalar(
                select(UserMemory).where(
                    UserMemory.id == UUID(memory_id),
                    UserMemory.tenant_id == self.tenant_id,
                    UserMemory.user_id == user_id,
                )
            )
            if memory is None:
                raise NotFoundError("Memory not found")
            memory.status = "confirmed"
            session.add(memory)
            session.flush()
            session.expunge(memory)
            return memory

    def delete_user_memory(self, user_id: str, memory_id: str) -> bool:
        with self.session_factory.begin() as session:
            memory = session.scalar(
                select(UserMemory).where(
                    UserMemory.id == UUID(memory_id),
                    UserMemory.tenant_id == self.tenant_id,
                    UserMemory.user_id == user_id,
                )
            )
            if memory is None:
                return False
            session.delete(memory)
            return True

    def write_query_audit(
        self,
        *,
        user_id: str,
        conversation_id: str,
        trace_id: str,
        normalized_sql: str,
        evidence: dict,
        sql_hash: str | None = None,
    ) -> QueryAudit:
        with self.session_factory.begin() as session:
            audit = QueryAudit(
                tenant_id=self.tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                normalized_sql=normalized_sql,
                sql_hash=sql_hash or _hash_sql(normalized_sql),
                evidence=evidence,
            )
            session.add(audit)
            session.flush()
            session.expunge(audit)
            return audit

    def _find_conversation(self, session, user_id: str, conversation_id: str) -> Conversation:
        conversation_uuid = UUID(conversation_id) if _is_uuid(conversation_id) else None
        statement = select(Conversation).where(
            Conversation.tenant_id == self.tenant_id,
            Conversation.user_id == user_id,
        )
        if conversation_uuid is not None:
            statement = statement.where(Conversation.id == conversation_uuid)
        else:
            statement = statement.where(
                Conversation.state["client_conversation_id"].as_string() == conversation_id
            )
        conversation = session.scalar(statement)
        if conversation is None:
            conversation = Conversation(
                tenant_id=self.tenant_id,
                user_id=user_id,
                title="Workspace",
                state={"client_conversation_id": conversation_id, "events": []},
            )
            session.add(conversation)
            session.flush()
        return conversation


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False
```

- [ ] **Step 4: Run repository tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_control_plane_repository.py -q`

Expected: PASS.

---

### Task 2: Structured Hybrid Retriever

**Files:**
- Modify: `backend/app/domain/retrieval.py`
- Test: `backend/tests/test_retrieval.py`

**Interfaces:**
- Consumes: `ControlPlaneRepository`
- Produces: `RetrievedItem(id: str, source_type: str, source_ref: str, content: str, score: float, metadata: dict)`
- Produces: `RetrievedContext(metrics, schema, knowledge, memories, fused_ids, warnings)`
- Produces: `HybridRetriever(repository, embedding_provider=None).retrieve(question, user_id, limit=8)`

- [ ] **Step 1: Extend retrieval tests**

Replace `backend/tests/test_retrieval.py` with:

```python
from app.domain.metrics import MetricDefinition
from app.domain.retrieval import HybridRetriever, RetrievedItem, reciprocal_rank_fusion


def test_rrf_rewards_documents_found_by_both_retrievers():
    fused = reciprocal_rank_fusion(
        keyword_ids=["metric:gmv", "schema:orders"],
        vector_ids=["schema:orders", "doc:revenue"],
    )
    assert fused[0][0] == "schema:orders"
    assert {item_id for item_id, _ in fused} == {
        "metric:gmv",
        "schema:orders",
        "doc:revenue",
    }


class FakeMemory:
    def __init__(self, kind: str, value: dict, status: str = "confirmed"):
        self.kind = kind
        self.value = value
        self.status = status
        self.id = "memory-1"


class FakeChunk:
    def __init__(self, source_type: str, source_ref: str, content: str, metadata=None):
        self.source_type = source_type
        self.source_ref = source_ref
        self.content = content
        self.metadata_json = metadata or {}


class FakeRepository:
    def list_published_metrics(self):
        return [
            MetricDefinition(
                name="delivered_revenue",
                version=1,
                label="已交付收入",
                description="Delivered order item price plus freight value.",
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
        ]

    def search_knowledge_keyword(self, query: str, limit: int = 10):
        return [
            FakeChunk(
                "knowledge",
                "knowledge/business_glossary.md",
                "Delivered revenue is not profit.",
                {"schema_snapshot": "snapshot-1"},
            )
        ]

    def list_user_memories(self, user_id: str, confirmed_only: bool = False):
        return [FakeMemory("semantic", {"term": "收入", "metric": "delivered_revenue"})]


def test_hybrid_retriever_returns_metric_knowledge_and_memory_context():
    context = HybridRetriever(FakeRepository()).retrieve("2018 年各州收入", "analyst-1")
    assert context.metrics[0].name == "delivered_revenue"
    assert context.knowledge[0].source_ref == "knowledge/business_glossary.md"
    assert context.memories[0].value["metric"] == "delivered_revenue"
    assert "vector retrieval unavailable" in context.warnings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend pytest backend/tests/test_retrieval.py -q`

Expected: FAIL because `HybridRetriever` and retrieval models do not exist.

- [ ] **Step 3: Implement retriever**

Modify `backend/app/domain/retrieval.py`:

```python
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.domain.metrics import MetricDefinition


def reciprocal_rank_fusion(
    *, keyword_ids: list[str], vector_ids: list[str], rank_constant: int = 60
) -> list[tuple[str, float]]:
    """Fuse ordered retrieval results without coupling to a vector database."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in (keyword_ids, vector_ids):
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1.0 / (rank_constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


@dataclass(frozen=True)
class RetrievedItem:
    id: str
    source_type: str
    source_ref: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedContext:
    metrics: list[MetricDefinition]
    schema: list[RetrievedItem]
    knowledge: list[RetrievedItem]
    memories: list[Any]
    fused_ids: list[str]
    warnings: list[str] = field(default_factory=list)


class HybridRetriever:
    def __init__(self, repository, embedding_provider=None):
        self.repository = repository
        self.embedding_provider = embedding_provider

    def retrieve(self, question: str, user_id: str, limit: int = 8) -> RetrievedContext:
        metrics = self._rank_metrics(question, self.repository.list_published_metrics())
        chunks = self.repository.search_knowledge_keyword(question, limit=limit)
        memories = self.repository.list_user_memories(user_id, confirmed_only=True)

        keyword_items = [
            RetrievedItem(
                id=f"{chunk.source_type}:{chunk.source_ref}",
                source_type=chunk.source_type,
                source_ref=chunk.source_ref,
                content=chunk.content,
                score=1.0,
                metadata=dict(chunk.metadata_json or {}),
            )
            for chunk in chunks
        ]
        metric_ids = [f"metric:{metric.name}:v{metric.version}" for metric in metrics]
        keyword_ids = [item.id for item in keyword_items]
        fused = reciprocal_rank_fusion(keyword_ids=[*metric_ids, *keyword_ids], vector_ids=[])
        fused_ids = [item_id for item_id, _ in fused]
        warnings = []
        if self.embedding_provider is None:
            warnings.append("vector retrieval unavailable")

        return RetrievedContext(
            metrics=metrics[:limit],
            schema=[item for item in keyword_items if item.source_type == "schema"],
            knowledge=[item for item in keyword_items if item.source_type != "schema"],
            memories=memories,
            fused_ids=fused_ids,
            warnings=warnings,
        )

    @staticmethod
    def _rank_metrics(question: str, metrics: list[MetricDefinition]) -> list[MetricDefinition]:
        normalized = question.lower()
        scored: list[tuple[int, str, MetricDefinition]] = []
        for metric in metrics:
            haystack = " ".join(
                [
                    metric.name,
                    metric.label,
                    metric.description,
                    *metric.allowed_dimensions,
                ]
            ).lower()
            score = sum(token in normalized or token in haystack for token in normalized.split())
            if metric.name.lower() in normalized or metric.label.lower() in normalized:
                score += 5
            if score:
                scored.append((score, metric.name, metric))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [metric for _, _, metric in scored]
```

- [ ] **Step 4: Run retrieval tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_retrieval.py -q`

Expected: PASS.

---

### Task 3: Persist Conversations, Memories, and Audit API State

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_governance_api.py`

**Interfaces:**
- Consumes: `ControlPlaneRepository`
- API `/api/v1/conversations`, `/api/v1/memories`, and `/api/v1/audit/queries` use repository state.

- [ ] **Step 1: Extend governance API tests**

Modify `backend/tests/test_governance_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def auth(client: TestClient, username: str, password: str) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_conversations_and_memories_are_scoped_to_authenticated_user():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    created = client.post("/api/v1/conversations", json={"title": "Revenue"}, headers=analyst)
    assert created.status_code == 201
    analyst_items = client.get("/api/v1/conversations", headers=analyst).json()["items"]
    admin_items = client.get("/api/v1/conversations", headers=admin).json()["items"]
    assert len(analyst_items) == 1
    assert analyst_items[0]["title"] == "Revenue"
    assert admin_items == []

    memory = client.post(
        "/api/v1/memories",
        json={"kind": "semantic", "value": {"term": "收入", "metric": "delivered_revenue"}},
        headers=analyst,
    )
    assert memory.status_code == 201
    assert memory.json()["status"] == "pending"
    assert client.get("/api/v1/memories", headers=admin).json()["items"] == []

    confirmed = client.post(
        f"/api/v1/memories/{memory.json()['id']}/confirm", headers=analyst
    )
    assert confirmed.json()["status"] == "confirmed"
    assert len(client.get("/api/v1/memories", headers=analyst).json()["items"]) == 1


def test_governance_mutations_require_admin_role():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")
    assert client.post("/api/v1/admin/metrics/sync", headers=analyst).status_code == 403
    assert client.post("/api/v1/admin/metrics/sync", headers=admin).status_code == 202


def test_audit_endpoint_returns_persisted_query_audits():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    response = client.post(
        "/api/v1/chat/stream",
        json={"question": "2018 年 1 月各州已交付收入是多少？", "conversation_id": "workspace"},
        headers=analyst,
    )
    assert response.status_code == 200
    audit = client.get("/api/v1/audit/queries", headers=analyst)
    assert audit.status_code == 200
    assert len(audit.json()["items"]) == 1
    assert audit.json()["items"][0]["user_id"] == "analyst-1"
```

- [ ] **Step 2: Run test to verify current audit path fails**

Run: `PYTHONPATH=backend pytest backend/tests/test_governance_api.py -q`

Expected: FAIL because audit endpoint returns `{"items": []}` after chat.

- [ ] **Step 3: Wire repository in `main.py`**

Modify `backend/app/main.py`:

```python
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, QueryAudit, SessionLocal
from app.infrastructure.control_plane import ControlPlaneRepository, NotFoundError
```

Inside `create_app`, replace local dictionaries with:

```python
    if testing:
        test_engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(test_engine)
        TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
        repository = ControlPlaneRepository(TestSession)
    else:
        repository = ControlPlaneRepository(SessionLocal)
```

Update conversation endpoints:

```python
    @app.get("/api/v1/conversations")
    async def list_conversations(user: dict = Depends(current_user)):
        return {
            "items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "user_id": item.user_id,
                    "created_at": item.created_at.isoformat(),
                    "state": item.state,
                }
                for item in repository.list_conversations(user["sub"])
            ]
        }

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(request: ConversationRequest, user: dict = Depends(current_user)):
        item = repository.get_or_create_conversation(
            user_id=user["sub"],
            conversation_id=str(uuid4()),
            title=request.title,
        )
        return {
            "id": str(item.id),
            "title": item.title,
            "user_id": item.user_id,
            "created_at": item.created_at.isoformat(),
        }
```

Update memory endpoints:

```python
    @app.get("/api/v1/memories")
    async def memories(user: dict = Depends(current_user)):
        return {
            "user_id": user["sub"],
            "items": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "value": item.value,
                    "status": item.status,
                    "created_at": item.created_at.isoformat(),
                }
                for item in repository.list_user_memories(user["sub"])
            ],
        }

    @app.post("/api/v1/memories", status_code=201)
    async def create_memory(request: MemoryRequest, user: dict = Depends(current_user)):
        item = repository.create_user_memory(
            user_id=user["sub"],
            kind=request.kind,
            value=request.value,
            status="pending" if request.kind == "semantic" else "confirmed",
        )
        return {
            "id": str(item.id),
            "kind": item.kind,
            "value": item.value,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
        }

    @app.post("/api/v1/memories/{memory_id}/confirm")
    async def confirm_memory(memory_id: str, user: dict = Depends(current_user)):
        try:
            item = repository.confirm_user_memory(user["sub"], memory_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Memory not found") from exc
        return {
            "id": str(item.id),
            "kind": item.kind,
            "value": item.value,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
        }

    @app.delete("/api/v1/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: str, user: dict = Depends(current_user)):
        repository.delete_user_memory(user["sub"], memory_id)
```

Update audit endpoint:

```python
    @app.get("/api/v1/audit/queries")
    async def audit(user: dict = Depends(current_user)):
        with repository.session_factory() as session:
            rows = session.scalars(
                select(QueryAudit)
                .where(QueryAudit.tenant_id == repository.tenant_id, QueryAudit.user_id == user["sub"])
                .order_by(QueryAudit.created_at.desc())
            ).all()
        return {
            "items": [
                {
                    "id": str(item.id),
                    "user_id": item.user_id,
                    "conversation_id": item.conversation_id,
                    "trace_id": item.trace_id,
                    "sql_hash": item.sql_hash,
                    "normalized_sql": item.normalized_sql,
                    "evidence": item.evidence,
                    "created_at": item.created_at.isoformat(),
                }
                for item in rows
            ]
        }
```

- [ ] **Step 4: Run governance API tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_governance_api.py -q`

Expected: PASS when Task 5 has already added audit persistence. Implement Task 5 before running this endpoint test.

---

### Task 4: Agent Service Uses Retrieved Context

**Files:**
- Modify: `backend/app/agent/service.py`
- Test: `backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `RetrievedContext`
- Produces: `AgentService.resolve_intent(question, context) -> tuple[MetricDefinition, list[str], str, str]`
- Produces: `AgentService.build_result(...) -> AnalysisResult`

- [ ] **Step 1: Add context-driven agent tests**

Modify `backend/tests/test_agent.py`:

```python
import pytest

from app.agent.service import AgentService, AmbiguousMetricError
from app.domain.metrics import MetricDefinition
from app.domain.retrieval import RetrievedContext, RetrievedItem


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


def delivered_revenue_metric():
    return MetricDefinition(
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


@pytest.mark.asyncio
async def test_agent_returns_sql_and_complete_evidence_from_retrieved_context():
    metric = delivered_revenue_metric()
    context = RetrievedContext(
        metrics=[metric],
        schema=[
            RetrievedItem(
                id="schema:analytics.fct_order_items",
                source_type="schema",
                source_ref="analytics.fct_order_items",
                content="customer_state String",
                metadata={"schema_snapshot": "snapshot-1"},
            )
        ],
        knowledge=[
            RetrievedItem(
                id="knowledge:business_glossary",
                source_type="knowledge",
                source_ref="knowledge/business_glossary.md",
                content="Delivered revenue is not profit.",
            )
        ],
        memories=[],
        fused_ids=["metric:delivered_revenue:v1", "knowledge:business_glossary"],
    )
    service = AgentService(metrics=[], query_gateway=FakeQueryGateway())
    result = await service.analyze(
        question="2018年1月各州已交付收入是多少？",
        user_id="analyst-1",
        conversation_id="conversation-1",
        retrieved_context=context,
    )
    assert result.sql.statement.startswith("SELECT")
    assert result.evidence.metrics[0].name == "delivered_revenue"
    assert result.evidence.row_count == 2
    assert result.evidence.schema_snapshot == "snapshot-1"
    assert result.evidence.knowledge_refs == ["knowledge/business_glossary.md"]
    assert result.result_preview[0][0] == "SP"


@pytest.mark.asyncio
async def test_agent_requires_retrieved_metric_context():
    service = AgentService(metrics=[], query_gateway=FakeQueryGateway())
    with pytest.raises(AmbiguousMetricError):
        await service.analyze(
            question="收入是多少？",
            user_id="analyst-1",
            conversation_id="conversation-1",
            retrieved_context=RetrievedContext(
                metrics=[],
                schema=[],
                knowledge=[],
                memories=[],
                fused_ids=[],
            ),
        )
```

- [ ] **Step 2: Run tests to verify failures**

Run: `PYTHONPATH=backend pytest backend/tests/test_agent.py -q`

Expected: FAIL because `AgentService.analyze` does not accept `retrieved_context`.

- [ ] **Step 3: Update `AgentService` signatures and context logic**

Modify `backend/app/agent/service.py`:

```python
from app.domain.retrieval import RetrievedContext
```

Change `understand`:

```python
    async def understand(
        self, question: str, retrieved_context: RetrievedContext | None = None
    ) -> tuple[MetricDefinition, list[str], str, str]:
        if retrieved_context and retrieved_context.metrics:
            metric = retrieved_context.metrics[0]
            return metric, self._dimensions(question, metric), "2018-01-01", "2018-02-01"
        if retrieved_context is not None:
            raise AmbiguousMetricError("没有找到可发布的指标，请明确指标名称")
        ...
```

Change `analyze` signature:

```python
    async def analyze(
        self,
        *,
        question: str,
        user_id: str,
        conversation_id: str,
        retrieved_context: RetrievedContext | None = None,
    ) -> AnalysisResult:
        metric, dimensions, start, end = await self.understand(question, retrieved_context)
        ...
```

Add evidence refs:

```python
        schema_snapshot = None
        knowledge_refs = ["knowledge/business_glossary.md"]
        if retrieved_context is not None:
            knowledge_refs = [item.source_ref for item in retrieved_context.knowledge]
            for item in [*retrieved_context.schema, *retrieved_context.knowledge]:
                schema_snapshot = schema_snapshot or item.metadata.get("schema_snapshot")
```

Update `Evidence(...)`:

```python
                knowledge_refs=knowledge_refs,
                schema_snapshot=schema_snapshot or "unknown",
```

Set warnings:

```python
            warnings=retrieved_context.warnings if retrieved_context else [],
```

- [ ] **Step 4: Run agent tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_agent.py -q`

Expected: PASS.

---

### Task 5: LangGraph Runtime Executes Real Nodes and Persists Audit

**Files:**
- Modify: `backend/app/agent/graph.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_graph_runtime.py`
- Test: `backend/tests/test_governance_api.py`

**Interfaces:**
- Consumes: `AgentService`, `HybridRetriever`, `ControlPlaneRepository`
- Produces: `GovernedAgentRuntime(service, retriever=None, repository=None)`

- [ ] **Step 1: Add graph runtime test**

Create `backend/tests/test_graph_runtime.py`:

```python
import pytest

from app.agent.graph import GovernedAgentRuntime
from app.agent.service import AgentService
from app.domain.metrics import MetricDefinition
from app.domain.retrieval import RetrievedContext


class FakeQueryGateway:
    async def explain(self, sql: str, parameters: dict):
        return {"status": "ok"}

    async def execute(self, sql: str, parameters: dict):
        return {
            "query_id": "query-1",
            "columns": ["customer_state", "delivered_revenue"],
            "rows": [["SP", 1200.0]],
            "elapsed_ms": 10,
        }


class FakeRetriever:
    def __init__(self, metric):
        self.metric = metric

    def retrieve(self, question: str, user_id: str):
        return RetrievedContext(
            metrics=[self.metric],
            schema=[],
            knowledge=[],
            memories=[],
            fused_ids=[f"metric:{self.metric.name}:v{self.metric.version}"],
        )


class FakeRepository:
    def __init__(self):
        self.events = []
        self.audits = []

    def get_or_create_conversation(self, user_id: str, conversation_id: str, title: str):
        self.events.append(("conversation", user_id, conversation_id, title))
        return object()

    def append_conversation_event(self, user_id: str, conversation_id: str, event: dict):
        self.events.append(("event", user_id, conversation_id, event))
        return object()

    def write_query_audit(self, **kwargs):
        self.audits.append(kwargs)
        return object()


@pytest.mark.asyncio
async def test_graph_runtime_retrieves_context_and_persists_audit():
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
    repository = FakeRepository()
    runtime = GovernedAgentRuntime(
        AgentService(metrics=[], query_gateway=FakeQueryGateway()),
        retriever=FakeRetriever(metric),
        repository=repository,
    )
    result = await runtime.analyze(
        question="2018 年各州已交付收入",
        user_id="analyst-1",
        conversation_id="workspace",
    )
    assert result.evidence.metrics[0].name == "delivered_revenue"
    assert repository.audits[0]["user_id"] == "analyst-1"
    assert "SELECT" in repository.audits[0]["normalized_sql"]
```

- [ ] **Step 2: Run graph test to verify it fails**

Run: `PYTHONPATH=backend pytest backend/tests/test_graph_runtime.py -q`

Expected: FAIL because runtime constructor does not accept `retriever` or `repository`.

- [ ] **Step 3: Refactor `GovernedAgentRuntime`**

Modify `backend/app/agent/graph.py` constructor:

```python
    def __init__(self, service: AgentService, retriever=None, repository=None):
        self.service = service
        self.retriever = retriever
        self.repository = repository
        self.graph = self._build_graph()
```

Update `AgentState`:

```python
    retrieved_context: object
    sql: str
    evidence: dict
```

Update nodes:

```python
        async def load_conversation(state: AgentState):
            if self.repository:
                self.repository.get_or_create_conversation(
                    user_id=state["user_id"],
                    conversation_id=state["conversation_id"],
                    title=state["question"][:80],
                )
                self.repository.append_conversation_event(
                    state["user_id"],
                    state["conversation_id"],
                    {"type": "user", "content": state["question"]},
                )
            return {"stage": "conversation_loaded"}

        async def retrieve_context(state: AgentState):
            context = (
                self.retriever.retrieve(state["question"], state["user_id"])
                if self.retriever
                else None
            )
            return {"stage": "context_retrieved", "retrieved_context": context}

        async def execute(state: AgentState):
            result = await self.service.analyze(
                question=state["question"],
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                retrieved_context=state.get("retrieved_context"),
            )
            if self.repository:
                self.repository.write_query_audit(
                    user_id=state["user_id"],
                    conversation_id=state["conversation_id"],
                    trace_id=result.trace_id,
                    normalized_sql=result.sql.statement,
                    evidence=result.evidence.model_dump(),
                )
                self.repository.append_conversation_event(
                    state["user_id"],
                    state["conversation_id"],
                    {"type": "assistant", "trace_id": result.trace_id, "answer": result.answer},
                )
            return {"stage": "answered", "result": result}
```

Graph edges:

```python
        builder.add_edge(START, "authorize")
        builder.add_edge("authorize", "load_conversation")
        builder.add_edge("load_conversation", "retrieve_context")
        builder.add_edge("retrieve_context", "execute_governed_query")
        builder.add_edge("execute_governed_query", END)
```

- [ ] **Step 4: Wire retriever and repository in `main.py`**

Modify imports:

```python
from app.domain.retrieval import HybridRetriever
```

After repository creation:

```python
    retriever = HybridRetriever(repository)
```

Agent metrics:

```python
    metrics = repository.list_published_metrics() or DEFAULT_METRICS
```

Runtime:

```python
    runtime = GovernedAgentRuntime(agent, retriever=retriever, repository=repository)
```

- [ ] **Step 5: Run graph and governance tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_graph_runtime.py backend/tests/test_governance_api.py -q`

Expected: PASS.

---

### Task 6: Governance Endpoints Use Real Control Plane Data

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- `/api/v1/metrics` returns repository metrics when available.
- `/api/v1/schemas` returns latest schema snapshot when available.
- `/api/v1/knowledge` returns indexed knowledge chunks.

- [ ] **Step 1: Extend API tests**

Modify `backend/tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_login_and_list_metrics():
    client = TestClient(create_app(testing=True))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "analyst", "password": "analyst-demo"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    metrics = client.get(
        "/api/v1/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert metrics.status_code == 200
    assert any(item["name"] == "delivered_revenue" for item in metrics.json())


def test_health_live_is_public():
    client = TestClient(create_app(testing=True))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_governance_catalog_endpoints_return_real_shapes():
    client = TestClient(create_app(testing=True))
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "analyst", "password": "analyst-demo"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    schemas = client.get("/api/v1/schemas", headers=headers)
    assert schemas.status_code == 200
    assert "models" in schemas.json()
    assert "snapshot" in schemas.json()

    knowledge = client.get("/api/v1/knowledge", headers=headers)
    assert knowledge.status_code == 200
    assert "documents" in knowledge.json()
    assert "embedding_model" in knowledge.json()
```

- [ ] **Step 2: Run API tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -q`

Expected: PASS after endpoint implementation.

- [ ] **Step 3: Update endpoint implementations**

In `backend/app/main.py`, update `/api/v1/metrics`:

```python
    @app.get("/api/v1/metrics")
    async def list_metrics(user: dict = Depends(current_user)):
        metrics = repository.list_published_metrics() or DEFAULT_METRICS
        return [metric.model_dump() for metric in metrics]
```

Update `/api/v1/schemas`:

```python
    @app.get("/api/v1/schemas")
    async def schemas(user: dict = Depends(current_user)):
        snapshot = repository.latest_schema_snapshot()
        if snapshot is None:
            return {"snapshot": "olist-v1", "models": sorted({m.model for m in DEFAULT_METRICS})}
        models = sorted(
            {
                f"{item.get('database')}.{item.get('table')}"
                for item in snapshot.payload
                if item.get("database") and item.get("table")
            }
        )
        return {"snapshot": snapshot.snapshot_hash, "models": models, "source": snapshot.source}
```

Update `/api/v1/knowledge`:

```python
    @app.get("/api/v1/knowledge")
    async def knowledge(user: dict = Depends(current_user)):
        chunks = repository.search_knowledge_keyword("", limit=50)
        return {
            "documents": [
                {
                    "source_type": chunk.source_type,
                    "source_ref": chunk.source_ref,
                    "content_hash": chunk.content_hash,
                    "metadata": chunk.metadata_json,
                }
                for chunk in chunks
            ],
            "embedding_model": get_settings().dashscope_embedding_model,
        }
```

- [ ] **Step 4: Run API tests**

Run: `PYTHONPATH=backend pytest backend/tests/test_api.py -q`

Expected: PASS.

---

### Task 7: Full Backend Verification

**Files:**
- Modify as needed based on failures.

**Interfaces:**
- Verifies the backend core works as a unit.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
PYTHONPATH=backend pytest \
  backend/tests/test_control_plane_repository.py \
  backend/tests/test_retrieval.py \
  backend/tests/test_agent.py \
  backend/tests/test_graph_runtime.py \
  backend/tests/test_governance_api.py \
  backend/tests/test_api.py \
  backend/tests/test_sql_guard.py \
  backend/tests/test_metric_compiler.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run all backend tests**

Run: `PYTHONPATH=backend pytest backend/tests -q`

Expected: PASS.

- [ ] **Step 3: Run static lint if available**

Run: `ruff check backend`

Expected: PASS. If `ruff` is not installed in the local environment, record that it could not be run.

- [ ] **Step 4: Review git diff**

Run: `git diff -- backend docs/superpowers`

Expected: Diff only includes the planned repository, retrieval, graph, service, API, tests, and docs changes.
