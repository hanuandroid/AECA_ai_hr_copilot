"""AI action parity with the business API (RBAC remediation step 8; decision D7: the AI may be stricter, never looser).

One table, two real executions per row: the business API over HTTP with the role's own token, and the Copilot
(propose -> confirm through ActionAgentService, which calls the same API with the same token). The invariant test
proves that no row lets the Copilot do something the API refuses. Rows where the Copilot is deliberately stricter are
the documented exceptions in docs/ai_permissions_matrix.md §3.1:
- E1 (approved 2026-10-17): an EMPLOYEE who is the current ticket assignee may change status through the API, never
  through the Copilot.
- E2: the Copilot's manager ticket scope is owner-based (tickets raised by the manager or a direct report); the API also
  lets a manager act on a ticket merely assigned to them.
Every action also requires confirmation (registry) and is re-authorized at confirmation (test_action_flow.py,
test_confirmation_parity.py).
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pytest
from sqlalchemy import update

from app.models.enums import TicketCategory, TicketPriority, TicketStatus
from app.models.ticket import Ticket
from app.services.ai.actions.models import ActionName
from app.services.ai.actions.registry import ACTION_REGISTRY
from app.services.ai.errors import AIPermissionDeniedError, AITargetNotFoundError
from tests.ai.actions.conftest import proposal
from tests.support.data import (
    ADMIN,
    EMPLOYEE,
    EMPLOYEE_PENDING_LEAVE_ID,
    EMPLOYEE_TICKET_ID,
    MANAGER,
    OUTSIDER,
    OUTSIDER_PENDING_LEAVE_ID,
    PASSWORD,
    future,
)

OUTSIDER_TICKET_ID = 50  # raised by OUTSIDER (no manager), assigned to MANAGER


async def _employee_assigned(db) -> None:
    await db.execute(update(Ticket).where(Ticket.id == EMPLOYEE_TICKET_ID).values(assignee_id=EMPLOYEE.id))
    await db.commit()


async def _outsider_ticket_assigned_to_manager(db) -> None:
    db.add(Ticket(id=OUTSIDER_TICKET_ID, employee_id=OUTSIDER.id, assignee_id=MANAGER.id, title="Badge broken",
                  description="My badge does not open the door.", category=TicketCategory.IT,
                  priority=TicketPriority.LOW, status=TicketStatus.OPEN))
    await db.commit()


@dataclass(frozen=True)
class Row:
    id: str
    user: str
    api: tuple[str, str, dict | None]  # method, url, json
    ai: dict[str, Any]  # scripted (untrusted) model proposal
    api_allowed: bool
    ai_allowed: bool
    setup: Callable[[Any], Awaitable[None]] | None = field(default=None)
    exception: str | None = None  # documented stricter-than-API row


LEAVE_DAY = future(70).isoformat()
TICKET = {"title": "VPN not working", "description": "VPN drops every few minutes.", "category": "it", "priority": "high"}
NEWS = {"title": "Sprint review", "body": "Sprint review moves to Thursday."}
L = "/api/v1/leaves/requests"

ROWS = [
    # LEAVE_CREATE: self only, every role
    *[Row(f"leave_create-{u}", u, ("POST", L, {"leave_type": "SICK", "start_date": LEAVE_DAY, "end_date": LEAVE_DAY, "reason": "Fever"}),
          proposal("LEAVE_CREATE", arguments={"leave_type": "sick", "start_date": LEAVE_DAY, "reason": "Fever"}), True, True)
      for u in ("employee", "manager", "admin")],
    # LEAVE_APPROVE / REJECT: manager -> direct reports only; admin -> anyone but self; employee never
    Row("leave_approve-manager-report", "manager", ("POST", f"{L}/{EMPLOYEE_PENDING_LEAVE_ID}/approve", None),
        proposal("LEAVE_APPROVE", resource_id=EMPLOYEE_PENDING_LEAVE_ID), True, True),
    Row("leave_approve-manager-outsider", "manager", ("POST", f"{L}/{OUTSIDER_PENDING_LEAVE_ID}/approve", None),
        proposal("LEAVE_APPROVE", resource_id=OUTSIDER_PENDING_LEAVE_ID), False, False),
    Row("leave_approve-employee", "employee", ("POST", f"{L}/{EMPLOYEE_PENDING_LEAVE_ID}/approve", None),
        proposal("LEAVE_APPROVE", resource_id=EMPLOYEE_PENDING_LEAVE_ID), False, False),
    Row("leave_reject-admin-outsider", "admin", ("POST", f"{L}/{OUTSIDER_PENDING_LEAVE_ID}/reject", None),
        proposal("LEAVE_REJECT", resource_id=OUTSIDER_PENDING_LEAVE_ID), True, True),
    Row("leave_reject-manager-outsider", "manager", ("POST", f"{L}/{OUTSIDER_PENDING_LEAVE_ID}/reject", None),
        proposal("LEAVE_REJECT", resource_id=OUTSIDER_PENDING_LEAVE_ID), False, False),
    # TICKET_CREATE: every role, for themselves
    *[Row(f"ticket_create-{u}", u, ("POST", "/api/v1/tickets", TICKET), proposal("TICKET_CREATE", arguments=TICKET), True, True)
      for u in ("employee", "manager", "admin")],
    # TICKET_ASSIGN: manager (team-raised ticket, assignee self/report), admin; employee never
    Row("ticket_assign-manager-team", "manager", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", {"assignee_id": EMPLOYEE.id}),
        proposal("TICKET_ASSIGN", resource_id=EMPLOYEE_TICKET_ID, assignee_name="Employee Test"), True, True),
    Row("ticket_assign-employee", "employee", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", {"assignee_id": EMPLOYEE.id}),
        proposal("TICKET_ASSIGN", resource_id=EMPLOYEE_TICKET_ID, assignee_name="Employee Test"), False, False),
    Row("ticket_assign-admin", "admin", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", {"assignee_id": MANAGER.id}),
        proposal("TICKET_ASSIGN", resource_id=EMPLOYEE_TICKET_ID, assignee_name="Manager Test"), True, True),
    Row("ticket_assign-manager-assigned-outsider-ticket", "manager",
        ("POST", f"/api/v1/tickets/{OUTSIDER_TICKET_ID}/assign", {"assignee_id": MANAGER.id}),
        proposal("TICKET_ASSIGN", resource_id=OUTSIDER_TICKET_ID, assignee_name="Manager Test"), True, False,
        setup=_outsider_ticket_assigned_to_manager, exception="E2"),
    # TICKET_STATUS_UPDATE
    Row("ticket_status-employee-owner", "employee", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", {"status": "RESOLVED"}),
        proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID, arguments={"status": "RESOLVED"}), False, False),
    Row("ticket_status-employee-assignee", "employee", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", {"status": "RESOLVED"}),
        proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID, arguments={"status": "RESOLVED"}), True, False,
        setup=_employee_assigned, exception="E1"),
    Row("ticket_status-manager-team", "manager", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", {"status": "RESOLVED"}),
        proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID, arguments={"status": "RESOLVED"}), True, True),
    Row("ticket_status-manager-assigned-outsider-ticket", "manager",
        ("POST", f"/api/v1/tickets/{OUTSIDER_TICKET_ID}/status", {"status": "RESOLVED"}),
        proposal("TICKET_STATUS_UPDATE", resource_id=OUTSIDER_TICKET_ID, arguments={"status": "RESOLVED"}), True, False,
        setup=_outsider_ticket_assigned_to_manager, exception="E2"),
    Row("ticket_status-admin", "admin", ("POST", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", {"status": "RESOLVED"}),
        proposal("TICKET_STATUS_UPDATE", resource_id=EMPLOYEE_TICKET_ID, arguments={"status": "RESOLVED"}), True, True),
    # ANNOUNCEMENT_CREATE: manager (TEAM), admin (ORG); employee never
    *[Row(f"announcement-{u}", u, ("POST", "/api/v1/announcements", NEWS), proposal("ANNOUNCEMENT_CREATE", arguments=NEWS),
          u != "employee", u != "employee") for u in ("employee", "manager", "admin")],
    # PROJECT_ASSIGN: manager -> self / direct reports; admin -> anyone; employee never
    Row("project_assign-manager-report", "manager", ("POST", f"/api/v1/employees/{EMPLOYEE.id}/projects", {"project_id": 1}),
        proposal("PROJECT_ASSIGN", target_employee_name="Employee Test", arguments={"project_name": "HR Policy Copilot"}), True, True),
    Row("project_assign-manager-outsider", "manager", ("POST", f"/api/v1/employees/{OUTSIDER.id}/projects", {"project_id": 1}),
        proposal("PROJECT_ASSIGN", target_employee_id=OUTSIDER.id, arguments={"project_name": "HR Policy Copilot"}), False, False),
    Row("project_assign-employee", "employee", ("POST", f"/api/v1/employees/{EMPLOYEE.id}/projects", {"project_id": 1}),
        proposal("PROJECT_ASSIGN", target_employee_name="Employee Test", arguments={"project_name": "HR Policy Copilot"}), False, False),
    Row("project_assign-admin-outsider", "admin", ("POST", f"/api/v1/employees/{OUTSIDER.id}/projects", {"project_id": 1}),
        proposal("PROJECT_ASSIGN", target_employee_id=OUTSIDER.id, arguments={"project_name": "HR Policy Copilot"}), True, True),
    # PROJECT_CREATE: admin only (API and AI since step 6)
    *[Row(f"project_create-{u}", u, ("POST", "/api/v1/employees/projects/catalog", {"name": f"Parity {u}"}),
          proposal("PROJECT_CREATE", arguments={"project_name": f"Parity {u}"}), u == "admin", u == "admin")
      for u in ("employee", "manager", "admin")],
    # EMPLOYEE_DEACTIVATE: admin, never self; last-active-admin guard stays in the API (step 4)
    Row("deactivate-admin-other", "admin", ("DELETE", f"/api/v1/employees/{OUTSIDER.id}", None),
        proposal("EMPLOYEE_DEACTIVATE", target_employee_id=OUTSIDER.id), True, True),
    Row("deactivate-admin-self", "admin", ("DELETE", f"/api/v1/employees/{ADMIN.id}", None),
        proposal("EMPLOYEE_DEACTIVATE", target_employee_id=ADMIN.id), False, False),
    *[Row(f"deactivate-{u}", u, ("DELETE", f"/api/v1/employees/{OUTSIDER.id}", None),
          proposal("EMPLOYEE_DEACTIVATE", target_employee_id=OUTSIDER.id), False, False) for u in ("employee", "manager")],
]


def test_every_registered_action_is_in_the_parity_table():
    covered = {row.ai["action"] for row in ROWS}
    assert covered == {name.value for name in ActionName if name in ACTION_REGISTRY}
    for name in ACTION_REGISTRY:
        roles = {row.user for row in ROWS if row.ai["action"] == name.value}
        assert roles >= {"employee", "manager", "admin"} or name.value in {"LEAVE_APPROVE", "LEAVE_REJECT"}, name


def test_every_action_requires_confirmation():
    assert all(spec.requires_confirmation for spec in ACTION_REGISTRY.values())


def test_the_copilot_is_never_broader_than_the_api():
    """D7 invariant over the table: AI allowed => API allowed. Stricter rows must name a documented exception."""
    for row in ROWS:
        assert not (row.ai_allowed and not row.api_allowed), row.id
        assert (row.api_allowed and not row.ai_allowed) == (row.exception is not None), row.id


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
async def test_api_side(client, auth, db, row):
    if row.setup:
        await row.setup(db)
    method, url, body = row.api
    response = await client.request(method, url, headers=await auth(row.user), json=body)
    assert (response.status_code == 200) is row.api_allowed, (row.id, response.status_code, response.text)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
async def test_copilot_side(agent, db, login, row):
    if row.setup:
        await row.setup(db)
    service, _, ctx = await agent(row.user, row.ai)
    try:
        proposed = await service.propose(ctx, "please do it")
    except (AIPermissionDeniedError, AITargetNotFoundError):
        assert row.ai_allowed is False, row.id
        return
    if proposed.status != "confirmation_required":  # refused as a clarification: nothing executable was issued
        assert row.ai_allowed is False and proposed.confirmation_id is None, (row.id, proposed)
        return
    password = PASSWORD if proposed.step_up_required else None  # AIS-12: step-up actions need the password
    result = await service.confirm(ctx, proposed.confirmation_id, await login(row.user), password=password)
    assert (result.status == "completed") is row.ai_allowed, (row.id, result)
