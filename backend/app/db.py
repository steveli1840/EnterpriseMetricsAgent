from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.settings import get_settings

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - used by minimal local test environments
    Vector = None


class Base(DeclarativeBase):
    pass


class MetricRecord(Base):
    __tablename__ = "metric_registry"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), index=True)
    definition: Mapped[dict] = mapped_column(JSON)
    definition_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="demo")
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_dimensions: Mapped[int]
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024) if Vector else JSON,
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class DataSourceConnection(Base):
    __tablename__ = "data_source_connections"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="demo")
    name: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_active: Mapped[bool] = mapped_column(default=False, index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="demo")
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256), default="New analysis")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class UserMemory(Base):
    __tablename__ = "user_memories"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="demo")
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class QueryAudit(Base):
    __tablename__ = "query_audit"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="demo")
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    sql_hash: Mapped[str] = mapped_column(String(64))
    normalized_sql: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


def engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


SessionLocal = sessionmaker(expire_on_commit=False)


def configure_session_factory() -> sessionmaker:
    if SessionLocal.kw.get("bind") is None:
        SessionLocal.configure(bind=engine())
    return SessionLocal


def session_factory() -> sessionmaker:
    return SessionLocal
