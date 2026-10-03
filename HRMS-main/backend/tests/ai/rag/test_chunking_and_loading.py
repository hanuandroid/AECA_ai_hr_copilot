"""Loader + deterministic, structure-aware chunking."""

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.models.hr_policy import HRPolicy
from app.services.ai.rag.chunker import chunk_document, index_key
from app.services.ai.rag.loader import load_policy_documents, normalise_text
from app.services.ai.rag.models import PolicyDocument
from tests.support.data import ADMIN, LEGACY_POLICY, POLICY_DOCS_DIR, TEST_POLICIES


def doc(text: str, policy_id: int = 1) -> PolicyDocument:
    text = normalise_text(text)
    return PolicyDocument(policy_id, "Test Policy", "TEST", "test.md", text, hashlib.sha256(text.encode()).hexdigest())


# --- Loading --------------------------------------------------------------------


async def test_loads_every_policy_with_metadata(db):
    documents, failed = await load_policy_documents(db, settings.policy_upload_dir)
    assert failed == []
    assert [d.title for d in documents] == [t for t, _, _ in TEST_POLICIES] + [LEGACY_POLICY[0]]
    leave = documents[0]
    assert (leave.category, leave.source_filename) == ("LEAVE", "seed_policy_01.md")
    assert "Sick leave: 10 days per year." in leave.text
    assert documents[-1].text == LEGACY_POLICY[2]  # content-column row


async def test_demo_markers_are_not_policy_text(db):
    documents, _ = await load_policy_documents(db, settings.policy_upload_dir)
    for document in documents:
        assert "DEMO / SEED DATA" not in document.text
        assert "<!--" not in document.text
    assert "DEMO / SEED DATA" in (POLICY_DOCS_DIR / "leave_policy.md").read_text(encoding="utf-8")


async def test_files_outside_upload_dir_and_missing_files_are_skipped(db, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("# Secret\n\nShould never be indexed.", encoding="utf-8")
    db.add(HRPolicy(title="Traversal", category="X", file_path=str(outside), uploaded_by=ADMIN.id))
    db.add(HRPolicy(title="Missing", category="X", file_path=str(Path(settings.policy_upload_dir) / "nope.md"), uploaded_by=ADMIN.id))
    await db.commit()
    documents, failed = await load_policy_documents(db, settings.policy_upload_dir)
    assert "Traversal" not in {d.title for d in documents}
    assert len(failed) == 2


async def test_pdf_policies_are_loaded(db):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(50, 800, "Remote Stipend Policy: employees receive a monthly internet stipend.")
    pdf.save()
    path = Path(settings.policy_upload_dir) / "stipend.pdf"
    path.write_bytes(buffer.getvalue())
    db.add(HRPolicy(title="Remote Stipend", category="BENEFITS", original_filename="stipend.pdf", file_path=str(path), uploaded_by=ADMIN.id))
    await db.commit()
    documents, _ = await load_policy_documents(db, settings.policy_upload_dir)
    stipend = next(d for d in documents if d.title == "Remote Stipend")
    assert "monthly internet stipend" in stipend.text


# --- Chunking -------------------------------------------------------------------

SAMPLE = """# Test Policy

Intro paragraph.

## Section A

Rule A1 is short.

Rule A2 is also short.

## Section B

### Detail

Rule B1.
"""


def test_chunks_follow_headings_and_keep_metadata():
    chunks = chunk_document(doc(SAMPLE), chunk_size=800, overlap=0)
    assert [(c.section, c.text) for c in chunks] == [
        ("Overview", "Intro paragraph."),
        ("Section A", "Rule A1 is short.\n\nRule A2 is also short."),
        ("Section B > Detail", "Rule B1."),
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert all((c.policy_id, c.policy_title, c.source_filename) == (1, "Test Policy", "test.md") for c in chunks)


def test_chunking_is_deterministic_with_stable_ids():
    first = chunk_document(doc(SAMPLE), 800, 0)
    second = chunk_document(doc(SAMPLE), 800, 0)
    assert first == second
    assert len({c.chunk_id for c in first}) == len(first)
    changed = chunk_document(doc(SAMPLE.replace("Rule B1.", "Rule B1 changed.")), 800, 0)
    assert [c.chunk_id for c in changed[:2]] == [c.chunk_id for c in first[:2]]
    assert changed[2].chunk_id != first[2].chunk_id


def test_index_key_changes_with_chunking_parameters():
    document = doc(SAMPLE)
    assert chunk_document(document, 800, 0)[0].index_key == index_key(document.checksum, 800, 0)
    assert index_key(document.checksum, 800, 0) != index_key(document.checksum, 400, 0)


def test_large_sections_split_on_paragraph_boundaries_with_overlap():
    paragraphs = [f"Rule {i}: " + "word " * 20 for i in range(10)]
    text = "# P\n\n## Big\n\n" + "\n\n".join(p.strip() for p in paragraphs)
    chunks = chunk_document(doc(text), chunk_size=300, overlap=150)
    assert len(chunks) > 1
    assert all(len(c.text) <= 300 for c in chunks)
    for chunk in chunks:  # no rule is cut in half
        for part in chunk.text.split("\n\n"):
            assert part in [p.strip() for p in paragraphs]
    assert chunks[1].text.split("\n\n")[0] == chunks[0].text.split("\n\n")[-1]  # overlap carries last rule


def test_oversized_paragraph_splits_on_sentences():
    sentence = "Employees must follow this long rule without exception. "
    chunks = chunk_document(doc("## S\n\n" + sentence * 12), chunk_size=200, overlap=0)
    assert all(len(c.text) <= 200 for c in chunks)
    assert all(c.text.endswith("exception.") for c in chunks)


@pytest.mark.parametrize("size,overlap", [(0, 0), (100, 100), (100, -1)])
def test_invalid_chunk_parameters_rejected(size, overlap):
    with pytest.raises(ValueError):
        chunk_document(doc(SAMPLE), size, overlap)


def test_demo_corpus_chunks_fit_configured_size():
    for _, _, filename in TEST_POLICIES:
        text = (POLICY_DOCS_DIR / filename).read_text(encoding="utf-8")
        chunks = chunk_document(doc(text), settings.ai_policy_chunk_size, settings.ai_policy_chunk_overlap)
        assert chunks and all(len(c.text) <= settings.ai_policy_chunk_size for c in chunks)
