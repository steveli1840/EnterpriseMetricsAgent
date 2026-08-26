import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.graph import GovernedAgentRuntime
from app.agent.service import AgentService, AmbiguousMetricError
from app.catalog import DEFAULT_METRICS, load_metrics
from app.domain.knowledge_service import KnowledgeIndexService
from app.domain.metrics import MetricDefinition
from app.domain.schema_service import SchemaControlService
from app.evaluation import load_cases, run_evaluation_suite
from app.db import (
    Base,
    DataSourceConnection,
    KnowledgeChunk,
    MetricRecord,
    SchemaSnapshot,
    configure_session_factory,
    session_factory,
)
from app.domain.retrieval import HybridRetriever
from app.infrastructure.control_plane import ControlPlaneRepository, NotFoundError
from app.infrastructure.providers import DashScopeEmbeddingProvider, DeepSeekChatProvider
from app.infrastructure.query import ControlPlaneQueryGateway, DemoQueryGateway
from app.security import DEMO_USERS, create_token, current_user
from app.settings import get_settings


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    question: str
    conversation_id: str = "default"


class ConversationRequest(BaseModel):
    title: str = "New analysis"


class ConversationUpdateRequest(BaseModel):
    title: str


class MemoryRequest(BaseModel):
    kind: str
    value: dict


class DataSourceRequest(BaseModel):
    name: str
    provider: str
    config: dict
    is_active: bool = False


class MetricUpsertRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1, default=1)
    label: str
    description: str
    model: str
    expression: str
    aggregation: str = "sum"
    time_dimension: str
    grain: str = "order_item"
    allowed_dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    owner: str = "analytics"
    status: str = "published"


def require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def _conversation_payload(item) -> dict:
    return {
        "id": str(item.id),
        "title": item.title,
        "user_id": item.user_id,
        "state": item.state,
        "created_at": item.created_at.isoformat(),
    }


def _memory_payload(item) -> dict:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "value": item.value,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


def _data_source_payload(item) -> dict:
    safe_config = {
        key: ("***" if "password" in key.lower() or "token" in key.lower() else value)
        for key, value in (item.config or {}).items()
    }
    return {
        "id": str(item.id),
        "name": item.name,
        "provider": item.provider,
        "status": item.status,
        "is_active": item.is_active,
        "config": safe_config,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat(),
    }


