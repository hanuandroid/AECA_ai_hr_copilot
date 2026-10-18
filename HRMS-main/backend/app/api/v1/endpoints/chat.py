from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.core.response import error_response, success_response
from app.models.employee import Employee
from app.schemas.chat import (
    ActionConfirmRequest,
    ActionRequest,
    ChatMessageCreate,
    ChatSessionCreate,
    CopilotRequest,
    PolicyQuestionRequest,
    SQLQuestionRequest,
)
from app.services.ai.actions.service import ActionAgentService, get_action_agent_service
from app.services.ai.authorization import AIUserContext, capabilities_for, get_ai_user_context, hr_read_scope
from app.services.ai.conversation.chat import ConversationalCopilot, get_conversation_service, get_conversational_copilot
from app.services.ai.conversation.models import ConversationDetail, ConversationMessageView, ConversationSummary
from app.services.ai.conversation.service import WITHHELD_REPLY, ConversationService
from app.services.ai.audit.recorder import record_error
from app.services.ai.errors import AIError, ai_error_response
from app.services.ai.limits import enforce_ai_limits
from app.services.ai.rag.service import PolicyRagService, get_policy_rag_service
from app.services.ai.sql.service import SQLAgentService, get_sql_agent_service
from app.services.auth import get_current_user, oauth2_scheme

router = APIRouter()


@router.post("")
async def ask_copilot(
    payload: CopilotRequest,
    ctx: AIUserContext = Depends(enforce_ai_limits),  # B-09: rate limit + daily budget, before any model call
    service: ConversationalCopilot = Depends(get_conversational_copilot),
):
    """Unified copilot. Order: JWT auth -> AI user context (INACTIVE rejected) -> message validation ->
    owner-scoped conversation (or a new one) -> bounded untrusted history -> LangGraph router (classify ->
    Policy RAG / SQL agent / action PROPOSAL) -> persisted turn -> response. Never executes an action:
    confirmation stays on POST /chat/actions/confirm."""
    try:
        result = await service.handle(ctx, payload.message, payload.conversation_id)
    except AIError as error:
        record_error(error)
        response = ai_error_response(error)
        conversation_id = getattr(error, "conversation_id", None)
        if conversation_id:
            response.headers["X-Conversation-Id"] = conversation_id
        return response
    return success_response(result.model_dump(exclude_none=True))


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    ctx: AIUserContext = Depends(get_ai_user_context),
    conversations: ConversationService = Depends(get_conversation_service),
):
    """The caller's own active conversations (newest first). There is no cross-user or admin view."""
    items = await conversations.list_conversations(ctx, limit)
    return success_response([ConversationSummary.of(item).model_dump(mode="json") for item in items])


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AIUserContext = Depends(get_ai_user_context),
    conversations: ConversationService = Depends(get_conversation_service),
):
    """One of the caller's conversations with its most recent `limit` messages (oldest first)."""
    conversation = await conversations.get_user_conversation(ctx, conversation_id)
    messages = await conversations.get_messages(conversation, limit)
    withheld = await conversations.withheld_message_ids(ctx, messages)  # RBAC step 7: re-check policy audiences
    detail = ConversationDetail(**ConversationSummary.of(conversation).model_dump(), messages=[
        ConversationMessageView.of(message, withheld=WITHHELD_REPLY if message.id in withheld else None)
        for message in messages
    ])
    return success_response(detail.model_dump(mode="json", exclude_none=True))


@router.delete("/conversations/{conversation_id}")
async def archive_conversation(
    conversation_id: str,
    ctx: AIUserContext = Depends(get_ai_user_context),
    conversations: ConversationService = Depends(get_conversation_service),
):
    """Archive (soft-delete) one of the caller's conversations; it can no longer be read or continued."""
    conversation = await conversations.archive(ctx, conversation_id)
    return success_response({"conversation_id": conversation.public_id, "status": conversation.status})


@router.post("/actions")
async def propose_action(
    payload: ActionRequest,
    ctx: AIUserContext = Depends(enforce_ai_limits),  # B-09: rate limit + daily budget, before any model call
    service: ActionAgentService = Depends(get_action_agent_service),
):
    """Propose an HR action. Never mutates: returns clarification_required or confirmation_required."""
    result = await service.propose(ctx, payload.question)
    return success_response(result.model_dump(exclude_none=True))


@router.post("/actions/confirm")
async def confirm_action(
    payload: ActionConfirmRequest,
    ctx: AIUserContext = Depends(enforce_ai_limits),  # B-09: rate limit + daily budget, before any model call
    service: ActionAgentService = Depends(get_action_agent_service),
    access_token: str = Depends(oauth2_scheme),
):
    """Execute a previously proposed action via the existing HRMS API, as the authenticated user."""
    result = await service.confirm(ctx, payload.confirmation_id, access_token, password=payload.password)
    return success_response(result.model_dump(exclude_none=True))


@router.post("/policy")
async def ask_policy(
    payload: PolicyQuestionRequest,
    ctx: AIUserContext = Depends(enforce_ai_limits),  # B-09: rate limit + daily budget, before any model call
    service: PolicyRagService = Depends(get_policy_rag_service),
):
    """Policy RAG. Order: JWT auth -> AI user context (INACTIVE rejected) -> POLICY_READ ->
    question validation -> retrieval -> LLM -> guard. Errors use the AI error envelope."""
    result = await service.answer(ctx, payload.question)
    return success_response(result.model_dump())


@router.post("/sql")
async def ask_sql(
    payload: SQLQuestionRequest,
    ctx: AIUserContext = Depends(enforce_ai_limits),  # B-09: rate limit + daily budget, before any model call
    service: SQLAgentService = Depends(get_sql_agent_service),
):
    """Read-only HR data questions. Order: JWT auth -> AI user context (INACTIVE rejected) ->
    SQL capability -> question validation -> LLM SQL -> AST validation -> scope rewrite + LIMIT ->
    re-validation -> read-only execution -> redaction -> formatting."""
    result = await service.answer(ctx, payload.question)
    return success_response(result.model_dump(exclude_none=True))


@router.get("/permissions")
async def my_ai_permissions(ctx: AIUserContext = Depends(get_ai_user_context)):
    """The caller's AI capabilities, derived only from server-side state (UX hint for the frontend;
    the backend re-checks every operation)."""
    return success_response(
        {
            "employee_id": ctx.employee_id,
            "role": ctx.role.value,
            "capabilities": sorted(capability.value for capability in capabilities_for(ctx.role)),
            "data_scope": hr_read_scope(ctx).level.value,
        }
    )


@router.post("/sessions")
async def create_chat_session(
    payload: ChatSessionCreate,
    current_user: Employee = Depends(get_current_user),
):
    _ = payload
    _ = current_user
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=error_response("CHAT_NOT_IMPLEMENTED", "Chat session creation is a Phase-3 stub and not implemented yet"),
    )


@router.post("/sessions/{session_id}/messages")
async def post_chat_message(
    session_id: str,
    payload: ChatMessageCreate,
    current_user: Employee = Depends(get_current_user),
):
    _ = session_id
    _ = payload
    _ = current_user
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=error_response("CHAT_NOT_IMPLEMENTED", "Chat messaging is a Phase-3 stub and not implemented yet"),
    )
