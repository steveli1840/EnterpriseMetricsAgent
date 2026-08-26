"""Time-window contract for metric queries.

Natural-language time understanding belongs to the LLM router.
This module only validates / repairs the structured start/end contract
required by ClickHouse ``{start:DateTime}`` / ``{end:DateTime}``.
"""

from __future__ import annotations

import json
from datetime import date, datetime


def is_query_datetime(value: str | None) -> bool:
    """Return True when value is already a ClickHouse-safe DateTime literal."""
    if not value or not str(value).strip():
        return False
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


async def resolve_time_window(
    start: str | None,
    end: str | None,
    *,
    question: str,
    chat_provider=None,
) -> tuple[str | None, str | None]:
    """Ensure start/end satisfy the query DateTime contract.

    1. If both bounds already parse as ISO dates → keep them.
    2. Otherwise ask the LLM to rewrite into exclusive ``[start, end)`` ISO dates.
    3. If no chat provider, leave unchanged (caller may fall back to data min/max).
    """
    start_ok = is_query_datetime(start)
    end_ok = is_query_datetime(end)
    if start_ok and end_ok:
        return str(start).strip(), str(end).strip()

    if chat_provider is None:
        return start, end

    payload = await chat_provider.complete_json(
        [
            {
                "role": "system",
                "content": (
                    "你负责把业务时间范围规范成查询参数。只返回JSON。\n"
                    "字段: start, end\n"
                    "要求:\n"
                    "- 使用半开区间 [start, end)，即 start 含、end 不含\n"
                    "- start/end 必须是 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS\n"
                    "- 禁止 YYYY、YYYY-MM 等不完整格式\n"
                    "- 例: 2018年 → start=2018-01-01, end=2019-01-01\n"
                    "- 例: 2018年1月 → start=2018-01-01, end=2018-02-01\n"
                    "结合用户问题理解意图；当前 start/end 可能不完整，请改写。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "start": start,
                        "end": end,
                        "today": date.today().isoformat(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return start, end

    fixed_start = data.get("start") or start
    fixed_end = data.get("end") or end
    if is_query_datetime(fixed_start) and is_query_datetime(fixed_end):
        return str(fixed_start).strip(), str(fixed_end).strip()
    return start, end
