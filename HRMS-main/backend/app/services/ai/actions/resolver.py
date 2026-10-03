"""Target / resource resolution from trusted application data (read-only).

- Every lookup is restricted to the caller's allowed target set for the action's capability
  (Phase 2 target rule: SELF / TEAM / REPORTS / ORG / ORG_EXCEPT_SELF) BEFORE matching, so names,
  ids and statuses of out-of-scope people are never revealed, and "not found" is identical for
  "does not exist" and "outside your scope".
- Names/ids proposed by the model are hints only. Ambiguity never resolves to a guess: it raises
  ClarificationNeeded listing only in-scope candidates.
- This module only SELECTs; it has no write path.
"""

from dataclasses import dataclass

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.enums import EmployeeStatus, LeaveRequestStatus
from app.models.leave_request import LeaveRequest
from app.models.project import Project
from app.models.ticket import Ticket
from app.services.ai.authorization import AIUserContext, Capability, TargetRule, grant_for, hr_read_scope
from app.services.ai.errors import AITargetNotFoundError

MAX_CANDIDATES = 5


class ClarificationNeeded(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ResolvedEmployee:
    id: int
    name: str


def _like(term: str) -> str:
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class TargetResolver:
    def __init__(self, db: AsyncSession, ctx: AIUserContext):
        self._db = db
        self._ctx = ctx

    # --- scope ------------------------------------------------------------------------

    def _target_filter(self, capability: Capability, column):
        """SQL condition restricting `column` (an employee id) to the capability's target rule."""
        rule = grant_for(self._ctx.role, capability)
        me = self._ctx.employee_id
        if rule == TargetRule.SELF:
            return column == me
        if rule == TargetRule.TEAM:
            return column.in_(sorted(self._ctx.team_ids))
        if rule == TargetRule.REPORTS:
            return column.in_(sorted(self._ctx.direct_report_ids - {me}))
        if rule == TargetRule.ORG:
            return column.is_not(None)
        if rule == TargetRule.ORG_EXCEPT_SELF:
            return column != me
        return false()  # no grant -> matches nothing

    # --- employees --------------------------------------------------------------------

    async def employee(
        self, capability: Capability, *, name: str | None, hint_id: int | None, role_label: str = "employee",
        active_only: bool = False,
    ) -> ResolvedEmployee:
        query = select(Employee.id, Employee.name).where(self._target_filter(capability, Employee.id))
        return await self._employee_from(query, name=name, hint_id=hint_id, role_label=role_label, active_only=active_only)

    async def assignee(self, *, name: str | None, hint_id: int | None) -> ResolvedEmployee:
        """Ticket assignee: must be an ACTIVE employee within the caller's HR read scope."""
        scope = hr_read_scope(self._ctx)
        query = select(Employee.id, Employee.name)
        if scope.employee_ids is not None:
            query = query.where(Employee.id.in_(sorted(scope.employee_ids)))
        return await self._employee_from(query, name=name, hint_id=hint_id, role_label="assignee", active_only=True)

    async def _employee_from(self, query, *, name, hint_id, role_label, active_only) -> ResolvedEmployee:
        if active_only:
            query = query.where(Employee.status == EmployeeStatus.ACTIVE)
        if hint_id is not None:
            row = (await self._db.execute(query.where(Employee.id == hint_id))).first()
            if row is None:
                raise AITargetNotFoundError()
            if name and name.strip().lower() not in row.name.lower():
                raise ClarificationNeeded(f"The {role_label} id and name you gave don't match. Which {role_label} do you mean?")
            return ResolvedEmployee(row.id, row.name)
        if not name or not name.strip():
            raise ClarificationNeeded(f"Which {role_label} do you mean?")
        term = name.strip()
        exact = (await self._db.execute(query.where(func.lower(Employee.name) == term.lower()).order_by(Employee.id))).all()
        rows = exact or (
            await self._db.execute(
                query.where(func.lower(Employee.name).like(_like(term), escape="\\")).order_by(Employee.id).limit(MAX_CANDIDATES + 1)
            )
        ).all()
        if not rows:
            raise AITargetNotFoundError()
        if len(rows) > 1:
            names = ", ".join(row.name for row in rows[:MAX_CANDIDATES])
            raise ClarificationNeeded(f"I found more than one matching {role_label} ({names}). Please give the full name.")
        return ResolvedEmployee(rows[0].id, rows[0].name)

    # --- leave requests -----------------------------------------------------------------

    async def pending_leave(
        self, capability: Capability, *, leave_id: int | None, employee_name: str | None, employee_id: int | None
    ) -> tuple[LeaveRequest, ResolvedEmployee]:
        in_scope = self._target_filter(capability, LeaveRequest.employee_id)
        if leave_id is not None:
            leave = (await self._db.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id, in_scope))).scalar_one_or_none()
            if leave is None:
                raise AITargetNotFoundError()
            owner = (await self._db.execute(select(Employee.id, Employee.name).where(Employee.id == leave.employee_id))).one()
            if leave.status != LeaveRequestStatus.PENDING:
                raise ClarificationNeeded(f"Leave request #{leave.id} is no longer pending (status {leave.status.value}).")
            return leave, ResolvedEmployee(owner.id, owner.name)

        if not employee_name and employee_id is None:
            raise ClarificationNeeded("Whose leave request do you mean?")
        owner = await self.employee(capability, name=employee_name, hint_id=employee_id)
        pending = (
            await self._db.execute(
                select(LeaveRequest)
                .where(LeaveRequest.employee_id == owner.id, LeaveRequest.status == LeaveRequestStatus.PENDING)
                .order_by(LeaveRequest.start_date, LeaveRequest.id)
            )
        ).scalars().all()
        if not pending:
            raise ClarificationNeeded(f"{owner.name} has no pending leave requests.")
        if len(pending) > 1:
            options = "; ".join(
                f"#{leave.id} {leave.leave_type.value} {leave.start_date.isoformat()} to {leave.end_date.isoformat()}"
                for leave in pending[:MAX_CANDIDATES]
            )
            raise ClarificationNeeded(f"{owner.name} has {len(pending)} pending leave requests ({options}). Which one?")
        return pending[0], owner

    # --- tickets -------------------------------------------------------------------------

    async def ticket(self, capability: Capability, *, ticket_id: int | None, title: str | None) -> Ticket:
        query = select(Ticket).where(self._target_filter(capability, Ticket.employee_id))
        if ticket_id is not None:
            ticket = (await self._db.execute(query.where(Ticket.id == ticket_id))).scalar_one_or_none()
            if ticket is None:
                raise AITargetNotFoundError()
            return ticket
        if not title or not title.strip():
            raise ClarificationNeeded("Which ticket do you mean? Please give its number or title.")
        rows = (
            await self._db.execute(
                query.where(func.lower(Ticket.title).like(_like(title.strip()), escape="\\")).order_by(Ticket.id).limit(MAX_CANDIDATES + 1)
            )
        ).scalars().all()
        if not rows:
            raise AITargetNotFoundError()
        if len(rows) > 1:
            options = "; ".join(f"#{t.id} {t.title}" for t in rows[:MAX_CANDIDATES])
            raise ClarificationNeeded(f"More than one ticket matches ({options}). Which one?")
        return rows[0]

    # --- projects (catalog: organisation reference data) ---------------------------------------

    async def project(self, *, name: str | None) -> Project:
        if not name or not name.strip():
            raise ClarificationNeeded("Which project do you mean?")
        term = name.strip()
        exact = (await self._db.execute(select(Project).where(func.lower(Project.name) == term.lower()))).scalars().all()
        rows = exact or (
            await self._db.execute(
                select(Project).where(func.lower(Project.name).like(_like(term), escape="\\")).order_by(Project.id).limit(MAX_CANDIDATES + 1)
            )
        ).scalars().all()
        if not rows:
            raise ClarificationNeeded(f"I couldn't find a project named '{term}'.")
        if len(rows) > 1:
            names = ", ".join(p.name for p in rows[:MAX_CANDIDATES])
            raise ClarificationNeeded(f"More than one project matches ({names}). Which one?")
        return rows[0]
