from pathlib import Path

import pytest

from app.agent.service import AgentService
from app.catalog import DEFAULT_METRICS
from app.evaluation import load_cases, run_evaluation_suite


class Gateway:
    async def explain(self, sql: str, parameters: dict):
        return {"status": "ok"}

    async def execute(self, sql: str, parameters: dict):
        return {"query_id": "query-1", "columns": [], "rows": [], "elapsed_ms": 0}


def test_olist_core_evaluation_has_at_least_sixty_cases():
    cases = load_cases(Path("evaluations/olist_core_v1.yaml"))
    assert len(cases) >= 60
    assert all(case.expected_metric for case in cases)
    assert any(case.should_refuse for case in cases)


@pytest.mark.asyncio
async def test_evaluation_runner_returns_quality_metrics():
    service = AgentService(metrics=DEFAULT_METRICS, query_gateway=Gateway())

    result = await run_evaluation_suite(
        service=service,
        path=Path("evaluations/olist_core_v1.yaml"),
    )

    assert result.suite == "olist-core-v1"
    assert result.cases >= 60
    assert result.passed + result.failed == result.cases
    assert result.refusal_accuracy == 1.0
    assert result.items[0].expected_metric == "delivered_revenue"
