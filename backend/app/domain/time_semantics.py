"""Governed relative-time resolution (clock + config, not prompt)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


@dataclass(frozen=True)
class TimeSemanticsConfig:
    timezone: str = "Asia/Shanghai"
    week_definition: str = "iso_week"  # iso_week | trailing_7_days
    week_start: str = "monday"


@dataclass(frozen=True)
class ResolvedTimeRange:
    start: str
    end: str
    label: str
    convention: str
    expression: str


@lru_cache(maxsize=1)
def load_time_semantics(path: str | None = None) -> TimeSemanticsConfig:
    candidates = [
        Path(path) if path else None,
        Path("config/time_semantics.yaml"),
        Path(__file__).resolve().parents[3] / "config" / "time_semantics.yaml",
    ]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        raw = yaml.safe_load(candidate.read_text()) or {}
        return TimeSemanticsConfig(
            timezone=str(raw.get("timezone") or "Asia/Shanghai"),
            week_definition=str(raw.get("week_definition") or "iso_week"),
            week_start=str(raw.get("week_start") or "monday"),
        )
    return TimeSemanticsConfig()


def _as_of_date(as_of: str | None, tz: ZoneInfo) -> date:
    if as_of:
        text = as_of.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return datetime.now(tz).date()


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def resolve_relative_time_range(
    expression: str,
    *,
    as_of: str | None = None,
    config: TimeSemanticsConfig | None = None,
) -> ResolvedTimeRange:
    """Map a relative expression to exclusive [start, end) ISO dates."""
    cfg = config or load_time_semantics()
    tz = ZoneInfo(cfg.timezone)
    today = _as_of_date(as_of, tz)
    expr = (expression or "").strip().lower().replace(" ", "")

    if expr in {"上周", "上一周", "last_week", "lastweek"}:
        if cfg.week_definition == "trailing_7_days":
            end = today
            start = today - timedelta(days=7)
            label = "过去7天"
            convention = "trailing_7_days"
        else:
            this_monday = _monday_of(today)
            start = this_monday - timedelta(days=7)
            end = this_monday
            label = "上一个自然周（周一至周日）"
            convention = "iso_week"
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label=label,
            convention=convention,
            expression=expression,
        )

    if expr in {"本周", "this_week", "thisweek"}:
        start = _monday_of(today)
        end = today + timedelta(days=1)
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label="本周至今（周一起）",
            convention="iso_week_to_date",
            expression=expression,
        )

    if expr in {"上月", "上个月", "last_month", "lastmonth"}:
        first_this = today.replace(day=1)
        end = first_this
        start = (first_this - timedelta(days=1)).replace(day=1)
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label="上一个自然月",
            convention="calendar_month",
            expression=expression,
        )

    if expr in {"本月", "this_month", "thismonth"}:
        start = today.replace(day=1)
        end = today + timedelta(days=1)
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label="本月至今",
            convention="calendar_month_to_date",
            expression=expression,
        )

    if expr in {"今年", "this_year", "thisyear"}:
        start = date(today.year, 1, 1)
        end = today + timedelta(days=1)
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label="今年至今",
            convention="calendar_year_to_date",
            expression=expression,
        )

    if expr in {"去年", "last_year", "lastyear"}:
        start = date(today.year - 1, 1, 1)
        end = date(today.year, 1, 1)
        return ResolvedTimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label="上一个自然年",
            convention="calendar_year",
            expression=expression,
        )

    # 过去N天 / 近N天
    for prefix in ("过去", "近"):
        if expr.startswith(prefix) and expr.endswith("天"):
            digits = "".join(ch for ch in expr[len(prefix) : -1] if ch.isdigit())
            if digits:
                days = max(1, int(digits))
                end = today
                start = today - timedelta(days=days)
                return ResolvedTimeRange(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    label=f"过去{days}天",
                    convention="trailing_n_days",
                    expression=expression,
                )

    raise ValueError(f"unsupported relative time expression: {expression}")


def detect_relative_time_expr(question: str) -> str | None:
    """Cheap detector for relative phrases in the user question."""
    text = (question or "").strip().lower().replace(" ", "")
    mapping = [
        ("上周", "上周"),
        ("上一周", "上周"),
        ("本周", "本周"),
        ("上个月", "上月"),
        ("上月", "上月"),
        ("本月", "本月"),
        ("去年", "去年"),
        ("今年", "今年"),
    ]
    for needle, expr in mapping:
        if needle in text:
            return expr
    import re

    match = re.search(r"(过去|近)(\d+)天", text)
    if match:
        return f"{match.group(1)}{match.group(2)}天"
    return None


def range_to_dict(resolved: ResolvedTimeRange) -> dict[str, Any]:
    return {
        "start": resolved.start,
        "end": resolved.end,
        "label": resolved.label,
        "convention": resolved.convention,
        "expression": resolved.expression,
    }
