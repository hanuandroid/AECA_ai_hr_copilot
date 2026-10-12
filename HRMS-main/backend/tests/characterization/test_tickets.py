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


# --- Manager ticket scope (production hardening S-01a, Phase 2) ------------------------------------------------
# Old behaviour (pinned here until 2026-10-12): every manager could list ALL tickets (descriptions included) and assign,
# re-status or add/complete onboarding tasks on any ticket. Invariant now (approved AI matrix scope, enforced in the
# business API): managers act on tickets raised by or assigned to themselves or raised by their direct reports;
# out-of-scope tickets look exactly like unknown ids; admins are unrestricted.


async def _outsider_ticket(client, auth, **overrides) -> int:
    response = await client.post("/api/v1/tickets", json=ticket_payload(title="Outsider ticket", **overrides),
                                 headers=await auth("outsider"))
    assert response.status_code == 200
    return data(response)["id"]


async def test_manager_lists_only_team_tickets(client, auth):
    outsider_ticket = await _outsider_ticket(client, auth)
    response = await client.get("/api/v1/tickets", params={"limit": 100}, headers=await auth("manager"))
    body = data(response)
    ids = {item["id"] for item in body["items"]}
    assert EMPLOYEE_TICKET_ID in ids and outsider_ticket not in ids
    assert "Outsider ticket" not in response.text
    assert body["meta"]["total"] == len(body["items"])


async def test_admin_lists_all_tickets(client, auth):
    outsider_ticket = await _outsider_ticket(client, auth)
    ids = {item["id"] for item in data(await client.get("/api/v1/tickets", params={"limit": 100}, headers=await auth("admin")))["items"]}
    assert {EMPLOYEE_TICKET_ID, outsider_ticket} <= ids


async def test_manager_sees_tickets_assigned_to_them_from_other_teams(client, auth):
    outsider_ticket = await _outsider_ticket(client, auth)
    assigned = await client.post(f"/api/v1/tickets/{outsider_ticket}/assign", json={"assignee_id": MANAGER.id},
                                 headers=await auth("admin"))
    assert assigned.status_code == 200
    ids = {item["id"] for item in data(await client.get("/api/v1/tickets", params={"limit": 100}, headers=await auth("manager")))["items"]}
    assert outsider_ticket in ids


@pytest.mark.parametrize("operation", ["assign", "status", "onboarding"])
async def test_manager_cannot_act_on_other_teams_tickets(client, auth, operation):
    outsider_ticket = await _outsider_ticket(client, auth, category="onboarding")
    headers = await auth("manager")
    path, body = {
        "assign": ("assign", {"assignee_id": MANAGER.id}),
        "status": ("status", {"status": "RESOLVED"}),
        "onboarding": ("onboarding-tasks", {"task_name": "Laptop setup"}),
    }[operation]
    response = await client.post(f"/api/v1/tickets/{outsider_ticket}/{path}", json=body, headers=headers)
    unknown = await client.post(f"/api/v1/tickets/999999/{path}", json=body, headers=headers)
    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()  # indistinguishable from an unknown ticket id
    ticket = next(item for item in data(await client.get("/api/v1/tickets", params={"limit": 100}, headers=await auth("admin")))["items"]
                  if item["id"] == outsider_ticket)
    assert ticket["status"] == "OPEN" and ticket["assignee_id"] is None  # nothing changed


async def test_manager_cannot_complete_other_teams_onboarding_task(client, auth):
    outsider_ticket = await _outsider_ticket(client, auth, category="onboarding")
    task = data(await client.post(f"/api/v1/tickets/{outsider_ticket}/onboarding-tasks", json={"task_name": "Badge"},
                                  headers=await auth("admin")))
    headers = await auth("manager")
    response = await client.post(f"/api/v1/tickets/{outsider_ticket}/onboarding-tasks/{task['id']}/complete", headers=headers)
    unknown = await client.post(f"/api/v1/tickets/{outsider_ticket}/onboarding-tasks/999999/complete", headers=headers)
    assert response.status_code == unknown.status_code == 404 and response.json() == unknown.json()


async def test_manager_assigns_only_to_self_or_direct_reports(client, auth):
    headers = await auth("manager")
    to_outsider = await client.post(f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", json={"assignee_id": OUTSIDER.id}, headers=headers)
    assert to_outsider.status_code == 403 and error_code(to_outsider) == "FORBIDDEN"
    to_report = await client.post(f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign", json={"assignee_id": EMPLOYEE.id}, headers=headers)
    assert to_report.status_code == 200 and data(to_report)["assignee_id"] == EMPLOYEE.id


async def test_admin_assigns_any_ticket_to_anyone(client, auth):
    outsider_ticket = await _outsider_ticket(client, auth)
    response = await client.post(f"/api/v1/tickets/{outsider_ticket}/assign", json={"assignee_id": EMPLOYEE.id}, headers=await auth("admin"))
    assert response.status_code == 200


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
