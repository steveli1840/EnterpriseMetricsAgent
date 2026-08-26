import json

import pytest

from app.domain.time_window import is_query_datetime, resolve_time_window


def test_is_query_datetime_accepts_iso_only():
    assert is_query_datetime("2018-01-01")
    assert is_query_datetime("2018-01-01 00:00:00")
    assert not is_query_datetime("2018-01")
    assert not is_query_datetime("2018")
    assert not is_query_datetime("")


@pytest.mark.asyncio
async def test_resolve_time_window_keeps_valid_iso():
    start, end = await resolve_time_window(
        "2018-01-01",
        "2019-01-01",
        question="2018 GMV",
        chat_provider=None,
    )
    assert start == "2018-01-01"
    assert end == "2019-01-01"


@pytest.mark.asyncio
async def test_resolve_time_window_asks_llm_to_repair_partial_dates():
    class FakeChat:
        async def complete_json(self, messages):
            assert "不完整" in messages[0]["content"] or "YYYY-MM-DD" in messages[0]["content"]
            return json.dumps({"start": "2018-01-01", "end": "2019-01-01"})

    start, end = await resolve_time_window(
        "2018-01",
        "2019-01",
        question="2018gmv",
        chat_provider=FakeChat(),
    )
    assert start == "2018-01-01"
    assert end == "2019-01-01"
