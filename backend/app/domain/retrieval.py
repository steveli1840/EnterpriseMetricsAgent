from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.metrics import MetricDefinition
from app.infrastructure.control_plane import query_terms


@dataclass(frozen=True)
class RetrievedKnowledge:
    source_type: str
    source_ref: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedMemory:
    kind: str
    value: dict
    score: float


@dataclass(frozen=True)
class RetrievedSchema:
    snapshot_hash: str
    source: str
    payload: dict


@dataclass(frozen=True)
class RetrievedContext:
    question: str
    metrics: list[MetricDefinition]
    schema: RetrievedSchema | None
    knowledge: list[RetrievedKnowledge]
    memories: list[RetrievedMemory]

    @property
    def knowledge_refs(self) -> list[str]:
        return [item.source_ref for item in self.knowledge]

    @property
    def schema_refs(self) -> list[str]:
        if self.schema is None:
            return []
        return [self.schema.snapshot_hash]


def reciprocal_rank_fusion(
    *, keyword_ids: list[str], vector_ids: list[str], rank_constant: int = 60
) -> list[tuple[str, float]]:
    """Fuse ordered retrieval results without coupling to a vector database."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in (keyword_ids, vector_ids):
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1.0 / (rank_constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class HybridRetriever:
    def __init__(self, repository, embedding_provider=None):
        self.repository = repository
        self.embedding_provider = embedding_provider

    async def retrieve(
        self,
        *,
        question: str,
        user_id: str,
        metrics: list[MetricDefinition],
        limit: int = 8,
    ) -> RetrievedContext:
        schema = self.repository.latest_schema_snapshot()
        knowledge_hits = self.repository.search_knowledge_keyword(question, limit=limit)
        vector_hits = await self._search_vector(question=question, limit=limit)
        fused_knowledge = self._fuse_knowledge_hits(
            keyword_hits=knowledge_hits,
            vector_hits=vector_hits,
            limit=limit,
        )
        memories = self.repository.list_user_memories(user_id, confirmed_only=True)

        return RetrievedContext(
            question=question,
            metrics=self._rank_metrics(question, metrics)[:limit],
            schema=(
                RetrievedSchema(
                    snapshot_hash=schema.snapshot_hash,
                    source=schema.source,
                    payload=schema.payload,
                )
                if schema is not None
                else None
            ),
            knowledge=[
                RetrievedKnowledge(
                    source_type=chunk.source_type,
                    source_ref=chunk.source_ref,
                    content=chunk.content,
                    score=self._text_score(question, f"{chunk.source_ref}\n{chunk.content}"),
                    metadata=chunk.metadata_json or {},
                )
                for chunk in fused_knowledge
            ],
            memories=[
                RetrievedMemory(
                    kind=memory.kind,
                    value=memory.value,
                    score=self._text_score(question, " ".join(map(str, memory.value.values()))),
                )
                for memory in memories
            ],
        )

    async def _search_vector(self, *, question: str, limit: int):
        if self.embedding_provider is None:
            return []
        try:
            vectors = await self.embedding_provider.embed([question])
        except Exception:
            return []
        if not vectors:
            return []
        return self.repository.search_knowledge_vector(vectors[0], limit=limit)

    def _fuse_knowledge_hits(self, *, keyword_hits: list, vector_hits: list, limit: int) -> list:
        if not vector_hits:
            return keyword_hits[:limit]
        by_id = {self._chunk_id(chunk): chunk for chunk in [*keyword_hits, *vector_hits]}
        fused = reciprocal_rank_fusion(
            keyword_ids=[self._chunk_id(chunk) for chunk in keyword_hits],
            vector_ids=[self._chunk_id(chunk) for chunk in vector_hits],
        )
        return [by_id[item_id] for item_id, _score in fused[:limit] if item_id in by_id]

    @staticmethod
    def _chunk_id(chunk) -> str:
        return str(getattr(chunk, "id", None) or getattr(chunk, "source_ref"))

    def _rank_metrics(
        self,
        question: str,
        metrics: list[MetricDefinition],
    ) -> list[MetricDefinition]:
        scored = [
            (self._metric_score(question, metric), metric)
            for metric in metrics
            if metric.status == "published"
        ]
        positives = [(score, metric) for score, metric in scored if score > 0]
        if not positives:
            return [metric for _score, metric in sorted(scored, key=lambda item: item[1].name)]
        positives.sort(key=lambda item: (-item[0], item[1].name))
        return [metric for _score, metric in positives]

    def _metric_score(self, question: str, metric: MetricDefinition) -> float:
        haystack = " ".join(
            [
                metric.name,
                metric.label,
                metric.description,
                metric.owner,
                " ".join(metric.allowed_dimensions),
            ]
        )
        return self._text_score(question, haystack)

    @staticmethod
    def _text_score(query: str, text: str) -> float:
        normalized_query = query.lower()
        normalized_text = text.lower()
        terms = query_terms(normalized_query.replace("？", " "))
        score = 0.0
        for term in terms:
            if term in normalized_text:
                score += 1.0
        if normalized_query and normalized_query in normalized_text:
            score += 2.0
        return score
