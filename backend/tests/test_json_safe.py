import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.agent.schemas import AnalysisResult, ChartHint, Evidence, SQLResult
from app.domain.json_safe import json_safe_rows, json_safe_value


def test_json_safe_decimal_and_dates():
    assert json_safe_value(Decimal("12.50")) == 12.5
    assert json_safe_value(date(2018, 1, 1)) == "2018-01-01"
    assert json_safe_value(datetime(2018, 1, 1, 12, 0, 0)).startswith("2018-01-01")
    assert json_safe_value(UUID("00000000-0000-0000-0000-000000000001")) == (
        "00000000-0000-0000-0000-000000000001"
    )


def test_json_safe_rows_roundtrip_json_dumps():
    rows = json_safe_rows([["SP", Decimal("125430.22")], ["RJ", Decimal("82440.10")]])
    payload = json.dumps({"rows": rows})
    assert "125430.22" in payload
    assert json.loads(payload)["rows"][0][1] == 125430.22


def test_analysis_result_with_decimal_preview_is_json_serializable():
    result = AnalysisResult(
        answer="ok",
        columns=["customer_state", "gmv"],
        result_preview=json_safe_rows([["SP", Decimal("100.5")]]),
        sql=SQLResult(statement="SELECT 1", query_id="t1"),
        evidence=Evidence(
            metrics=[],
            schema_refs=[],
            filters=[],
            time_window={"start": "2018-01-01", "end": "2018-02-01"},
            row_count=1,
            elapsed_ms=1,
        ),
        trace_id="trace-1",
        chart_hint=ChartHint(enabled=True, type="bar", x="customer_state", y="gmv"),
    )
    dumped = result.model_dump()
    json.dumps(dumped)
    result.model_dump_json()
