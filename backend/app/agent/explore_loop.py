from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.agent.memory import AgentMemory
from app.agent.schemas import AnalysisResult, ChartHint, Evidence, SQLResult
from app.domain.json_safe import json_safe_rows
from app.agent.tools import build_explore_registry
from app.agent.tools.base import ToolContext
from app.agent.tools.registry import ToolRegistry


@dataclass
class ExploreLoopResult:
    answer: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    sql: str = "-- explore"
    elapsed_ms: int = 0
    returned_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    iterations: int = 0
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


class ExploreLoop:
    """Loop Engineering explore contract: think → act → verify → repair until done."""

    def __init__(
        self,
        *,
        chat_provider,
        query_gateway,
        repository=None,
        registry: ToolRegistry | None = None,
        max_iters: int = 5,
        max_no_progress: int = 2,
    ):
        self.chat_provider = chat_provider
        self.query_gateway = query_gateway
        self.repository = repository
        self.registry = registry or build_explore_registry()
        self.max_iters = max_iters
        self.max_no_progress = max_no_progress

    async def run(
        self,
        *,
        question: str,
        user_id: str,
        conversation_id: str,
        memory: AgentMemory | None = None,
    ) -> ExploreLoopResult:
        memory = memory or AgentMemory()
        context = ToolContext(
            user_id=user_id,
            conversation_id=conversation_id,
            memory=memory,
            query_gateway=self.query_gateway,
            repository=self.repository,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are MetricLens explore agent for Olist ecommerce analytics.\n"
                    "Use tools to answer schema/data exploration questions.\n"
                    "Prefer list_tables / describe_table before inventing column names.\n"
                    "When the user uses pronouns (他/它/这个表), use memory.working.entities.table.\n"
                    "After a successful useful query, optionally save_memory.\n"
                    "When you have enough evidence, stop calling tools and give a final Chinese answer.\n"
                    "Never invent row counts; use returned_rows from tool results. "
                    "If truncated=true, say the result may be limited by LIMIT."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "memory": memory.to_prompt_dict(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        last_success: dict[str, Any] | None = None
        tool_trace: list[dict[str, Any]] = []
        warnings: list[str] = []
        no_progress = 0
        previous_signature: str | None = None

        if self.chat_provider is None or not hasattr(self.chat_provider, "complete_with_tools"):
            result = await self.registry.execute(
                "list_tables",
                {"include_row_counts": True},
                context,
            )
            data = result.data if result.ok else {}
            return ExploreLoopResult(
                answer=result.content if result.ok else f"探索失败: {result.error}",
                columns=list(data.get("columns") or []),
                rows=[list(row) for row in (data.get("preview") or [])],
                sql=str(data.get("sql") or "-- explore"),
                elapsed_ms=int(data.get("elapsed_ms") or 0),
                returned_rows=int(data.get("returned_rows") or 0),
                warnings=["chat provider tool-calling unavailable; used list_tables fallback"],
                iterations=1,
                tool_trace=[{"tool": "list_tables", "ok": result.ok}],
            )

        tools = self.registry.openai_schemas()
        for iteration in range(1, self.max_iters + 1):
            assistant = await self.chat_provider.complete_with_tools(messages, tools)
            tool_calls = assistant.get("tool_calls") or []
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant.get("content") or "",
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                }
            )

            if not tool_calls:
                answer = (assistant.get("content") or "").strip() or "已完成探索。"
                returned_rows = int((last_success or {}).get("returned_rows") or 0)
                return ExploreLoopResult(
                    answer=answer,
                    columns=list((last_success or {}).get("columns") or []),
                    rows=[list(row) for row in ((last_success or {}).get("preview") or [])],
                    sql=str((last_success or {}).get("sql") or "-- explore"),
                    elapsed_ms=int((last_success or {}).get("elapsed_ms") or 0),
                    returned_rows=returned_rows,
                    warnings=warnings,
                    iterations=iteration,
                    tool_trace=tool_trace,
                )

            progress_this_round = False
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    arguments = {}
                    result_error = (
                        "ERROR: invalid tool arguments JSON"
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or str(uuid4()),
                            "content": result_error,
                        }
                    )
                    tool_trace.append({"tool": name, "ok": False, "error": "invalid args"})
                    warnings.append("invalid tool arguments JSON")
                    continue

                result = await self.registry.execute(name, arguments, context)
                tool_trace.append(
                    {
                        "tool": name,
                        "ok": result.ok,
                        "error": result.error,
                        "args": arguments,
                    }
                )
                signature = f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
                if signature != previous_signature:
                    progress_this_round = True
                    previous_signature = signature
                if result.ok and result.data:
                    last_success = result.data
                    if name in {"list_tables", "describe_table", "run_sql"} and self.repository is not None:
                        await self.registry.execute(
                            "save_memory",
                            {
                                "tool_name": name,
                                "args": arguments,
                                "note": f"returned_rows={result.data.get('returned_rows')}",
                            },
                            context,
                        )
                elif not result.ok:
                    warnings.append(result.error or f"{name} failed")

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or str(uuid4()),
                        "content": result.to_message_content(),
                    }
                )

            if not progress_this_round:
                no_progress += 1
            else:
                no_progress = 0
            if no_progress >= self.max_no_progress:
                warnings.append("explore loop stopped: no progress")
                break

        if last_success is not None:
            answer = await self._final_summary(question, last_success, memory)
            return ExploreLoopResult(
                answer=answer,
                columns=list(last_success.get("columns") or []),
                rows=[list(row) for row in (last_success.get("preview") or [])],
                sql=str(last_success.get("sql") or "-- explore"),
                elapsed_ms=int(last_success.get("elapsed_ms") or 0),
                returned_rows=int(last_success.get("returned_rows") or 0),
                warnings=warnings + [f"stopped after {self.max_iters} iterations"],
                iterations=self.max_iters,
                tool_trace=tool_trace,
            )
        return ExploreLoopResult(
            answer="探索未完成：" + ("；".join(warnings) if warnings else "请换个方式提问。"),
            warnings=warnings,
            iterations=self.max_iters,
            tool_trace=tool_trace,
        )

    async def _final_summary(self, question: str, data: dict[str, Any], memory: AgentMemory) -> str:
        returned_rows = int(data.get("returned_rows") or 0)
        truncated = bool(data.get("truncated"))
        preview = data.get("preview") or []
        if self.chat_provider is None or not hasattr(self.chat_provider, "complete_json"):
            examples = "、".join(str(row[0]) for row in preview[:5] if row)
            suffix = "；结果可能被 LIMIT 截断。" if truncated else ""
            return f"查询返回 {returned_rows} 行。示例：{examples}{suffix}"
        payload = await self.chat_provider.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "根据查询元数据生成一句中文摘要，返回JSON: {\"summary\":\"...\"}。"
                        "必须使用 returned_rows；preview 仅作样例；truncated=true 时说明可能被截断。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "returned_rows": returned_rows,
                            "truncated": truncated,
                            "columns": data.get("columns") or [],
                            "preview": [[str(c) for c in row] for row in preview[:10]],
                            "memory": memory.to_prompt_dict(),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        try:
            return json.loads(payload).get("summary") or f"查询返回 {returned_rows} 行。"
        except json.JSONDecodeError:
            return f"查询返回 {returned_rows} 行。"


def explore_result_to_analysis(
    explore: ExploreLoopResult,
    *,
    trace_id: str,
    knowledge_refs: list[str] | None = None,
) -> AnalysisResult:
    row_count = explore.returned_rows or len(explore.rows)
    return AnalysisResult(
        answer=explore.answer,
        columns=explore.columns,
        result_preview=json_safe_rows(explore.rows[:100]),
        sql=SQLResult(statement=explore.sql, query_id=trace_id),
        evidence=Evidence(
            metrics=[],
            schema_refs=[],
            knowledge_refs=knowledge_refs or [],
            filters=[],
            time_window={"start": "", "end": ""},
            row_count=row_count,
            elapsed_ms=explore.elapsed_ms,
            schema_snapshot="olist-v1",
        ),
        trace_id=trace_id,
        warnings=explore.warnings,
        # Catalog / schema exploration: table only, no auto chart.
        chart_hint=ChartHint(enabled=False),
    )
