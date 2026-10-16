"""Employee directory scope (RBAC remediation step 2, approved Decision 1).

Rules under test (enforced by the backend handlers, not the UI):
- employee list: EMPLOYEE 403; MANAGER self + direct reports (active); ADMIN all active employees;
- employee detail: EMPLOYEE own record; MANAGER self + direct reports; ADMIN anyone. Out-of-scope ids return the same
  404 EMPLOYEE_NOT_FOUND as unknown ids (no existence disclosure);
- organisation chart: ACTIVE employees only, fields id/name/role/manager_id (+ children); a manager who is not
  visible is never referenced;
- employee photos: only employees visible on the chart (active), for every role; forged/inactive ids -> 404.
Synthetic fixtures only (tests/support/data.py).
"""

from pathlib import Path

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from tests.support.data import ADMIN, EMPLOYEE, EMPLOYEE_SENSITIVE, INACTIVE, MANAGER, OUTSIDER
from tests.support.http import data, error_code

LIST, CHART = "/api/v1/employees", "/api/v1/org/tree"
DIRECTORY_FIELDS = {"id", "name", "email", "department_id", "role", "status", "joining_date"}
CHART_FIELDS = {"id", "name", "role", "manager_id", "children"}
SENSITIVE_VALUES = [str(value) for value in EMPLOYEE_SENSITIVE.values()]
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")


def detail_url(employee_id: int) -> str:
    return f"/api/v1/employees/{employee_id}"


def photo_url(employee_id: int) -> str:
    return f"/api/v1/employees/{employee_id}/profile-picture"


async def listed_ids(client, auth, role: str, **params) -> tuple[set[int], int]:
    response = await client.get(LIST, params={"limit": 100, **params}, headers=await auth(role))
    assert response.status_code == 200, response.text
    body = data(response)
    for item in body["items"]:
        assert set(item) == DIRECTORY_FIELDS
    return {item["id"] for item in body["items"]}, body["meta"]["total"]


def chart_nodes(tree: list[dict]) -> list[dict]:
    nodes = []
    for node in tree:
        nodes.append(node)
        nodes.extend(chart_nodes(node["children"]))
    return nodes


