"""The only writer of ai_audit_events: INSERT only, in its own session (independent of the request's
business session, so a request rollback cannot erase evidence and audit I/O cannot hold business locks)."""

import json

import structlog

from app.db.session import SessionLocal
from app.models.ai_audit_event import AIAuditEvent
from app.services.ai.audit.recorder import AuditEvent, AuditScope

logger = structlog.get_logger()


def _row(scope: AuditScope, event: AuditEvent) -> AIAuditEvent:
    return AIAuditEvent(
        event_id=event.event_id,
        request_id=scope.request_id,
        sequence=event.sequence,
        conversation_id=scope.conversation_id,
        employee_id=scope.employee_id,
        event_type=event.event_type.value,
        status=event.status.value,
        route=event.route,
        duration_ms=event.duration_ms,
        metadata_json=json.dumps(event.metadata.model_dump(mode="json", exclude_none=True), sort_keys=True),
        created_at=event.created_at,
    )


async def write(scope: AuditScope, events: list[AuditEvent]) -> None:
    """Insert events (raises on failure; callers decide fail-open vs fail-closed)."""
    if not events:
        return
    async with SessionLocal() as session:
        session.add_all([_row(scope, event) for event in events])
        await session.commit()


async def flush(scope: AuditScope) -> None:
    """Persist the scope's unpersisted events. Never raises: on failure the same sanitized events go to
    the structured log (`ai.audit.fallback`) so evidence - including denials - is not discarded."""
    pending = [event for event in scope.events if not event.persisted]
    if not pending:
        return
    try:
        await write(scope, pending)
        for event in pending:
            event.persisted = True
    except Exception as exc:  # noqa: BLE001 - audit storage failure must not change the request outcome
        logger.error("ai.audit.write_failed", request_id=scope.request_id, events=len(pending), error=type(exc).__name__)
        for event in pending:
            logger.warning("ai.audit.fallback", request_id=scope.request_id, event_id=event.event_id,
                           sequence=event.sequence, employee_id=scope.employee_id, conversation_id=scope.conversation_id,
                           event_type=event.event_type.value, status=event.status.value, route=event.route,
                           duration_ms=event.duration_ms, **event.metadata.model_dump(mode="json", exclude_none=True))
