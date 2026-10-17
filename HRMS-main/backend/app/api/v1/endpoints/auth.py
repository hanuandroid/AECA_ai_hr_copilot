import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import login_limiter
from app.core.response import error_response, success_response
from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from app.schemas.auth import ChangePasswordRequest, LoginRequest
from app.services.auth import get_current_user

router = APIRouter()
logger = structlog.get_logger()


def _throttle_login(request: Request, identifier: str) -> None:
    """Production hardening B-09: at most AUTH_LOGIN_RATE_LIMIT_PER_MINUTE attempts per client IP + normalized account
    identifier, checked before the DB lookup and password hashing. Nothing about the attempt (password) is logged."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}|{identifier.strip().lower()}"
    if not login_limiter.allow(key, settings.auth_login_rate_limit_per_minute):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_response("TOO_MANY_ATTEMPTS", "Too many sign-in attempts. Please wait a minute and try again."),
        )


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    _throttle_login(request, payload.email)
    result = await db.execute(select(Employee).where(Employee.email == payload.email))
    user = result.scalar_one_or_none()

    # Inactive accounts get the same generic error as bad credentials (no account-status disclosure).
    if user is None or not verify_password(payload.password, user.hashed_password) or user.status != EmployeeStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response("INVALID_CREDENTIALS", "Invalid email or password"),
        )

    tokens = {
        "access_token": create_access_token(subject=str(user.id), role=user.role.value, token_version=user.token_version),
        "refresh_token": create_refresh_token(subject=str(user.id), token_version=user.token_version),
        "token_type": "bearer",
    }
    return success_response(tokens)


@router.post("/token")
async def token(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    _throttle_login(request, form_data.username)
    result = await db.execute(select(Employee).where(Employee.email == form_data.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(form_data.password, user.hashed_password) or user.status != EmployeeStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response("INVALID_CREDENTIALS", "Invalid email or password"),
        )

    return {
        "access_token": create_access_token(subject=str(user.id), role=user.role.value, token_version=user.token_version),
        "token_type": "bearer",
    }


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service password change (RBAC step 4, Decision 2).

    - Identity: the authenticated, database-loaded user (inactive users are rejected by get_current_user).
    - Throttled with the login limiter (AUTH_LOGIN_RATE_LIMIT_PER_MINUTE attempts per account per minute, successful
      and failed alike), before any password hashing. The limiter is in-process: it holds for the supported
      single-worker deployment only (see app/core/rate_limit.py).
    - Sessions (B-17, approved 2026-10-17): a successful change increments the user's token_version, so every token
      issued before it - including the one used for this request - is refused from the next request on; the caller
      signs in again with the new password. A failed attempt changes nothing.
    - Nothing about the passwords is logged or returned.
    """
    if not login_limiter.allow(f"change-password|{current_user.id}", settings.auth_login_rate_limit_per_minute):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_response("TOO_MANY_ATTEMPTS", "Too many password change attempts. Please wait a minute and try again."),
        )
    if not verify_password(payload.current_password, current_user.hashed_password):
        logger.warning("auth.password_change_failed", employee_id=current_user.id, reason="INVALID_CURRENT_PASSWORD")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_CURRENT_PASSWORD", "The current password is incorrect"),
        )
    if not payload.new_password.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=error_response("INVALID_NEW_PASSWORD", "The new password must not be blank"))
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=error_response("PASSWORD_UNCHANGED", "The new password must differ from the current one"))

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.token_version = current_user.token_version + 1  # B-17: revoke every earlier token
    await db.commit()
    logger.info("auth.password_changed", employee_id=current_user.id, tokens_revoked=True)
    return success_response({
        "password_changed": True,
        "existing_tokens": "All earlier sessions, including this one, have been signed out. Sign in again with the new password.",
    })
