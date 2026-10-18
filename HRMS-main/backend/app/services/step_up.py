"""Password step-up for high-impact operations (G-11, extends AIS-12 to the business API).

A valid access token is not enough for operations that let a stolen token take over or lock out accounts, create
standing access or reach the whole organisation. The caller re-enters their own current password in the
`X-Step-Up-Password` header. Shared by the business endpoints and the Copilot's confirmation path.

- Authorization is always checked first (the endpoints call this after their existing role and validation checks), so
  an unauthorized caller gets the same answer as before and never reaches a password check.
- Called before any mutation. A refused step-up changes nothing.
- Missing password: decided without hashing and without using the attempt budget. Otherwise each attempt counts against
  `step_up_limiter` (its own instance, never shared with sign-in keys), BEFORE bcrypt.
- STEP_UP_ENFORCEMENT: `off` (no check), `log` (check and log the outcome, never block; the initial rollout mode) or
  `enforce` (403 STEP_UP_REQUIRED / STEP_UP_FAILED, 429 STEP_UP_THROTTLED).
- The password is never logged, stored, audited or echoed. Logs carry the operation, the employee id and the outcome.
"""

import enum

import structlog
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.rate_limit import step_up_limiter
from app.core.response import error_response
from app.core.security import verify_password
from app.models.employee import Employee

logger = structlog.get_logger()

STEP_UP_HEADER = "X-Step-Up-Password"
MAX_STEP_UP_PASSWORD_CHARS = 128  # the account password policy's maximum length


class StepUpResult(str, enum.Enum):
    OK = "OK"
    MISSING = "MISSING"
    INVALID = "INVALID"
    THROTTLED = "THROTTLED"


def check_step_up(employee_id: int, hashed_password: str | None, password: object, *, key_prefix: str) -> StepUpResult:
    """The outcome of one step-up attempt by `employee_id` (no side effects other than the attempt counter)."""
    if password is None or password == "":
        return StepUpResult.MISSING
    if not step_up_limiter.allow(f"{key_prefix}|{employee_id}", settings.auth_login_rate_limit_per_minute):
        return StepUpResult.THROTTLED
    if (not isinstance(password, str) or len(password) > MAX_STEP_UP_PASSWORD_CHARS or not hashed_password
            or not verify_password(password, hashed_password)):
        return StepUpResult.INVALID
    return StepUpResult.OK


_REFUSALS = {
    StepUpResult.MISSING: (status.HTTP_403_FORBIDDEN, "STEP_UP_REQUIRED",
                           "Re-enter your password to confirm this operation."),
    StepUpResult.INVALID: (status.HTTP_403_FORBIDDEN, "STEP_UP_FAILED",
                           "Your password could not be verified. Nothing was changed."),
    StepUpResult.THROTTLED: (status.HTTP_429_TOO_MANY_REQUESTS, "STEP_UP_THROTTLED",
                             "Too many password attempts. Please wait a minute and try again."),
}


def enforce_step_up(current_user: Employee, password: str | None, operation: str) -> None:
    """For business endpoints: call after authorization and validation, before any change. Raises only in `enforce`."""
    mode = settings.step_up_enforcement
    if mode == "off":
        return
    result = check_step_up(current_user.id, current_user.hashed_password, password, key_prefix="api-step-up")
    if result == StepUpResult.OK:
        return
    logger.warning("auth.step_up.not_satisfied", operation=operation, employee_id=current_user.id,
                   outcome=result.value, mode=mode)
    if mode != "enforce":
        return
    http_status, code, message = _REFUSALS[result]
    raise HTTPException(status_code=http_status, detail=error_response(code, message))
