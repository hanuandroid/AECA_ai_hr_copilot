"""Hybrid retrieval: relevance, exclusion, top-k, threshold, deterministic ordering."""

import pytest

from app.services.ai.rag.retriever import (
    PolicyRetriever,
    content_terms,
    inverse_document_frequency,
    lexical_overlap,
)
from app.services.ai.rag.vector_store import SQLitePolicyVectorStore
from tests.ai.rag.conftest import EMPLOYEE_AUDIENCES, TOP_K


@pytest.mark.parametrize(
    "question,title,section",
    [
        ("How many sick leaves can I take?", "Leave Policy", "Sick Leave"),
        ("Can I take a half-day leave?", "Leave Policy", "Half-Day Leave"),
        ("What happens if I am late?", "Attendance Policy", "Late Arrival"),
        ("What is the leave approval process?", "Leave Policy", "Approval Process"),
        ("What is the policy for uploading documents?", "Document Upload Policy", None),
        ("How long do I have to submit travel claims?", "Travel Reimbursement", None),
    ],
)
async def test_relevant_policy_is_retrieved_first(ingested, retriever, question, title, section):
    hits = await retriever.retrieve(question, audiences=EMPLOYEE_AUDIENCES)
    assert hits, question
    assert hits[0].chunk.policy_title == title
    if section:
        assert hits[0].chunk.section == section


async def test_wfh_question_retrieves_wfh_evidence(ingested, retriever):
    """Two policies legitimately answer this: the Attendance Policy's "Work From Home Attendance"
    section (states that WFH needs manager approval) and the WFH Policy itself (library title "WFH Policy")."""
    hits = await retriever.retrieve("Can I work from home?", audiences=EMPLOYEE_AUDIENCES)
    titles = [h.chunk.policy_title for h in hits]
    assert "WFH Policy" in titles
    assert set(titles) <= {"WFH Policy", "Attendance Policy"}
    assert any("approval" in h.chunk.text for h in hits)


@pytest.mark.parametrize(
    "question",
    ["What is the stock option vesting schedule?", "What is Rahul's salary?", "Give me PAN information.", "Tell me a joke"],
)
async def test_irrelevant_questions_retrieve_nothing(ingested, retriever, question):
    assert await retriever.retrieve(question, audiences=EMPLOYEE_AUDIENCES) == []


async def test_irrelevant_policies_excluded_from_context(ingested, retriever):
    hits = await retriever.retrieve("What happens if I am late?", audiences=EMPLOYEE_AUDIENCES)
    assert {h.chunk.policy_title for h in hits} == {"Attendance Policy"}


async def test_top_k_is_respected(db, embedder, ingested):
    retriever = PolicyRetriever(SQLitePolicyVectorStore(db, embedder.model_id), embedder, top_k=2, min_score=0.0)
    assert len(await retriever.retrieve("leave policy attendance work from home", audiences=EMPLOYEE_AUDIENCES)) == 2
    default = await PolicyRetriever(SQLitePolicyVectorStore(db, embedder.model_id), embedder, top_k=TOP_K, min_score=0.35).retrieve("leave", audiences=EMPLOYEE_AUDIENCES)
    assert len(default) <= TOP_K


async def test_min_score_threshold(db, embedder, ingested):
    strict = PolicyRetriever(SQLitePolicyVectorStore(db, embedder.model_id), embedder, top_k=4, min_score=0.99)
    assert await strict.retrieve("How many sick leaves can I take?", audiences=EMPLOYEE_AUDIENCES) == []


async def test_retrieval_is_deterministic_and_ordered(ingested, retriever):
    first = await retriever.retrieve("What are the attendance rules?", audiences=EMPLOYEE_AUDIENCES)
    second = await retriever.retrieve("What are the attendance rules?", audiences=EMPLOYEE_AUDIENCES)
    assert [(h.chunk.chunk_id, h.score) for h in first] == [(h.chunk.chunk_id, h.score) for h in second]
    keys = [(-h.score, h.chunk.policy_id, h.chunk.chunk_index) for h in first]
    assert keys == sorted(keys)


async def test_retrieved_chunks_carry_metadata_but_no_embeddings(ingested, retriever):
    hit = (await retriever.retrieve("How many sick leaves can I take?", audiences=EMPLOYEE_AUDIENCES))[0]
    assert hit.chunk.chunk_id and hit.chunk.policy_id and hit.chunk.source_filename == "seed_policy_01.md"
    assert 0 < hit.score <= 1
    assert not hasattr(hit.chunk, "embedding")


async def test_empty_index_returns_nothing(retriever):
    assert await retriever.retrieve("How many sick leaves can I take?", audiences=EMPLOYEE_AUDIENCES) == []


def test_lexical_scoring_helpers():
    assert content_terms("How many sick leaves can I take?") == {"sick", "leave"}
    assert content_terms("uploading documents") == {"upload", "document"}
    idf = inverse_document_frequency([{"leave", "sick"}, {"leave", "late"}, {"leave"}])
    assert idf["sick"] > idf["leave"]
    rare_missing = lexical_overlap({"leave", "maternity"}, {"leave"}, idf, unseen_idf=5.0)
    assert rare_missing < 0.5  # the unknown term dominates
    assert lexical_overlap(set(), {"leave"}, idf, 5.0) == 0.0
