import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain.schema_service import SchemaControlService, schema_snapshot_hash
from app.infrastructure.control_plane import ControlPlaneRepository


class Gateway:
    async def test_connection(self):
        return {"status": "ok", "elapsed_ms": 2}

    async def introspect_schema(self):
        return [
            {"database": "analytics", "table": "b", "column": "id", "type": "String"},
            {"database": "analytics", "table": "a", "column": "amount", "type": "Float64"},
        ]


def make_repo():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return ControlPlaneRepository(Session, tenant_id="tenant-a")


def test_schema_snapshot_hash_is_stable_for_sorted_json():
    payload = [{"table": "a", "column": "id"}, {"column": "amount", "table": "b"}]
    same_payload_different_key_order = [
        {"column": "id", "table": "a"},
        {"table": "b", "column": "amount"},
    ]
    assert schema_snapshot_hash(payload) == schema_snapshot_hash(same_payload_different_key_order)


@pytest.mark.asyncio
async def test_schema_service_refreshes_snapshot():
    repo = make_repo()
    service = SchemaControlService(repository=repo, query_gateway=Gateway())

    snapshot = await service.refresh_schema_snapshot()

    assert snapshot.source == "clickhouse"
    assert snapshot.payload[0]["table"] == "a"
    assert repo.latest_schema_snapshot().snapshot_hash == snapshot.snapshot_hash
    assert service.list_models()["models"] == ["analytics.a", "analytics.b"]
    assert service.describe_model("analytics.a")["columns"] == [
        {"name": "amount", "type": "Float64"}
    ]
