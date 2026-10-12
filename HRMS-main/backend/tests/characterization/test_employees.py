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


async def test_manager_cannot_assign_employee_outside_team(client, auth):
    """Production hardening B-05 (approved remediation of R4).

    Old behaviour (pinned here until 2026-10-11): no team check, so any manager could assign any employee.
    Invariant now: managers assign only themselves or their direct reports; nothing is written on denial.
    """
    headers = await auth("manager")
    response = await client.post(
        f"/api/v1/employees/{OUTSIDER.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "Reviewer"},
        headers=headers,
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"
    listing = await client.get(f"/api/v1/employees/{OUTSIDER.id}/projects", headers=await auth("admin"))
    assert data(listing) == []


async def test_manager_can_assign_themself(client, auth):
    response = await client.post(
        f"/api/v1/employees/{MANAGER.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "Lead"},
        headers=await auth("manager"),
    )
    assert response.status_code == 200


async def test_admin_can_assign_any_employee(client, auth):
    response = await client.post(
        f"/api/v1/employees/{OUTSIDER.id}/projects",
        json={"project_id": PROJECT_ID, "role_on_project": "Reviewer"},
        headers=await auth("admin"),
    )
    assert response.status_code == 200


# --- DELETE /employees/{id}/projects/{project_id} (production hardening B-05, approved follow-up) ---------------
# Old behaviour (until 2026-10-12): any manager could remove any employee's project assignment.
# Invariant now: same scope as assignment - managers only for themselves or direct reports; admins anyone.


async def _assign(client, auth, employee_id: int) -> None:
    response = await client.post(f"/api/v1/employees/{employee_id}/projects", json={"project_id": PROJECT_ID},
                                 headers=await auth("admin"))
    assert response.status_code == 200


async def _assigned_project_ids(client, auth, employee_id: int) -> list[int]:
    listing = await client.get(f"/api/v1/employees/{employee_id}/projects", headers=await auth("admin"))
    return [item["project_id"] for item in data(listing)]


async def test_manager_cannot_remove_assignment_outside_team(client, auth):
    await _assign(client, auth, OUTSIDER.id)
    headers = await auth("manager")
    response = await client.delete(f"/api/v1/employees/{OUTSIDER.id}/projects/{PROJECT_ID}", headers=headers)
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"
    assert PROJECT_ID in await _assigned_project_ids(client, auth, OUTSIDER.id)  # nothing removed
    # Same answer when no such assignment exists: out-of-scope assignments cannot be probed.
    missing = await client.delete(f"/api/v1/employees/{OUTSIDER.id}/projects/987654", headers=headers)
    assert missing.status_code == 403 and missing.json() == response.json()


async def test_manager_removes_direct_reports_assignment(client, auth):
    await _assign(client, auth, EMPLOYEE.id)
    response = await client.delete(f"/api/v1/employees/{EMPLOYEE.id}/projects/{PROJECT_ID}", headers=await auth("manager"))
    assert response.status_code == 200
    assert PROJECT_ID not in await _assigned_project_ids(client, auth, EMPLOYEE.id)


async def test_admin_removes_any_assignment(client, auth):
    await _assign(client, auth, OUTSIDER.id)
    response = await client.delete(f"/api/v1/employees/{OUTSIDER.id}/projects/{PROJECT_ID}", headers=await auth("admin"))
    assert response.status_code == 200


async def test_employee_cannot_remove_assignments(client, auth):
    await _assign(client, auth, EMPLOYEE.id)
    response = await client.delete(f"/api/v1/employees/{EMPLOYEE.id}/projects/{PROJECT_ID}", headers=await auth("employee"))
    assert response.status_code == 403
    assert PROJECT_ID in await _assigned_project_ids(client, auth, EMPLOYEE.id)


# --- Other manager-targeted employee routes (production hardening S-01, Phase 2) --------------------------------
# Old behaviour (until 2026-10-12): any manager could upload documents or payslips into ANY employee's records, change
# ANY employee's job title (including admins' and their own) and read ANY employee's project assignments.
# Invariant now: managers act only for direct reports (self allowed only for document upload and project listing).


def _upload(title: str = "Signed contract") -> dict:
    return {"data": {"title": title, "document_type": "OTHER"}, "files": {"file": ("note.txt", b"hello", "text/plain")}}


async def _document_titles(client, auth, user_key: str) -> list[str]:
    return [item["title"] for item in data(await client.get("/api/v1/employees/me/documents", headers=await auth(user_key)))]


@pytest.mark.parametrize("route", ["documents", "documents/payslip"])
async def test_manager_cannot_upload_into_other_teams_records(client, auth, route):
    response = await client.post(f"/api/v1/employees/{OUTSIDER.id}/{route}", headers=await auth("manager"), **_upload("Cross-team upload"))
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert "Cross-team upload" not in await _document_titles(client, auth, "outsider")  # nothing stored


@pytest.mark.parametrize("route", ["documents", "documents/payslip"])
async def test_manager_uploads_for_direct_report(client, auth, route):
    response = await client.post(f"/api/v1/employees/{EMPLOYEE.id}/{route}", headers=await auth("manager"), **_upload("Report upload"))
    assert response.status_code == 200
    assert "Report upload" in await _document_titles(client, auth, "employee")


async def test_manager_cannot_upload_own_payslip_but_can_upload_own_document(client, auth):
    headers = await auth("manager")
    payslip = await client.post(f"/api/v1/employees/{MANAGER.id}/documents/payslip", headers=headers, **_upload("Own payslip"))
    assert payslip.status_code == 403
    document = await client.post(f"/api/v1/employees/{MANAGER.id}/documents", headers=headers, **_upload("Own document"))
    assert document.status_code == 200


@pytest.mark.parametrize("route", ["documents", "documents/payslip"])
async def test_admin_uploads_for_anyone(client, auth, route):
    response = await client.post(f"/api/v1/employees/{OUTSIDER.id}/{route}", headers=await auth("admin"), **_upload("Admin upload"))
    assert response.status_code == 200


@pytest.mark.parametrize("target", [OUTSIDER, MANAGER, ADMIN], ids=["other-team", "self", "admin"])
async def test_manager_cannot_change_job_title_outside_direct_reports(client, auth, target):
    response = await client.put(f"/api/v1/employees/{target.id}/job-title", json={"designation": "Chief Everything"},
                                headers=await auth("manager"))
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    detail = data(await client.get(f"/api/v1/employees/{target.id}", headers=await auth("admin")))
    assert detail["job_title"] != "Chief Everything"


async def test_manager_changes_direct_reports_job_title_and_admin_anyones(client, auth):
    report = await client.put(f"/api/v1/employees/{EMPLOYEE.id}/job-title", json={"designation": "Senior Engineer"},
                              headers=await auth("manager"))
    assert report.status_code == 200
    admin = await client.put(f"/api/v1/employees/{OUTSIDER.id}/job-title", json={"designation": "Analyst"}, headers=await auth("admin"))
    assert admin.status_code == 200


async def test_manager_reads_project_assignments_only_within_scope(client, auth):
    headers = await auth("manager")
    assert (await client.get(f"/api/v1/employees/{OUTSIDER.id}/projects", headers=headers)).status_code == 403
    assert (await client.get(f"/api/v1/employees/{EMPLOYEE.id}/projects", headers=headers)).status_code == 200
    assert (await client.get(f"/api/v1/employees/{MANAGER.id}/projects", headers=headers)).status_code == 200
    assert (await client.get(f"/api/v1/employees/{OUTSIDER.id}/projects", headers=await auth("admin"))).status_code == 200


async def test_removing_assignment_of_unknown_employee_is_not_found(client, auth):
    response = await client.delete(f"/api/v1/employees/9999/projects/{PROJECT_ID}", headers=await auth("admin"))
    assert response.status_code == 404
    assert error_code(response) == "ASSIGNMENT_NOT_FOUND"


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


# --- GET /employees/{id}: field-level visibility (production hardening B-01, approved remediation of R1) ----------
# Old behaviour (pinned here until 2026-10-11): every field, including salary, bank, PAN, DOB and PF/ESI identifiers,
# was returned to ANY authenticated user, so sequential ids exposed the whole organisation.
# Invariant now: financial/government-ID fields -> the employee themself and ADMIN only; phone/address/blood type ->
# additionally the employee's direct manager. Restricted fields are returned as null (response shape unchanged).

PERSONAL_KEYS = {"phone", "address", "blood_type"}
PERSONAL_VALUES = {"phone": "+1-555-0100", "address": "1 Test Street", "blood_type": "O+"}


@pytest.fixture
async def employee_with_personal_fields(db):
    from sqlalchemy import update

    from app.models.employee import Employee

    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(**PERSONAL_VALUES))
    await db.commit()


