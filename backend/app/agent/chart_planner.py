"""Chart planner: hard gate → optional LLM plan → hard veto."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.schemas import ChartHint

_META_Y = frozenset(
    {
        "position",
        "ordinal",
        "ordinal_position",
        "id",
        "type",
        "database",
        "table",
        "name",
        "engine",
        "uuid",
    }
)
_TIME_COL = re.compile(r"(^|_)(month|date|day|week|year|time|at)(_|$)", re.I)
_CATALOG_SQL = re.compile(
    r"\bsystem\.(tables|columns)\b|\bDESCRIBE\b|\bSHOW\s+TABLES\b",
    re.I,
)


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str) and value.strip() != "":
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _unique_count(values: list[str]) -> int:
    return len({value for value in values})


def looks_like_catalog(columns: list[str], sql: str = "") -> bool:
    if sql and _CATALOG_SQL.search(sql):
        return True
    lower = [column.lower() for column in columns]
    if "database" in lower and "name" in lower:
        return True
    if "table" in lower and "name" in lower and "type" in lower:
        return True
    if "position" in lower and "type" in lower:
        return True
    return False


def hard_gate(
    *,
    route: str,
    columns: list[str],
    rows: list[list],
    sql: str = "",
    dimensions: list[str] | None = None,
) -> ChartHint | None:
    """Return a disabled hint when charting is clearly inappropriate.

    Returns None when the case is a candidate for LLM planning (or deterministic allow).
    """
    if route in {"explore", "direct"}:
        return ChartHint(enabled=False)
    if looks_like_catalog(columns, sql):
        return ChartHint(enabled=False)
    if len(columns) < 2 or len(rows) < 2:
        return ChartHint(enabled=False)
    if not (dimensions or []):
        # Metric total without breakdown → table / single number is enough.
        return ChartHint(enabled=False)

    y_idx = len(columns) - 1
    sample = rows[:20]
    if not sample or not all(_is_numeric(row[y_idx]) if len(row) > y_idx else False for row in sample):
        return ChartHint(enabled=False)
    if columns[y_idx].lower() in _META_Y:
        return ChartHint(enabled=False)
    return None


def _default_plan(
    columns: list[str],
    rows: list[list],
    *,
    dimensions: list[str] | None = None,
    metric_name: str | None = None,
) -> ChartHint:
    x = dimensions[0] if dimensions and dimensions[0] in columns else columns[0]
    y = metric_name if metric_name and metric_name in columns else columns[-1]
    x_idx = columns.index(x)
    categories = [str(row[x_idx]) for row in rows]
    if _unique_count(categories) < 2:
        return ChartHint(enabled=False)
    chart_type = "line" if _TIME_COL.search(x) else "bar"
    return ChartHint(enabled=True, type=chart_type, x=x, y=y)


def hard_veto(hint: ChartHint, *, columns: list[str], rows: list[list]) -> ChartHint:
    if not hint.enabled:
        return ChartHint(enabled=False)
    if not hint.x or not hint.y:
        return ChartHint(enabled=False)
    if hint.x not in columns or hint.y not in columns:
        return ChartHint(enabled=False)
    if hint.y.lower() in _META_Y:
        return ChartHint(enabled=False)
    x_idx = columns.index(hint.x)
    y_idx = columns.index(hint.y)
    sample = rows[:20]
    if not all(_is_numeric(row[y_idx]) if len(row) > y_idx else False for row in sample):
        return ChartHint(enabled=False)
    categories = [str(row[x_idx]) for row in rows]
    if _unique_count(categories) < 2:
        return ChartHint(enabled=False)
    chart_type = hint.type if hint.type in {"bar", "line"} else None
    if chart_type is None:
        chart_type = "line" if _TIME_COL.search(hint.x) else "bar"
    return ChartHint(enabled=True, type=chart_type, x=hint.x, y=hint.y)


async def plan_chart(
    *,
    question: str,
    route: str,
    columns: list[str],
    rows: list[list],
    sql: str = "",
    dimensions: list[str] | None = None,
    metric_name: str | None = None,
    chat_provider=None,
) -> ChartHint:
    gated = hard_gate(
        route=route,
        columns=columns,
        rows=rows,
        sql=sql,
        dimensions=dimensions,
    )
    if gated is not None:
        return gated

    fallback = _default_plan(
        columns,
        rows,
        dimensions=dimensions,
        metric_name=metric_name,
    )
    if chat_provider is None or not hasattr(chat_provider, "complete_json"):
        return hard_veto(fallback, columns=columns, rows=rows)

    preview = [[str(cell) for cell in row] for row in rows[:12]]
    try:
        payload = await chat_provider.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "你是数据分析可视化规划器。根据问题和结果预览，判断是否需要图表。"
                        "只返回 JSON："
                        '{"enabled":true/false,"type":"bar"|"line"|null,"x":"列名"|null,"y":"列名"|null}。'
                        "规则：字段清单/元数据/目录查询不要图；类别对比用 bar；时间趋势用 line；"
                        "单值或无分析意义不要图。x/y 必须是给定 columns 之一。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "route": route,
                            "metric": metric_name,
                            "dimensions": dimensions or [],
                            "columns": columns,
                            "preview": preview,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        data = json.loads(payload)
        planned = ChartHint(
            enabled=bool(data.get("enabled")),
            type=data.get("type"),
            x=data.get("x"),
            y=data.get("y"),
        )
        return hard_veto(planned, columns=columns, rows=rows)
    except Exception:
        return hard_veto(fallback, columns=columns, rows=rows)
