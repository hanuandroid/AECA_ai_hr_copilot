from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.employee import Employee
from app.models.hr_policy import HRPolicy
from app.services.ai.authorization import AIUserContext, build_ai_user_context
from app.services.ai.embeddings import FakeEmbeddingProvider
from app.services.ai.llm.fake import FakeLLMProvider
from app.services.ai.rag.ingest import ingest_policies
from app.services.ai.rag.retriever import PolicyRetriever
from app.services.ai.rag.service import PolicyRagService, get_policy_rag_service
from app.services.ai.rag.vector_store import SQLitePolicyVectorStore
from tests.support.data import ADMIN, USERS

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
TOP_K = 4
MIN_SCORE = 0.35
EMPLOYEE_AUDIENCES = frozenset({"ALL"})  # RBAC step 7: what an EMPLOYEE may retrieve


class CountingFactory:
    """Wraps a provider; records how often the service asked for it (lazy-after-auth checks)."""

    def __init__(self, provider):
        self.provider = provider
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.provider


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
async def ingested(db, embedder):
    return await ingest_policies(
        db, embedder, upload_dir=settings.policy_upload_dir, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )


@pytest.fixture
def ctx_for(db) -> Callable:
    async def _ctx_for(user_key: str) -> AIUserContext:
        employee = (await db.execute(select(Employee).where(Employee.id == USERS[user_key].id))).scalar_one()
        return await build_ai_user_context(employee, db)

    return _ctx_for


@pytest.fixture
def make_service(db, embedder) -> Callable:
    def _make(llm, *, embedder_override=None, store_factory=None) -> tuple[PolicyRagService, CountingFactory, CountingFactory]:
        llm_factory = CountingFactory(llm)
        embedder_factory = CountingFactory(embedder_override or embedder)
        service = PolicyRagService(
            store_factory=store_factory or (lambda model_id: SQLitePolicyVectorStore(db, model_id)),
            embedder_factory=embedder_factory,
            llm_factory=llm_factory,
            top_k=TOP_K,
            min_score=MIN_SCORE,
        )
        return service, llm_factory, embedder_factory

    return _make


@pytest.fixture
def retriever(db, embedder) -> PolicyRetriever:
    return PolicyRetriever(SQLitePolicyVectorStore(db, embedder.model_id), embedder, top_k=TOP_K, min_score=MIN_SCORE)


@pytest.fixture
def override_rag(embedder):
    """Route POST /chat/policy through fake providers (real auth, DB and retrieval)."""
    installed = {}

    def _install(llm) -> CountingFactory:
        llm_factory = CountingFactory(llm)
        embedder_factory = CountingFactory(embedder)

        def _dependency(db: AsyncSession = Depends(get_db)) -> PolicyRagService:
            return PolicyRagService(
                store_factory=lambda model_id: SQLitePolicyVectorStore(db, model_id),
                embedder_factory=embedder_factory,
                llm_factory=llm_factory,
                top_k=TOP_K,
                min_score=MIN_SCORE,
            )

        app.dependency_overrides[get_policy_rag_service] = _dependency
        installed["embedder"] = embedder_factory
        return llm_factory

    yield _install
    app.dependency_overrides.pop(get_policy_rag_service, None)


def answering_llm(answer: str, citations: list[str] | None = None, grounded: bool = True) -> FakeLLMProvider:
    return FakeLLMProvider().add_structured_rule(
        lambda _prompt: True, {"answer": answer, "grounded": grounded, "citations": citations or ["S1"]}
    )


async def add_policy_file(db, title: str, category: str, filename: str, body: str) -> HRPolicy:
    upload_dir = Path(settings.policy_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / filename).write_text(body, encoding="utf-8")
    policy = HRPolicy(
        title=title,
        category=category,
        original_filename=filename,
        file_path=str(upload_dir / filename),
        mime_type="text/markdown",
        size_bytes=len(body),
        uploaded_by=ADMIN.id,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy
