"""Retention for AI-owned tables (production hardening Phase 2, B-14). Operator tooling, not a request path.

PROVISIONAL defaults (config): conversations 90 days after last activity; pending actions 30 days after their terminal
state or expiry; audit events retained >= 365 days and NEVER deleted here (append-only; deletion needs an approved
legal/security procedure). Pending actions in EXECUTING or UNKNOWN state are never purged: they need reconciliation
(see services/ai/actions/reconciliation.py).

Safety: dry run by default. Applying requires an operator who is an ACTIVE ADMIN employee in the database; the purge is
recorded as a RETENTION_PURGE audit event (counts only, attributed to that operator).
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_audit_event import AIAuditEvent
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.ai_pending_action import AIPendingAction
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role

logger = structlog.get_logger()

TERMINAL_ACTION_STATUSES = ("COMPLETED", "FAILED", "DENIED", "EXPIRED", "INVALID")
NEVER_PURGED_ACTION_STATUSES = ("EXECUTING", "UNKNOWN")


class RetentionAuthorizationError(Exception):
    """The operator is not an active ADMIN employee."""


@dataclass(frozen=True)
class RetentionReport:
    applied: bool
    now: str
    conversations: int
    conversation_messages: int
    pending_actions: int
    audit_events_past_retention: int  # reported only, never deleted
    pending_actions_needing_reconciliation: int  # EXECUTING/UNKNOWN, never deleted

    def as_dict(self) -> dict:
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, like the AI tables


def _conversation_filter(now: datetime):
    return AIConversation.updated_at < now - timedelta(days=settings.ai_conversation_retention_days)


def _pending_action_filter(now: datetime):
    cutoff = now - timedelta(days=settings.ai_pending_action_retention_days)
    terminal = and_(AIPendingAction.status.in_(TERMINAL_ACTION_STATUSES),
                    func.coalesce(AIPendingAction.used_at, AIPendingAction.expires_at) < cutoff)
    expired_never_confirmed = and_(AIPendingAction.status == "PENDING", AIPendingAction.expires_at < cutoff)
    return or_(terminal, expired_never_confirmed)


async def _count(db: AsyncSession, column, condition) -> int:
    return int((await db.execute(select(func.count(column)).where(condition))).scalar_one())


async def _authorize_operator(db: AsyncSession, operator_employee_id: int) -> Employee:
    operator = (await db.execute(select(Employee).where(Employee.id == operator_employee_id))).scalar_one_or_none()
    if operator is None or operator.role != Role.ADMIN or operator.status != EmployeeStatus.ACTIVE:
        raise RetentionAuthorizationError("retention cleanup requires an active ADMIN operator")
    return operator


async def run_retention(db: AsyncSession, *, apply: bool = False, operator_employee_id: int | None = None,
                        now: datetime | None = None) -> RetentionReport:
    """Count (dry run) or delete (apply) records past retention. Audit events are only counted."""
    now = now or _utcnow()
    operator: tuple[int, str] | None = None  # (id, role) captured now: commit() expires the ORM object
    if apply:
        if operator_employee_id is None:
            raise RetentionAuthorizationError("--apply requires an operator employee id")
        admin = await _authorize_operator(db, operator_employee_id)
        operator = (admin.id, admin.role.value)

    expired_conversations = select(AIConversation.id).where(_conversation_filter(now))
    conversations = await _count(db, AIConversation.id, _conversation_filter(now))
    messages = await _count(db, AIConversationMessage.id, AIConversationMessage.conversation_id.in_(expired_conversations))
    actions = await _count(db, AIPendingAction.id, _pending_action_filter(now))
    audit_old = await _count(db, AIAuditEvent.id,
                             AIAuditEvent.created_at < now - timedelta(days=settings.ai_audit_retention_days))
    needs_reconciliation = await _count(db, AIPendingAction.id, AIPendingAction.status.in_(NEVER_PURGED_ACTION_STATUSES))

    if apply:
        # Messages first: SQLite does not enforce ON DELETE CASCADE unless foreign keys are switched on.
        await db.execute(delete(AIConversationMessage).where(AIConversationMessage.conversation_id.in_(expired_conversations)))
        await db.execute(delete(AIConversation).where(_conversation_filter(now)))
        await db.execute(delete(AIPendingAction).where(_pending_action_filter(now)))
        await db.commit()
        await _audit_purge(operator, {"conversations": conversations, "conversation_messages": messages,
                                      "pending_actions": actions})

    report = RetentionReport(applied=apply, now=now.isoformat(), conversations=conversations,
                             conversation_messages=messages, pending_actions=actions,
                             audit_events_past_retention=audit_old, pending_actions_needing_reconciliation=needs_reconciliation)
    logger.info("ai.retention.run", **report.as_dict(), operator_employee_id=operator_employee_id if apply else None)
    return report


CHECK_DUE_EXIT_CODE = 3  # same convention as scripts/stale_actions_report.py


def check_exit_code(report: RetentionReport) -> int:
    """AIS-17: exit status of `retention_purge.py --check`: 3 when conversations, messages or pending actions are past
    their retention (a purge is due), else 0. Audit events past retention are informational (they are never deleted
    by tooling) and EXECUTING/UNKNOWN actions are reported by stale_actions_report.py."""
    due = report.conversations + report.conversation_messages + report.pending_actions
    return CHECK_DUE_EXIT_CODE if due else 0


async def _audit_purge(operator: tuple[int, str], counts: dict[str, int]) -> None:
    """Append RETENTION_PURGE events (one per table, counts only). Fail-closed is not needed: the purge is done; a
    write failure falls back to the structured log like every other audit write."""
    from app.services.ai.audit import recorder, store
    from app.services.ai.audit.events import AuditEventType, AuditStatus

    scope = recorder.new_scope()
    token = recorder.activate(scope)
    try:
        recorder.bind_user(*operator)
        for table, count in counts.items():
            recorder.record(AuditEventType.RETENTION_PURGE, AuditStatus.SUCCESS, operation=f"retention:{table}", row_count=count)
    finally:
        recorder.deactivate(token)
    await store.flush(scope)