@pytest.fixture
async def photos(db):
    """Give OUTSIDER (active) and INACTIVE a stored profile photo."""
    directory = Path(settings.profile_photo_upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for user in (OUTSIDER, INACTIVE):
        path = directory / f"user-{user.id}.png"
        path.write_bytes(PNG)
        await db.execute(update(Employee).where(Employee.id == user.id)
                         .values(profile_photo_path=str(path), profile_photo_mime="image/png"))
    await db.commit()


# --- EMPLOYEE -------------------------------------------------------------------------------------------------------


async def test_employee_cannot_list_the_directory(client, auth):
    """Decision 1 (was: every active employee with email and joining date)."""
    response = await client.get(LIST, params={"limit": 100}, headers=await auth("employee"))
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert "Outsider Test" not in response.text and OUTSIDER.email not in response.text


@pytest.mark.parametrize("params", [{"q": "Outsider"}, {"q": str(OUTSIDER.id)}, {"department_id": 1}, {"location": "a"}])
async def test_employee_list_filters_cannot_reopen_the_directory(client, auth, params):
    response = await client.get(LIST, params=params, headers=await auth("employee"))
    assert response.status_code == 403


async def test_employee_reads_own_record_with_own_sensitive_fields(client, auth):
    body = data(await client.get(detail_url(EMPLOYEE.id), headers=await auth("employee")))
    assert body["id"] == EMPLOYEE.id
    for key, value in EMPLOYEE_SENSITIVE.items():
        assert body[key] == value  # own financial fields remain visible to self (B-01)


@pytest.mark.parametrize("target", [OUTSIDER.id, MANAGER.id, ADMIN.id, INACTIVE.id])
async def test_employee_cannot_read_other_records(client, auth, target):
    """Decision 1 (was: directory fields of anyone, sensitive fields nulled)."""
    response = await client.get(detail_url(target), headers=await auth("employee"))
    assert response.status_code == 404 and error_code(response) == "EMPLOYEE_NOT_FOUND"


@pytest.mark.parametrize("role", ["employee", "manager"])
async def test_out_of_scope_and_unknown_ids_are_indistinguishable(client, auth, role):
    headers = await auth(role)
    hidden = await client.get(detail_url(OUTSIDER.id), headers=headers)
    for forged in (999_999, 0, 2**31 - 1):
        unknown = await client.get(detail_url(forged), headers=headers)
        assert (unknown.status_code, unknown.json()) == (hidden.status_code, hidden.json())


async def test_unknown_id_is_a_real_404_for_admin_too(client, auth):
    """Was HTTP 200 with an EMPLOYEE_NOT_FOUND body; now a 404 so that hidden and unknown ids look the same."""
    response = await client.get(detail_url(999_999), headers=await auth("admin"))
    assert response.status_code == 404 and error_code(response) == "EMPLOYEE_NOT_FOUND"


# --- MANAGER --------------------------------------------------------------------------------------------------------


async def test_manager_lists_only_self_and_direct_reports(client, auth):
    ids, total = await listed_ids(client, auth, "manager")
    assert ids == {MANAGER.id, EMPLOYEE.id}  # INACTIVE report excluded (active directory), OUTSIDER/ADMIN not reports
    assert total == len(ids)  # counts and pagination do not reveal hidden employees


@pytest.mark.parametrize("params", [{"q": "Outsider"}, {"q": str(OUTSIDER.id)}, {"q": "test"}, {"department_id": 1},
                                    {"offset": 1, "limit": 1}])
async def test_manager_filters_stay_within_scope(client, auth, params):
    ids, total = await listed_ids(client, auth, "manager", **params)
    assert ids <= {MANAGER.id, EMPLOYEE.id}
    assert OUTSIDER.id not in ids and ADMIN.id not in ids and total <= 2


@pytest.mark.parametrize("target", [MANAGER.id, EMPLOYEE.id, INACTIVE.id])
async def test_manager_reads_self_and_direct_reports(client, auth, target):
    response = await client.get(detail_url(target), headers=await auth("manager"))
    assert response.status_code == 200 and data(response)["id"] == target


async def test_manager_sees_reports_personal_but_not_financial_fields(client, auth):
    body = data(await client.get(detail_url(EMPLOYEE.id), headers=await auth("manager")))
    assert all(body[key] is None for key in EMPLOYEE_SENSITIVE)  # B-01 unchanged


@pytest.mark.parametrize("target", [OUTSIDER.id, ADMIN.id])
async def test_manager_cannot_read_non_reports(client, auth, target):
    """Decision 1 (was: directory fields of any employee)."""
    response = await client.get(detail_url(target), headers=await auth("manager"))
    assert response.status_code == 404 and error_code(response) == "EMPLOYEE_NOT_FOUND"


async def test_indirect_reports_are_not_in_scope(client, auth, db):
    """Direct reports only: OUTSIDER reporting to EMPLOYEE (who reports to MANAGER) stays hidden from MANAGER."""
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(manager_id=EMPLOYEE.id))
    await db.commit()
    assert (await client.get(detail_url(OUTSIDER.id), headers=await auth("manager"))).status_code == 404
    ids, _ = await listed_ids(client, auth, "manager")
    assert OUTSIDER.id not in ids


async def test_directory_list_is_not_the_skill_search(client, auth):
    """The approved organisation-wide manager skill search is a separate, restricted path (plan step 3); the general
    directory does not carry skills or widen to it."""
    response = await client.get(LIST, params={"q": "Python"}, headers=await auth("manager"))
    assert all("skill" not in key for item in data(response)["items"] for key in item)


# --- ADMIN ----------------------------------------------------------------------------------------------------------


