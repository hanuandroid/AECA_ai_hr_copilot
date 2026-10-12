from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import error_response
from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


@dataclass(frozen=True, slots=True)
class InactiveUserRejection:
    """Server-side record that an authenticated-but-INACTIVE user was rejected (set on request.state only after the
    token was validated and the employee loaded from the DB). Lets observers such as the AI audit middleware attribute
    the denial without trusting any client-supplied identity."""

    employee_id: int
    role: str


INACTIVE_REJECTION_STATE_KEY = "inactive_user_rejection"


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Employee:
    try:
        payload = decode_token(token)
        subject = payload.get("sub")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_response("INVALID_TOKEN", "Token is invalid"))

    # Only access tokens authenticate requests: refresh, missing and unknown types are rejected.
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_response("INVALID_TOKEN", "Token is invalid"))

    if subject is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_response("INVALID_TOKEN", "Token missing subject"))
    try:
        employee_id = int(subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_response("INVALID_TOKEN", "Token is invalid"))

    result = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_response("USER_NOT_FOUND", "User not found"))
    # Deactivated users lose access immediately, including tokens issued before deactivation.
    if employee.status != EmployeeStatus.ACTIVE:
        setattr(request.state, INACTIVE_REJECTION_STATE_KEY,
                InactiveUserRejection(employee_id=employee.id, role=getattr(employee.role, "value", str(employee.role))))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_response("USER_INACTIVE", "This account is not active"))
    return employee


def require_roles(*allowed: Role):
    async def _checker(current_user: Employee = Depends(get_current_user)) -> Employee:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_response("FORBIDDEN", "You do not have required permissions"),
            )
        return current_user

    return _checker
