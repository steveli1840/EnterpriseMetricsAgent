import asyncio
import hashlib
from pathlib import Path

import yaml

from app.celery_app import celery_app
from app.infrastructure.providers import DashScopeEmbeddingProvider
from app.settings import get_settings


@celery_app.task(name="knowledge.embed_documents", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def embed_documents(chunks: list[str]) -> list[dict]:
    provider = DashScopeEmbeddingProvider(get_settings())
    vectors = asyncio.run(provider.embed(chunks))
    return [
        {"content_hash": hashlib.sha256(text.encode()).hexdigest(), "embedding": vector}
        for text, vector in zip(chunks, vectors, strict=True)
    ]


@celery_app.task(name="metrics.validate_yaml")
def validate_metric_yaml(path: str) -> dict:
    from app.domain.metrics import MetricDefinition

    payload = yaml.safe_load(Path(path).read_text())
    metric = MetricDefinition.model_validate(payload)
    return metric.model_dump()