async def test_admin_keeps_the_organisation_wide_directory(client, auth):
    ids, total = await listed_ids(client, auth, "admin")
    assert ids == {ADMIN.id, MANAGER.id, EMPLOYEE.id, OUTSIDER.id} and total == 4  # active only, as before


@pytest.mark.parametrize("target", [OUTSIDER.id, EMPLOYEE.id, INACTIVE.id])
async def test_admin_reads_any_record(client, auth, target):
    response = await client.get(detail_url(target), headers=await auth("admin"))
    assert response.status_code == 200 and data(response)["id"] == target


# --- organisation chart and photos (all roles) ----------------------------------------------------------------------


@pytest.mark.parametrize("role", ["employee", "manager", "admin"])
async def test_chart_shows_active_employees_with_approved_fields_only(client, auth, role):
    response = await client.get(CHART, headers=await auth(role))
    nodes = chart_nodes(data(response)["items"])
    assert {node["id"] for node in nodes} == {ADMIN.id, MANAGER.id, EMPLOYEE.id, OUTSIDER.id}  # INACTIVE excluded
    for node in nodes:
        assert set(node) == CHART_FIELDS
    assert "@" not in response.text  # no emails
    assert not [value for value in SENSITIVE_VALUES if value in response.text]


async def test_chart_never_references_a_hidden_manager(client, auth, db):
    await db.execute(update(Employee).where(Employee.id == MANAGER.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    nodes = chart_nodes(data(await client.get(CHART, headers=await auth("admin")))["items"])
    by_id = {node["id"]: node for node in nodes}
    assert MANAGER.id not in by_id
    assert by_id[EMPLOYEE.id]["manager_id"] is None  # promoted to a root, not orphaned or pointing at a hidden id


@pytest.mark.parametrize("role", ["employee", "manager", "admin"])
async def test_photos_follow_chart_visibility(client, auth, photos, role):
    headers = await auth(role)
    visible = await client.get(photo_url(OUTSIDER.id), headers=headers)  # active -> on the chart
    assert visible.status_code == 200 and visible.content == PNG
    hidden = await client.get(photo_url(INACTIVE.id), headers=headers)  # inactive -> not on the chart
    unknown = await client.get(photo_url(999_999), headers=headers)
    assert hidden.status_code == unknown.status_code == 404 and hidden.json() == unknown.json()


# --- cross-cutting --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [LIST, detail_url(EMPLOYEE.id), CHART, photo_url(OUTSIDER.id)])
async def test_anonymous_requests_are_unauthenticated(client, url):
    assert (await client.get(url)).status_code == 401


@pytest.mark.parametrize("url", [LIST, detail_url(EMPLOYEE.id), CHART, photo_url(OUTSIDER.id)])
async def test_inactive_user_token_is_rejected(client, url):
    from app.core.security import create_access_token

    token = create_access_token(subject=str(INACTIVE.id), role=INACTIVE.role.value)
    response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"


async def test_forged_role_claim_does_not_unlock_the_directory(client):
    """The role comes from the database, not the JWT: an EMPLOYEE token claiming ADMIN is still an EMPLOYEE."""
    from app.core.security import create_access_token

    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get(LIST, headers=headers)).status_code == 403
    assert (await client.get(detail_url(OUTSIDER.id), headers=headers)).status_code == 404


@pytest.mark.parametrize("role", ["manager", "admin"])
async def test_list_and_detail_payloads_carry_no_unintended_fields(client, auth, role):
    listing = await client.get(LIST, params={"limit": 100}, headers=await auth(role))
    assert not [value for value in SENSITIVE_VALUES if value in listing.text]
    assert "hashed_password" not in listing.text and "profile_photo_path" not in listing.text
    detail = await client.get(detail_url(EMPLOYEE.id), headers=await auth(role))
    assert "hashed_password" not in detail.text and "profile_photo_path" not in detail.text
