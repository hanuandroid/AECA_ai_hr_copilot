"""Vector storage boundary for policy chunks.

Decision: no vector database exists in this repository (SQLite only, no pgvector). The corpus is
small (tens to low hundreds of chunks), so vectors are stored as JSON in the AI-owned
`policy_chunks` table and cosine similarity is computed in Python. Everything else talks to the
`PolicyVectorStore` protocol, so Chroma/FAISS/pgvector can replace this class without touching
retrieval or the RAG service. A store is bound to one embedding model id; vectors from different
models are never compared.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PolicyAudience, PolicyStatus
from app.models.hr_policy import HRPolicy
from app.models.policy_chunk import PolicyChunk
from app.services.ai.embeddings import cosine_similarity
from app.services.ai.rag.models import PolicyChunkData


_KNOWN_AUDIENCES = frozenset(audience.value for audience in PolicyAudience)


@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk: PolicyChunkData
    embedding: list[float]


class PolicyVectorStore(Protocol):
    embedding_model: str

    async def upsert(self, policy_id: int, chunks: list[StoredChunk]) -> int: ...

    async def similarity_search(
        self, query_embedding: list[float], top_k: int, *, audiences: frozenset[str]
    ) -> list[tuple[PolicyChunkData, float]]: ...

    async def all_chunks(self, *, audiences: frozenset[str]) -> list[StoredChunk]: ...

    async def delete_policy(self, policy_id: int) -> int: ...

    async def indexed_policies(self) -> dict[int, str]: ...

    async def indexed_policy_ids(self) -> set[int]: ...


class SQLitePolicyVectorStore:
    def __init__(self, db: AsyncSession, embedding_model: str):
        self._db = db
        self.embedding_model = embedding_model

    async def upsert(self, policy_id: int, chunks: list[StoredChunk]) -> int:
        """Replace every chunk of `policy_id` (all models) with `chunks`. Idempotent per document."""
        await self.delete_policy(policy_id)
        for stored in chunks:
            if stored.chunk.policy_id != policy_id:
                raise ValueError("chunk does not belong to policy")
            self._db.add(
                PolicyChunk(
                    chunk_id=stored.chunk.chunk_id,
                    policy_id=policy_id,
                    index_key=stored.chunk.index_key,
                    source_filename=stored.chunk.source_filename,
                    policy_title=stored.chunk.policy_title,
                    category=stored.chunk.category,
                    section=stored.chunk.section,
                    chunk_index=stored.chunk.chunk_index,
                    text=stored.chunk.text,
                    embedding=stored.embedding,
                    embedding_model=self.embedding_model,
                )
            )
        await self._db.flush()
        return len(chunks)

    async def delete_policy(self, policy_id: int) -> int:
        result = await self._db.execute(delete(PolicyChunk).where(PolicyChunk.policy_id == policy_id))
        return result.rowcount or 0

    async def all_chunks(self, *, audiences: frozenset[str]) -> list[StoredChunk]:
        """Retrievable chunks: only those of policies that are PUBLISHED right now AND whose audience is in
        `audiences` (RBAC step 7; required, no default). Status and audience are read from hr_policies at query time,
        so an archived, unpublished or reclassified policy is not served even before the next ingestion run, and
        chunks outside the caller's audiences never reach scoring (they cannot influence IDF or thresholds)."""
        allowed = sorted(audience for audience in audiences if audience in _KNOWN_AUDIENCES)
        if not allowed:
            return []
        rows = (
            await self._db.execute(
                select(PolicyChunk)
                .join(HRPolicy, HRPolicy.id == PolicyChunk.policy_id)
                .where(PolicyChunk.embedding_model == self.embedding_model,
                       HRPolicy.status == PolicyStatus.PUBLISHED.value,
                       HRPolicy.audience.in_(allowed))
                .order_by(PolicyChunk.policy_id.asc(), PolicyChunk.chunk_index.asc())
            )
        ).scalars().all()
        return [StoredChunk(chunk=_to_data(row), embedding=list(row.embedding)) for row in rows]

    async def similarity_search(
        self, query_embedding: list[float], top_k: int, *, audiences: frozenset[str]
    ) -> list[tuple[PolicyChunkData, float]]:
        scored = [
            (stored.chunk, cosine_similarity(query_embedding, stored.embedding))
            for stored in await self.all_chunks(audiences=audiences)
            if len(stored.embedding) == len(query_embedding)
        ]
        scored.sort(key=lambda item: (-item[1], item[0].policy_id, item[0].chunk_index))
        return scored[: max(top_k, 0)]

    async def indexed_policies(self) -> dict[int, str]:
        """policy_id -> index_key currently indexed for this embedding model."""
        rows = (
            await self._db.execute(
                select(PolicyChunk.policy_id, PolicyChunk.index_key)
                .where(PolicyChunk.embedding_model == self.embedding_model)
                .distinct()
            )
        ).all()
        return {policy_id: key for policy_id, key in rows}

    async def indexed_policy_ids(self) -> set[int]:
        """Every policy with chunks, for any embedding model (used to remove stale entries)."""
        return set((await self._db.execute(select(PolicyChunk.policy_id).distinct())).scalars().all())


def _to_data(row: PolicyChunk) -> PolicyChunkData:
    return PolicyChunkData(
        chunk_id=row.chunk_id,
        policy_id=row.policy_id,
        policy_title=row.policy_title,
        category=row.category,
        source_filename=row.source_filename,
        section=row.section,
        chunk_index=row.chunk_index,
        text=row.text,
        index_key=row.index_key,
    )
