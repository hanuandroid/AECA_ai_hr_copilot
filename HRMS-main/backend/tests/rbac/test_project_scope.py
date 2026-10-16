"""Project catalogue and status authorization (RBAC remediation step 6, approved decision 5 / X-3).

Old behaviour (until 2026-10-17): any MANAGER could create catalogue projects and change the status of any project.

Invariant now:
- POST /employees/projects/catalog: ADMIN only (the single creation path; assignment never creates projects).
- PUT /employees/projects/catalog/{id}/status: ADMIN any project. MANAGER only while they, or an ACTIVE direct report
  (one level), are assigned to the project in employee_projects - evaluated on every request, no ownership field.
  Otherwise 403 FORBIDDEN (the catalogue itself is visible to every manager, so existence is not hidden).
- EMPLOYEE: 403 on both, unchanged.
The AI has no project-status action, and PROJECT_CREATE stays ADMIN-only (tests/ai/actions).
"""

import pytest
from sqlalchemy import func, select, update

from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.employee_project import EmployeeProject
from app.models.enums import EmployeeStatus, ProjectStatus
from app.models.project import Project
from tests.support.data import EMPLOYEE, MANAGER, OUTSIDER, PROJECT_ID
from tests.support.http import data, error_code

CATALOG = "/api/v1/employees/projects/catalog"


async def _assign(db, employee_id: int, project_id: int = PROJECT_ID) -> None:
    db.add(EmployeeProject(employee_id=employee_id, project_id=project_id, role_on_project="Engineer"))
    await db.commit()


async def _status_of(db, project_id: int = PROJECT_ID) -> ProjectStatus:
    db.expire_all()
    return (await db.execute(select(Project.status).where(Project.id == project_id))).scalar_one()


def _set_status(client, headers, project_id: int = PROJECT_ID, value: str = "ON_HOLD", **extra):
    return client.put(f"{CATALOG}/{project_id}/status", headers=headers, json={"status": value, **extra})


# --- catalogue creation --------------------------------------------------------------------------------------------


async def test_only_admin_creates_catalogue_projects(client, auth, db):
    before = (await db.execute(select(func.count(Project.id)))).scalar_one()
    for user in ("manager", "employee"):
        response = await client.post(CATALOG, headers=await auth(user), json={"name": f"Shadow {user}", "status": "PLANNED"})
        assert response.status_code == 403 and error_code(response) == "FORBIDDEN", user
    db.expire_all()
    assert (await db.execute(select(func.count(Project.id)))).scalar_one() == before
    created = await client.post(CATALOG, headers=await auth("admin"), json={"name": "Admin Project"})
    assert created.status_code == 200 and data(created)["status"] == "ONGOING"


async def test_forged_admin_claim_cannot_create_projects(client):
    token = create_access_token(subject=str(MANAGER.id), role="ADMIN")
    response = await client.post(CATALOG, headers={"Authorization": f"Bearer {token}"}, json={"name": "Forged"})
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"


async def test_assignment_never_creates_a_project(client, auth, db):
    before = (await db.execute(select(func.count(Project.id)))).scalar_one()
    response = await client.post(f"/api/v1/employees/{EMPLOYEE.id}/projects", headers=await auth("manager"),
                                 json={"project_id": 999, "project_name": "Invented", "role_on_project": "Lead"})
    assert response.status_code == 404 and error_code(response) == "PROJECT_NOT_FOUND"
    db.expire_all()
    assert (await db.execute(select(func.count(Project.id)))).scalar_one() == before


# --- status changes ------------------------------------------------------------------------------------------------


async def test_manager_without_team_assignment_cannot_change_status(client, auth, db):
    response = await _set_status(client, await auth("manager"))
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _status_of(db) == ProjectStatus.ONGOING


async def test_manager_assigned_to_the_project_changes_status(client, auth, db):
    await _assign(db, MANAGER.id)
    response = await _set_status(client, await auth("manager"))
    assert response.status_code == 200 and await _status_of(db) == ProjectStatus.ON_HOLD


async def test_manager_whose_direct_report_is_assigned_changes_status(client, auth, db):
    await _assign(db, EMPLOYEE.id)
    response = await _set_status(client, await auth("manager"), value="completed")
    assert response.status_code == 200 and await _status_of(db) == ProjectStatus.COMPLETED


