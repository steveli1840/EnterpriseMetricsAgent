from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MetricDefinition(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    label: str
    description: str
    model: str
    expression: str
    aggregation: Literal["sum", "avg", "count", "count_distinct", "max", "min"]
    time_dimension: str
    grain: str
    allowed_dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    owner: str
    status: Literal["draft", "published", "deprecated"] = "draft"

    @model_validator(mode="after")
    def published_metrics_are_described(self) -> "MetricDefinition":
        if self.status == "published" and not self.description.strip():
            raise ValueError("published metrics require a description")
        return self


def _aggregate(metric: MetricDefinition) -> str:
    if metric.aggregation == "count_distinct":
        return f"uniqExact({metric.expression})"
    return f"{metric.aggregation}({metric.expression})"


def compile_metric_query(
    metric: MetricDefinition,
    *,
    dimensions: list[str],
    start: str,
    end: str,
    dialect: Literal["clickhouse", "bigquery"],
) -> str:
    if metric.status != "published":
        raise ValueError("only published metrics can be queried")
    unknown = sorted(set(dimensions) - set(metric.allowed_dimensions))
    if unknown:
        raise ValueError(f"dimensions are not allowed for {metric.name}: {', '.join(unknown)}")

    selections = [*dimensions, f"{_aggregate(metric)} AS {metric.name}"]
    where = [*metric.filters]
    if dialect == "clickhouse":
        where.extend(
            [
                f"{metric.time_dimension} >= {{start:DateTime}}",
                f"{metric.time_dimension} < {{end:DateTime}}",
            ]
        )
    else:
        where.extend(
            [
                f"{metric.time_dimension} >= @start",
                f"{metric.time_dimension} < @end",
            ]
        )
    lines = [f"SELECT {', '.join(selections)}", f"FROM {metric.model}", f"WHERE {' AND '.join(where)}"]
    if dimensions:
        lines.append(f"GROUP BY {', '.join(dimensions)}")
    lines.append(f"ORDER BY {metric.name} DESC")
    return "\n".join(lines)

