"""ActionAgentService. The LLM proposes actions; the backend authorizes and executes them.

propose(ctx, question)
    active + at least one action capability       (before any LLM call; inactive -> 403)
    LLM -> ActionProposal                           (untrusted; only role-allowed actions are offered)
    registry lookup (ActionName -> fixed route)     (unknown action -> invalid output)
    run_authorized(ctx, spec.capability, ...)       (capability)
      resolve target within scope + require(target) (target-level authorization, no existence oracle)
      validate typed parameters                     (ambiguity / missing data -> clarification, never a guess)
      ConfirmationService.create                    (all data-changing actions require confirmation)
    -> confirmation_required {summary, confirmation_id, expires_at}

confirm(ctx, confirmation_id, access_token)
    load + verify server record                     (user-, action-, target-, payload-bound; TTL; unused)
    run_authorized(ctx, spec.capability, ..., TargetEmployee)   (re-checked at execution time)
      claim (atomic, single use) -> HRMSActionClient (existing API, caller's own token) -> result
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import time

import structlog
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import login_limiter
from app.core.security import verify_password
from app.db.session import get_db
from app.models.employee import Employee
from app.services.ai.actions.client import HRMSActionClient
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.provider import observe
from app.services.ai.audit.recorder import record, record_critical
from app.services.ai.actions.confirmation import ConfirmationService
from app.services.ai.actions.models import ActionName, ActionProposal, ActionResponse
from app.services.ai.actions.prompts import ACTION_SYSTEM_PROMPT, build_action_message
from app.services.ai.actions.registry import ACTION_REGISTRY, ActionSpec
from app.services.ai.actions.resolver import ClarificationNeeded, TargetResolver
from app.services.ai.actions.validators import prepare_action
from app.services.ai.authorization import AIUserContext, TargetEmployee, grant_for, run_authorized
from app.services.ai.errors import (
    AIActionOutcomeUnknownError,
    AIBackendUnavailableError,
    AIConfirmationInvalidError,
    AIError,
    AIInactiveUserError,
    AIPermissionDeniedError,
    AIStepUpFailedError,
    AIStepUpThrottledError,
)
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.providers import get_llm_provider
from app.services.ai.redaction import redact
from app.services.ai.validation import validate_question

logger = structlog.get_logger()

COMPLETED_MESSAGES = {
    ActionName.LEAVE_CREATE: "Your leave request has been submitted.",
    ActionName.LEAVE_APPROVE: "Leave request approved.",
    ActionName.LEAVE_REJECT: "Leave request rejected.",
    ActionName.TICKET_CREATE: "Your ticket has been created.",
    ActionName.TICKET_ASSIGN: "Ticket assigned.",
    ActionName.TICKET_STATUS_UPDATE: "Ticket status updated.",
    ActionName.ANNOUNCEMENT_CREATE: "Announcement published.",
    ActionName.PROJECT_ASSIGN: "Employee assigned to the project.",
    ActionName.PROJECT_CREATE: "Project created.",
    ActionName.EMPLOYEE_DEACTIVATE: "Employee account deactivated.",
}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # stored as naive UTC (SQLite DateTime)


@dataclass
class ActionAuditRecord:
    """Audit-ready summary of one action step (Phase 8 will persist it). No bodies, tokens or PII values."""

    employee_id: int
    role: str
    stage: str  # proposed | confirmed
    action: str | None = None
    target_employee_id: int | None = None
    resource_id: int | None = None
    confirmation_record_id: int | None = None
    outcome: str = "pending"
    error_code: str | None = None
    expired: bool = False
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())
    started: float = field(default_factory=time.perf_counter, repr=False)


class ActionAgentService:
    def __init__(
        self,
        *,
        db: AsyncSession,
        llm_factory: Callable[[], LLMProvider],
        client_factory: Callable[[str], HRMSActionClient],
        ttl_seconds: int,
        now: Callable[[], datetime] = utcnow,
        today: Callable[[], date] = date.today,
    ):
        self._db = db
        self._llm_factory = llm_factory
        self._client_factory = client_factory
        self._confirmations = ConfirmationService(db, ttl_seconds=ttl_seconds, now=now)
        self._today = today
        self.last_audit: ActionAuditRecord | None = None

    def allowed_actions(self, ctx: AIUserContext) -> list[ActionSpec]:
        return [spec for spec in ACTION_REGISTRY.values() if grant_for(ctx.role, spec.capability) is not None]

    # --- propose ------------------------------------------------------------------------------

    async def propose(self, ctx: AIUserContext, question: object) -> ActionResponse:
        audit = self._audit(ctx, "proposed")
        try:
            if not ctx.is_active:
                raise AIInactiveUserError()
            allowed = self.allowed_actions(ctx)
            if not allowed:
                raise AIPermissionDeniedError()
            cleaned = validate_question(question)
            proposal = await observe(self._llm_factory(), "action_proposal").generate_structured(
                [LLMMessage(role="user", content=build_action_message(cleaned, allowed, self._today()))],
                ActionProposal,
                system=ACTION_SYSTEM_PROMPT,
            )
            if proposal.action == "NONE" or proposal.needs_clarification:
                audit.outcome = "clarification"
                return ActionResponse(status="clarification_required", message=_clarification_text(proposal, allowed))

            spec = ACTION_REGISTRY[ActionName(proposal.action)]
            audit.action = spec.name.value
            response = await run_authorized(ctx, spec.capability, lambda: self._prepare(ctx, spec, proposal, audit))
            return response
        except ClarificationNeeded as clarification:
            audit.outcome = "clarification"
            return ActionResponse(status="clarification_required", message=clarification.message, action=audit.action)
        except AIError as error:
            audit.outcome, audit.error_code = "rejected", error.code
            raise
        finally:
            self._log(audit)

    async def _prepare(self, ctx: AIUserContext, spec: ActionSpec, proposal: ActionProposal, audit: ActionAuditRecord) -> ActionResponse:
        prepared = await prepare_action(spec, proposal, ctx, TargetResolver(self._db, ctx))
        token, record = await self._confirmations.create(ctx, prepared)
        audit.target_employee_id, audit.resource_id = prepared.target_employee_id, prepared.resource_id
        audit.confirmation_record_id, audit.outcome = record.id, "confirmation_required"
        return ActionResponse(
            status="confirmation_required",
            message="Please confirm this action.",
            action=spec.name.value,
            summary=prepared.summary,
            confirmation_id=token,
            expires_at=record.expires_at.isoformat() + "Z",
            step_up_required=True if spec.step_up else None,
        )

    # --- confirm ------------------------------------------------------------------------------

    async def confirm(self, ctx: AIUserContext, confirmation_id: object, access_token: str, *,
                      password: object = None) -> ActionResponse:
        audit = self._audit(ctx, "confirmed")
        try:
            if not ctx.is_active:
                raise AIInactiveUserError()
            record, prepared = await self._confirmations.load_valid(ctx, confirmation_id)
            spec = ACTION_REGISTRY[prepared.action]
            audit.action, audit.confirmation_record_id = spec.name.value, record.id
            audit.target_employee_id, audit.resource_id = record.target_employee_id, record.resource_id
            resource = TargetEmployee(record.target_employee_id) if record.target_employee_id else None
            try:
                return await run_authorized(
                    ctx, spec.capability, lambda: self._execute(record.id, spec, prepared, access_token, audit,
                                                                step_up=(ctx.employee_id, password)),
                    resource=resource,
                )
            except AIPermissionDeniedError:
                await self._confirmations.finish(record.id, "DENIED", "AI_PERMISSION_DENIED")
                raise
        except AIError as error:
            if audit.outcome != "unknown":  # keep the B-10 unknown-outcome classification
                audit.outcome = "rejected"
            audit.error_code = error.code
            audit.expired = isinstance(error, AIConfirmationInvalidError) and error.detail == "expired"
            raise
        finally:
            self._log(audit)

    async def _execute(self, record_id: int, spec: ActionSpec, prepared, access_token: str, audit: ActionAuditRecord,
                       *, step_up: tuple[int, object]) -> ActionResponse:
        # AIS-12: after authorization (above), before the claim: a refused password leaves the confirmation PENDING.
        if spec.step_up:
            await self._verify_step_up(*step_up)
        # B-12: claim first (atomic, single use). Only the request that wins the claim records ACTION_CONFIRMED, so a
        # replay that loses the race leaves no "confirmed" evidence (it is audited as ACTION_REJECTED instead).
        await self._confirmations.claim(record_id)
        # Fail-closed: the mutation runs only if the won confirmation is durably audited first. If that write fails,
        # nothing has executed, so the claim is released back to PENDING (atomically): the user can retry once auditing
        # works again, and execution stays single-use (a concurrent replay already lost the claim).
        try:
            await record_critical(AuditEventType.ACTION_CONFIRMED, AuditStatus.SUCCESS, route="ACTION", action=spec.name.value,
                                  confirmation_record_id=record_id, target_employee_id=prepared.target_employee_id,
                                  resource_id=prepared.resource_id, confirmation_state="CONFIRMED")
        except AIError:
            await self._confirmations.release(record_id)
            raise
        try:
            outcome = await self._client_factory(access_token).execute(prepared)
        except AIActionOutcomeUnknownError:
            # B-10: deadline exceeded. The business handler may have committed: record UNKNOWN, never retry.
            await self._confirmations.finish(record_id, "UNKNOWN", AIActionOutcomeUnknownError.code)
            audit.outcome = "unknown"
            raise
        except AIBackendUnavailableError:
            await self._confirmations.finish(record_id, "FAILED", AIBackendUnavailableError.code)
            raise
        if outcome.ok:
            await self._confirmations.finish(record_id, "COMPLETED", None)
            audit.outcome = "completed"
            return ActionResponse(status="completed", message=_completed_message(spec, outcome.data), action=spec.name.value,
                                  summary=prepared.summary, result=_result_view(outcome.data))
        await self._confirmations.finish(record_id, "FAILED", outcome.error_code)
        audit.outcome, audit.error_code = "failed", outcome.error_code
        return ActionResponse(status="failed", message=outcome.message or "The HR system rejected this request.",
                              action=spec.name.value, summary=prepared.summary, error_code=outcome.error_code)

    # --- helpers ------------------------------------------------------------------------------

    async def _verify_step_up(self, employee_id: int, password: object) -> None:
        """The confirming user re-enters their own password. Throttled per user (login limiter, before any hashing);
        the password is never logged, stored or audited."""
        if not login_limiter.allow(f"ai-step-up|{employee_id}", settings.auth_login_rate_limit_per_minute):
            raise AIStepUpThrottledError()
        hashed = (await self._db.execute(select(Employee.hashed_password).where(Employee.id == employee_id))).scalar_one_or_none()
        if (not isinstance(password, str) or not password or len(password) > MAX_STEP_UP_PASSWORD_CHARS
                or not hashed or not verify_password(password, hashed)):
            raise AIStepUpFailedError()

    @staticmethod
    def _audit(ctx: AIUserContext, stage: str) -> ActionAuditRecord:
        return ActionAuditRecord(employee_id=ctx.employee_id, role=ctx.role.value, stage=stage)

    def _log(self, audit: ActionAuditRecord) -> None:
        self.last_audit = audit
        logger.info("ai.action", **{key: value for key, value in asdict(audit).items() if key != "started"})
        _record_action_events(audit)


_DENIAL_CODES = {"AI_PERMISSION_DENIED", "AI_USER_INACTIVE", "AI_TARGET_NOT_FOUND", "AI_CONFIRMATION_INVALID",
                 "AI_STEP_UP_FAILED", "AI_STEP_UP_THROTTLED"}
MAX_STEP_UP_PASSWORD_CHARS = 128  # the account password policy's maximum length


def _record_action_events(audit: ActionAuditRecord) -> None:
    """Lifecycle events from what actually happened. ACTION_EXECUTED only after the HRMS API succeeded."""
    duration = (time.perf_counter() - audit.started) * 1000
    ids = {"action": audit.action, "target_employee_id": audit.target_employee_id, "resource_id": audit.resource_id,
           "confirmation_record_id": audit.confirmation_record_id}
    if audit.outcome == "clarification":
        record(AuditEventType.CLARIFICATION_REQUESTED, AuditStatus.CLARIFICATION_REQUIRED, route="ACTION",
               duration_ms=duration, action=audit.action)
    elif audit.outcome == "confirmation_required":
        record(AuditEventType.ACTION_PROPOSED, AuditStatus.SUCCESS, route="ACTION", duration_ms=duration, **ids)
        record(AuditEventType.ACTION_CONFIRMATION_REQUESTED, AuditStatus.CONFIRMATION_REQUIRED, route="ACTION",
               confirmation_state="PENDING", **ids)
    elif audit.outcome == "completed":
        record(AuditEventType.ACTION_EXECUTED, AuditStatus.SUCCESS, route="ACTION", duration_ms=duration,
               confirmation_state="COMPLETED", **ids)
    elif audit.outcome == "unknown":
        # B-10: neither success nor confirmed failure - the business call exceeded its deadline and may have applied.
        record(AuditEventType.ACTION_OUTCOME_UNKNOWN, AuditStatus.FAILED, route="ACTION", duration_ms=duration,
               error_code=audit.error_code, confirmation_state="UNKNOWN", **ids)
    elif audit.expired:
        record(AuditEventType.ACTION_EXPIRED, AuditStatus.FAILED, route="ACTION", duration_ms=duration,
               error_code=audit.error_code, confirmation_state="EXPIRED", **ids)
    elif audit.outcome in ("rejected", "failed"):
        denied = audit.error_code in _DENIAL_CODES
        record(AuditEventType.ACTION_REJECTED, AuditStatus.DENIED if denied else AuditStatus.FAILED, route="ACTION",
               duration_ms=duration, error_code=audit.error_code,
               confirmation_state="FAILED" if audit.outcome == "failed" else None, **ids)


def _clarification_text(proposal: ActionProposal, allowed: list[ActionSpec]) -> str:
    if proposal.needs_clarification and proposal.clarification_question:
        return redact(proposal.clarification_question, max_length=300)
    names = ", ".join(spec.name.value.replace("_", " ").lower() for spec in allowed)
    return f"I can help you with these actions: {names}. What would you like to do?"


def _completed_message(spec: ActionSpec, data: dict[str, Any] | None) -> str:
    message = COMPLETED_MESSAGES[spec.name]
    status = (data or {}).get("status")
    if spec.name == ActionName.LEAVE_CREATE and status:
        message += f" Status: {status}."
    return message


_RESULT_KEYS = ("id", "status", "leave_type", "start_date", "end_date", "title", "priority", "category",
                "assignee_id", "employee_id", "project_id", "role_on_project", "name")


def _result_view(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    return {key: data[key] for key in _RESULT_KEYS if key in data}


def get_action_agent_service(request: Request, db: AsyncSession = Depends(get_db)) -> ActionAgentService:
    """FastAPI dependency. The client targets the same running app (request.app) with the caller's token."""
    app = request.app
    return ActionAgentService(
        db=db,
        llm_factory=get_llm_provider,
        client_factory=lambda token: HRMSActionClient(app, token, timeout_seconds=settings.ai_action_execution_timeout_seconds),
        ttl_seconds=settings.ai_action_confirmation_ttl_seconds,
    )
