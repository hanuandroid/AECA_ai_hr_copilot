"""(Re)build the Policy RAG index from the hr_policies library.

Usage (from backend/):  python scripts/ingest_policies.py
Docker:                 docker-compose exec api python scripts/ingest_policies.py

Run after seeding, after uploading/changing policies, or after changing AI_EMBEDDING_MODEL /
AI_POLICY_CHUNK_SIZE / AI_POLICY_CHUNK_OVERLAP. Safe to run repeatedly (unchanged documents are skipped).
With AI_EMBEDDING_PROVIDER=fastembed the first run downloads the embedding model into
AI_EMBEDDING_CACHE_DIR.
"""

import asyncio

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.ai.providers import build_embedding_provider
from app.services.ai.rag.ingest import ingest_policies


async def main() -> None:
    configure_logging()
    embedder = build_embedding_provider(settings)
    async with SessionLocal() as db:
        report = await ingest_policies(
            db,
            embedder,
            upload_dir=settings.policy_upload_dir,
            chunk_size=settings.ai_policy_chunk_size,
            chunk_overlap=settings.ai_policy_chunk_overlap,
        )
    print(
        f"Policy index ({report.embedding_model}): {report.documents} documents, "
        f"{report.indexed} re-indexed, {report.unchanged} unchanged, {report.chunks_written} chunks written, "
        f"{report.removed_policies} stale removed, unreadable policy ids: {report.failed_policy_ids or 'none'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
