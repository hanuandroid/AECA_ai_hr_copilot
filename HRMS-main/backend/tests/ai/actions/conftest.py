"""Action-agent fixtures. Execution always goes through the REAL FastAPI business endpoints against
the temp DB; only the LLM is scripted."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, LeaveRequestStatus, LeaveType, ProjectStatus, Role
from app.models.leave_request import LeaveRequest
from app.models.project import Project
from app.services.ai.actions.client import HRMSActionClient
from app.services.ai.actions.models import ActionProposal
from app.services.ai.actions.service import ActionAgentService, get_action_agent_service
from app.services.ai.authorization import AIUserContext, build_ai_user_context
from app.services.ai.errors import AIInvalidOutputError
from tests.support.data import MANAGER, USERS, future

TTL_SECONDS = 300
TODAY = date(2026, 10, 1)

RAVI_KUMAR, RAVI_SHAH, RAVI_VERMA = 6, 7, 8  # Kumar + Shah report to MANAGER; Verma is outside the team
RAVI_KUMAR_LEAVES = (10, 11)  # two pending -> ambiguous
RAVI_VERMA_LEAVE = 13


@pytest.fixture(autouse=True)
async def action_data(db):
    hashed = (await db.execute(select(Employee.hashed_password).where(Employee.id == MANAGER.id))).scalar_one()
    for employee_id, name, manager_id in ((RAVI_KUMAR, "Ravi Kumar", MANAGER.id), (RAVI_SHAH, "Ravi Shah", MANAGER.id),
                                          (RAVI_VERMA, "Ravi Verma", None)):
        db.add(Employee(id=employee_id, name=name, email=f"{name.split()[1].lower()}@hrms-test.dev", hashed_password=hashed,
                        department_id=1, manager_id=manager_id, role=Role.EMPLOYEE, status=EmployeeStatus.ACTIVE,
                        joining_date=date(2023, 1, 1)))
    await db.flush()
    db.add_all([
        LeaveRequest(id=10, employee_id=RAVI_KUMAR, leave_type=LeaveType.CASUAL, start_date=future(30), end_date=future(31),
                     reason="Trip", status=LeaveRequestStatus.PENDING),
        LeaveRequest(id=11, employee_id=RAVI_KUMAR, leave_type=LeaveType.SICK, start_date=future(40), end_date=future(40),
                     reason="Appointment", status=LeaveRequestStatus.PENDING),
        LeaveRequest(id=12, employee_id=RAVI_SHAH, leave_type=LeaveType.EARNED, start_date=future(50), end_date=future(52),
                     reason="Vacation", status=LeaveRequestStatus.PENDING),
        LeaveRequest(id=13, employee_id=RAVI_VERMA, leave_type=LeaveType.CASUAL, start_date=future(60), end_date=future(60),
                     reason="Errand", status=LeaveRequestStatus.PENDING),
        Project(id=2, name="HR Policy Analytics", description="Analytics", status=ProjectStatus.PLANNED),
    ])
    await db.commit()


class ScriptedActionLLM:
    """Untrusted action proposer: returns a scripted proposal, validated exactly like a real provider."""

    name = model = "scripted-action"

    def __init__(self, proposal: dict[str, Any]):
        self.proposal = proposal
        self.calls: list[tuple[str, str]] = []

    async def generate(self, *args, **kwargs):
        raise NotImplementedError

    async def generate_structured(self, messages, schema, *, system=None, max_tokens=None):
        assert schema is ActionProposal
        self.calls.append((system or "", messages[-1].content))
        try:
            return schema.model_validate(self.proposal)
        except Exception as exc:
            raise AIInvalidOutputError() from exc


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ctx_for(db) -> Callable:
    async def _ctx_for(user_key: str) -> AIUserContext:
        employee = (await db.execute(select(Employee).where(Employee.id == USERS[user_key].id))).scalar_one()
        return await build_ai_user_context(employee, db)

    return _ctx_for


@pytest.fixture
def agent(db, clock, ctx_for, login) -> Callable:
    """agent(user_key, proposal) -> (service, llm, ctx); service.propose/confirm hit the real API."""

    async def _agent(user_key: str, proposal: dict[str, Any]):
        llm = ScriptedActionLLM(proposal)
        service = ActionAgentService(
            db=db, llm_factory=lambda: llm, client_factory=lambda token: HRMSActionClient(app, token),
            ttl_seconds=TTL_SECONDS, now=clock, today=lambda: TODAY,
        )
        return service, llm, await ctx_for(user_key)

    return _agent


@pytest.fixture
def propose_and_confirm(agent, login):
    async def _run(user_key: str, proposal: dict[str, Any], question: str = "please do it"):
        service, llm, ctx = await agent(user_key, proposal)
        proposed = await service.propose(ctx, question)
        assert proposed.status == "confirmation_required", proposed
        return await service.confirm(ctx, proposed.confirmation_id, await login(user_key)), proposed

    return _run


@pytest.fixture
def override_actions(clock):
    def _install(llm) -> None:
        def _dependency(request: Request, db: AsyncSession = Depends(get_db)) -> ActionAgentService:
            return ActionAgentService(
                db=db, llm_factory=lambda: llm, client_factory=lambda token: HRMSActionClient(request.app, token),
                ttl_seconds=TTL_SECONDS, now=clock, today=lambda: TODAY,
            )

        app.dependency_overrides[get_action_agent_service] = _dependency

    yield _install
    app.dependency_overrides.pop(get_action_agent_service, None)


def proposal(action: str, **fields: Any) -> dict[str, Any]:
    arguments = fields.pop("arguments", {})
    return {"action": action, "needs_clarification": False, **fields, "arguments": arguments}
