"""Ticket status authorization (RBAC remediation step 5, approved Decision 4).

Old behaviour (pinned in tests/characterization/test_tickets.py until 2026-10-17): an EMPLOYEE could change the status
of any ticket they raised OR were assigned.

Invariant now (POST /tickets/{id}/status, the only route that sets a status chosen by the caller):
- ADMIN: any ticket. MANAGER: tickets raised by or assigned to themselves, or raised by a direct report (S-01a scope,
  unchanged; out-of-scope ids look unknown).
- EMPLOYEE: only tickets currently assigned to them. Raising a ticket no longer grants status changes; employees keep
  creating tickets and reading their status.
- POST /tickets/{id}/assign also moves OPEN -> IN_PROGRESS; it was already MANAGER/ADMIN only (unchanged).
- The role is read from the database; extra body fields are ignored and never persisted.
The AI action TICKET_STATUS_UPDATE stays stricter (EMPLOYEE denied even as assignee; tests/ai/actions/test_ticket_status_ai.py).
"""

import pytest
from sqlalchemy import select, update

from app.core.security import create_access_token
from app.models.enums import TicketStatus
from app.models.ticket import Ticket
from tests.support.data import ADMIN, EMPLOYEE, EMPLOYEE_TICKET_ID, INACTIVE, MANAGER, OUTSIDER
from tests.support.http import data, error_code


async def _state(db, ticket_id: int) -> tuple:
    db.expire_all()
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one()
    return ticket.status, ticket.assignee_id, ticket.employee_id, ticket.title, ticket.description, ticket.category, ticket.priority


async def _set_assignee(db, ticket_id: int, assignee_id: int | None) -> None:
    await db.execute(update(Ticket).where(Ticket.id == ticket_id).values(assignee_id=assignee_id))
    await db.commit()


async def _raise_ticket(client, auth, user: str) -> int:
    response = await client.post("/api/v1/tickets", headers=await auth(user), json={
        "title": "Printer jammed", "description": "Second floor printer is jammed.", "category": "it", "priority": "low"})
    assert response.status_code == 200
    return data(response)["id"]


def _status(client, ticket_id: int, headers: dict, **body):
    return client.post(f"/api/v1/tickets/{ticket_id}/status", headers=headers, json=body)


# --- employees: create and read, never re-status their own ticket ------------------------------------------------


@pytest.mark.parametrize("new_status", ["OPEN", "IN_PROGRESS", "RESOLVED", "resolved", "CLOSED"])
async def test_ticket_owner_employee_cannot_change_status(client, auth, db, new_status):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("employee"), status=new_status)
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


async def test_employee_still_creates_tickets_and_reads_their_status(client, auth):
    ticket_id = await _raise_ticket(client, auth, "employee")
    listed = await client.get("/api/v1/tickets", params={"mine": "true"}, headers=await auth("employee"))
    assert listed.status_code == 200
    assert {t["id"]: t["status"] for t in data(listed)["items"]}[ticket_id] == "OPEN"


@pytest.mark.parametrize("extra", [
    {"assignee_id": EMPLOYEE.id},
    {"employee_id": OUTSIDER.id, "role": "ADMIN"},
    {"assignee_id": EMPLOYEE.id, "title": "Hijacked", "category": "HR", "priority": "HIGH", "is_manager": True},
])
async def test_mixed_payloads_do_not_unlock_status_or_persist_fields(client, auth, db, extra):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("employee"), status="RESOLVED", **extra)
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


async def test_extra_fields_are_ignored_even_for_an_authorized_manager(client, auth, db):
    _, assignee, owner, title, description, category, priority = await _state(db, EMPLOYEE_TICKET_ID)
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("manager"), status="RESOLVED",
                             assignee_id=OUTSIDER.id, employee_id=OUTSIDER.id, title="Hijacked")
    assert response.status_code == 200
    assert await _state(db, EMPLOYEE_TICKET_ID) == (TicketStatus.RESOLVED, assignee, owner, title, description, category, priority)


async def test_forged_admin_role_claim_is_ignored(client, db):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    response = await _status(client, EMPLOYEE_TICKET_ID, {"Authorization": f"Bearer {token}"}, status="RESOLVED")
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


async def test_unrelated_employee_is_refused_and_unknown_ticket_is_404(client, auth, db):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    refused = await _status(client, EMPLOYEE_TICKET_ID, await auth("outsider"), status="RESOLVED")
    assert refused.status_code == 403 and error_code(refused) == "FORBIDDEN"
    unknown = await _status(client, 9999, await auth("employee"), status="RESOLVED")
    assert unknown.status_code == 404 and error_code(unknown) == "TICKET_NOT_FOUND"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_no_alternate_write_route_for_a_ticket(client, auth, db, method):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await client.request(method, f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}", headers=await auth("employee"),
                                    json={"status": "RESOLVED"})
    # No /tickets/{id} route exists at all (404); the route-permission completeness test proves there is no other one.
    assert response.status_code in (404, 405)
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