async def _detail(client, auth, viewer: str, employee_id: int = EMPLOYEE.id) -> dict:
    response = await client.get(f"/api/v1/employees/{employee_id}", headers=await auth(viewer))
    assert response.status_code == 200
    return data(response)


@pytest.mark.parametrize("viewer", ["employee", "admin"], ids=["self", "admin"])
async def test_self_and_admin_see_all_restricted_fields(client, auth, employee_with_personal_fields, viewer):
    detail = await _detail(client, auth, viewer)
    for key, value in EMPLOYEE_SENSITIVE.items():
        assert detail[key] == value, key
    for key, value in PERSONAL_VALUES.items():
        assert detail[key] == value, key


async def test_direct_manager_sees_personal_but_not_financial_fields(client, auth, employee_with_personal_fields):
    detail = await _detail(client, auth, "manager")  # MANAGER is EMPLOYEE's direct manager
    for key, value in PERSONAL_VALUES.items():
        assert detail[key] == value, key
    assert SENSITIVE_KEYS <= set(detail)
    assert all(detail[key] is None for key in SENSITIVE_KEYS)


async def test_peer_sees_neither_financial_nor_personal_fields(client, auth, employee_with_personal_fields):
    detail = await _detail(client, auth, "outsider")  # same role, not the manager
    assert all(detail[key] is None for key in SENSITIVE_KEYS | PERSONAL_KEYS)
    assert (detail["id"], detail["name"], detail["email"]) == (EMPLOYEE.id, "Employee Test", EMPLOYEE.email)  # directory info kept


