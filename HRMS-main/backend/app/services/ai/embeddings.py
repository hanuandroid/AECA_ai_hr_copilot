"""Embedding providers. The only module that imports an embedding SDK (lazily)."""

import asyncio
import hashlib
import math
import re
from typing import Any, Protocol, runtime_checkable

import structlog

from app.services.ai.errors import AIEmbeddingError, AIUnavailableError

logger = structlog.get_logger()
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Provider-neutral embedding interface. Vectors are L2-normalised.

    `model_id` identifies the vector space; vectors from different model_ids are never compared.
    """

    name: str
    model_id: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed documents/passages."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query (some models use a query-specific instruction)."""
        ...


class FakeEmbeddingProvider:
    """Deterministic, offline hash-based embeddings (feature hashing over word tokens).

    Texts sharing words get higher cosine similarity, which is enough to exercise
    retrieval logic in tests without downloading a model.
    """

    name = "fake"

    def __init__(self, dimension: int = 256):
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        self.model_id = f"fake-hash-{dimension}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return _normalise(vector)


class FastEmbedEmbeddingProvider:
    """Local ONNX embeddings via fastembed (decision D2): no API key, model set by AI_EMBEDDING_MODEL.

    The model is loaded lazily on first use (first call downloads it into `cache_dir`), and all
    CPU work runs in a worker thread so the event loop is never blocked. `model` can be injected
    for tests; production code never passes it.
    """

    name = "fastembed"

    def __init__(self, model_name: str, cache_dir: str | None = None, model: Any | None = None):
        self.model_id = model_name
        self._cache_dir = cache_dir
        self._model = model
        self._lock = asyncio.Lock()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._get_model()
        try:
            vectors = await asyncio.to_thread(lambda: [list(map(float, v)) for v in model.passage_embed(texts)])
        except Exception as exc:  # provider internals must never reach the client
            logger.error("ai.embedding.failed", provider=self.name, error_type=type(exc).__name__)
            raise AIEmbeddingError() from exc
        return [_normalise(vector) for vector in vectors]

    async def embed_query(self, text: str) -> list[float]:
        model = await self._get_model()
        try:
            vectors = await asyncio.to_thread(lambda: [list(map(float, v)) for v in model.query_embed(text)])
        except Exception as exc:
            logger.error("ai.embedding.failed", provider=self.name, error_type=type(exc).__name__)
            raise AIEmbeddingError() from exc
        if len(vectors) != 1:
            raise AIEmbeddingError()
        return _normalise(vectors[0])

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
        return self._model

    def _load(self) -> Any:
        try:
            from fastembed import TextEmbedding  # lazy: heavy native dependency (onnxruntime)

            return TextEmbedding(model_name=self.model_id, cache_dir=self._cache_dir)
        except Exception as exc:
            logger.error("ai.embedding.load_failed", provider=self.name, model=self.model_id, error_type=type(exc).__name__)
            raise AIUnavailableError() from exc


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)
