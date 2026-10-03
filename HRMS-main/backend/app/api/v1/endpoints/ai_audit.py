"""Read-only AI audit API (Phase 8). There are deliberately no POST/PUT/PATCH/DELETE routes: only backend
code creates audit events, and the table is append-only."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success_response
from app.db.session import get_db
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.query import AuditFilters, audit_scope_for, list_events, metrics
from app.services.ai.authorization import AIUserContext, Capability, get_ai_user_context, require

router = APIRouter()


@router.get("/audit")
async def list_audit_events(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    event_type: AuditEventType | None = None,
    status: AuditStatus | None = None,
    route: str | None = Query(default=None, pattern=r"^[A-Z+]{1,60}$"),
    request_id: str | None = Query(default=None, pattern=r"^[0-9a-f]{32}$"),
    conversation_id: str | None = Query(default=None, pattern=r"^[0-9a-f-]{36}$"),
    employee_id: int | None = Query(default=None, ge=1),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    ctx: AIUserContext = Depends(get_ai_user_context),
    db: AsyncSession = Depends(get_db),
):
    """ADMIN (AUDIT_READ_ORG): all events, optionally one employee. Everyone else (AUDIT_READ_OWN): only events of
    their own requests; asking for another employee is denied."""
    scoped_employee = audit_scope_for(ctx, employee_id)
    filters = AuditFilters(event_type=event_type, status=status, route=route, request_id=request_id,
                           conversation_id=conversation_id, created_from=created_from, created_to=created_to)
    items, total = await list_events(db, filters, scoped_employee, limit=limit, offset=offset)
    return success_response({"items": items, "meta": {"total": total, "limit": limit, "offset": offset}})


@router.get("/audit/metrics")
async def audit_metrics(
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    ctx: AIUserContext = Depends(get_ai_user_context),
    db: AsyncSession = Depends(get_db),
):
    """Organisation-wide AI metrics: ADMIN only (AUDIT_READ_ORG)."""
    require(ctx, Capability.AUDIT_READ_ORG)
    return success_response(await metrics(db, created_from, created_to))