async def test_manager_of_someone_else_sees_no_restricted_fields(client, auth):
    detail = await _detail(client, auth, "manager", OUTSIDER.id)  # OUTSIDER has no manager
    assert all(detail[key] is None for key in SENSITIVE_KEYS | PERSONAL_KEYS)


async def test_enumerating_ids_does_not_disclose_sensitive_values(client, auth, employee_with_personal_fields):
    headers = await auth("outsider")
    seen = 0
    for employee_id in range(1, 8):  # every seeded id plus unknown ones
        response = await client.get(f"/api/v1/employees/{employee_id}", headers=headers)
        if employee_id == OUTSIDER.id or not response.json().get("success"):
            continue  # own record, or EMPLOYEE_NOT_FOUND for unknown ids
        seen += 1
        for value in [*EMPLOYEE_SENSITIVE.values(), *PERSONAL_VALUES.values()]:
            assert str(value) not in response.text
    assert seen >= 4  # the enumeration really covered the other employees


async def test_system_payslip_is_dob_password_protected(client, auth):
    """pypdf upgrade guard (production hardening B-22): system payslips are generated with reportlab and encrypted with
    pypdf using the employee's DOB (DD-MM-YY). Verifies the encryption round trip on the upgraded pypdf."""
    from datetime import date, timedelta
    from io import BytesIO

    from pypdf import PdfReader

    headers = await auth("employee")
    previous_month = date.today().replace(day=1) - timedelta(days=1)  # same period rule as employees.py
    payslip_id = f"system-payslip-{EMPLOYEE.id}-{previous_month.year}-{previous_month.month:02d}"
    response = await client.get(f"/api/v1/employees/me/documents/{payslip_id}/download", headers=headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")

    reader = PdfReader(BytesIO(response.content))
    assert reader.is_encrypted
    assert not reader.decrypt("00-00-00")  # wrong password
    reader = PdfReader(BytesIO(response.content))
    password = date.fromisoformat(EMPLOYEE_SENSITIVE["date_of_birth"]).strftime("%d-%m-%y")  # seed: pan_dob = DOB
    assert reader.decrypt(password)
    assert len(reader.pages) >= 1


async def test_r1_exposure_does_not_extend_to_password_hash(client, auth):
    for user_key in ("employee", "manager", "admin"):
        response = await client.get(f"/api/v1/employees/{MANAGER.id}", headers=await auth(user_key))
        assert "hashed_password" not in response.text
        assert "$2b$" not in response.text
