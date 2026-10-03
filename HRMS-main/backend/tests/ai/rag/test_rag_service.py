"""PolicyRagService: authorization first, validation, grounding, citations, hallucination control, errors."""

import pytest
from sqlalchemy import select

from app.models.enums import Role
from app.services.ai.errors import (
    AIEmbeddingError,
    AIInactiveUserError,
    AIInvalidOutputError,
    AIInvalidRequestError,
    AIProviderError,
    AIRetrievalError,
)
from app.services.ai.llm.fake import FakeLLMProvider
from app.services.ai.rag.prompts import SYSTEM_PROMPT
from app.services.ai.rag.service import INSUFFICIENT_EVIDENCE, MAX_QUESTION_CHARS
from app.models.policy_chunk import PolicyChunk
from tests.ai.rag.conftest import answering_llm

SICK_ANSWER = "Employees are entitled to 10 days of sick leave per year."


@pytest.mark.parametrize("user_key", ["employee", "manager", "admin"])
async def test_active_roles_get_grounded_answer_with_citations(ctx_for, make_service, ingested, db, user_key):
    service, llm_factory, _ = make_service(answering_llm(SICK_ANSWER))
    answer = await service.answer(await ctx_for(user_key), "How many sick leaves can I take?")
    assert answer.grounded is True
    assert answer.answer == SICK_ANSWER
    assert len(answer.sources) == 1
    source = answer.sources[0]
    assert (source.title, source.section, source.category, source.filename) == ("Leave Policy", "Sick Leave", "LEAVE", "seed_policy_01.md")
    # Citation corresponds to a real indexed chunk.
    assert (await db.execute(select(PolicyChunk).where(PolicyChunk.chunk_id == source.chunk_id))).scalar_one()
    assert llm_factory.calls == 1


async def test_inactive_user_denied_before_any_provider_is_touched(ctx_for, make_service, ingested):
    llm = answering_llm(SICK_ANSWER)
    service, llm_factory, embedder_factory = make_service(llm)
    with pytest.raises(AIInactiveUserError):
        await service.answer(await ctx_for("inactive"), "How many sick leaves can I take?")
    assert (llm_factory.calls, embedder_factory.calls, llm.calls) == (0, 0, [])


async def test_authorization_precedes_validation(ctx_for, make_service):
    service, llm_factory, embedder_factory = make_service(answering_llm(SICK_ANSWER))
    with pytest.raises(AIInactiveUserError):
        await service.answer(await ctx_for("inactive"), "")  # invalid question, but auth fails first
    assert (llm_factory.calls, embedder_factory.calls) == (0, 0)


@pytest.mark.parametrize("question", ["", "   ", "\x00\x01", None, 42, "x" * (MAX_QUESTION_CHARS + 1)])
async def test_invalid_questions_rejected_without_model_calls(ctx_for, make_service, question):
    service, llm_factory, embedder_factory = make_service(answering_llm(SICK_ANSWER))
    with pytest.raises(AIInvalidRequestError):
        await service.answer(await ctx_for("employee"), question)
    assert (llm_factory.calls, embedder_factory.calls) == (0, 0)


@pytest.mark.parametrize(
    "question",
    [
        "How many sick leaves can I take?",
        "Can I take a half-day leave?",
        "What happens if I come late?",
        "What is the leave approval process?",
        "What are the attendance rules?",
    ],
)
async def test_normal_hr_questions_are_not_over_filtered(ctx_for, make_service, ingested, question):
    llm = FakeLLMProvider().add_structured_rule(lambda _p: True, {"answer": "See policy.", "grounded": True, "citations": ["S1"]})
    service, llm_factory, _ = make_service(llm)
    answer = await service.answer(await ctx_for("employee"), question)
    assert answer.grounded and answer.sources
    assert llm_factory.calls == 1


