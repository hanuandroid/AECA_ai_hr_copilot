"""Read-only audit queries and metrics. Filters are closed (typed parameters only, no expressions), results
are paginated, and the caller's scope is decided by the Phase 2 matrix (AUDIT_READ_ORG vs AUDIT_READ_OWN)."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_audit_event import AIAuditEvent
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.authorization import AIUserContext, Capability, authorize, require
from app.services.ai.errors import AIPermissionDeniedError

MAX_PAGE = 100
METRICS_SAMPLE = 10_000  # latest request events used for latency percentiles
AUDIT_API_PREFIX = "/api/v1/ai/"


@dataclass(frozen=True)
class AuditFilters:
    event_type: AuditEventType | None = None
    status: AuditStatus | None = None
    route: str | None = None
    request_id: str | None = None
    conversation_id: str | None = None
    employee_id: int | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


def audit_scope_for(ctx: AIUserContext, requested_employee_id: int | None) -> int | None:
    """None = organisation-wide (AUDIT_READ_ORG); otherwise the only employee id the caller may see."""
    if authorize(ctx, Capability.AUDIT_READ_ORG).allowed:
        return requested_employee_id
    require(ctx, Capability.AUDIT_READ_OWN)
    if requested_employee_id is not None and requested_employee_id != ctx.employee_id:
        raise AIPermissionDeniedError()
    return ctx.employee_id


def _filtered(query: Select, filters: AuditFilters, employee_id: int | None) -> Select:
    conditions = []
    if employee_id is not None:
        conditions.append(AIAuditEvent.employee_id == employee_id)
    if filters.event_type is not None:
        conditions.append(AIAuditEvent.event_type == filters.event_type.value)
    if filters.status is not None:
        conditions.append(AIAuditEvent.status == filters.status.value)
    if filters.route is not None:
        conditions.append(AIAuditEvent.route == filters.route)
    if filters.request_id is not None:
        conditions.append(AIAuditEvent.request_id == filters.request_id)
    if filters.conversation_id is not None:
        conditions.append(AIAuditEvent.conversation_id == filters.conversation_id)
    if filters.created_from is not None:
        conditions.append(AIAuditEvent.created_at >= filters.created_from)
    if filters.created_to is not None:
        conditions.append(AIAuditEvent.created_at <= filters.created_to)
    return query.where(*conditions) if conditions else query


def event_view(row: AIAuditEvent) -> dict[str, Any]:
    return {
        "event_id": row.event_id, "request_id": row.request_id, "sequence": row.sequence,
        "conversation_id": row.conversation_id, "employee_id": row.employee_id, "event_type": row.event_type,
        "status": row.status, "route": row.route, "duration_ms": row.duration_ms,
        "metadata": json.loads(row.metadata_json or "{}"), "created_at": row.created_at.isoformat(),
    }


async def list_events(db: AsyncSession, filters: AuditFilters, employee_id: int | None, *, limit: int,
                      offset: int) -> tuple[list[dict[str, Any]], int]:
    total = (await db.execute(_filtered(select(func.count(AIAuditEvent.id)), filters, employee_id))).scalar_one()
    rows = (await db.execute(
        _filtered(select(AIAuditEvent), filters, employee_id)
        .order_by(AIAuditEvent.created_at.desc(), AIAuditEvent.id.desc())
        .limit(min(limit, MAX_PAGE)).offset(offset)
    )).scalars()
    return [event_view(row) for row in rows], total


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)], 2)


def _latency(values: list[float]) -> dict[str, Any]:
    return {"count": len(values), "avg": round(sum(values) / len(values), 2) if values else None,
            "p95": _percentile(values, 0.95), "max": round(max(values), 2) if values else None}


async def metrics(db: AsyncSession, created_from: datetime | None, created_to: datetime | None) -> dict[str, Any]:
    window = AuditFilters(created_from=created_from, created_to=created_to)
    counts: dict[tuple[str, str], int] = {
        (event_type, status): count for event_type, status, count in (await db.execute(
            _filtered(select(AIAuditEvent.event_type, AIAuditEvent.status, func.count(AIAuditEvent.id)), window, None)
            .group_by(AIAuditEvent.event_type, AIAuditEvent.status)
        )).all()
    }

    def count(event_type: AuditEventType, status: AuditStatus | None = None) -> int:
        return sum(n for (t, s), n in counts.items() if t == event_type.value and (status is None or s == status.value))

    requests = (await db.execute(
        _filtered(select(AIAuditEvent.status, AIAuditEvent.duration_ms, AIAuditEvent.metadata_json), window, None)
        .where(AIAuditEvent.event_type == AuditEventType.AI_HTTP_REQUEST.value)
        .order_by(AIAuditEvent.id.desc()).limit(METRICS_SAMPLE)
    )).all()
    # Audit/metrics reads are themselves audited but are not AI requests.
    ai_requests = [(status, duration) for status, duration, meta in requests
                   if not json.loads(meta or "{}").get("endpoint", "").startswith(AUDIT_API_PREFIX)]

    providers = (await db.execute(
        _filtered(select(AIAuditEvent.duration_ms, AIAuditEvent.metadata_json), window, None)
        .where(AIAuditEvent.event_type == AuditEventType.AI_PROVIDER_RESPONSE.value)
        .order_by(AIAuditEvent.id.desc()).limit(METRICS_SAMPLE)
    )).all()
    provider_meta = [json.loads(meta or "{}") for _, meta in providers]
    input_tokens = [m["input_tokens"] for m in provider_meta if m.get("input_tokens") is not None]
    output_tokens = [m["output_tokens"] for m in provider_meta if m.get("output_tokens") is not None]
    costs = [m["estimated_cost_usd"] for m in provider_meta if m.get("estimated_cost_usd") is not None]

    usage = await _usage(db, window, [meta for _, _, meta in requests
                                       if not json.loads(meta or "{}").get("endpoint", "").startswith(AUDIT_API_PREFIX)])
    refusal_reasons: dict[str, int] = {}
    for (meta,) in (await db.execute(
        _filtered(select(AIAuditEvent.metadata_json), window, None)
        .where(AIAuditEvent.event_type == AuditEventType.REFUSAL.value).limit(METRICS_SAMPLE)
    )).all():
        reason = json.loads(meta or "{}").get("reason_code", "UNKNOWN")
        refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1

    return {
        "window": {"created_from": created_from.isoformat() if created_from else None,
                   "created_to": created_to.isoformat() if created_to else None},
        "requests_total": len(ai_requests),
        "requests_success": sum(1 for status, _ in ai_requests if status == AuditStatus.SUCCESS.value),
        "requests_failed": sum(1 for status, _ in ai_requests if status == AuditStatus.FAILED.value),
        "requests_denied": sum(1 for status, _ in ai_requests if status == AuditStatus.DENIED.value),
        "requests_refused": count(AuditEventType.REFUSAL),
        "requests_clarification": count(AuditEventType.CLARIFICATION_REQUESTED),
        "authorization_denied": count(AuditEventType.AUTHORIZATION_DENIED),
        "sql_security_rejections": count(AuditEventType.SQL_SECURITY_REJECTION),
        "action_proposals": count(AuditEventType.ACTION_PROPOSED),
        "action_confirmations": count(AuditEventType.ACTION_CONFIRMED),
        "action_executions": count(AuditEventType.ACTION_EXECUTED),
        "action_rejections": count(AuditEventType.ACTION_REJECTED),
        "action_expirations": count(AuditEventType.ACTION_EXPIRED),
        "provider_calls": count(AuditEventType.AI_PROVIDER_REQUEST),
        "provider_errors": count(AuditEventType.AI_PROVIDER_ERROR),
        "ai_errors": count(AuditEventType.AI_ERROR),
        "refusals_by_reason": dict(sorted(refusal_reasons.items())),
        "latency_ms": _latency([d for _, d in ai_requests if d is not None]),
        "provider_latency_ms": _latency([d for d, _ in providers if d is not None]),
        "tokens": {"input": sum(input_tokens) if input_tokens else None,
                   "output": sum(output_tokens) if output_tokens else None,
                   "calls_with_usage": len(input_tokens)},
        # Estimated only from configured pricing and reported usage; null when either is missing. Not billing.
        "estimated_cost_usd": round(sum(costs), 6) if costs else None,
        **usage,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _increment(counter: dict[str, int], key: object) -> None:
    if isinstance(key, str) and key:
        counter[key] = counter.get(key, 0) + 1


async def _usage(db: AsyncSession, window: AuditFilters, request_meta: list[str]) -> dict[str, Any]:
    """AI usage dashboard figures (Phase 10). Operational metrics only, derived from existing audit events."""

    async def metadata(event_type: AuditEventType, status: AuditStatus | None = None) -> list[dict[str, Any]]:
        query = _filtered(select(AIAuditEvent.metadata_json), window, None).where(AIAuditEvent.event_type == event_type.value)
        if status is not None:
            query = query.where(AIAuditEvent.status == status.value)
        return [json.loads(meta or "{}") for (meta,) in (await db.execute(query.limit(METRICS_SAMPLE))).all()]

    by_role: dict[str, int] = {}
    for meta in request_meta:
        _increment(by_role, json.loads(meta or "{}").get("actor_role"))

    intents: dict[str, int] = {}
    for meta in await metadata(AuditEventType.CHAT_CLASSIFIED, AuditStatus.SUCCESS):
        for intent in meta.get("intents") or []:
            _increment(intents, intent)

    actions: dict[str, int] = {}
    proposals = await metadata(AuditEventType.ACTION_PROPOSED)
    for meta in proposals:
        _increment(actions, meta.get("action"))

    policy_answers = await metadata(AuditEventType.POLICY_RESPONSE, AuditStatus.SUCCESS)
    no_answer = sum(1 for meta in policy_answers if meta.get("grounded") is False)
    confirmed = len(await metadata(AuditEventType.ACTION_CONFIRMED))
    executed = len(await metadata(AuditEventType.ACTION_EXECUTED))
    failed_after_confirm = sum(1 for meta in await metadata(AuditEventType.ACTION_REJECTED)
                               if meta.get("confirmation_state") == "FAILED")
    tools = {
        "policy_rag": len(await metadata(AuditEventType.POLICY_REQUEST)),
        "sql_agent": len(await metadata(AuditEventType.SQL_REQUEST)),
        "action_agent": len(proposals),
    }
    return {
        "requests_by_role": dict(sorted(by_role.items())),
        "intents": dict(sorted(intents.items(), key=lambda item: (-item[1], item[0]))),
        "tools": tools,
        "actions_proposed_by_type": dict(sorted(actions.items(), key=lambda item: (-item[1], item[0]))),
        "policy_answers": len(policy_answers),
        "rag_no_answer_rate": _rate(no_answer, len(policy_answers)),
        "action_confirmation_rate": _rate(confirmed, len(proposals)),
        "action_execution": {"succeeded": executed, "failed": failed_after_confirm,
                             "success_rate": _rate(executed, executed + failed_after_confirm)},
    }
