"""Ingestion idempotency + vector store boundary."""

from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.models.policy_chunk import PolicyChunk
from app.services.ai.embeddings import FakeEmbeddingProvider
from app.services.ai.rag.ingest import ingest_policies
from app.services.ai.rag.vector_store import SQLitePolicyVectorStore
from tests.ai.rag.conftest import CHUNK_OVERLAP, CHUNK_SIZE, add_policy_file
from tests.support.data import TEST_POLICIES


async def _ingest(db, embedder, chunk_size=CHUNK_SIZE):
    return await ingest_policies(db, embedder, upload_dir=settings.policy_upload_dir, chunk_size=chunk_size, chunk_overlap=CHUNK_OVERLAP)


async def _chunk_rows(db):
    return (await db.execute(select(PolicyChunk).order_by(PolicyChunk.policy_id, PolicyChunk.chunk_index))).scalars().all()


async def test_ingestion_indexes_all_policies(db, ingested):
    assert ingested.documents == len(TEST_POLICIES) + 1
    assert ingested.indexed == ingested.documents
    assert ingested.failed_policy_ids == []
    rows = await _chunk_rows(db)
    assert len(rows) == ingested.chunks_written > 0
    assert {r.embedding_model for r in rows} == {"fake-hash-256"}
    assert all(len(r.embedding) == 256 for r in rows)
    sick = next(r for r in rows if r.section == "Sick Leave")
    assert (sick.policy_title, sick.category, sick.source_filename) == ("Leave Policy", "LEAVE", "seed_policy_01.md")


async def test_second_ingestion_is_a_no_op(db, embedder, ingested):
    before = [(r.chunk_id, r.id) for r in await _chunk_rows(db)]
    report = await _ingest(db, embedder)
    assert (report.indexed, report.unchanged, report.chunks_written) == (0, ingested.documents, 0)
    assert [(r.chunk_id, r.id) for r in await _chunk_rows(db)] == before
    total = (await db.execute(select(func.count(PolicyChunk.id)))).scalar_one()
    distinct = (await db.execute(select(func.count(func.distinct(PolicyChunk.chunk_id))))).scalar_one()
    assert total == distinct  # no duplicates


async def test_changed_document_is_reindexed_alone(db, embedder, ingested):
    path = Path(settings.policy_upload_dir) / "seed_policy_03.md"  # WFH policy
    path.write_text(path.read_text(encoding="utf-8") + "\n\n## Equipment\n\nA monitor may be requested.\n", encoding="utf-8")
    report = await _ingest(db, embedder)
    assert (report.indexed, report.unchanged) == (1, ingested.documents - 1)
    assert "Equipment" in {r.section for r in await _chunk_rows(db)}


async def test_chunk_parameter_change_triggers_reindex(db, embedder, ingested):
    report = await _ingest(db, embedder, chunk_size=300)
    assert report.indexed == ingested.documents
    assert all(len(r.text) <= 300 for r in await _chunk_rows(db))


async def test_unreadable_policy_chunks_are_removed(db, embedder, ingested):
    (Path(settings.policy_upload_dir) / "seed_policy_02.md").unlink()  # Attendance policy file lost
    report = await _ingest(db, embedder)
    assert report.removed_policies == 1
    assert "Attendance Policy" not in {r.policy_title for r in await _chunk_rows(db)}


async def test_new_upload_is_indexed(db, embedder, ingested):
    await add_policy_file(db, "Parking Policy", "FACILITIES", "parking.md", "# Parking Policy\n\n## Spaces\n\nParking is first come, first served.")
    report = await _ingest(db, embedder)
    assert report.indexed == 1
    assert "Parking Policy" in {r.policy_title for r in await _chunk_rows(db)}


async def test_store_is_scoped_to_one_embedding_model(db, ingested):
    other = FakeEmbeddingProvider(dimension=64)
    assert await SQLitePolicyVectorStore(db, other.model_id).all_chunks() == []
    report = await _ingest(db, other)  # a model change re-indexes everything
    assert report.indexed == ingested.documents
    assert {r.embedding_model for r in await _chunk_rows(db)} == {other.model_id}


async def test_similarity_search_and_delete(db, embedder, ingested):
    store = SQLitePolicyVectorStore(db, embedder.model_id)
    query = await embedder.embed_query("sick leave days per year")
    first = await store.similarity_search(query, top_k=3)
    assert len(first) == 3
    assert first == await store.similarity_search(query, top_k=3)  # deterministic
    assert [score for _, score in first] == sorted((score for _, score in first), reverse=True)
    leave_id = first[0][0].policy_id
    assert await store.delete_policy(leave_id) > 0
    await db.commit()
    assert all(c.policy_id != leave_id for c, _ in await store.similarity_search(query, top_k=50))


async def test_migration_downgrade_and_upgrade(tmp_path):
    """0017 is reversible and 0001-0017 rebuild from scratch (run on a separate file DB)."""
    import os

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from tests.conftest import BACKEND_DIR

    db_file = tmp_path / "migrate.db"
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db_file.as_posix()}")
        assert "policy_chunks" in inspect(engine).get_table_names()
        command.downgrade(config, "0016_employee_documents_uploaded_by")
        assert "policy_chunks" not in inspect(engine).get_table_names()
        assert "hr_policies" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "policy_chunks" in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        os.environ["DATABASE_URL"] = previous
