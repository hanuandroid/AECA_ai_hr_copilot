from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from app.services.ai.errors import AIInactiveUserError
from app.services.auth import get_current_user


@dataclass(frozen=True, slots=True)
class AIUserContext:
    """Immutable identity for every AI operation, built only from trusted server-side DB state.

    CB Nest has no separate user table (the Employee row is the user) and no organisation
    concept (single tenant), so there is no user_id/organization_id distinct from employee_id.
    Nothing here may come from the prompt, the LLM, tool arguments or frontend claims.
    """

    employee_id: int
    role: Role
    is_active: bool
    manager_id: int | None
    department_id: int | None
    direct_report_ids: frozenset[int]

    @property
    def team_ids(self) -> frozenset[int]:
        """Caller plus direct reports (employees.manager_id == caller)."""
        return self.direct_report_ids | {self.employee_id}


async def build_ai_user_context(employee: Employee, db: AsyncSession) -> AIUserContext:
    report_ids = (await db.execute(select(Employee.id).where(Employee.manager_id == employee.id))).scalars().all()
    return AIUserContext(
        employee_id=employee.id,
        role=Role(employee.role),
        is_active=employee.status == EmployeeStatus.ACTIVE,
        manager_id=employee.manager_id,
        department_id=employee.department_id,
        direct_report_ids=frozenset(report_ids),
    )


async def get_ai_user_context(
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AIUserContext:
    """FastAPI dependency for every AI endpoint.

    Identity comes from the existing JWT dependency (role re-loaded from the DB). INACTIVE
    users are rejected here, before any retrieval, model call, SQL or tool execution (R9).
    """
    from app.services.ai.audit.events import AuditEventType, AuditStatus, ReasonCode
    from app.services.ai.audit.recorder import bind_user, record

    # Audit identity comes only from the authenticated, DB-loaded employee (role kept as historical metadata).
    bind_user(current_user.id, getattr(current_user.role, "value", str(current_user.role)))
    if current_user.status != EmployeeStatus.ACTIVE:
        record(AuditEventType.AUTHORIZATION_DENIED, AuditStatus.DENIED, capability="AI_ACCESS",
               reason_code=ReasonCode.INACTIVE_USER)
        raise AIInactiveUserError()
    return await build_ai_user_context(current_user, db)
