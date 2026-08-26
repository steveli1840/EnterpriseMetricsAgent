import asyncio
import hashlib
import json
from pathlib import Path

import clickhouse_connect
from sqlalchemy import delete, select

from app.catalog import DEFAULT_METRICS
from app.db import (
    DataSourceConnection,
    KnowledgeChunk,
    MetricRecord,
    SchemaSnapshot,
    SessionLocal,
    configure_session_factory,
)
from app.infrastructure.providers import DashScopeEmbeddingProvider, DeepSeekChatProvider
from app.settings import get_settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def main():
    settings = get_settings()
    configure_session_factory()
    if not settings.dashscope_api_key.get_secret_value():
        raise RuntimeError("DASHSCOPE_API_KEY is required to initialize the vector index")
    if not settings.deepseek_api_key.get_secret_value():
        raise RuntimeError("DEEPSEEK_API_KEY is required to initialize the agent")
    await DeepSeekChatProvider(settings).health()

    clickhouse = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password.get_secret_value(),
        database=settings.clickhouse_database,
    )
    schema_rows = clickhouse.query(
        "SELECT database, table, name, type FROM system.columns "
        "WHERE database = 'analytics' ORDER BY table, position"
    ).result_rows
    schema_payload = [
        {"database": row[0], "table": row[1], "column": row[2], "type": row[3]}
        for row in schema_rows
    ]
    snapshot_hash = digest(json.dumps(schema_payload, sort_keys=True))

    documents: list[tuple[str, str, str]] = []
    for metric in DEFAULT_METRICS:
        documents.append(
            (
                "metric",
                f"metric:{metric.name}:v{metric.version}",
                f"{metric.label}\n{metric.description}\nmodel={metric.model}\n"
                f"dimensions={','.join(metric.allowed_dimensions)}",
            )
        )
    for path in sorted(Path("knowledge").glob("**/*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".yaml", ".csv"}:
            documents.append(("knowledge", str(path), path.read_text()))
    for table in sorted({row[1] for row in schema_rows}):
        fields = [f"{row[2]} {row[3]}" for row in schema_rows if row[1] == table]
        documents.append(("schema", f"analytics.{table}", f"analytics.{table}\n" + "\n".join(fields)))

    provider = DashScopeEmbeddingProvider(settings)
    vectors: list[list[float]] = []
    for start in range(0, len(documents), 10):
        vectors.extend(await provider.embed([item[2] for item in documents[start : start + 10]]))

    with SessionLocal.begin() as session:
        if session.scalar(select(DataSourceConnection).where(DataSourceConnection.is_active.is_(True))) is None:
            session.add(
                DataSourceConnection(
                    tenant_id="demo",
                    name="Local Olist ClickHouse",
                    provider="clickhouse",
                    status="active",
                    is_active=True,
                    config={
                        "host": settings.clickhouse_host,
                        "port": settings.clickhouse_port,
                        "database": settings.clickhouse_database,
                        "user": settings.clickhouse_user,
                    },
                    created_by="system",
                )
            )
        for metric in DEFAULT_METRICS:
            definition = metric.model_dump()
            definition_hash = digest(json.dumps(definition, sort_keys=True))
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
        if session.scalar(select(SchemaSnapshot).where(SchemaSnapshot.snapshot_hash == snapshot_hash)) is None:
            session.add(
                SchemaSnapshot(source="clickhouse", snapshot_hash=snapshot_hash, payload=schema_payload)
            )
        session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.embedding_model == settings.dashscope_embedding_model,
                KnowledgeChunk.embedding_dimensions == settings.dashscope_embedding_dimensions,
            )
        )
        for (source_type, source_ref, content), vector in zip(documents, vectors, strict=True):
            session.add(
                KnowledgeChunk(
                    source_type=source_type,
                    source_ref=source_ref,
                    content=content,
                    content_hash=digest(content),
                    embedding_model=settings.dashscope_embedding_model,
                    embedding_dimensions=settings.dashscope_embedding_dimensions,
                    embedding=vector,
                    metadata_json={"schema_snapshot": snapshot_hash},
                )
            )
    print({"metrics": len(DEFAULT_METRICS), "documents": len(documents), "snapshot": snapshot_hash})


if __name__ == "__main__":
    asyncio.run(main())
