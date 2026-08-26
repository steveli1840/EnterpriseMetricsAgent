import asyncio
from time import perf_counter
from uuid import uuid4

from app.domain.json_safe import json_safe_rows, json_safe_value
from app.settings import Settings


class ClickHouseQueryGateway:
    def __init__(self, settings: Settings, config: dict | None = None):
        self.settings = settings
        self.config = config or {}

    def _client(self):
        import clickhouse_connect

        return clickhouse_connect.get_client(
            host=self.config.get("host", self.settings.clickhouse_host),
            port=int(self.config.get("port", self.settings.clickhouse_port)),
            database=self.config.get("database", self.settings.clickhouse_database),
            username=self.config.get("user", self.settings.clickhouse_user),
            password=self.config.get(
                "password",
                self.settings.clickhouse_password.get_secret_value(),
            ),
            settings={
                "readonly": 1,
                "max_execution_time": self.settings.max_query_seconds,
                "max_result_rows": self.settings.max_query_rows,
                "result_overflow_mode": "break",
            },
        )

    async def explain(self, sql: str, parameters: dict):
        client = self._client()
        await asyncio.to_thread(client.command, f"EXPLAIN {sql}", parameters=parameters)
        return {"status": "ok"}

    async def execute(self, sql: str, parameters: dict):
        client = self._client()
        started = perf_counter()
        result = await asyncio.to_thread(client.query, sql, parameters=parameters)
        return {
            "query_id": str(uuid4()),
            "columns": list(result.column_names),
            "rows": json_safe_rows([list(row) for row in result.result_rows]),
            "elapsed_ms": round((perf_counter() - started) * 1000),
        }

    async def test_connection(self):
        client = self._client()
        started = perf_counter()
        await asyncio.to_thread(client.command, "SELECT 1")
        return {"status": "ok", "elapsed_ms": round((perf_counter() - started) * 1000)}

    async def introspect_schema(self):
        client = self._client()
        database = self.config.get("database", self.settings.clickhouse_database)
        result = await asyncio.to_thread(
            client.query,
            "SELECT database, table, name, type FROM system.columns "
            "WHERE database = {database:String} ORDER BY table, position",
            parameters={"database": database},
        )
        return [
            {"database": row[0], "table": row[1], "column": row[2], "type": row[3]}
            for row in result.result_rows
        ]


class DemoQueryGateway:
    """Only used by tests and explicit demo fallback mode."""

    async def explain(self, sql: str, parameters: dict):
        return {"status": "ok"}

    async def execute(self, sql: str, parameters: dict):
        return {
            "query_id": "demo-query",
            "columns": ["customer_state", "delivered_revenue"],
            "rows": [["SP", 125430.22], ["RJ", 82440.10], ["MG", 71209.44]],
            "elapsed_ms": 18,
        }

    async def test_connection(self):
        return {"status": "ok", "elapsed_ms": 1}

    async def introspect_schema(self):
        return [
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
        ]


class BigQueryQueryGateway:
    def __init__(self, *, project: str, maximum_bytes_billed: int, dataset: str | None = None):
        from google.cloud import bigquery

        self.bigquery = bigquery
        self.client = bigquery.Client(project=project)
        self.project = project
        self.dataset = dataset
        self.maximum_bytes_billed = maximum_bytes_billed

    async def explain(self, sql: str, parameters: dict):
        config = self.bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = await asyncio.to_thread(self.client.query, sql, job_config=config)
        if job.total_bytes_processed > self.maximum_bytes_billed:
            raise ValueError("query exceeds the configured BigQuery byte limit")
        return {"estimated_bytes": job.total_bytes_processed}

    async def execute(self, sql: str, parameters: dict):
        config = self.bigquery.QueryJobConfig(maximum_bytes_billed=self.maximum_bytes_billed)
        started = perf_counter()
        job = await asyncio.to_thread(self.client.query, sql, job_config=config)
        rows = await asyncio.to_thread(lambda: list(job.result()))
        columns = [field.name for field in job.schema]
        return {
            "query_id": job.job_id,
            "columns": columns,
            "rows": json_safe_rows(
                [[json_safe_value(row[column]) for column in columns] for row in rows]
            ),
            "elapsed_ms": round((perf_counter() - started) * 1000),
        }

    async def test_connection(self):
        started = perf_counter()
        await asyncio.to_thread(self.client.query, "SELECT 1")
        return {"status": "ok", "elapsed_ms": round((perf_counter() - started) * 1000)}

    async def introspect_schema(self):
        if not self.dataset:
            raise ValueError("BigQuery schema refresh requires config.dataset")

        def load_columns():
            tables = list(self.client.list_tables(f"{self.project}.{self.dataset}"))
            columns = []
            for table_item in tables:
                table = self.client.get_table(table_item.reference)
                for field in table.schema:
                    columns.append(
                        {
                            "database": self.project,
                            "table": f"{self.dataset}.{table.table_id}",
                            "column": field.name,
                            "type": field.field_type,
                        }
                    )
            return columns

        return await asyncio.to_thread(load_columns)


class ControlPlaneQueryGateway:
    def __init__(self, *, repository, settings: Settings):
        self.repository = repository
        self.settings = settings

    def _gateway(self):
        source = self.repository.active_data_source()
        if source is None:
            return ClickHouseQueryGateway(self.settings)
        if source.provider == "clickhouse":
            return ClickHouseQueryGateway(self.settings, source.config)
        if source.provider == "bigquery":
            return BigQueryQueryGateway(
                project=source.config["project"],
                maximum_bytes_billed=int(source.config.get("maximum_bytes_billed", 1_000_000_000)),
                dataset=source.config.get("dataset"),
            )
        raise ValueError(f"Unsupported active data source provider: {source.provider}")

    async def explain(self, sql: str, parameters: dict):
        return await self._gateway().explain(sql, parameters)

    async def execute(self, sql: str, parameters: dict):
        return await self._gateway().execute(sql, parameters)

    async def test_connection(self):
        return await self._gateway().test_connection()

    async def introspect_schema(self):
        return await self._gateway().introspect_schema()
