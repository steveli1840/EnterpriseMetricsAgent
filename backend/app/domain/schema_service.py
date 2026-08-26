import hashlib
import json

from app.db import SchemaSnapshot


def schema_snapshot_hash(payload: list[dict]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()


class SchemaControlService:
    def __init__(self, *, repository, query_gateway):
        self.repository = repository
        self.query_gateway = query_gateway

    async def test_active_data_source(self) -> dict:
        source = self.repository.active_data_source()
        result = await self.query_gateway.test_connection()
        return {
            **result,
            "source": source.name if source is not None else "settings.clickhouse",
            "provider": source.provider if source is not None else "clickhouse",
        }

    async def refresh_schema_snapshot(self) -> SchemaSnapshot:
        source = self.repository.active_data_source()
        payload = await self.query_gateway.introspect_schema()
        payload = sorted(
            payload,
            key=lambda item: (
                str(item.get("database", "")),
                str(item.get("table", "")),
                str(item.get("column", "")),
            ),
        )
        return self.repository.upsert_schema_snapshot(
            source=source.provider if source is not None else "clickhouse",
            snapshot_hash=schema_snapshot_hash(payload),
            payload=payload,
        )

    def list_models(self) -> dict:
        snapshot = self.repository.latest_schema_snapshot()
        if snapshot is None:
            return {"snapshot": None, "source": None, "models": []}
        models = sorted(
            {
                f"{item['database']}.{item['table']}"
                for item in snapshot.payload
                if item.get("database") and item.get("table")
            }
        )
        return {"snapshot": snapshot.snapshot_hash, "source": snapshot.source, "models": models}

    def describe_model(self, model: str) -> dict:
        snapshot = self.repository.latest_schema_snapshot()
        if snapshot is None:
            return {"snapshot": None, "source": None, "model": model, "columns": []}
        columns = [
            {
                "name": item.get("column") or item.get("name"),
                "type": item.get("type"),
            }
            for item in snapshot.payload
            if f"{item.get('database')}.{item.get('table')}" == model
            and (item.get("column") or item.get("name"))
        ]
        return {
            "snapshot": snapshot.snapshot_hash,
            "source": snapshot.source,
            "model": model,
            "columns": columns,
        }
