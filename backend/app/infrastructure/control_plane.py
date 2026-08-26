import hashlib
import json
import math
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import (
    Conversation,
    DataSourceConnection,
    KnowledgeChunk,
    MetricRecord,
    QueryAudit,
    SchemaSnapshot,
    UserMemory,
)
from app.domain.metrics import MetricDefinition


class NotFoundError(LookupError):
    pass


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def query_terms(query: str) -> list[str]:
    terms = [term.lower() for term in query.split() if term.strip()]
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
    for size in range(2, min(8, len(cjk)) + 1):
        terms.extend(cjk[index : index + size].lower() for index in range(len(cjk) - size + 1))
    return sorted(set(terms), key=lambda item: (-len(item), item))


class ControlPlaneRepository:
    def __init__(
        self,
        session_factory: sessionmaker,
        tenant_id: str = "demo",
        configure_session_factory=None,
    ):
        self.session_factory = session_factory
        self.tenant_id = tenant_id
        self.configure_session_factory = configure_session_factory

    def _sessions(self) -> sessionmaker:
        if self.session_factory.kw.get("bind") is None and self.configure_session_factory:
            self.session_factory = self.configure_session_factory()
        return self.session_factory

    def list_published_metrics(self) -> list[MetricDefinition]:
        with self._sessions()() as session:
            records = session.scalars(
                select(MetricRecord)
                .where(MetricRecord.status == "published")
                .order_by(MetricRecord.name, MetricRecord.version.desc())
            ).all()
        return [MetricDefinition.model_validate(record.definition) for record in records]

    def get_metric(self, name: str) -> MetricDefinition | None:
        with self._sessions()() as session:
            record = session.scalar(
                select(MetricRecord)
                .where(MetricRecord.name == name)
                .order_by(MetricRecord.version.desc())
            )
        if record is None:
            return None
        return MetricDefinition.model_validate(record.definition)

    def upsert_metric(self, metric: MetricDefinition) -> MetricDefinition:
        definition = metric.model_dump()
        definition_hash = hashlib.sha256(
            json.dumps(definition, sort_keys=True).encode()
        ).hexdigest()
        with self._sessions().begin() as session:
            existing = session.scalar(
                select(MetricRecord).where(
                    MetricRecord.name == metric.name,
                    MetricRecord.version == metric.version,
                )
            )
            if existing is None:
                session.add(
                    MetricRecord(
                        name=metric.name,
                        version=metric.version,
                        status=metric.status,
                        definition=definition,
                        definition_hash=definition_hash,
                    )
                )
            else:
                existing.status = metric.status
                existing.definition = definition
                existing.definition_hash = definition_hash
        return metric

    def sync_metrics_from_definitions(self, metrics: list[MetricDefinition]) -> int:
        for metric in metrics:
            self.upsert_metric(metric)
        return len(metrics)

    def latest_schema_snapshot(self) -> SchemaSnapshot | None:
        with self._sessions()() as session:
            return session.scalar(
                select(SchemaSnapshot).order_by(SchemaSnapshot.created_at.desc())
            )

    def upsert_schema_snapshot(
        self,
        *,
        source: str,
        snapshot_hash: str,
        payload: list[dict],
    ) -> SchemaSnapshot:
        with self._sessions().begin() as session:
            snapshot = session.scalar(
                select(SchemaSnapshot).where(SchemaSnapshot.snapshot_hash == snapshot_hash)
            )
            if snapshot is None:
                snapshot = SchemaSnapshot(
                    source=source,
                    snapshot_hash=snapshot_hash,
                    payload=payload,
                )
                session.add(snapshot)
            return snapshot

    def list_data_sources(self) -> list[DataSourceConnection]:
        with self._sessions()() as session:
            return session.scalars(
                select(DataSourceConnection)
                .where(DataSourceConnection.tenant_id == self.tenant_id)
                .order_by(DataSourceConnection.is_active.desc(), DataSourceConnection.created_at.desc())
            ).all()

    def active_data_source(self) -> DataSourceConnection | None:
        with self._sessions()() as session:
            return session.scalar(
                select(DataSourceConnection)
                .where(
                    DataSourceConnection.tenant_id == self.tenant_id,
                    DataSourceConnection.status == "active",
                    DataSourceConnection.is_active.is_(True),
                )
                .order_by(DataSourceConnection.created_at.desc())
            )

    def create_data_source(
        self,
        *,
        name: str,
        provider: str,
        config: dict,
        created_by: str,
        is_active: bool = False,
    ) -> DataSourceConnection:
        if provider not in {"clickhouse", "bigquery"}:
            raise ValueError("Unsupported data source provider")
        with self._sessions().begin() as session:
            if is_active:
                self._deactivate_data_sources(session)
            source = DataSourceConnection(
                tenant_id=self.tenant_id,
                name=name,
                provider=provider,
                config=config,
                created_by=created_by,
                is_active=is_active,
                status="active",
            )
            session.add(source)
            return source

    def activate_data_source(self, source_id: str) -> DataSourceConnection:
        if not _is_uuid(source_id):
            raise NotFoundError("Data source not found")
        with self._sessions().begin() as session:
            source = session.scalar(
                select(DataSourceConnection).where(
                    DataSourceConnection.id == UUID(source_id),
                    DataSourceConnection.tenant_id == self.tenant_id,
                    DataSourceConnection.status == "active",
                )
            )
            if source is None:
                raise NotFoundError("Data source not found")
            self._deactivate_data_sources(session)
            source.is_active = True
            return source

    def search_knowledge_keyword(self, query: str, limit: int = 10) -> list[KnowledgeChunk]:
        terms = query_terms(query)
        with self._sessions()() as session:
            chunks = session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.tenant_id == self.tenant_id)
                .limit(200)
            ).all()
        if not terms:
            return chunks[:limit]

        scored: list[tuple[int, KnowledgeChunk]] = []
        for chunk in chunks:
            haystack = f"{chunk.source_ref}\n{chunk.content}".lower()
            score = sum(term in haystack for term in terms)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].source_ref))
        return [chunk for _, chunk in scored[:limit]]

    def search_knowledge_vector(
        self,
        vector: list[float],
        limit: int = 10,
    ) -> list[KnowledgeChunk]:
        if not vector:
            return []
        database_ranked = self._search_knowledge_vector_database(vector=vector, limit=limit)
        if database_ranked is not None:
            return database_ranked
        with self._sessions()() as session:
            chunks = session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.tenant_id == self.tenant_id)
                .limit(500)
            ).all()

        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in chunks:
            embedding = chunk.embedding
            if not embedding:
                continue
            score = cosine_similarity(vector, list(embedding))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].source_ref))
        return [chunk for _, chunk in scored[:limit]]

    def replace_knowledge_chunks(
        self,
        *,
        chunks: list[dict],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> int:
        with self._sessions().begin() as session:
            existing = session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == self.tenant_id)
            ).all()
            for chunk in existing:
                session.delete(chunk)
            for chunk in chunks:
                session.add(
                    KnowledgeChunk(
                        tenant_id=self.tenant_id,
                        source_type=chunk["source_type"],
                        source_ref=chunk["source_ref"],
                        content=chunk["content"],
                        content_hash=chunk["content_hash"],
                        embedding_model=embedding_model,
                        embedding_dimensions=embedding_dimensions,
                        embedding=chunk.get("embedding"),
                        metadata_json=chunk.get("metadata", {}),
                    )
                )
        return len(chunks)

    def _search_knowledge_vector_database(
        self,
        *,
        vector: list[float],
        limit: int,
    ) -> list[KnowledgeChunk] | None:
        try:
            order_expression = KnowledgeChunk.embedding.cosine_distance(vector)
        except AttributeError:
            return None
        try:
            with self._sessions()() as session:
                return session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.tenant_id == self.tenant_id,
                        KnowledgeChunk.embedding.is_not(None),
                    )
                    .order_by(order_expression)
                    .limit(limit)
                ).all()
        except Exception:
            return None

    def get_or_create_conversation(
        self, user_id: str, conversation_id: str, title: str
    ) -> Conversation:
        with self._sessions().begin() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                conversation = Conversation(
                    tenant_id=self.tenant_id,
                    user_id=user_id,
                    title=title,
                    state={"client_conversation_id": conversation_id, "events": []},
                )
                session.add(conversation)
            return conversation

    def append_conversation_event(
        self,
        user_id: str,
        conversation_id: str,
        event: dict,
    ) -> Conversation:
        with self._sessions().begin() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                conversation = Conversation(
                    tenant_id=self.tenant_id,
                    user_id=user_id,
                    title="New analysis",
                    state={"client_conversation_id": conversation_id, "events": []},
                )
                session.add(conversation)

            state = dict(conversation.state or {})
            events = list(state.get("events", []))
            events.append({**event, "created_at": datetime.now(UTC).isoformat()})
            state["events"] = events
            state.setdefault("client_conversation_id", conversation_id)
            conversation.state = state
            return conversation

    def get_conversation_snapshot(self, user_id: str, conversation_id: str) -> dict:
        with self._sessions()() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                return {"events": [], "focus": {}}
            state = dict(conversation.state or {})
            return {
                "events": list(state.get("events", [])),
                "focus": dict(state.get("focus") or {}),
            }

    def update_conversation_focus(
        self,
        user_id: str,
        conversation_id: str,
        focus: dict,
    ) -> Conversation:
        with self._sessions().begin() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                conversation = Conversation(
                    tenant_id=self.tenant_id,
                    user_id=user_id,
                    title="New analysis",
                    state={"client_conversation_id": conversation_id, "events": []},
                )
                session.add(conversation)
            state = dict(conversation.state or {})
            merged = dict(state.get("focus") or {})
            merged.update({key: value for key, value in focus.items() if value})
            state["focus"] = merged
            state.setdefault("client_conversation_id", conversation_id)
            conversation.state = state
            return conversation

    def list_conversations(self, user_id: str) -> list[Conversation]:
        with self._sessions()() as session:
            return session.scalars(
                select(Conversation)
                .where(
                    Conversation.tenant_id == self.tenant_id,
                    Conversation.user_id == user_id,
                )
                .order_by(Conversation.created_at.desc())
            ).all()

    def get_conversation(self, user_id: str, conversation_id: str) -> Conversation | None:
        with self._sessions()() as session:
            return self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )

    def rename_conversation(
        self, user_id: str, conversation_id: str, title: str
    ) -> Conversation | None:
        with self._sessions().begin() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                return None
            conversation.title = title.strip() or conversation.title
            return conversation

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._sessions().begin() as session:
            conversation = self._find_conversation(
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                return False
            session.delete(conversation)
            return True

    def create_user_memory(
        self,
        user_id: str,
        kind: str,
        value: dict,
        status: str,
    ) -> UserMemory:
        with self._sessions().begin() as session:
            memory = UserMemory(
                tenant_id=self.tenant_id,
                user_id=user_id,
                kind=kind,
                value=value,
                status=status,
            )
            session.add(memory)
            return memory

    def list_user_memories(
        self,
        user_id: str,
        confirmed_only: bool = False,
    ) -> list[UserMemory]:
        query = select(UserMemory).where(
            UserMemory.tenant_id == self.tenant_id,
            UserMemory.user_id == user_id,
        )
        if confirmed_only:
            query = query.where(UserMemory.status == "confirmed")
        with self._sessions()() as session:
            return session.scalars(query.order_by(UserMemory.created_at.desc())).all()

    def confirm_user_memory(self, user_id: str, memory_id: str) -> UserMemory:
        with self._sessions().begin() as session:
            memory = self._find_memory(session=session, user_id=user_id, memory_id=memory_id)
            if memory is None:
                raise NotFoundError("Memory not found")
            memory.status = "confirmed"
            return memory

    def delete_user_memory(self, user_id: str, memory_id: str) -> bool:
        with self._sessions().begin() as session:
            memory = self._find_memory(session=session, user_id=user_id, memory_id=memory_id)
            if memory is None:
                return False
            session.delete(memory)
            return True

    def list_query_audits(self, user_id: str) -> list[QueryAudit]:
        with self._sessions()() as session:
            return session.scalars(
                select(QueryAudit)
                .where(
                    QueryAudit.tenant_id == self.tenant_id,
                    QueryAudit.user_id == user_id,
                )
                .order_by(QueryAudit.created_at.desc())
            ).all()

    def write_query_audit(
        self,
        *,
        user_id: str,
        conversation_id: str,
        trace_id: str,
        normalized_sql: str,
        evidence: dict,
        sql_hash_value: str | None = None,
    ) -> QueryAudit:
        with self._sessions().begin() as session:
            audit = QueryAudit(
                tenant_id=self.tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                sql_hash=sql_hash_value or sql_hash(normalized_sql),
                normalized_sql=normalized_sql,
                evidence=evidence,
            )
            session.add(audit)
            return audit

    def _find_conversation(
        self,
        *,
        session,
        user_id: str,
        conversation_id: str,
    ) -> Conversation | None:
        conditions = [
            Conversation.tenant_id == self.tenant_id,
            Conversation.user_id == user_id,
        ]
        if _is_uuid(conversation_id):
            conditions.append(Conversation.id == UUID(conversation_id))
        else:
            conditions.append(
                Conversation.state["client_conversation_id"].as_string() == conversation_id
            )
        return session.scalar(select(Conversation).where(*conditions))

    def _find_memory(self, *, session, user_id: str, memory_id: str) -> UserMemory | None:
        if not _is_uuid(memory_id):
            return None
        return session.scalar(
            select(UserMemory).where(
                UserMemory.id == UUID(memory_id),
                UserMemory.tenant_id == self.tenant_id,
                UserMemory.user_id == user_id,
            )
        )

    def _deactivate_data_sources(self, session) -> None:
        for source in session.scalars(
            select(DataSourceConnection).where(DataSourceConnection.tenant_id == self.tenant_id)
        ):
            source.is_active = False


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
