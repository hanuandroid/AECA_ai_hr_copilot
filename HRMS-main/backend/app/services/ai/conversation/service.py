"""ConversationService: the only writer of ai_conversations / ai_conversation_messages.

- Ownership: every lookup is `public_id == :id AND employee_id == ctx.employee_id AND status == 'ACTIVE'`.
  Unknown, malformed, archived and other users' ids all raise the same AIConversationNotFoundError.
- Ordering: `sequence` comes from an atomic `UPDATE ... SET message_count = message_count + 1 ...
  RETURNING message_count` on the conversation row, plus a UNIQUE(conversation_id, sequence) guard, so
  concurrent requests can never share or reorder sequence numbers. Timestamps are not used for order.
- Bounds: at most `max_messages` per conversation; context reads at most `context_messages` rows (one
  indexed, LIMITed query) and at most `context_chars` characters.
- Privacy: content is redacted and length-capped before it is stored. No prompts, SQL, rows,
  confirmation tokens, JWTs, roles or permissions are stored. Nothing here is authorization.
"""

import uuid
from collections.abc import Callable
from datetime import datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.hr_policy import HRPolicy
from app.services.ai.actions.service import utcnow
from app.services.ai.authorization import AIUserContext, policy_audiences
from app.services.ai.errors import AIConversationLimitError, AIConversationNotFoundError
from app.services.ai.orchestration.followup import ConversationContext, HistoryMessage, looks_like_confirmation
from app.services.ai.redaction import redact

logger = structlog.get_logger()

ACTIVE, ARCHIVED = "ACTIVE", "ARCHIVED"
USER, ASSISTANT = "USER", "ASSISTANT"
TITLE_CHARS = 80
CONTEXT_MESSAGE_CHARS = 300  # per message in the classifier context
CAPABILITY_ROUTES = frozenset({"POLICY", "SQL", "ACTION"})
ANCHOR_STATUSES = frozenset({"answered", "partial", "clarification_required", "confirmation_required"})
# RBAC step 7: shown on replay instead of a reply that cited a policy the reader can no longer read.
WITHHELD_REPLY = "This answer is hidden because it cited an HR policy you can no longer access."
# R1: what the classifier context shows instead of the assistant reply of a turn that used the SQL route. That reply
# can contain HR record values (e.g. "The result is <email>."), which must not reach the model provider again.
SQL_REPLY_IN_CONTEXT = "(Answered from HR records; the result is not included here.)"


