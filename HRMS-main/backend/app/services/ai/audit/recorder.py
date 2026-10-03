"""Request-scoped audit recording. Passive: recording never authorizes, blocks or changes an outcome.

One AuditScope per AI HTTP request (created by AIAuditMiddleware) holds the request_id that every
event, the router and the conversation layer share. Identity is bound only from trusted server-side
objects: the user by get_ai_user_context (DB-loaded employee), the conversation by the conversation
service after its owner check. Nothing a client, conversation history or model says can set them.

`record(...)` is synchronous and in-memory (cheap, cannot fail the request). Events are persisted when
the middleware flushes the scope after the response. `record_critical(...)` persists immediately and
raises if it cannot: it is used before executing a confirmed action (fail-closed for the only
mutating path).
"""

import hashlib
import hmac
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError

from app.core.config import settings
from app.services.ai.audit.events import AuditEventType, AuditMetadata, AuditStatus, ReasonCode
from app.services.ai.errors import AIAuditUnavailableError, AIError

logger = structlog.get_logger()


@dataclass
class AuditEvent:
    event_id: str
    sequence: int
    event_type: AuditEventType
    status: AuditStatus
    route: str | None
    duration_ms: float | None
    metadata: AuditMetadata
    created_at: datetime
    persisted: bool = False


@dataclass
class AuditScope:
    request_id: str
    started: float = field(default_factory=time.monotonic)
    employee_id: int | None = None
    actor_role: str | None = None  # historical metadata from the DB-loaded user; never used for decisions
    conversation_id: str | None = None
    events: list[AuditEvent] = field(default_factory=list)

    def bind_user(self, employee_id: int, role: str | None = None) -> None:
        if self.employee_id is None:
            self.employee_id = employee_id
            self.actor_role = role
        elif self.employee_id != employee_id:  # never re-bind a request to another identity
            logger.error("ai.audit.identity_conflict", request_id=self.request_id)

    def bind_conversation(self, conversation_id: str) -> None:
        if self.conversation_id is None:
            self.conversation_id = conversation_id
        elif self.conversation_id != conversation_id:
            logger.error("ai.audit.conversation_conflict", request_id=self.request_id)


_current: ContextVar[AuditScope | None] = ContextVar("ai_audit_scope", default=None)


def new_scope() -> AuditScope:
    return AuditScope(request_id=uuid.uuid4().hex)


def activate(scope: AuditScope):
    return _current.set(scope)


def deactivate(token) -> None:
    _current.reset(token)


def current_scope() -> AuditScope | None:
    return _current.get()


def current_request_id() -> str | None:
    scope = _current.get()
    return scope.request_id if scope else None


def bind_user(employee_id: int, role: str | None = None) -> None:
    scope = _current.get()
    if scope is not None:
        scope.bind_user(employee_id, role)


def bind_conversation(conversation_id: str) -> None:
    scope = _current.get()
    if scope is not None:
        scope.bind_conversation(conversation_id)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, like the other AI tables


def record(
    event_type: AuditEventType,
    status: AuditStatus,
    *,
    route: str | None = None,
    duration_ms: float | None = None,
    **metadata,
) -> AuditEvent | None:
    """Append an event to the current request's scope (no-op outside an AI request). Never raises."""
    scope = _current.get()
    try:
        meta = AuditMetadata(**metadata)
    except ValidationError:
        # Blocked by design: a disallowed field or value never reaches storage or logs.
        logger.warning("ai.audit.metadata_rejected", event_type=event_type.value, keys=sorted(metadata))
        meta = AuditMetadata(error_code="AUDIT_METADATA_REJECTED")
    if scope is None:
        return None
    event = AuditEvent(event_id=uuid.uuid4().hex, sequence=len(scope.events) + 1, event_type=event_type, status=status,
                       route=route, duration_ms=None if duration_ms is None else round(duration_ms, 2), metadata=meta,
                       created_at=_utcnow())
    scope.events.append(event)
    logger.info("ai.audit.event", request_id=scope.request_id, event_id=event.event_id, event_type=event_type.value,
                status=status.value, route=route, duration_ms=event.duration_ms,
                **meta.model_dump(mode="json", exclude_none=True))
    return event


async def record_critical(event_type: AuditEventType, status: AuditStatus, *, route: str | None = None, **metadata) -> None:
    """Record AND persist now. Raises AIAuditUnavailableError if audit is enabled but cannot be written."""
    event = record(event_type, status, route=route, **metadata)
    scope = _current.get()
    if event is None or scope is None or not settings.ai_audit_enabled:
        return
    from app.services.ai.audit import store

    try:
        await store.write(scope, [event])
    except Exception as exc:  # noqa: BLE001 - any storage failure must stop the mutation
        logger.error("ai.audit.critical_write_failed", request_id=scope.request_id, event_type=event_type.value,
                     error=type(exc).__name__)
        raise AIAuditUnavailableError() from exc
    event.persisted = True


_REASON_CODES = {
    "prompt_injection": ReasonCode.PROMPT_INJECTION, "sensitive_data": ReasonCode.SENSITIVE_DATA_REQUEST,
    "destructive": ReasonCode.DESTRUCTIVE_REQUEST, "direct_access": ReasonCode.DIRECT_SYSTEM_ACCESS,
    "unsupported": ReasonCode.UNSUPPORTED_REQUEST, "ambiguous": ReasonCode.AMBIGUOUS_REQUEST,
    "multiple_actions": ReasonCode.MULTIPLE_ACTIONS, "too_many_requests": ReasonCode.TOO_MANY_REQUESTS,
    "confirmation_in_chat": ReasonCode.CONFIRMATION_IN_CHAT, "empty": ReasonCode.AMBIGUOUS_REQUEST,
}


def reason_code_for(classification_reason: str | None, default: ReasonCode) -> ReasonCode:
    """Map an internal classification reason ("rules:prompt_injection") to a closed ReasonCode."""
    suffix = (classification_reason or "").split(":", 1)[-1]
    return _REASON_CODES.get(suffix, default)


def record_error(error: AIError) -> None:
    """The safe public outcome of a failed AI request (code + status only)."""
    record(AuditEventType.AI_ERROR, AuditStatus.DENIED if error.http_status in (401, 403) else AuditStatus.FAILED,
           error_code=error.code, http_status=error.http_status)


def text_fingerprint(text: str) -> tuple[int, str]:
    """(length, keyed hash prefix) of user/assistant text: correlation without storing content."""
    key = hashlib.sha256(b"ai-audit-fingerprint:" + settings.jwt_secret_key.encode("utf-8")).digest()
    digest = hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return len(text), digest


class Timer:
    """Monotonic stopwatch in milliseconds."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000