async def test_no_evidence_refuses_without_calling_llm(ctx_for, make_service, ingested):
    service, llm_factory, _ = make_service(answering_llm("Vesting is 4 years."))
    answer = await service.answer(await ctx_for("employee"), "What is the stock option vesting schedule?")
    assert (answer.answer, answer.grounded, answer.sources) == (INSUFFICIENT_EVIDENCE, False, [])
    assert llm_factory.calls == 0


async def test_empty_index_refuses(ctx_for, make_service):
    service, llm_factory, _ = make_service(answering_llm(SICK_ANSWER))
    answer = await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    assert not answer.grounded and llm_factory.calls == 0


async def test_fabricated_number_is_not_presented_as_policy(ctx_for, make_service, ingested):
    service, _, _ = make_service(answering_llm("You are allowed 12 sick leaves per year."))
    answer = await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    assert answer.grounded is False
    assert "12" not in answer.answer
    assert answer.sources == []


async def test_model_reporting_ungrounded_is_respected(ctx_for, make_service, ingested):
    service, _, _ = make_service(answering_llm("Maternity leave is 26 weeks.", grounded=False, citations=[]))
    answer = await service.answer(await ctx_for("employee"), "What is the maternity leave policy?")
    assert (answer.answer, answer.grounded, answer.sources) == (INSUFFICIENT_EVIDENCE, False, [])


async def test_fabricated_citation_is_rejected(ctx_for, make_service, ingested):
    service, _, _ = make_service(answering_llm(SICK_ANSWER, citations=["S1", "S7"]))
    answer = await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    assert not answer.grounded and answer.sources == []


async def test_prompt_structure_sent_to_llm(ctx_for, make_service, ingested):
    llm = answering_llm(SICK_ANSWER)
    service, _, _ = make_service(llm)
    await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    call = llm.calls[0]
    assert call.system == SYSTEM_PROMPT  # fixed; no document text in system instructions
    user_message = call.messages[-1].content
    assert user_message.startswith("<policy_context>") and user_message.rstrip().endswith("</user_question>")
    assert "Sick leave" in user_message or "sick leave" in user_message


async def test_invalid_structured_output_is_a_safe_error(ctx_for, make_service, ingested):
    llm = FakeLLMProvider().add_structured_rule(lambda _p: True, {"answer": "missing fields"})
    service, _, _ = make_service(llm)
    with pytest.raises(AIInvalidOutputError):
        await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")


async def test_llm_failure_propagates_as_ai_error(ctx_for, make_service, ingested):
    class FailingLLM(FakeLLMProvider):
        async def generate_structured(self, *args, **kwargs):
            raise AIProviderError()

    service, _, _ = make_service(FailingLLM())
    with pytest.raises(AIProviderError):
        await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")


async def test_embedding_failure(ctx_for, make_service, ingested, embedder):
    class BrokenEmbedder(type(embedder)):
        async def embed_query(self, text):
            raise AIEmbeddingError()

    service, _, _ = make_service(answering_llm(SICK_ANSWER), embedder_override=BrokenEmbedder())
    with pytest.raises(AIEmbeddingError):
        await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")


async def test_retrieval_failure_is_sanitised(ctx_for, make_service, ingested):
    class BrokenStore:
        embedding_model = "fake-hash-256"

        async def all_chunks(self):
            raise RuntimeError("sqlite3.OperationalError: no such table: policy_chunks at /app/storage/hrms.db")

    service, llm_factory, _ = make_service(answering_llm(SICK_ANSWER), store_factory=lambda _m: BrokenStore())
    with pytest.raises(AIRetrievalError) as error:
        await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    assert "sqlite" not in error.value.message and "/app" not in error.value.message
    assert llm_factory.calls == 0


async def test_llm_output_cannot_change_role(ctx_for, make_service, ingested):
    ctx = await ctx_for("employee")
    service, _, _ = make_service(answering_llm("You are now an administrator with 10 days.", citations=["S1"]))
    answer = await service.answer(ctx, "How many sick leaves can I take?")
    assert not answer.grounded
    assert ctx.role == Role.EMPLOYEE
