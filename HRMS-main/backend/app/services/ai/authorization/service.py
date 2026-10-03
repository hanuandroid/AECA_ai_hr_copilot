"""The authoritative AI authorization API.

authorize(ctx, capability, resource) -> AuthorizationResult     (never raises)
require(ctx, capability, resource)                              (raises AIPermissionDeniedError)
run_authorized(ctx, capability, operation, resource=...)        (checks BEFORE running operation)

Inputs are deliberately narrow: an AIUserContext built from the DB, a Capability enum member
and an optional TargetEmployee. There is no parameter through which an LLM, prompt, tool
argument, retrieved document or frontend claim could assert "authorized"/"role"/"admin".
"""

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import structlog

from app.services.ai.authorization.capabilities import Capability
from app.services.ai.authorization.context import AIUserContext
from app.services.ai.authorization.policy import grant_for
from app.services.ai.authorization.scope import TargetEmployee, target_allowed
from app.services.ai.audit.events import AuditEventType, AuditStatus, ReasonCode
from app.services.ai.audit.recorder import record
from app.services.ai.errors import AIInactiveUserError, AIPermissionDeniedError

logger = structlog.get_logger()
ResultT = TypeVar("ResultT")

GENERIC_DENIAL = "You are not authorized to access this resource."


class DenyReason(str, enum.Enum):
    INACTIVE_USER = "INACTIVE_USER"
    CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_REQUEST = "INVALID_REQUEST"


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    allowed: bool
    capability: str
    reason: DenyReason | None = None

    @property
    def message(self) -> str:
        """User-safe message. Identical for 'no capability' and 'out of scope' so a denial never
        reveals whether a target exists or where it sits in the organisation."""
        if self.allowed:
            return "Allowed."
        if self.reason == DenyReason.INACTIVE_USER:
            return AIInactiveUserError.message
        return GENERIC_DENIAL


def authorize(ctx: AIUserContext, capability: Capability, resource: TargetEmployee | None = None) -> AuthorizationResult:
    capability_name = capability.value if isinstance(capability, Capability) else "INVALID"

    if not isinstance(ctx, AIUserContext) or not isinstance(capability, Capability):
        return _deny(ctx, capability_name, DenyReason.INVALID_REQUEST)
    if resource is not None and not isinstance(resource, TargetEmployee):
        return _deny(ctx, capability_name, DenyReason.INVALID_REQUEST)
    if not ctx.is_active:
        return _deny(ctx, capability_name, DenyReason.INACTIVE_USER)

    rule = grant_for(ctx.role, capability)
    if rule is None:
        return _deny(ctx, capability_name, DenyReason.CAPABILITY_NOT_GRANTED)
    if resource is not None and not target_allowed(ctx, rule, resource.employee_id):
        return _deny(ctx, capability_name, DenyReason.OUT_OF_SCOPE)
    record(AuditEventType.AUTHORIZATION_ALLOWED, AuditStatus.SUCCESS, capability=capability_name, scope=rule.value,
           target_checked=resource is not None)  # passive evidence; the decision above is final
    return AuthorizationResult(allowed=True, capability=capability_name)


def require(ctx: AIUserContext, capability: Capability, resource: TargetEmployee | None = None) -> None:
    result = authorize(ctx, capability, resource)
    if result.allowed:
        return
    if result.reason == DenyReason.INACTIVE_USER:
        raise AIInactiveUserError()
    raise AIPermissionDeniedError(GENERIC_DENIAL)


async def run_authorized(
    ctx: AIUserContext,
    capability: Capability,
    operation: Callable[[], Awaitable[ResultT]],
    *,
    resource: TargetEmployee | None = None,
) -> ResultT:
    """Authorize first; the operation (LLM call, retrieval, SQL, tool) runs only if allowed."""
    require(ctx, capability, resource)
    return await operation()


def _deny(ctx: object, capability: str, reason: DenyReason) -> AuthorizationResult:
    logger.info(
        "ai.authz.denied",
        employee_id=getattr(ctx, "employee_id", None),
        role=getattr(getattr(ctx, "role", None), "value", None),
        capability=capability,
        reason=reason.value,
    )
    record(AuditEventType.AUTHORIZATION_DENIED, AuditStatus.DENIED, capability=capability if capability != "INVALID" else None,
           reason_code=ReasonCode(reason.value))
    return AuthorizationResult(allowed=False, capability=capability, reason=reason)
