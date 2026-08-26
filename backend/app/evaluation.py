from pathlib import Path

import yaml
from pydantic import BaseModel

from app.agent.service import AmbiguousMetricError, AgentService
from app.domain.metrics import compile_metric_query
from app.domain.sql_guard import SQLPolicyError, validate_read_only_sql


class EvaluationCase(BaseModel):
    id: str
    question: str
    expected_metric: str
    expected_dimensions: list[str]
    should_refuse: bool = False


class EvaluationCaseResult(BaseModel):
    id: str
    question: str
    expected_metric: str
    actual_metric: str | None = None
    expected_dimensions: list[str]
    actual_dimensions: list[str] = []
    should_refuse: bool
    refused: bool
    passed: bool
    error: str | None = None


class EvaluationRunResult(BaseModel):
    suite: str
    cases: int
    passed: int
    failed: int
    pass_rate: float
    metric_accuracy: float
    dimension_accuracy: float
    refusal_accuracy: float
    items: list[EvaluationCaseResult]


def load_cases(path: Path) -> list[EvaluationCase]:
    payload = yaml.safe_load(path.read_text())
    cases: list[EvaluationCase] = []
    for scenario in payload["scenarios"]:
        for index, question in enumerate(scenario["questions"], start=1):
            cases.append(
                EvaluationCase(
                    id=f"{scenario['id']}-{index:02d}",
                    question=question,
                    expected_metric=scenario["expected_metric"],
                    expected_dimensions=scenario.get("expected_dimensions", []),
                    should_refuse=scenario.get("should_refuse", False),
                )
            )
    return cases


def load_suite_version(path: Path) -> str:
    payload = yaml.safe_load(path.read_text())
    return str(payload["version"])


async def run_evaluation_suite(
    *,
    service: AgentService,
    path: Path,
) -> EvaluationRunResult:
    cases = load_cases(path)
    items = [await _evaluate_case(service, case) for case in cases]
    passed = sum(item.passed for item in items)
    metric_cases = [item for item in items if not item.should_refuse]
    refusal_cases = [item for item in items if item.should_refuse]
    metric_hits = sum(item.actual_metric == item.expected_metric for item in metric_cases)
    dimension_hits = sum(
        sorted(item.actual_dimensions) == sorted(item.expected_dimensions)
        for item in metric_cases
    )
    refusal_hits = sum(item.refused for item in refusal_cases)
    return EvaluationRunResult(
        suite=load_suite_version(path),
        cases=len(items),
        passed=passed,
        failed=len(items) - passed,
        pass_rate=round(passed / len(items), 4) if items else 0,
        metric_accuracy=round(metric_hits / len(metric_cases), 4) if metric_cases else 0,
        dimension_accuracy=round(dimension_hits / len(metric_cases), 4) if metric_cases else 0,
        refusal_accuracy=round(refusal_hits / len(refusal_cases), 4) if refusal_cases else 0,
        items=items,
    )


async def _evaluate_case(service: AgentService, case: EvaluationCase) -> EvaluationCaseResult:
    try:
        if _looks_unsafe(case.question):
            raise AmbiguousMetricError("unsafe request refused")
        context = await service.retrieve_context(
            question=case.question,
            user_id="evaluation",
        )
        metric = service.resolve_metric(case.question, context)
        dimensions = service._dimensions(case.question, metric, context)
        sql = compile_metric_query(
            metric,
            dimensions=dimensions,
            start="2018-01-01",
            end="2018-02-01",
            dialect="clickhouse",
        )
        validate_read_only_sql(sql, {metric.model}, max_rows=1000)
        refused = False
        error = None
    except (AmbiguousMetricError, SQLPolicyError, ValueError) as exc:
        metric = None
        dimensions = []
        refused = True
        error = str(exc)

    passed = (
        refused
        if case.should_refuse
        else (
            not refused
            and metric is not None
            and metric.name == case.expected_metric
            and sorted(dimensions) == sorted(case.expected_dimensions)
        )
    )
    return EvaluationCaseResult(
        id=case.id,
        question=case.question,
        expected_metric=case.expected_metric,
        actual_metric=metric.name if metric is not None else None,
        expected_dimensions=case.expected_dimensions,
        actual_dimensions=dimensions,
        should_refuse=case.should_refuse,
        refused=refused,
        passed=passed,
        error=error,
    )


def _looks_unsafe(question: str) -> bool:
    normalized = question.lower()
    unsafe_terms = (
        "drop",
        "delete",
        "insert",
        "update",
        "grant",
        "删除",
        "更新",
        "插入",
        "授予",
        "利润",
        "赚钱",
        "profit",
        "ebitda",
    )
    return any(term in normalized for term in unsafe_terms)
