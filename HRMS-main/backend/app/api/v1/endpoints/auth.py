from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import login_limiter
from app.core.response import error_response, success_response
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from app.schemas.auth import LoginRequest

router = APIRouter()


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
        "access_token": create_access_token(subject=str(user.id), role=user.role.value),
        "refresh_token": create_refresh_token(subject=str(user.id)),
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
        "access_token": create_access_token(subject=str(user.id), role=user.role.value),
        "token_type": "bearer",
    }
