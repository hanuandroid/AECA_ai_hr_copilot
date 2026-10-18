"""Confirmation-time re-authorization against Steps 2-7 (RBAC remediation step 8).

A proposal records what the user asked for, never authority. At confirmation:
1. the caller's AIUserContext is rebuilt from the database by the confirm endpoint (role, status, direct reports);
2. ActionAgentService.confirm re-runs the capability and target check (run_authorized) with that fresh context;
3. the business endpoint re-checks everything again with the user's own token (scope, assignee rules, last-admin
   guard, audiences, state).
So a change between proposal and confirmation (role, deactivation, reporting line, target state) leaves the proposal
unexecutable: either AI_PERMISSION_DENIED / AI inactive (step 2) or a failed result from the API (step 3); nothing is
written. Single-use, user-bound and TTL checks are covered in test_action_flow.py / test_action_api.py.
"""

import pytest
from sqlalchemy import func, select, update

from app.models.ai_pending_action import AIPendingAction
from app.models.employee import Employee
from app.models.employee_project import EmployeeProject
from app.models.enums import EmployeeStatus, Role, TicketStatus
from app.models.ticket import Ticket
from app.services.ai.authorization import build_ai_user_context
from app.services.ai.errors import AIInactiveUserError, AIPermissionDeniedError
from tests.ai.actions.conftest import RAVI_KUMAR, ScriptedActionLLM, proposal
from tests.support.data import EMPLOYEE, EMPLOYEE_TICKET_ID, MANAGER, OUTSIDER, PASSWORD
from tests.support.db import business_snapshot
from tests.support.http import data

URL, CONFIRM = "/api/v1/chat/actions", "/api/v1/chat/actions/confirm"


async def _fresh_ctx(db, employee_id: int):
    db.expire_all()
    return await build_ai_user_context((await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one(), db)


async def _set(db, employee_id: int, **values) -> None:
    await db.execute(update(Employee).where(Employee.id == employee_id).values(**values))
    await db.commit()


async def _statuses(db) -> list[str]:
    db.expire_all()
    return [row.status for row in (await db.execute(select(AIPendingAction).order_by(AIPendingAction.id))).scalars()]


async def _ticket(db) -> Ticket:
    db.expire_all()
    return (await db.execute(select(Ticket).where(Ticket.id == EMPLOYEE_TICKET_ID))).scalar_one()


async def test_role_change_between_proposal_and_confirmation_is_enforced(agent, db, login):
    service, _, ctx = await agent("manager", proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID,
                                                      arguments={"status": "RESOLVED"}))
    proposed = await service.propose(ctx, "resolve it")
    await _set(db, MANAGER.id, role=Role.EMPLOYEE)
    before = business_snapshot()
    with pytest.raises(AIPermissionDeniedError):
        await service.confirm(await _fresh_ctx(db, MANAGER.id), proposed.confirmation_id, await login("manager"))
    assert business_snapshot() == before and (await _ticket(db)).status == TicketStatus.OPEN
    assert await _statuses(db) == ["DENIED"]


async def test_deactivated_account_cannot_execute_its_pending_action(agent, db, login):
    service, _, ctx = await agent("manager", proposal("TICKET_ASSIGN", resource_id=EMPLOYEE_TICKET_ID,
                                                      assignee_name="Employee Test"))
    proposed = await service.propose(ctx, "assign it")
    token = await login("manager")
    await _set(db, MANAGER.id, status=EmployeeStatus.INACTIVE)
    before = business_snapshot()
    with pytest.raises(AIInactiveUserError):
        await service.confirm(await _fresh_ctx(db, MANAGER.id), proposed.confirmation_id, token)
    assert business_snapshot() == before and (await _ticket(db)).assignee_id is None


