"""Characterization of api/v1/endpoints/employees.py routes relevant to the AI layer:
project assignment, project catalog, deactivation, directory access and sensitive data (R1)."""

import pytest

from tests.support.data import ADMIN, EMPLOYEE, EMPLOYEE_SENSITIVE, MANAGER, OUTSIDER, PROJECT_ID, PROJECT_NAME
from tests.support.http import data, error_code

SENSITIVE_KEYS = {
    "current_salary_usd", "bank_name", "bank_account_number", "bank_account_name", "bank_branch",
    "bank_ifsc", "pan_number", "pan_name", "pan_dob", "date_of_birth", "pf_uan", "esi_no",
}

# --- POST /employees/{id}/projects -----------------------------------------


async def test_employee_cannot_assign_projects(client, auth):
    response = await client.post(
        f"/api/v1/employees/{EMPLOYEE.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "AI Engineer"},
        headers=await auth("employee"),
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_manager_assigns_team_employee_to_project(client, auth):
    headers = await auth("manager")
    response = await client.post(
        f"/api/v1/employees/{EMPLOYEE.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "AI Engineer"},
        headers=headers,
    )
    assert response.status_code == 200
    assert data(response) == {"employee_id": EMPLOYEE.id, "project_id": PROJECT_ID, "role_on_project": "AI Engineer"}

    listing = await client.get(f"/api/v1/employees/{EMPLOYEE.id}/projects", headers=headers)
    assert [(p["project_name"], p["role_on_project"]) for p in data(listing)] == [(PROJECT_NAME, "AI Engineer")]


async def test_manager_can_assign_employee_outside_team(client, auth):
    """CURRENT BEHAVIOUR (R4): no team check. The AI layer must be stricter (D7)."""
    response = await client.post(
        f"/api/v1/employees/{OUTSIDER.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "Reviewer"},
        headers=await auth("manager"),
    )
    assert response.status_code == 200


async def test_reassigning_updates_role_instead_of_duplicating(client, auth):
    headers = await auth("admin")
    url = f"/api/v1/employees/{EMPLOYEE.id}/projects"
    await client.post(url, json={"project_id": PROJECT_ID, "role_on_project": "AI Engineer"}, headers=headers)
    response = await client.post(url, json={"project_id": PROJECT_ID, "role_on_project": "Tech Lead"}, headers=headers)
    assert data(response)["role_on_project"] == "Tech Lead"
    assert len(data(await client.get(url, headers=headers))) == 1


@pytest.mark.parametrize(
    "employee_id,project_id,code",
    [(9999, PROJECT_ID, "EMPLOYEE_NOT_FOUND"), (EMPLOYEE.id, 9999, "PROJECT_NOT_FOUND")],
    ids=["missing-employee", "missing-project"],
)
async def test_assign_project_not_found(client, auth, employee_id, project_id, code):
    response = await client.post(
        f"/api/v1/employees/{employee_id}/projects", json={"project_id": project_id}, headers=await auth("admin")
    )
    assert response.status_code == 404
    assert error_code(response) == code


async def test_employee_cannot_list_another_employees_projects(client, auth):
    response = await client.get(f"/api/v1/employees/{OUTSIDER.id}/projects", headers=await auth("employee"))
    assert response.status_code == 403


# --- POST /employees/projects/catalog --------------------------------------


async def test_employee_cannot_create_project(client, auth):
    response = await client.post("/api/v1/employees/projects/catalog", json={"name": "New Thing"}, headers=await auth("employee"))
    assert response.status_code == 403


@pytest.mark.parametrize("user_key", ["manager", "admin"])
async def test_manager_and_admin_create_project(client, auth, user_key):
    """CURRENT BEHAVIOUR: MANAGER may create projects. The AI matrix is stricter (ADMIN only)."""
    response = await client.post(
        "/api/v1/employees/projects/catalog",
        json={"name": "Leave Analytics", "description": "Dashboards", "status": "planned"},
        headers=await auth(user_key),
    )
    assert response.status_code == 200
    assert data(response)["status"] == "PLANNED"


async def test_duplicate_project_name_rejected_case_insensitively(client, auth):
    response = await client.post(
        "/api/v1/employees/projects/catalog", json={"name": PROJECT_NAME.upper()}, headers=await auth("admin")
    )
    assert response.status_code == 409
    assert error_code(response) == "PROJECT_ALREADY_EXISTS"


async def test_invalid_project_status_rejected(client, auth):
    response = await client.post(
        "/api/v1/employees/projects/catalog", json={"name": "Other", "status": "CANCELLED"}, headers=await auth("admin")
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_PROJECT_STATUS"


# --- DELETE /employees/{id} (deactivation) ---------------------------------


@pytest.mark.parametrize("user_key", ["employee", "manager"])
async def test_non_admin_cannot_deactivate(client, auth, user_key):
    response = await client.delete(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth(user_key))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_admin_deactivates_employee_softly(client, auth):
    response = await client.delete(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"))
    assert response.status_code == 200
    assert data(response) == {"id": OUTSIDER.id, "status": "INACTIVE"}

    directory = await client.get("/api/v1/employees", params={"limit": 100}, headers=await auth("admin"))
    assert OUTSIDER.id not in {item["id"] for item in data(directory)["items"]}


@pytest.mark.parametrize(
    "employee_id,status,code",
    [(ADMIN.id, 400, "INVALID_OPERATION"), (5, 400, "ALREADY_INACTIVE"), (9999, 404, "EMPLOYEE_NOT_FOUND")],
    ids=["self", "already-inactive", "missing"],
)
async def test_deactivation_guards(client, auth, employee_id, status, code):
    response = await client.delete(f"/api/v1/employees/{employee_id}", headers=await auth("admin"))
    assert response.status_code == status
    assert error_code(response) == code


# --- Directory & sensitive data --------------------------------------------


async def test_directory_is_visible_to_employees_without_sensitive_fields(client, auth):
    response = await client.get("/api/v1/employees", params={"limit": 100}, headers=await auth("employee"))
    assert response.status_code == 200
    items = data(response)["items"]
    assert {item["id"] for item in items} == {1, 2, 3, 4}  # INACTIVE excluded
    for item in items:
        assert set(item) == {"id", "name", "email", "department_id", "role", "status", "joining_date"}


async def test_finance_profile_returns_only_own_data(client, auth):
    response = await client.get("/api/v1/finance/profile/me", headers=await auth("employee"))
    assert data(response)["employee_id"] == EMPLOYEE.id


async def test_r1_employee_detail_exposes_sensitive_fields_to_any_user(client, auth):
    """SECURITY FINDING R1 - documents CURRENT (insecure) behaviour; do not "fix" this test.

    GET /api/v1/employees/{id} returns salary, bank, PAN, DOB and PF data of ANY employee to
    ANY authenticated user (here: OUTSIDER reading EMPLOYEE).

    TODO(R1): role-gate these fields in employees.py::get_employee (decision D4, separate approved
    change). When fixed, update this test to assert the fields are absent for non-privileged callers.
    The AI layer never calls this route: see app/services/ai/guards.py and tests/ai/test_guards.py.
    """
    response = await client.get(f"/api/v1/employees/{EMPLOYEE.id}", headers=await auth("outsider"))
    assert response.status_code == 200
    detail = data(response)
    assert SENSITIVE_KEYS <= set(detail)
    assert detail["current_salary_usd"] == EMPLOYEE_SENSITIVE["current_salary_usd"]
    assert detail["bank_account_number"] == EMPLOYEE_SENSITIVE["bank_account_number"]
    assert detail["bank_ifsc"] == EMPLOYEE_SENSITIVE["bank_ifsc"]
    assert detail["pan_number"] == EMPLOYEE_SENSITIVE["pan_number"]
    assert detail["date_of_birth"] == EMPLOYEE_SENSITIVE["date_of_birth"]
    assert detail["pf_uan"] == EMPLOYEE_SENSITIVE["pf_uan"]
    # hashed_password is NOT part of the response.
    assert "hashed_password" not in detail


async def test_r1_exposure_does_not_extend_to_password_hash(client, auth):
    for user_key in ("employee", "manager", "admin"):
        response = await client.get(f"/api/v1/employees/{MANAGER.id}", headers=await auth(user_key))
        assert "hashed_password" not in response.text
        assert "$2b$" not in response.text
