"""ConversationalCopilot: one conversation turn around the Phase 6 router.

Order (identity first, history never above authorization):
  1. trusted AIUserContext (dependency; INACTIVE rejected) and message validation;
  2. resolve the conversation owner-scoped, or create one for this user;
  3. load the bounded, untrusted context (before this turn's message);
  4. store the USER message (redacted);
  5. run the router with the SAME trusted ctx; history is passed only as untrusted context;
  6. store the ASSISTANT message: the reply, or - on failure - the safe error message marked
     status="error" with its code (never presented as an answer, never used as a follow-up anchor);
  7. return the response with the conversation id (on errors: X-Conversation-Id header).
"""

import uuid
from typing import Any

import structlog
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.recorder import Timer, bind_conversation, current_request_id, record, text_fingerprint
from app.services.ai.authorization import AIUserContext
from app.services.ai.conversation.service import ASSISTANT, USER, ConversationService
from app.services.ai.errors import AIError, AIInactiveUserError, AIRoutingError
from app.services.ai.orchestration.models import CopilotResponse
from app.services.ai.orchestration.service import CopilotService, get_copilot_service
from app.services.ai.validation import validate_question

logger = structlog.get_logger()


class ConversationalCopilot:
    def __init__(self, *, conversations: ConversationService, copilot: CopilotService):
        self._conversations = conversations
        self._copilot = copilot

    async def handle(self, ctx: AIUserContext, message: object, conversation_id: object = None) -> CopilotResponse:
        if not ctx.is_active:
            raise AIInactiveUserError()
        cleaned = validate_question(message)
        if conversation_id is None:
            conversation = await self._conversations.create(ctx, cleaned)
        else:
            conversation = await self._conversations.get_user_conversation(ctx, conversation_id)
        bind_conversation(conversation.public_id)  # only after the owner check (or creation for this user)
        context = await self._conversations.get_recent_context(conversation, ctx)

        request_id = current_request_id() or uuid.uuid4().hex
        timer = Timer()
        chars, digest = text_fingerprint(cleaned)
        record(AuditEventType.CHAT_REQUEST, AuditStatus.STARTED, message_chars=chars, message_hash=digest,
               follow_up=None if context.is_empty else False)
        user_message = await self._conversations.append_message(conversation, USER, cleaned, request_id=request_id,
                                                                 reserve=1)
        try:
            turn = await self._copilot.answer_turn(ctx, cleaned, context=None if context.is_empty else context,
                                                   request_id=request_id, conversation_id=conversation.public_id)
        except Exception as exc:
            error = exc if isinstance(exc, AIError) else AIRoutingError()
            record(AuditEventType.CHAT_RESPONSE, AuditStatus.FAILED, duration_ms=timer.ms, error_code=error.code)
            await self._conversations.append_message(conversation, ASSISTANT, error.message, request_id=request_id,
                                                     route=_routes(self._copilot.last_state), status="error",
                                                     error_code=error.code)
            error.conversation_id = conversation.public_id  # type: ignore[attr-defined]  (X-Conversation-Id header)
            if error is exc:
                raise
            raise error from exc

        response = turn.response
        await self._conversations.set_resolved_content(user_message, turn.resolved_question)
        await self._conversations.append_message(conversation, ASSISTANT, response.message, request_id=request_id,
                                                 route="+".join(response.routes), status=response.status,
                                                 error_code=response.code, source_policy_ids=_cited_policies(response))
        response_chars, response_digest = text_fingerprint(response.message)
        record(AuditEventType.CHAT_RESPONSE, _CHAT_STATUS.get(response.status, AuditStatus.SUCCESS),
               route="+".join(response.routes), duration_ms=timer.ms, intents=response.intents, routes=response.routes,
               source=response.classification_source, follow_up=response.follow_up, parts=len(response.parts),
               response_chars=response_chars, response_hash=response_digest)
        logger.info("ai.conversation.turn", conversation_id=conversation.public_id, request_id=request_id,
                    follow_up=response.follow_up, status=response.status)
        return response.model_copy(update={"conversation_id": conversation.public_id})


_CHAT_STATUS = {
    "answered": AuditStatus.SUCCESS, "partial": AuditStatus.PARTIAL, "refused": AuditStatus.REFUSED,
    "clarification_required": AuditStatus.CLARIFICATION_REQUIRED,
    "confirmation_required": AuditStatus.CONFIRMATION_REQUIRED,
}


def _cited_policies(response: Any) -> list[int]:
    """Policy ids cited by the POLICY parts of this reply (RBAC step 7: replay re-checks their audience)."""
    cited: list[int] = []
    for part in response.parts:
        sources = (part.data or {}).get("sources") if part.route == "POLICY" else None
        for source in sources or []:
            policy_id = source.get("policy_id") if isinstance(source, dict) else None
            if isinstance(policy_id, int):
                cited.append(policy_id)
    return cited


def _routes(state: Any) -> str | None:
    parts = (state or {}).get("parts") or []
    return "+".join(part["route"] for part in parts) or None


def get_conversation_service(db: AsyncSession = Depends(get_db)) -> ConversationService:
    return ConversationService(
        db,
        max_messages=settings.ai_conversation_max_messages,
        context_messages=settings.ai_conversation_context_messages,
        context_chars=settings.ai_conversation_context_chars,
        stored_message_chars=settings.ai_conversation_stored_message_chars,
    )


def get_conversational_copilot(
    conversations: ConversationService = Depends(get_conversation_service),
    copilot: CopilotService = Depends(get_copilot_service),
) -> ConversationalCopilot:
    return ConversationalCopilot(conversations=conversations, copilot=copilot)