async def test_assignments_outside_the_team_do_not_count(client, auth, db, people):
    await _assign(db, OUTSIDER.id)  # no manager
    await _assign(db, people.report_b)  # another manager's report
    await _assign(db, people.indirect)  # indirect report of MANAGER (reports to EMPLOYEE)
    response = await _set_status(client, await auth("manager"))
    assert response.status_code == 403 and await _status_of(db) == ProjectStatus.ONGOING
    other = await _set_status(client, people.headers(people.manager_b))  # manager_b's report IS assigned
    assert other.status_code == 200


async def test_inactive_direct_report_assignment_does_not_count(client, auth, db):
    await _assign(db, EMPLOYEE.id)
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    response = await _set_status(client, await auth("manager"))
    assert response.status_code == 403 and await _status_of(db) == ProjectStatus.ONGOING


async def test_assignment_changes_apply_to_the_next_request(client, auth, db):
    manager = await auth("manager")
    assert (await _set_status(client, manager)).status_code == 403
    assigned = await client.post(f"/api/v1/employees/{EMPLOYEE.id}/projects", headers=manager, json={"project_id": PROJECT_ID})
    assert assigned.status_code == 200
    assert (await _set_status(client, manager, value="ON_HOLD")).status_code == 200
    removed = await client.delete(f"/api/v1/employees/{EMPLOYEE.id}/projects/{PROJECT_ID}", headers=manager)
    assert removed.status_code == 200
    assert (await _set_status(client, manager, value="ONGOING")).status_code == 403
    assert await _status_of(db) == ProjectStatus.ON_HOLD
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(manager_id=None))  # report leaves the team
    await _assign(db, EMPLOYEE.id)
    assert (await _set_status(client, manager, value="ONGOING")).status_code == 403


async def test_forged_body_fields_do_not_grant_scope(client, auth, db, people):
    await _assign(db, people.report_b, project_id=PROJECT_ID)
    response = await _set_status(client, await auth("manager"), employee_id=EMPLOYEE.id, manager_id=MANAGER.id,
                                 assigned=True, team=[EMPLOYEE.id], project_id=PROJECT_ID)
    assert response.status_code == 403 and await _status_of(db) == ProjectStatus.ONGOING


async def test_scope_is_per_project(client, auth, db):
    db.add(Project(id=50, name="Other Project", description="x", status=ProjectStatus.PLANNED))
    await db.commit()
    await _assign(db, EMPLOYEE.id, project_id=50)
    assert (await _set_status(client, await auth("manager"), project_id=50)).status_code == 200
    assert (await _set_status(client, await auth("manager"), project_id=PROJECT_ID)).status_code == 403


async def test_admin_changes_any_status_and_unknown_project_is_404(client, auth, db):
    assert (await _set_status(client, await auth("admin"))).status_code == 200
    assert await _status_of(db) == ProjectStatus.ON_HOLD
    for user in ("admin", "manager"):
        missing = await _set_status(client, await auth(user), project_id=999)
        assert missing.status_code == 404 and error_code(missing) == "PROJECT_NOT_FOUND"


async def test_invalid_status_still_rejected_for_authorized_callers(client, auth, db):
    await _assign(db, MANAGER.id)
    response = await _set_status(client, await auth("manager"), value="CANCELLED")
    assert response.status_code == 400 and error_code(response) == "INVALID_PROJECT_STATUS"
    assert await _status_of(db) == ProjectStatus.ONGOING


@pytest.mark.parametrize("user", ["employee", "outsider"])
async def test_employees_cannot_change_status_even_when_assigned(client, auth, db, user):
    await _assign(db, EMPLOYEE.id)
    await _assign(db, OUTSIDER.id)
    response = await _set_status(client, await auth(user))
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert await _status_of(db) == ProjectStatus.ONGOING


async def test_anonymous_and_inactive(client, auth, db):
    assert (await _set_status(client, {})).status_code == 401
    assert (await client.post(CATALOG, json={"name": "Anon"})).status_code == 401
    inactive = await _set_status(client, await auth("inactive"))
    assert inactive.status_code == 403 and error_code(inactive) == "USER_INACTIVE"
    assert await _status_of(db) == ProjectStatus.ONGOING