def parse_public_id(value: object) -> str | None:
    """Canonical UUID string, or None for anything malformed (treated exactly like 'not found')."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


class ConversationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        max_messages: int,
        context_messages: int,
        context_chars: int,
        stored_message_chars: int,
        now: Callable[[], datetime] = utcnow,
    ):
        self._db = db
        self._max_messages = max_messages
        self._context_messages = context_messages
        self._context_chars = context_chars
        self._stored_chars = stored_message_chars
        self._now = now

    # --- conversations ------------------------------------------------------------------------

    async def create(self, ctx: AIUserContext, first_message: str) -> AIConversation:
        now = self._now()
        conversation = AIConversation(public_id=str(uuid.uuid4()), employee_id=ctx.employee_id,
                                      title=redact(first_message, max_length=TITLE_CHARS), status=ACTIVE, message_count=0,
                                      created_at=now, updated_at=now)
        self._db.add(conversation)
        await self._db.commit()
        logger.info("ai.conversation.created", conversation_id=conversation.public_id, employee_id=ctx.employee_id)
        return conversation

    async def get_user_conversation(self, ctx: AIUserContext, public_id: object) -> AIConversation:
        canonical = parse_public_id(public_id)
        if canonical is None:
            raise AIConversationNotFoundError()
        conversation = (await self._db.execute(
            select(AIConversation).where(AIConversation.public_id == canonical,
                                         AIConversation.employee_id == ctx.employee_id,
                                         AIConversation.status == ACTIVE)
        )).scalar_one_or_none()
        if conversation is None:
            raise AIConversationNotFoundError()
        return conversation

    async def list_conversations(self, ctx: AIUserContext, limit: int) -> list[AIConversation]:
        return list((await self._db.execute(
            select(AIConversation)
            .where(AIConversation.employee_id == ctx.employee_id, AIConversation.status == ACTIVE)
            .order_by(AIConversation.updated_at.desc(), AIConversation.id.desc())
            .limit(limit)
        )).scalars())

    async def archive(self, ctx: AIUserContext, public_id: object) -> AIConversation:
        conversation = await self.get_user_conversation(ctx, public_id)
        conversation.status = ARCHIVED
        conversation.updated_at = self._now()
        await self._db.commit()
        logger.info("ai.conversation.archived", conversation_id=conversation.public_id, employee_id=ctx.employee_id)
        return conversation

    # --- messages -----------------------------------------------------------------------------

    async def append_message(
        self,
        conversation: AIConversation,
        role: str,
        content: str,
        *,
        request_id: str,
        route: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        reserve: int = 0,
        source_policy_ids: list[int] | None = None,
    ) -> AIConversationMessage:
        """Append one message with the next sequence number (atomic). `reserve` keeps room for the reply."""
        now = self._now()
        sequence = (await self._db.execute(
            update(AIConversation)
            .where(AIConversation.id == conversation.id, AIConversation.status == ACTIVE,
                   AIConversation.message_count + reserve < self._max_messages)
            .values(message_count=AIConversation.message_count + 1, updated_at=now)
            .returning(AIConversation.message_count)
            .execution_options(synchronize_session=False)
        )).scalar_one_or_none()
        if sequence is None:
            await self._db.commit()  # nothing changed; end the transaction without expiring loaded objects
            raise AIConversationLimitError()
        message = AIConversationMessage(conversation_id=conversation.id, sequence=sequence, role=role,
                                        content=redact(content, max_length=self._stored_chars), request_id=request_id,
                                        route=route, status=status, error_code=error_code, created_at=now,
                                        source_policy_ids=sorted(set(source_policy_ids)) if source_policy_ids else None)
        self._db.add(message)
        await self._db.commit()
        return message

    async def set_resolved_content(self, message: AIConversationMessage, resolved: str | None) -> None:
        if resolved:
            message.resolved_content = redact(resolved, max_length=self._stored_chars)
            await self._db.commit()

    async def withheld_message_ids(self, ctx: AIUserContext, messages: list[AIConversationMessage]) -> set[int]:
        """RBAC step 7: replies whose cited policies are not ALL readable by the caller *now* (audience changed, role
        changed, policy gone or unknown audience). Checked on every replay; nothing is cached."""
        cited = {policy_id for message in messages for policy_id in (message.source_policy_ids or [])}
        if not cited:
            return set()
        readable = policy_audiences(ctx)
        audiences = dict((await self._db.execute(
            select(HRPolicy.id, HRPolicy.audience).where(HRPolicy.id.in_(sorted(cited)))
        )).all())
        return {
            message.id for message in messages
            if any(audiences.get(policy_id) not in readable for policy_id in (message.source_policy_ids or []))
        }

    async def get_messages(self, conversation: AIConversation, limit: int) -> list[AIConversationMessage]:
        """The most recent `limit` messages, oldest first (bounded, indexed query)."""
        rows = list((await self._db.execute(
            select(AIConversationMessage)
            .where(AIConversationMessage.conversation_id == conversation.id)
            .order_by(AIConversationMessage.sequence.desc())
            .limit(limit)
        )).scalars())
        return list(reversed(rows))

    # --- context ------------------------------------------------------------------------------

    async def get_recent_context(self, conversation: AIConversation, ctx: AIUserContext) -> ConversationContext:
        messages = await self.get_messages(conversation, self._context_messages)
        # RBAC step 7: a reply citing a policy the caller can no longer read never reaches the classifier context.
        return build_context(messages, self._context_chars, withheld=await self.withheld_message_ids(ctx, messages))


def build_context(messages: list[AIConversationMessage], max_chars: int, *, withheld: set[int] = frozenset()) -> ConversationContext:
    """Untrusted, bounded context from the last messages (oldest first).

    - recent: newest messages first until `max_chars` is used, each capped at CONTEXT_MESSAGE_CHARS. Assistant
      replies of SQL turns are replaced by SQL_REPLY_IN_CONTEXT (see _context_text).
    - anchor: the latest successfully routed turn (refused and failed turns are skipped), paired with its
      USER message by request_id; only that user text (resolved form) is ever forwarded to a service.
    - flags describe only the latest assistant message ("yes"/"confirm" turns are skipped).
    """
    recent: list[HistoryMessage] = []
    budget = max_chars
    for message in reversed(messages):
        text = _context_text(message, withheld)[:CONTEXT_MESSAGE_CHARS]
        if len(text) > budget:
            break
        recent.append(HistoryMessage(role=message.role, text=text))  # type: ignore[arg-type]
        budget -= len(text)
    recent.reverse()

    users_by_request = {m.request_id: m for m in messages if m.role == USER}
    # "yes"/"confirm" turns (answered with "confirm via the button") are transparent: they neither anchor
    # a follow-up nor clear the pending-proposal state of the turn before them.
    assistants = [m for m in messages if m.role == ASSISTANT and not (
        m.request_id in users_by_request and looks_like_confirmation(users_by_request[m.request_id].content))]
    latest = assistants[-1] if assistants else None
    anchor_question = anchor_route = None
    for message in reversed(assistants):
        user = users_by_request.get(message.request_id)
        if user is None or message.status not in ANCHOR_STATUSES or not message.route:  # refused/error excluded
            continue
        anchor_question = user.resolved_content or user.content
        routes = set((message.route or "").split("+"))
        anchor_route = message.route if len(routes) == 1 and routes <= CAPABILITY_ROUTES else None
        break

    return ConversationContext(
        recent=tuple(recent),
        anchor_question=anchor_question,
        anchor_route=anchor_route,
        awaiting_clarification=bool(latest and latest.status == "clarification_required"),
        awaiting_confirmation=bool(latest and latest.status == "confirmation_required"),
    )


def _context_text(message: AIConversationMessage, withheld: set[int]) -> str:
    """The text a stored message contributes to the classifier context.

    An assistant reply of a turn whose (backend-recorded) route includes SQL is never forwarded: it may quote record
    values that redaction cannot recognise (names, emails, leave reasons, ticket text). This is decided by the stored
    route, not by inspecting the text, and it also applies to messages stored before this rule. A combined turn
    (e.g. POLICY+SQL) is replaced as a whole. The stored message itself is unchanged, so the owner's replay still
    shows the answer."""
    if message.id in withheld:
        return WITHHELD_REPLY
    if message.role == ASSISTANT and "SQL" in (message.route or "").split("+"):
        return SQL_REPLY_IN_CONTEXT
    return message.content