async def test_target_leaving_the_team_invalidates_a_project_assignment(agent, db, login):
    service, _, ctx = await agent("manager", proposal("PROJECT_ASSIGN", target_employee_name="Employee Test",
                                                      arguments={"project_name": "HR Policy Copilot"}))
    proposed = await service.propose(ctx, "assign")
    await _set(db, EMPLOYEE.id, manager_id=None)
    with pytest.raises(AIPermissionDeniedError):
        await service.confirm(await _fresh_ctx(db, MANAGER.id), proposed.confirmation_id, await login("manager"))
    db.expire_all()
    assert (await db.execute(select(func.count(EmployeeProject.id)).where(EmployeeProject.employee_id == EMPLOYEE.id))).scalar_one() == 0


async def test_ticket_owner_leaving_the_team_invalidates_a_status_change(agent, db, login):
    service, _, ctx = await agent("manager", proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID,
                                                      arguments={"status": "RESOLVED"}))
    proposed = await service.propose(ctx, "resolve")
    await _set(db, EMPLOYEE.id, manager_id=None)
    with pytest.raises(AIPermissionDeniedError):
        await service.confirm(await _fresh_ctx(db, MANAGER.id), proposed.confirmation_id, await login("manager"))
    assert (await _ticket(db)).status == TicketStatus.OPEN


async def test_assignee_leaving_the_team_is_refused_by_the_api_at_confirmation(agent, db, login):
    """The ticket stays in scope (raised by a report), but the chosen assignee no longer reports to the manager:
    the business API refuses the assignment with the manager's own token, so nothing changes."""
    service, _, ctx = await agent("manager", proposal("TICKET_ASSIGN", resource_id=EMPLOYEE_TICKET_ID, assignee_name="Ravi Kumar"))
    proposed = await service.propose(ctx, "assign")
    await _set(db, RAVI_KUMAR, manager_id=None)
    result = await service.confirm(await _fresh_ctx(db, MANAGER.id), proposed.confirmation_id, await login("manager"))
    assert result.status == "failed" and result.error_code == "FORBIDDEN"
    assert (await _ticket(db)).assignee_id is None and await _statuses(db) == ["FAILED"]


async def test_demoted_admin_loses_org_scope_before_confirming(agent, db, login):
    service, _, ctx = await agent("admin", proposal("PROJECT_ASSIGN", target_employee_id=OUTSIDER.id,
                                                    arguments={"project_name": "HR Policy Copilot"}))
    proposed = await service.propose(ctx, "assign")
    await _set(db, MANAGER.id, role=Role.ADMIN)  # keep an active admin
    await _set(db, 1, role=Role.MANAGER)
    with pytest.raises(AIPermissionDeniedError):
        await service.confirm(await _fresh_ctx(db, 1), proposed.confirmation_id, await login("admin"))
    db.expire_all()
    assert (await db.execute(select(func.count(EmployeeProject.id)).where(EmployeeProject.employee_id == OUTSIDER.id))).scalar_one() == 0


async def test_target_state_change_is_refused_by_the_api(agent, db, login, client, auth):
    service, _, ctx = await agent("admin", proposal("EMPLOYEE_DEACTIVATE", target_employee_id=OUTSIDER.id))
    proposed = await service.propose(ctx, "deactivate")
    assert (await client.delete(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"))).status_code == 200
    result = await service.confirm(ctx, proposed.confirmation_id, await login("admin"), password=PASSWORD)  # AIS-12
    assert result.status == "failed" and result.error_code == "ALREADY_INACTIVE"


async def test_confirm_endpoint_rebuilds_identity_from_the_database(client, auth, override_actions, db):
    """Over HTTP: the confirm request's context comes from the DB, not from the proposal or the token's role claim."""
    override_actions(ScriptedActionLLM(proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID,
                                                arguments={"status": "RESOLVED"})))
    headers = await auth("manager")
    proposed = data(await client.post(URL, json={"question": "resolve the VPN ticket"}, headers=headers))
    assert proposed["status"] == "confirmation_required"
    await _set(db, MANAGER.id, role=Role.EMPLOYEE)
    response = await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"]}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "AI_PERMISSION_DENIED"
    assert (await _ticket(db)).status == TicketStatus.OPEN
