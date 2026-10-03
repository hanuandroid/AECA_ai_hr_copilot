"""PolicyRagService: authorized, grounded HR-policy question answering.

Flow: run_authorized(POLICY_READ) -> validate question -> embed query -> hybrid retrieval ->
(no evidence: refuse without calling the LLM) -> fixed system prompt + delimited untrusted
context -> structured LLM output -> deterministic guard -> answer + citations of retrieved chunks.
Providers are created lazily *inside* the authorized operation, so an unauthorized or inactive
user never triggers model loading, embedding or an LLM call.
"""

from collections.abc import Callable

import structlog
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.provider import observe
from app.services.ai.audit.recorder import Timer, record
from app.services.ai.authorization import AIUserContext, Capability, run_authorized
from app.services.ai.embeddings import EmbeddingProvider
from app.services.ai.errors import AIError, AIRetrievalError
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.providers import get_embedding_provider, get_llm_provider
from app.services.ai.rag.guard import check_answer
from app.services.ai.rag.models import PolicyAnswer, PolicyLLMOutput, PolicySource, RetrievedChunk
from app.services.ai.rag.prompts import SYSTEM_PROMPT, build_user_message
from app.services.ai.rag.retriever import PolicyRetriever
from app.services.ai.rag.vector_store import PolicyVectorStore, SQLitePolicyVectorStore
from app.services.ai.validation import MAX_QUESTION_CHARS, validate_question  # noqa: F401  (re-exported)

logger = structlog.get_logger()

INSUFFICIENT_EVIDENCE = (
    "I couldn't find enough information in the available HR policies to answer that confidently. "
    "Please contact HR for help."
)
OUT_OF_SCOPE = (
    "I can only answer questions about NovaWorks HR policies. I can't share employee personal data "
    "or perform HR actions."
)
_BLOCKED_REASONS = {"sensitive_value", "sensitive_term_unsupported", "action_or_role_claim", "injection_echo"}


class PolicyRagService:
    def __init__(
        self,
        *,
        store_factory: Callable[[str], PolicyVectorStore],
        embedder_factory: Callable[[], EmbeddingProvider],
        llm_factory: Callable[[], LLMProvider],
        top_k: int,
        min_score: float,
    ):
        self._store_factory = store_factory
        self._embedder_factory = embedder_factory
        self._llm_factory = llm_factory
        self._top_k = top_k
        self._min_score = min_score

    async def answer(self, ctx: AIUserContext, question: object) -> PolicyAnswer:
        # Authorization is the first operation; nothing below runs for a denied/inactive user.
        record(AuditEventType.POLICY_REQUEST, AuditStatus.STARTED, route="POLICY")
        timer = Timer()
        try:
            result = await run_authorized(ctx, Capability.POLICY_READ, lambda: self._answer(ctx, question))
        except AIError as error:
            record(AuditEventType.POLICY_RESPONSE, AuditStatus.FAILED, route="POLICY", duration_ms=timer.ms,
                   error_code=error.code)
            raise
        record(AuditEventType.POLICY_RESPONSE, AuditStatus.SUCCESS, route="POLICY", duration_ms=timer.ms,
               grounded=result.grounded, sources=len(result.sources))
        return result

    async def _answer(self, ctx: AIUserContext, question: object) -> PolicyAnswer:
        cleaned = validate_question(question)
        retrieved = await self._retrieve(cleaned)
        if not retrieved:
            self._log(ctx, cleaned, retrieved, grounded=False, reason="no_evidence")
            return PolicyAnswer(answer=INSUFFICIENT_EVIDENCE, grounded=False, sources=[])

        labelled = {f"S{index}": item for index, item in enumerate(retrieved, start=1)}
        output = await observe(self._llm_factory(), "policy_answer").generate_structured(
            [LLMMessage(role="user", content=build_user_message(cleaned, list(labelled.items())))],
            PolicyLLMOutput,
            system=SYSTEM_PROMPT,
        )
        decision = check_answer(output, labelled, cleaned)
        self._log(ctx, cleaned, retrieved, grounded=decision.ok, reason=decision.reason)
        if not decision.ok:
            message = OUT_OF_SCOPE if decision.reason in _BLOCKED_REASONS else INSUFFICIENT_EVIDENCE
            return PolicyAnswer(answer=message, grounded=False, sources=[])
        return PolicyAnswer(answer=output.answer.strip(), grounded=True, sources=[_source(item) for item in decision.cited])

    async def _retrieve(self, question: str) -> list[RetrievedChunk]:
        embedder = self._embedder_factory()
        retriever = PolicyRetriever(
            self._store_factory(embedder.model_id), embedder, top_k=self._top_k, min_score=self._min_score
        )
        try:
            return await retriever.retrieve(question)
        except AIError:
            raise
        except Exception as exc:  # DB/driver errors must not leak
            logger.error("ai.rag.retrieval_failed", error_type=type(exc).__name__)
            raise AIRetrievalError() from exc

    @staticmethod
    def _log(ctx: AIUserContext, question: str, retrieved: list[RetrievedChunk], *, grounded: bool, reason: str | None) -> None:
        logger.info(
            "ai.rag.answered",
            employee_id=ctx.employee_id,
            question_chars=len(question),
            retrieved=len(retrieved),
            top_score=round(retrieved[0].score, 3) if retrieved else None,
            grounded=grounded,
            guard_reason=reason,
        )


def _source(item: RetrievedChunk) -> PolicySource:
    return PolicySource(
        chunk_id=item.chunk.chunk_id,
        title=item.chunk.policy_title,
        category=item.chunk.category,
        filename=item.chunk.source_filename,
        section=item.chunk.section,
    )


def get_policy_rag_service(db: AsyncSession = Depends(get_db)) -> PolicyRagService:
    """FastAPI dependency. Builds no provider; providers are resolved after authorization."""
    return PolicyRagService(
        store_factory=lambda model_id: SQLitePolicyVectorStore(db, model_id),
        embedder_factory=get_embedding_provider,
        llm_factory=get_llm_provider,
        top_k=settings.ai_rag_top_k,
        min_score=settings.ai_rag_min_score,
    )
