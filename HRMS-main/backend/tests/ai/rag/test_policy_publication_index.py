"""Only PUBLISHED policies can reach the Policy RAG index or be retrieved (production hardening closure, 2026-10-14).

Security rule: an unpublished draft (manager submission), a rejected submission or an archived version must never be
used for Copilot answers - neither through ingestion nor through chunks already in the index."""

import uuid

from sqlalchemy import select, update

from app.core.config import settings
from app.models.hr_policy import HRPolicy
from app.models.policy_chunk import PolicyChunk
from app.services.ai.rag.ingest import ingest_policies
from tests.ai.rag.conftest import CHUNK_OVERLAP, CHUNK_SIZE
from tests.support.http import data

QUESTION = "How many days per week may I work from the moon base?"
DRAFT = b"# Moon base work\n\nEmployees may work from the moon base three days per week."


async def _ingest(db, embedder):
    return await ingest_policies(db, embedder, upload_dir=settings.policy_upload_dir, chunk_size=CHUNK_SIZE,
                                 chunk_overlap=CHUNK_OVERLAP)


async def _upload(client, auth, role: str, title: str = "Moon Base Policy", body: bytes = DRAFT) -> dict:
    files = {"file": ("moon.md", body, "text/markdown")}
    response = await client.post("/api/v1/hr-policies/upload", headers=await auth(role),
                                 data={"title": title, "category": "WORKPLACE"}, files=files)
    return data(response)


async def _indexed(db, policy_id: int) -> int:
    db.expire_all()
    return len((await db.execute(select(PolicyChunk.id).where(PolicyChunk.policy_id == policy_id))).scalars().all())


def _retrieved_ids(chunks) -> set[int]:
    return {chunk.chunk.policy_id for chunk in chunks}


async def test_submitted_draft_is_never_indexed_or_retrieved(client, auth, db, embedder, retriever):
    draft = await _upload(client, auth, "manager")
    report = await _ingest(db, embedder)
    assert await _indexed(db, draft["id"]) == 0
    assert draft["id"] not in _retrieved_ids(await retriever.retrieve(QUESTION))
    published = (await db.execute(select(HRPolicy.id).where(HRPolicy.status == "PUBLISHED"))).scalars().all()
    assert report.documents + len(report.failed_policy_ids) == len(published)


async def test_published_submission_is_indexed_after_ingestion(client, auth, db, embedder, retriever):
    draft = await _upload(client, auth, "manager")
    await client.post(f"/api/v1/hr-policies/{draft['id']}/publish", headers=await auth("admin"))
    await _ingest(db, embedder)
    assert await _indexed(db, draft["id"]) > 0
    assert draft["id"] in _retrieved_ids(await retriever.retrieve(QUESTION))


async def test_archived_version_stops_being_retrieved_before_reindex(client, auth, db, embedder, retriever):
    v1 = await _upload(client, auth, "admin")
    await _ingest(db, embedder)
    assert v1["id"] in _retrieved_ids(await retriever.retrieve(QUESTION))

    v2 = await _upload(client, auth, "admin", body=b"# Moon base work\n\nMoon base work is limited to one day per week.")
    db.expire_all()
    results = _retrieved_ids(await retriever.retrieve(QUESTION))  # no re-ingestion yet
    assert v1["id"] not in results

    await _ingest(db, embedder)
    assert await _indexed(db, v1["id"]) == 0  # stale chunks removed by ingestion
    assert v2["id"] in _retrieved_ids(await retriever.retrieve(QUESTION))


async def test_stale_chunks_of_a_draft_are_never_served(client, auth, db, embedder, retriever):
    """Defence in depth: even if a draft's chunks were in the index (e.g. restored from an old backup), retrieval
    checks the policy's current status."""
    published = await _upload(client, auth, "admin")
    await _ingest(db, embedder)
    await db.execute(update(HRPolicy).where(HRPolicy.id == published["id"]).values(status="SUBMITTED"))
    await db.commit()
    assert await _indexed(db, published["id"]) > 0
    assert published["id"] not in _retrieved_ids(await retriever.retrieve(QUESTION))


async def test_rejected_submission_is_not_indexed(client, auth, db, embedder):
    draft = await _upload(client, auth, "manager", title=f"Rejected {uuid.uuid4().hex[:6]}")
    await client.post(f"/api/v1/hr-policies/{draft['id']}/reject", headers=await auth("admin"))
    await _ingest(db, embedder)
    assert await _indexed(db, draft["id"]) == 0
