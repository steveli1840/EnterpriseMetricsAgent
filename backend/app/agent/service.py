import json
import re
from dataclasses import dataclass, field
from uuid import uuid4

from app.agent.explore_loop import ExploreLoop, explore_result_to_analysis
from app.agent.memory import AgentMemory, MemoryManager
from app.agent.chart_planner import plan_chart
from app.agent.schemas import AnalysisResult, Evidence, MetricEvidence, SQLResult
from app.domain.json_safe import json_safe_rows
from app.domain.metrics import MetricDefinition, compile_metric_query
from app.domain.retrieval import RetrievedContext
from app.domain.sql_guard import validate_read_only_sql
from app.domain.time_semantics import detect_relative_time_expr, resolve_relative_time_range
from app.domain.time_window import is_query_datetime, resolve_time_window
from app.infrastructure.control_plane import query_terms, sql_hash


class AmbiguousMetricError(ValueError):
    pass


_AFFIRM_RE = re.compile(
    r"^(好|好的|可以|行|嗯|嗯嗯|ok|okay|yes|yep|来吧|继续|拆一下|拆解一下|按这个来|麻烦了)[!！.。]?$",
    re.I,
)
_DIM_ASK_RE = re.compile(r"(有哪[些么]维度|哪些维度|什么维度|维度有哪些|拆解一下|怎么拆|按什么拆)")


@dataclass
class ResolvedIntent:
    route: str = "direct"  # metric | explore | direct
    is_metric_query: bool = False
    metric: MetricDefinition | None = None
    dimensions: list[str] = field(default_factory=list)
    start: str | None = None
    end: str | None = None
    time_expr: str | None = None  # relative: 上周 / 本月 / 过去7天
    direct_answer: str | None = None
    pending_action: dict | None = None
    clear_pending: bool = False
    time_convention: str | None = None

class AgentService:
    def __init__(
        self,
        *,
        metrics: list[MetricDefinition],
        query_gateway,
        chat_provider=None,
        retriever=None,
        repository=None,
        schema_service=None,
        memory_manager: MemoryManager | None = None,
    ):
        self.metrics = metrics
        self.query_gateway = query_gateway
        self.chat_provider = chat_provider
        self.retriever = retriever
        self.repository = repository
        self.schema_service = schema_service
        self.memory_manager = memory_manager or MemoryManager(repository)

    def published_metrics(self) -> list[MetricDefinition]:
        if self.repository is None:
            return [m for m in self.metrics if m.status == "published"]
        persisted = self.repository.list_published_metrics()
        return persisted or [m for m in self.metrics if m.status == "published"]

    def resolve_metric(
        self,
        question: str,
        context: RetrievedContext | None = None,
    ) -> MetricDefinition:
        normalized = question.lower()
        scored: list[tuple[int, MetricDefinition]] = []
        metrics = context.metrics if context and context.metrics else self.published_metrics()
        memory_aliases = self._memory_aliases(context)
        knowledge_aliases = self._knowledge_aliases(context, prefix="metric")
        for metric in metrics:
            candidate_aliases = (
                metric.name,
                metric.label,
                metric.description,
                *memory_aliases.get(metric.name, ()),
                *knowledge_aliases.get(metric.name, ()),
            )
            score = sum(alias.lower() in normalized for alias in candidate_aliases if alias)
            score += self._knowledge_metric_score(question, metric, context)
            if score:
                scored.append((score, metric))
        if not scored:
            raise AmbiguousMetricError("没有找到可发布的指标，请明确指标名称")
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return scored[0][1]

    def _dimensions(
        self,
        question: str,
        metric: MetricDefinition,
        context: RetrievedContext | None = None,
    ) -> list[str]:
        dimension_aliases = self._knowledge_aliases(context, prefix="dimension")
        memory_aliases = self._dimension_memory_aliases(context)
        scored: list[tuple[int, str]] = []
        normalized = question.lower()
        for dimension in metric.allowed_dimensions:
            aliases = (
                dimension,
                *dimension_aliases.get(dimension, ()),
                *memory_aliases.get(dimension, ()),
            )
            matched = [len(alias) for alias in aliases if alias and alias.lower() in normalized]
            if matched:
                scored.append((max(matched), dimension))
        if not scored:
            return []
        best_score = max(score for score, _dimension in scored)
        return [dimension for score, dimension in scored if score == best_score]

    @staticmethod
    def _memory_aliases(context: RetrievedContext | None) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        if context is None:
            return aliases
        for memory in context.memories:
            metric_name = memory.value.get("metric")
            term = memory.value.get("term") or memory.value.get("alias")
            if metric_name and term:
                aliases.setdefault(str(metric_name), []).append(str(term))
        return aliases

    @staticmethod
    def _dimension_memory_aliases(context: RetrievedContext | None) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        if context is None:
            return aliases
        for memory in context.memories:
            dimension_name = memory.value.get("dimension")
            term = memory.value.get("term") or memory.value.get("alias")
            if dimension_name and term:
                aliases.setdefault(str(dimension_name), []).append(str(term))
        return aliases

    @staticmethod
    def _knowledge_aliases(
        context: RetrievedContext | None,
        *,
        prefix: str,
    ) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        if context is None:
            return aliases
        marker = f"{prefix}."
        for item in context.knowledge:
            for raw_line in item.content.splitlines():
                line = raw_line.strip().lstrip("-").strip()
                if not line.lower().startswith(marker) or ":" not in line:
                    continue
                key, raw_aliases = line.split(":", 1)
                name = key.removeprefix(marker).strip()
                values = [value.strip() for value in raw_aliases.split(",") if value.strip()]
                aliases.setdefault(name, []).extend(values)
        return aliases

    @staticmethod
    def _knowledge_metric_score(
        question: str,
        metric: MetricDefinition,
        context: RetrievedContext | None,
    ) -> int:
        if context is None:
            return 0
        terms = query_terms(question)
        identifiers = (metric.name.lower(), metric.label.lower())
        score = 0
        for item in context.knowledge:
            content = item.content.lower()
            if not any(identifier and identifier in content for identifier in identifiers):
                continue
            overlap = sum(term in content for term in terms)
            if overlap:
                score += 2 + overlap
        return score

    async def retrieve_context(self, *, question: str, user_id: str) -> RetrievedContext | None:
        if self.retriever is None:
            return None
        return await self.retriever.retrieve(
            question=question,
            user_id=user_id,
            metrics=self.published_metrics(),
        )

    async def understand(
        self,
        question: str,
        context: RetrievedContext | None = None,
        memory: AgentMemory | None = None,
    ) -> ResolvedIntent:
        """Route-only understanding: metric | explore | direct. Never generate SQL."""
        metrics_list = context.metrics if context and context.metrics else self.published_metrics()
        catalog = [
            {
                "name": m.name,
                "label": m.label,
                "description": m.description,
                "model": m.model,
                "allowed_dimensions": m.allowed_dimensions,
            }
            for m in metrics_list
            if m.status == "published"
        ]

        knowledge_snippets: list[str] = []
        if context is not None:
            knowledge_snippets = [item.content for item in (context.knowledge or [])]

        memory = memory or AgentMemory()
        followup = self._resolve_followup(question, memory)
        if followup is not None:
            return followup

        memory_payload = memory.to_prompt_dict()

        if self.chat_provider is None:
            try:
                metric = self.resolve_metric(question, context)
                return ResolvedIntent(
                    route="metric",
                    is_metric_query=True,
                    metric=metric,
                    dimensions=self._dimensions(question, metric, context),
                )
            except AmbiguousMetricError:
                lowered = question.lower()
                if any(token in lowered for token in ("表", "字段", "列", "schema", "城市", "多少行")):
                    return ResolvedIntent(route="explore")
                return ResolvedIntent(
                    route="direct",
                    direct_answer="可用的指标包括："
                    + "、".join(m.label for m in self.published_metrics()),
                )

        all_context = json.dumps(
            {
                "question": question,
                "metrics": catalog,
                "knowledge": knowledge_snippets[:3],
                "memory": memory_payload,
            },
            ensure_ascii=False,
        )
        payload = await self.chat_provider.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "你是受治理的数据分析路由助手。只做意图分类，禁止生成SQL。\n"
                        "返回JSON字段:\n"
                        "- route: metric | explore | direct\n"
                        "- metric/dimensions/start/end/time_expr: route=metric 时填写\n"
                        "- 相对时间（上周/本月/过去N天）只填 time_expr，不要自己猜绝对日期\n"
                        "- 绝对时间才填 start/end，必须是 YYYY-MM-DD（半开区间[start,end)）\n"
                        "- 禁止 2018 或 2018-01 这种不完整格式\n"
                        "- direct_answer: route=direct 时的中文回答\n"
                        "规则:\n"
                        "- 已发布指标的业务查询/按维度拆解查数 → metric\n"
                        "- 问指标有哪些维度/怎么拆：优先看 memory.working.entities.metric，"
                        "只允许回答该指标 allowed_dimensions，不要编造字段\n"
                        "- 短确认(好/可以/行)且 memory 有 pending_action 或 metric → metric，"
                        "继承 time_window，选用 suggested/allowed 维度\n"
                        "- 表/字段/schema/表大小/城市列表/数据探查/指代追问 → explore\n"
                        "- 纯解释口径且无需查数 → direct\n"
                        "指代(他/它/这个表)时仍 route=explore，由记忆承接实体。"
                    ),
                },
                {"role": "user", "content": all_context},
            ]
        )
        intent = json.loads(payload)
        route = str(intent.get("route") or "").lower().strip()
        if route not in {"metric", "explore", "direct"}:
            if intent.get("is_metric_query"):
                route = "metric"
            else:
                route = "explore" if not intent.get("direct_answer") else "direct"

        if route == "metric":
            metric_name = self._coerce_metric_name(intent.get("metric"))
            selected = next(
                (
                    m
                    for m in self.published_metrics()
                    if m.name == metric_name and m.status == "published"
                ),
                None,
            )
            if selected is None:
                selected = self._metric_from_memory(memory)
            if selected is None:
                return ResolvedIntent(
                    route="direct",
                    direct_answer=f"未找到指标'{metric_name}'。可用: "
                    + "、".join(m.label for m in self.published_metrics()),
                )
            time_expr = intent.get("time_expr") or detect_relative_time_expr(question)
            start, end = intent.get("start") or None, intent.get("end") or None
            if time_expr:
                start, end = None, None
            elif not start or not end:
                mem_start, mem_end = self._time_from_memory(memory)
                start = start or mem_start
                end = end or mem_end
            dimensions = [
                d
                for d in self._coerce_dimension_list(intent.get("dimensions"))
                if d in selected.allowed_dimensions
            ]
            return ResolvedIntent(
                route="metric",
                is_metric_query=True,
                metric=selected,
                dimensions=dimensions,
                start=start,
                end=end,
                time_expr=time_expr,
                clear_pending=True,
            )
        if route == "explore":
            return ResolvedIntent(route="explore")

        # If LLM returned a free-form dimension lecture, replace with governed catalog.
        dim_followup = self._resolve_dimension_question(question, memory)
        if dim_followup is not None:
            return dim_followup

        return ResolvedIntent(
            route="direct",
            direct_answer=intent.get("direct_answer") or "请换个方式提问。",
        )

    def _resolve_followup(self, question: str, memory: AgentMemory) -> ResolvedIntent | None:
        text = question.strip()
        if not text:
            return None

        dim = self._resolve_dimension_question(text, memory)
        if dim is not None:
            return dim

        if not _AFFIRM_RE.match(text):
            return None

        entities = memory.working.entities
        pending = entities.get("pending_action") or {}
        metric = self._metric_from_memory(memory)
        if metric is None and pending.get("metric"):
            metric = next(
                (m for m in self.published_metrics() if m.name == pending.get("metric")),
                None,
            )
        if metric is None:
            return None

        preferred = list(pending.get("suggested_dimensions") or [])
        dimensions = [d for d in preferred if d in metric.allowed_dimensions][:1]
        if not dimensions and metric.allowed_dimensions:
            dimensions = [metric.allowed_dimensions[0]]

        start = pending.get("start") or None
        end = pending.get("end") or None
        if not start or not end:
            mem_start, mem_end = self._time_from_memory(memory)
            start = start or mem_start
            end = end or mem_end

        return ResolvedIntent(
            route="metric",
            is_metric_query=True,
            metric=metric,
            dimensions=dimensions,
            start=start,
            end=end,
            clear_pending=True,
        )

    def _resolve_dimension_question(
        self, question: str, memory: AgentMemory
    ) -> ResolvedIntent | None:
        if not _DIM_ASK_RE.search(question):
            return None
        metric = self._metric_from_question(question) or self._metric_from_memory(memory)
        if metric is None:
            return None
        start, end = self._time_from_memory(memory)
        suggested = list(metric.allowed_dimensions[:2])
        answer = self._format_metric_dimensions(metric)
        return ResolvedIntent(
            route="direct",
            direct_answer=answer,
            metric=metric,
            start=start,
            end=end,
            pending_action={
                "type": "metric_breakdown",
                "metric": metric.name,
                "suggested_dimensions": suggested,
                "start": start or "",
                "end": end or "",
            },
        )

    def _metric_from_memory(self, memory: AgentMemory) -> MetricDefinition | None:
        name = (memory.working.entities.get("metric") or {}).get("name")
        if not name:
            return None
        return next((m for m in self.published_metrics() if m.name == name), None)

    def _metric_from_question(self, question: str) -> MetricDefinition | None:
        lowered = question.lower()
        scored: list[tuple[int, MetricDefinition]] = []
        for metric in self.published_metrics():
            aliases = [metric.name, metric.label]
            score = sum(1 for alias in aliases if alias and alias.lower() in lowered)
            if score:
                scored.append((score, metric))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return scored[0][1]

    @staticmethod
    def _time_from_memory(memory: AgentMemory) -> tuple[str | None, str | None]:
        window = memory.working.entities.get("time_window") or {}
        start = window.get("start") or None
        end = window.get("end") or None
        return start, end

    @staticmethod
    def _coerce_metric_name(value: object) -> str | None:
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("name") or value.get("metric")
        if isinstance(value, str):
            name = value.strip()
            return name or None
        return None

    @staticmethod
    def _coerce_dimension_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        names: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("dimension")
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names

    @staticmethod
    def _format_metric_dimensions(metric: MetricDefinition) -> str:
        dims = metric.allowed_dimensions or []
        lines = [
            f"**{metric.label}**（`{metric.name}`）当前受治理口径只允许以下维度拆解：",
            "",
        ]
        if not dims:
            lines.append("- （未配置 allowed_dimensions）")
        else:
            for dim in dims:
                lines.append(f"- `{dim}`")
        lines.extend(
            [
                "",
                f"模型：`{metric.model}`",
                f"过滤：{'; '.join(metric.filters) if metric.filters else '无'}",
                "",
                "以上维度来自指标 YAML，不会使用未授权字段。",
            ]
        )
        if dims:
            hint = "、".join(f"`{d}`" for d in dims[:2])
            lines.append(f"回复「好」可按 {hint} 继续查数。")
        return "\n".join(lines)

    async def analyze(
        self,
        *,
        question: str,
        user_id: str,
        conversation_id: str,
        context: RetrievedContext | None = None,
        intent: ResolvedIntent | None = None,
        schema_evidence: dict | None = None,
        memory: AgentMemory | None = None,
    ) -> AnalysisResult:
        """ReAct-style: LLM decides → execute → summarize. No hardcoded SQL."""
        memory = memory or self.memory_manager.load(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        context = context or await self.retrieve_context(question=question, user_id=user_id)
        if self.repository is not None:
            self.repository.append_conversation_event(
                user_id, conversation_id, {"type": "user", "content": question},
            )

        resolved = intent or await self.understand(question, context, memory=memory)
        trace_id = str(uuid4())
        route = resolved.route or ("metric" if resolved.is_metric_query else "direct")

        if route == "explore" or (not resolved.is_metric_query and route != "direct"):
            explore = ExploreLoop(
                chat_provider=self.chat_provider,
                query_gateway=self.query_gateway,
                repository=self.repository,
            )
            explore_result = await explore.run(
                question=question,
                user_id=user_id,
                conversation_id=conversation_id,
                memory=memory,
            )
            result_payload = explore_result_to_analysis(
                explore_result,
                trace_id=trace_id,
                knowledge_refs=context.knowledge_refs if context else [],
            )
            self.memory_manager.commit_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                question=question,
                answer=result_payload.answer,
                intent=ResolvedIntent(route="explore"),
                columns=result_payload.columns,
                rows=result_payload.result_preview,
                sql=result_payload.sql.statement,
                trace_id=trace_id,
                result=result_payload.model_dump(),
            )
            return result_payload

        if route == "direct" or not resolved.is_metric_query:
            answer = resolved.direct_answer or "请换个方式提问。"
            result_payload = AnalysisResult(
                answer=answer,
                columns=[],
                result_preview=[],
                sql=SQLResult(statement="-- direct answer", query_id=trace_id),
                evidence=Evidence(
                    metrics=[],
                    schema_refs=[],
                    knowledge_refs=context.knowledge_refs if context else [],
                    filters=[],
                    time_window={
                        "start": resolved.start or "",
                        "end": resolved.end or "",
                    },
                    row_count=0,
                    elapsed_ms=0,
                    schema_snapshot="olist-v1",
                ),
                trace_id=trace_id,
            )
            self.memory_manager.commit_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                question=question,
                answer=result_payload.answer,
                intent=resolved,
                columns=[],
                rows=[],
                sql="-- direct answer",
                trace_id=trace_id,
                time_window={
                    "start": resolved.start or "",
                    "end": resolved.end or "",
                },
                pending_action=resolved.pending_action,
                clear_pending=resolved.clear_pending,
                result=result_payload.model_dump(),
            )
            return result_payload

        # Metric query — governed compilation
        metric = resolved.metric
        assert metric is not None
        dimensions = resolved.dimensions

        time_expr = resolved.time_expr or detect_relative_time_expr(question)
        time_convention = resolved.time_convention
        start = resolved.start
        end = resolved.end

        if time_expr:
            try:
                bound = resolve_relative_time_range(time_expr)
                start, end = bound.start, bound.end
                time_convention = bound.convention
                resolved.time_expr = time_expr
                resolved.time_convention = time_convention
            except ValueError:
                time_expr = None

        # Inherit time window from memory when the follow-up omits dates
        if (not start or not end) and memory is not None:
            mem_start, mem_end = self._time_from_memory(memory)
            start = start or mem_start
            end = end or mem_end

        # If dates not specified, query the actual data range from the metric's model
        if not start or not end:
            try:
                date_sql = (
                    f"SELECT min({metric.time_dimension}), max({metric.time_dimension}) "
                    f"FROM {metric.model}"
                )
                validated_date = validate_read_only_sql(date_sql, {metric.model})
                date_result = await self.query_gateway.execute(validated_date.sql, {})
                date_rows = date_result.get("rows", [])
                if date_rows and len(date_rows[0]) >= 2:
                    start = start or (str(date_rows[0][0])[:10] if date_rows[0][0] else None)
                    end = end or (str(date_rows[0][1])[:10] if date_rows[0][1] else None)
            except Exception:
                pass

        # Absolute incomplete dates: LLM repair. Relative must already be tool-resolved.
        if not (is_query_datetime(start) and is_query_datetime(end)):
            start, end = await resolve_time_window(
                start,
                end,
                question=question,
                chat_provider=self.chat_provider,
            )

        if not (is_query_datetime(start) and is_query_datetime(end)):
            raise ValueError(
                f"时间参数未通过 DateTime 契约: start={start!r}, end={end!r}"
            )

        # Compile and execute governed metric SQL
        schema_evidence = schema_evidence or self.lookup_schema(metric)
        sql = compile_metric_query(
            metric, dimensions=dimensions, start=start or "", end=end or "", dialect="clickhouse"
        )
        validated = validate_read_only_sql(sql, {metric.model}, max_rows=1000)
        parameters = {"start": start or "", "end": end or ""}
        await self.query_gateway.explain(validated.sql, parameters)
        result = await self.query_gateway.execute(validated.sql, parameters)
        rows = json_safe_rows(result.get("rows", []))
        columns = result.get("columns", [])

        # LLM summarizes
        summary = await self._summarize(question, metric, dimensions, start, end, columns, rows)

        schema_refs = [metric.model, *dimensions]
        schema_snapshot = "olist-v1"
        knowledge_refs: list[str] = []
        if context is not None:
            schema_refs = [*schema_refs, *context.schema_refs]
            knowledge_refs = context.knowledge_refs
            if context.schema is not None:
                schema_snapshot = context.schema.snapshot_hash
        if schema_evidence:
            schema_refs = [*schema_refs, *[
                f"{schema_evidence['model']}.{col['name']}"
                for col in schema_evidence.get("columns", []) if col.get("name")
            ]]
            if schema_evidence.get("snapshot"):
                schema_snapshot = schema_evidence["snapshot"]

        chart_hint = await plan_chart(
            question=question,
            route="metric",
            columns=columns,
            rows=rows,
            sql=validated.sql,
            dimensions=dimensions,
            metric_name=metric.name,
            chat_provider=self.chat_provider,
        )

        result_payload = AnalysisResult(
            answer=summary,
            columns=columns,
            result_preview=rows[:100],
            sql=SQLResult(statement=validated.sql, query_id=result.get("query_id", trace_id)),
            evidence=Evidence(
                metrics=[MetricEvidence(
                    name=metric.name, version=metric.version,
                    label=metric.label, owner=metric.owner,
                )],
                schema_refs=schema_refs,
                knowledge_refs=knowledge_refs,
                filters=metric.filters,
                time_window={"start": start or "", "end": end or ""},
                row_count=len(rows),
                elapsed_ms=result.get("elapsed_ms", 0),
                schema_snapshot=schema_snapshot,
            ),
            trace_id=trace_id,
            chart_hint=chart_hint,
        )
        if self.repository is not None:
            self.repository.write_query_audit(
                user_id=user_id, conversation_id=conversation_id,
                trace_id=trace_id, normalized_sql=validated.sql,
                sql_hash_value=sql_hash(validated.sql),
                evidence=result_payload.evidence.model_dump(),
            )
        # Keep resolved dates on intent so memory stores them
        resolved.start = start
        resolved.end = end
        self.memory_manager.commit_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            question=question,
            answer=result_payload.answer,
            intent=resolved,
            columns=columns,
            rows=rows,
            sql=validated.sql,
            trace_id=trace_id,
            time_window={"start": start or "", "end": end or ""},
            clear_pending=True,
            result=result_payload.model_dump(),
        )
        return result_payload
    async def _summarize(
        self,
        question: str,
        metric: MetricDefinition,
        dimensions: list[str],
        start: str | None,
        end: str | None,
        columns: list[str],
        rows: list[list],
    ) -> str:
        """LLM generates natural-language summary of query results."""
        if not rows:
            return f"查询返回0行。指标「{metric.label}」在指定时间范围内没有数据。"
        if self.chat_provider is None:
            top = rows[0]
            dim = f"{top[0]}的" if len(top) > 1 else ""
            return f"{dim}{metric.label}为{top[-1]:,.2f}。"

        preview = rows[:10]
        try:
            payload = await self.chat_provider.complete_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "根据用户问题和查询结果生成一句简洁中文摘要。"
                            '返回JSON: {"summary": "..."}。不要超过两句话。'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": question,
                                "metric": metric.label,
                                "dimensions": dimensions,
                                "time": f"{start} ~ {end}",
                                "columns": columns,
                                "preview": [[str(c) for c in row] for row in preview],
                                "total_rows": len(rows),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
            data = json.loads(payload)
            return data.get("summary", f"查询返回{len(rows)}行。")
        except Exception:
            top = rows[0]
            dim = f"{top[0]}的" if len(top) > 1 else ""
            return f"{dim}{metric.label}为{top[-1]:,.2f}。"

    def lookup_schema(self, metric: MetricDefinition) -> dict | None:
        if self.schema_service is None:
            return None
        return self.schema_service.describe_model(metric.model)
