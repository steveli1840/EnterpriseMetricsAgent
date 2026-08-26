from pydantic import BaseModel, Field


class MetricEvidence(BaseModel):
    name: str
    version: int
    label: str
    owner: str


class SQLResult(BaseModel):
    dialect: str = "clickhouse"
    statement: str
    query_id: str


class Evidence(BaseModel):
    metrics: list[MetricEvidence]
    schema_refs: list[str]
    knowledge_refs: list[str] = Field(default_factory=list)
    filters: list[str]
    time_window: dict[str, str]
    warehouse: str = "clickhouse"
    row_count: int
    elapsed_ms: int
    schema_snapshot: str = "olist-v1"


class ChartHint(BaseModel):
    """Backend hint for whether the UI should auto-render a chart."""

    enabled: bool = False
    type: str | None = None  # "bar" | "line"
    x: str | None = None
    y: str | None = None


class AnalysisResult(BaseModel):
    answer: str
    columns: list[str]
    result_preview: list[list]
    sql: SQLResult
    evidence: Evidence
    trace_id: str
    warnings: list[str] = Field(default_factory=list)
    chart_hint: ChartHint | None = None