def _build_testing_repository() -> ControlPlaneRepository:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    glossary_path = Path("knowledge/business_glossary.md")
    glossary_content = (
        glossary_path.read_text()
        if glossary_path.exists()
        else "metric.delivered_revenue: 已交付收入\n"
    )
    with session_factory.begin() as session:
        for metric in DEFAULT_METRICS:
            session.add(
                MetricRecord(
                    name=metric.name,
                    version=metric.version,
                    status=metric.status,
                    definition=metric.model_dump(),
                    definition_hash=f"test-{metric.name}-{metric.version}",
                )
            )
        session.add(
            DataSourceConnection(
                tenant_id="demo",
                name="Local Olist ClickHouse",
                provider="clickhouse",
                status="active",
                is_active=True,
                config={
                    "host": "clickhouse",
                    "port": 8123,
                    "database": "analytics",
                    "user": "agent_readonly",
                },
                created_by="system",
            )
        )
        session.add(
            SchemaSnapshot(
                source="demo",
                snapshot_hash="olist-v1",
                payload=[
                    {
                        "database": "analytics",
                        "table": "fct_order_items",
                        "column": "customer_state",
                        "type": "String",
                    },
                    {
                        "database": "analytics",
                        "table": "fct_order_items",
                        "column": "price",
                        "type": "Float64",
                    },
                    {
                        "database": "analytics",
                        "table": "fct_reviews",
                        "column": "review_score",
                        "type": "UInt8",
                    },
                ],
            )
        )
        session.add(
            KnowledgeChunk(
                tenant_id="demo",
                source_type="knowledge",
                source_ref="knowledge/business_glossary.md",
                content=glossary_content,
                content_hash="test-business-glossary",
                embedding_model="test",
                embedding_dimensions=1024,
                embedding=None,
                metadata_json={"schema_snapshot": "olist-v1"},
            )
        )
    return ControlPlaneRepository(session_factory, tenant_id="demo")


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(title="Enterprise Metrics Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"] ,
        allow_headers=["*"],
    )
    settings = get_settings()
    repository = (
        _build_testing_repository()
        if testing
        else ControlPlaneRepository(
            session_factory(),
            tenant_id="demo",
            configure_session_factory=configure_session_factory,
        )
    )
    gateway = DemoQueryGateway() if testing else ControlPlaneQueryGateway(
        repository=repository,
        settings=settings,
    )
    embedding_provider = None
    if not testing and settings.dashscope_api_key.get_secret_value():
        embedding_provider = DashScopeEmbeddingProvider(settings)
    retriever = HybridRetriever(repository, embedding_provider=embedding_provider)
    knowledge_service = KnowledgeIndexService(
        repository=repository,
        metrics=DEFAULT_METRICS,
        knowledge_root=Path("knowledge"),
        embedding_provider=embedding_provider,
        embedding_model=settings.dashscope_embedding_model if embedding_provider else "none",
        embedding_dimensions=settings.dashscope_embedding_dimensions if embedding_provider else 0,
    )
    schema_service = SchemaControlService(repository=repository, query_gateway=gateway)
    chat_provider = None
    if not testing and settings.deepseek_api_key.get_secret_value():
        chat_provider = DeepSeekChatProvider(settings)
    agent = AgentService(
        metrics=DEFAULT_METRICS,
        query_gateway=gateway,
        chat_provider=chat_provider,
        retriever=retriever,
        repository=repository,
        schema_service=schema_service,
    )
    runtime = GovernedAgentRuntime(agent)
    evaluation_path = Path("evaluations/olist_core_v1.yaml")
    latest_evaluation: dict | None = None

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        settings = get_settings()
        return {
            "status": "ready" if testing else "configured",
            "chat_provider": bool(settings.deepseek_api_key.get_secret_value()),
            "embedding_provider": bool(settings.dashscope_api_key.get_secret_value()),
        }

    @app.post("/api/v1/auth/login")
    async def login(request: LoginRequest):
        user = DEMO_USERS.get(request.username)
        if not user or user["password"] != request.password:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"access_token": create_token(request.username), "token_type": "bearer"}

    @app.get("/api/v1/metrics")
    async def list_metrics(user: dict = Depends(current_user)):
        return [metric.model_dump() for metric in agent.published_metrics()]

    @app.get("/api/v1/metrics/{name}")
    async def get_metric(name: str, user: dict = Depends(current_user)):
        metric = repository.get_metric(name)
        if metric is None:
            metric = next((item for item in agent.published_metrics() if item.name == name), None)
        if metric is None:
            raise HTTPException(status_code=404, detail="Metric not found")
        return metric.model_dump()

    @app.post("/api/v1/admin/metrics", status_code=201)
    async def upsert_metric(request: MetricUpsertRequest, user: dict = Depends(current_user)):
        require_admin(user)
        try:
            metric = MetricDefinition.model_validate(request.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        saved = repository.upsert_metric(metric)
        return saved.model_dump()

    @app.post("/api/v1/admin/metrics/sync", status_code=202)
    async def sync_metrics(user: dict = Depends(current_user)):
        require_admin(user)
        metrics = load_metrics() or DEFAULT_METRICS
        count = repository.sync_metrics_from_definitions(metrics)
        return {"status": "completed", "metrics": count}

    @app.get("/api/v1/conversations")
    async def list_conversations(user: dict = Depends(current_user)):
        return {
            "items": [
                _conversation_payload(item)
                for item in repository.list_conversations(user["sub"])
            ]
        }

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(request: ConversationRequest, user: dict = Depends(current_user)):
        item = repository.get_or_create_conversation(
            user["sub"],
            str(uuid4()),
            request.title,
        )
        return _conversation_payload(item)

    @app.get("/api/v1/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user: dict = Depends(current_user)):
        item = repository.get_conversation(user["sub"], conversation_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return _conversation_payload(item)

    @app.patch("/api/v1/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str,
        request: ConversationUpdateRequest,
        user: dict = Depends(current_user),
    ):
        item = repository.rename_conversation(user["sub"], conversation_id, request.title)
        if item is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return _conversation_payload(item)

    @app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str, user: dict = Depends(current_user)):
        deleted = repository.delete_conversation(user["sub"], conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return None

    @app.post("/api/v1/chat/stream")
    async def chat(request: ChatRequest, user: dict = Depends(current_user)):
        async def events() -> AsyncIterator[str]:
            steps = [
                "理解问题与路由",
                "解析时间与口径",
                "生成并校验查询",
                "查询数据仓库",
                "汇总答案",
            ]
            for step in steps:
                yield f"event: progress\ndata: {json.dumps({'label': step}, ensure_ascii=False)}\n\n"
            try:
                result = await runtime.analyze(
                    question=request.question,
                    user_id=user["sub"],
                    conversation_id=request.conversation_id,
                )
                yield f"event: result\ndata: {result.model_dump_json()}\n\n"
            except AmbiguousMetricError as exc:
                payload = json.dumps({"detail": str(exc)}, ensure_ascii=False)
                yield f"event: clarification\ndata: {payload}\n\n"
            except Exception as exc:
                payload = json.dumps(
                    {"detail": f"查询失败: {exc}"},
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/v1/schemas")
    async def schemas(user: dict = Depends(current_user)):
        return schema_service.list_models()

    @app.get("/api/v1/schemas/{model:path}")
    async def describe_schema_model(model: str, user: dict = Depends(current_user)):
        return schema_service.describe_model(model)

    @app.post("/api/v1/admin/schemas/refresh", status_code=202)
    async def refresh_schemas(user: dict = Depends(current_user)):
        require_admin(user)
        try:
            snapshot = await schema_service.refresh_schema_snapshot()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Schema refresh failed: {exc}")
        return {
            "status": "completed",
            "snapshot": snapshot.snapshot_hash,
            "source": snapshot.source,
            "columns": len(snapshot.payload),
        }

    @app.get("/api/v1/data-sources")
    async def data_sources(user: dict = Depends(current_user)):
        return {"items": [_data_source_payload(item) for item in repository.list_data_sources()]}

    @app.post("/api/v1/admin/data-sources", status_code=201)
    async def create_data_source(request: DataSourceRequest, user: dict = Depends(current_user)):
        require_admin(user)
        try:
            item = repository.create_data_source(
                name=request.name,
                provider=request.provider,
                config=request.config,
                created_by=user["sub"],
                is_active=request.is_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _data_source_payload(item)

    @app.post("/api/v1/admin/data-sources/{source_id}/activate")
    async def activate_data_source(source_id: str, user: dict = Depends(current_user)):
        require_admin(user)
        try:
            item = repository.activate_data_source(source_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="Data source not found")
        return _data_source_payload(item)

    @app.post("/api/v1/admin/data-sources/test")
    async def test_active_data_source(user: dict = Depends(current_user)):
        require_admin(user)
        try:
            return await schema_service.test_active_data_source()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Data source test failed: {exc}")

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

    @app.post("/api/v1/admin/knowledge/reindex", status_code=202)
    async def reindex_knowledge(user: dict = Depends(current_user)):
        require_admin(user)
        try:
            return await knowledge_service.reindex()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Knowledge reindex failed: {exc}")

    @app.post("/api/v1/admin/knowledge/upload", status_code=201)
    async def upload_knowledge(
        user: dict = Depends(current_user),
        file: UploadFile = File(...),
        reindex: bool = True,
    ):
        require_admin(user)
        raw = await file.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="file must be UTF-8 text") from exc
        try:
            path = knowledge_service.save_uploaded_document(
                filename=file.filename or "upload.md",
                content=text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = {"status": "uploaded", "path": str(path)}
        if reindex:
            try:
                indexed = await knowledge_service.reindex()
                result.update(indexed)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"uploaded but reindex failed: {exc}",
                ) from exc
        return result

    @app.get("/api/v1/memories")
    async def memories(user: dict = Depends(current_user)):
        return {
            "user_id": user["sub"],
            "items": [_memory_payload(item) for item in repository.list_user_memories(user["sub"])],
        }

    @app.post("/api/v1/memories", status_code=201)
    async def create_memory(request: MemoryRequest, user: dict = Depends(current_user)):
        item = repository.create_user_memory(
            user["sub"],
            kind=request.kind,
            value=request.value,
            status="pending" if request.kind == "semantic" else "confirmed",
        )
        return _memory_payload(item)

    @app.post("/api/v1/memories/{memory_id}/confirm")
    async def confirm_memory(memory_id: str, user: dict = Depends(current_user)):
        try:
            item = repository.confirm_user_memory(user["sub"], memory_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="Memory not found")
        return _memory_payload(item)

    @app.delete("/api/v1/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: str, user: dict = Depends(current_user)):
        repository.delete_user_memory(user["sub"], memory_id)

    @app.get("/api/v1/audit/queries")
    async def audit(user: dict = Depends(current_user)):
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
                for item in repository.list_query_audits(user["sub"])
            ]
        }

    @app.get("/api/v1/evaluations")
    async def evaluations(user: dict = Depends(current_user)):
        return {
            "cases": len(load_cases(evaluation_path)),
            "latest": latest_evaluation,
        }

    @app.post("/api/v1/evaluations/run", status_code=202)
    async def run_evaluations(user: dict = Depends(current_user)):
        nonlocal latest_evaluation
        require_admin(user)
        result = await run_evaluation_suite(service=agent, path=evaluation_path)
        latest_evaluation = result.model_dump(exclude={"items"})
        return result.model_dump()

    return app


app = create_app()
