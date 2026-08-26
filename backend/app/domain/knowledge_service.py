import hashlib
from pathlib import Path

from app.domain.metrics import MetricDefinition


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class KnowledgeIndexService:
    def __init__(
        self,
        *,
        repository,
        metrics: list[MetricDefinition],
        knowledge_root: Path,
        embedding_provider=None,
        embedding_model: str = "none",
        embedding_dimensions: int = 0,
    ):
        self.repository = repository
        self.metrics = metrics
        self.knowledge_root = knowledge_root
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions

    async def reindex(self) -> dict:
        documents = self._build_documents()
        vectors = await self._embed([document["content"] for document in documents])
        chunks = [
            {
                **document,
                "content_hash": content_hash(document["content"]),
                "embedding": vector,
            }
            for document, vector in zip(documents, vectors, strict=True)
        ]
        count = self.repository.replace_knowledge_chunks(
            chunks=chunks,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
        )
        return {
            "status": "completed",
            "documents": count,
            "embedding_model": self.embedding_model,
            "dimensions": self.embedding_dimensions,
        }

    def _build_documents(self) -> list[dict]:
        schema = self.repository.latest_schema_snapshot()
        snapshot_hash = schema.snapshot_hash if schema is not None else None
        documents = [
            {
                "source_type": "metric",
                "source_ref": f"metric:{metric.name}:v{metric.version}",
                "content": (
                    f"{metric.label}\n{metric.description}\nmodel={metric.model}\n"
                    f"dimensions={','.join(metric.allowed_dimensions)}"
                ),
                "metadata": {"schema_snapshot": snapshot_hash},
            }
            for metric in self.metrics
            if metric.status == "published"
        ]
        documents.extend(self._knowledge_documents(snapshot_hash=snapshot_hash))
        return documents

    def save_uploaded_document(self, *, filename: str, content: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith("."):
            raise ValueError("invalid filename")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".md", ".txt", ".yaml", ".csv"}:
            raise ValueError("only .md/.txt/.yaml/.csv are supported")
        upload_dir = self.knowledge_root / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / safe_name
        target.write_text(content, encoding="utf-8")
        return target

    def _knowledge_documents(self, *, snapshot_hash: str | None) -> list[dict]:
        if not self.knowledge_root.exists():
            return []
        documents = []
        for path in sorted(self.knowledge_root.glob("**/*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".yaml", ".csv"}:
                continue
            documents.append(
                {
                    "source_type": "knowledge",
                    "source_ref": str(path.relative_to(self.knowledge_root.parent)),
                    "content": path.read_text(),
                    "metadata": {"schema_snapshot": snapshot_hash},
                }
            )
        return documents

    async def _embed(self, texts: list[str]) -> list[list[float] | None]:
        if self.embedding_provider is None:
            return [None for _ in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 10):
            vectors.extend(await self.embedding_provider.embed(texts[start : start + 10]))
        return vectors
