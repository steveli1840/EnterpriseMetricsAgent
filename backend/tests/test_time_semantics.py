from datetime import date

from app.domain.time_semantics import (
    TimeSemanticsConfig,
    detect_relative_time_expr,
    resolve_relative_time_range,
)


def test_last_week_iso_week_is_previous_monday_sunday():
    # 2026-07-12 is Sunday → this Monday is 2026-07-06 → last week 06-29..07-06
    cfg = TimeSemanticsConfig(week_definition="iso_week")
    resolved = resolve_relative_time_range("上周", as_of="2026-07-12", config=cfg)
    assert resolved.start == "2026-06-29"
    assert resolved.end == "2026-07-06"
    assert resolved.convention == "iso_week"


def test_last_week_trailing_seven_days():
    cfg = TimeSemanticsConfig(week_definition="trailing_7_days")
    resolved = resolve_relative_time_range("上周", as_of="2026-07-12", config=cfg)
    assert resolved.start == "2026-07-05"
    assert resolved.end == "2026-07-12"
    assert resolved.convention == "trailing_7_days"


def test_detect_relative_time_expr():
    assert detect_relative_time_expr("上周的gmv是多少") == "上周"
    assert detect_relative_time_expr("过去7天订单") == "过去7天"
    assert detect_relative_time_expr("2018年GMV") is None
