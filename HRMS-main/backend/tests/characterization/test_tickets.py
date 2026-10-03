"""Characterization of api/v1/endpoints/tickets.py (wrapped by AI tools in Phase 5)."""

import pytest

from tests.support.data import EMPLOYEE, EMPLOYEE_TICKET_ID, MANAGER, OUTSIDER
from tests.support.http import data, error_code


def ticket_payload(**overrides):
    payload = {"title": "Laptop broken", "description": "Screen flickers constantly.", "category": "it", "priority": "high"}
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("user_key", ["employee", "manager", "admin"])
async def test_any_role_creates_open_unassigned_ticket(client, auth, user_key):
    response = await client.post("/api/v1/tickets", json=ticket_payload(), headers=await auth(user_key))
    assert response.status_code == 200
    ticket = data(response)
    assert ticket["status"] == "OPEN"
    assert ticket["assignee_id"] is None
    assert ticket["category"] == "IT"
    assert ticket["priority"] == "HIGH"
    assert ticket["onboarding_tasks"] == []


@pytest.mark.parametrize("overrides", [{"category": "FINANCE"}, {"priority": "URGENT"}])
async def test_invalid_ticket_enums_rejected(client, auth, overrides):
    response = await client.post("/api/v1/tickets", json=ticket_payload(**overrides), headers=await auth("employee"))
    assert response.status_code == 400
    assert error_code(response) == "INVALID_TICKET_ENUM"


async def test_employee_lists_only_own_or_assigned_tickets(client, auth):
    await client.post("/api/v1/tickets", json=ticket_payload(title="Outsider ticket"), headers=await auth("outsider"))
    response = await client.get("/api/v1/tickets", headers=await auth("employee"))
    assert response.status_code == 200
    items = data(response)["items"]
    assert items
    assert all(EMPLOYEE.id in (item["employee_id"], item["assignee_id"]) for item in items)


async def test_manager_lists_all_tickets(client, auth):
    await client.post("/api/v1/tickets", json=ticket_payload(title="Outsider ticket"), headers=await auth("outsider"))
    response = await client.get("/api/v1/tickets", headers=await auth("manager"))
    owners = {item["employee_id"] for item in data(response)["items"]}
    assert {EMPLOYEE.id, OUTSIDER.id} <= owners


# --- POST /tickets/{id}/assign ---------------------------------------------


async def test_employee_cannot_assign_ticket(client, auth):
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", json={"assignee_id": MANAGER.id}, headers=await auth("employee")
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_manager_assigns_ticket_and_status_moves_to_in_progress(client, auth):
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", json={"assignee_id": MANAGER.id}, headers=await auth("manager")
    )
    assert response.status_code == 200
    ticket = data(response)
    assert ticket["assignee_id"] == MANAGER.id
    assert ticket["status"] == "IN_PROGRESS"


@pytest.mark.parametrize(
    "ticket_id,assignee_id,code",
    [(9999, 2, "TICKET_NOT_FOUND"), (EMPLOYEE_TICKET_ID, 9999, "ASSIGNEE_NOT_FOUND")],
    ids=["missing-ticket", "missing-assignee"],
)
async def test_assign_not_found(client, auth, ticket_id, assignee_id, code):
    response = await client.post(
        f"/api/v1/tickets/{ticket_id}/assign", json={"assignee_id": assignee_id}, headers=await auth("admin")
    )
    assert response.status_code == 404
    assert error_code(response) == code


# --- POST /tickets/{id}/status ---------------------------------------------


async def test_ticket_owner_employee_can_update_status(client, auth):
    """CURRENT BEHAVIOUR: owners/assignees may update status. The AI matrix is stricter (EMPLOYEE: no)."""
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", json={"status": "resolved"}, headers=await auth("employee")
    )
    assert response.status_code == 200
    assert data(response)["status"] == "RESOLVED"


async def test_unrelated_employee_cannot_update_ticket_status(client, auth):
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", json={"status": "RESOLVED"}, headers=await auth("outsider")
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_manager_can_update_any_ticket_status(client, auth):
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", json={"status": "IN_PROGRESS"}, headers=await auth("manager")
    )
    assert response.status_code == 200
    assert data(response)["status"] == "IN_PROGRESS"


async def test_invalid_ticket_status_rejected(client, auth):
    response = await client.post(
        f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status", json={"status": "CLOSED"}, headers=await auth("manager")
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_TICKET_STATUS"


async def test_status_update_missing_ticket_is_404(client, auth):
    response = await client.post("/api/v1/tickets/9999/status", json={"status": "RESOLVED"}, headers=await auth("manager"))
    assert response.status_code == 404
    assert error_code(response) == "TICKET_NOT_FOUND"