async def test_employee_cannot_change_status_through_assignment(client, auth, db):
    """Assignment moves OPEN -> IN_PROGRESS; it stays MANAGER/ADMIN only, including self-assignment."""
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await client.post(f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", headers=await auth("employee"),
                                 json={"assignee_id": EMPLOYEE.id})
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


# --- assignees keep their status updates -------------------------------------------------------------------------


async def test_employee_assignee_changes_status_of_a_ticket_raised_by_someone_else(client, auth, db):
    ticket_id = await _raise_ticket(client, auth, "manager")
    assigned = await client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=await auth("manager"),
                                 json={"assignee_id": EMPLOYEE.id})
    assert assigned.status_code == 200 and data(assigned)["status"] == "IN_PROGRESS"
    response = await _status(client, ticket_id, await auth("employee"), status="RESOLVED")
    assert response.status_code == 200 and data(response)["status"] == "RESOLVED"
    assert (await _state(db, ticket_id))[:2] == (TicketStatus.RESOLVED, EMPLOYEE.id)


async def test_owner_who_is_also_the_assignee_may_change_status(client, auth):
    """The assignment (made by a manager) grants it, not the ownership."""
    assigned = await client.post(f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", headers=await auth("manager"),
                                 json={"assignee_id": EMPLOYEE.id})
    assert assigned.status_code == 200
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("employee"), status="RESOLVED")
    assert response.status_code == 200 and data(response)["status"] == "RESOLVED"


async def test_former_assignee_loses_status_rights_after_reassignment(client, auth, db):
    ticket_id = await _raise_ticket(client, auth, "outsider")
    await _set_assignee(db, ticket_id, EMPLOYEE.id)
    assert (await _status(client, ticket_id, await auth("employee"), status="IN_PROGRESS")).status_code == 200
    reassigned = await client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=await auth("admin"),
                                   json={"assignee_id": MANAGER.id})
    assert reassigned.status_code == 200
    before = await _state(db, ticket_id)
    response = await _status(client, ticket_id, await auth("employee"), status="RESOLVED")
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _state(db, ticket_id) == before


# --- managers and admins (scope unchanged) -----------------------------------------------------------------------


async def test_manager_changes_status_of_a_team_ticket(client, auth, db):
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("manager"), status="IN_PROGRESS")
    assert response.status_code == 200 and (await _state(db, EMPLOYEE_TICKET_ID))[0] == TicketStatus.IN_PROGRESS


async def test_manager_cannot_change_status_outside_their_scope(client, auth, db):
    ticket_id = await _raise_ticket(client, auth, "outsider")
    before = await _state(db, ticket_id)
    response = await _status(client, ticket_id, await auth("manager"), status="RESOLVED")
    unknown = await _status(client, 9999, await auth("manager"), status="RESOLVED")
    assert response.status_code == unknown.status_code == 404 and response.json() == unknown.json()
    assert await _state(db, ticket_id) == before


async def test_manager_assignee_from_another_team_may_change_status(client, auth, db):
    ticket_id = await _raise_ticket(client, auth, "outsider")
    await _set_assignee(db, ticket_id, MANAGER.id)
    response = await _status(client, ticket_id, await auth("manager"), status="RESOLVED")
    assert response.status_code == 200


async def test_admin_changes_status_of_any_ticket(client, auth, db):
    ticket_id = await _raise_ticket(client, auth, "outsider")
    response = await _status(client, ticket_id, await auth("admin"), status="RESOLVED")
    assert response.status_code == 200 and (await _state(db, ticket_id))[0] == TicketStatus.RESOLVED
    assert ADMIN.id not in (await _state(db, ticket_id))[1:3]  # admin needs no ownership or assignment


# --- anonymous and inactive --------------------------------------------------------------------------------------


async def test_anonymous_is_401(client, db):
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await _status(client, EMPLOYEE_TICKET_ID, {}, status="RESOLVED")
    assert response.status_code == 401
    assert await _state(db, EMPLOYEE_TICKET_ID) == before


async def test_inactive_user_is_refused_even_as_assignee(client, auth, db):
    await _set_assignee(db, EMPLOYEE_TICKET_ID, INACTIVE.id)
    before = await _state(db, EMPLOYEE_TICKET_ID)
    response = await _status(client, EMPLOYEE_TICKET_ID, await auth("inactive"), status="RESOLVED")
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"
    assert await _state(db, EMPLOYEE_TICKET_ID) == before
