"""Policy ingestion: hr_policies -> load -> normalise -> chunk -> embed -> policy_chunks.

Idempotent: a document is re-indexed only when its index_key (text + chunking parameters) or the
embedding model changes; chunks of policies that disappeared or became unreadable are removed.
This is an operator action (scripts/ingest_policies.py), not a request-time AI path, and it only
writes the AI-owned policy_chunks table.
"""

from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.embeddings import EmbeddingProvider
from app.services.ai.errors import AIEmbeddingError
from app.services.ai.rag.chunker import chunk_document, index_key
from app.services.ai.rag.loader import load_policy_documents
from app.services.ai.rag.vector_store import SQLitePolicyVectorStore, StoredChunk

logger = structlog.get_logger()


@dataclass
class IngestionReport:
    embedding_model: str
    documents: int = 0
    indexed: int = 0
    unchanged: int = 0
    chunks_written: int = 0
    removed_policies: int = 0
    failed_policy_ids: list[int] = field(default_factory=list)


async def ingest_policies(
    db: AsyncSession,
    embedder: EmbeddingProvider,
    *,
    upload_dir: str,
    chunk_size: int,
    chunk_overlap: int,
) -> IngestionReport:
    store = SQLitePolicyVectorStore(db, embedder.model_id)
    report = IngestionReport(embedding_model=embedder.model_id)

    documents, failed = await load_policy_documents(db, upload_dir)
    report.documents = len(documents)
    report.failed_policy_ids = list(failed)
    already_indexed = await store.indexed_policies()

    for document in documents:
        if already_indexed.get(document.policy_id) == index_key(document.checksum, chunk_size, chunk_overlap):
            report.unchanged += 1
            continue
        chunks = chunk_document(document, chunk_size, chunk_overlap)
        vectors = await embedder.embed([chunk.embedding_text() for chunk in chunks])
        if len(vectors) != len(chunks):
            raise AIEmbeddingError()
        report.chunks_written += await store.upsert(
            document.policy_id, [StoredChunk(chunk=chunk, embedding=vector) for chunk, vector in zip(chunks, vectors)]
        )
        report.indexed += 1

    current_ids = {document.policy_id for document in documents}
    for stale_id in sorted(await store.indexed_policy_ids() - current_ids):
        await store.delete_policy(stale_id)
        report.removed_policies += 1

    await db.commit()
    logger.info(
        "ai.rag.ingested",
        embedding_model=report.embedding_model,
        documents=report.documents,
        indexed=report.indexed,
        unchanged=report.unchanged,
        chunks_written=report.chunks_written,
        removed=report.removed_policies,
        failed=len(report.failed_policy_ids),
    )
    return report
