"""Team calendar scope (finding S-06; approved product decisions 2026-10-17).

Old behaviour (until 2026-10-17): MANAGER and ADMIN saw every employee (inactive included); EMPLOYEE saw their whole
department (inactive included); every viewer got each person's role, approved-leave and WFH days.

Invariant now (GET /api/v1/calendar/team, `_calendar_scope` in endpoints/team_calendar.py):
- ADMIN: every ACTIVE employee. MANAGER: self + ACTIVE direct reports (employees.manager_id, one level). EMPLOYEE: self.
  Departments play no part. Inactive people (and their markers) never appear.
- Leave and WFH markers only for the people in scope; LEAVE = approved leave only (never type, reason or pending).
- `employee_role` is returned to ADMIN only.
- Role/status come from the database on every request; query parameters and JWT role claims cannot widen the scope.
Holidays and birthdays are unchanged (tested at the end).
"""

from datetime import date, time

import pytest
from sqlalchemy import select, update

from app.core.security import create_access_token
from app.models.attendance_log import AttendanceLog
from app.models.employee import Employee
from app.models.enums import AttendanceStatus, EmployeeStatus, LeaveRequestStatus, LeaveType, Role
from app.models.leave_request import LeaveRequest
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER, OUTSIDER
from tests.support.http import data

# A fixed month far from the shared seed, whose leave dates are relative to today (tests/support/data.py `future`),
# so only this module's markers can appear.
Y, M = 2031, 3
URL = "/api/v1/calendar/team"
FINANCE_PEER = 30  # ACTIVE, Finance like MANAGER, no manager: a department peer who is not a direct report
SECRET_REASON = "confidential medical appointment"
EMPLOYEE_MARKERS = {date(Y, M, 2).isoformat(): "LEAVE", date(Y, M, 3).isoformat(): "WFH"}


def _day(n: int) -> str:
    return date(Y, M, n).isoformat()


async def _add_employee(db, employee_id: int, name: str, department_id: int | None, manager_id: int | None,
                        role: Role = Role.EMPLOYEE) -> None:
    hashed = (await db.execute(select(Employee.hashed_password).where(Employee.id == MANAGER.id))).scalar_one()
    db.add(Employee(id=employee_id, name=name, email=f"{name.split()[0].lower()}{employee_id}@hrms-test.dev",
                    hashed_password=hashed, department_id=department_id, manager_id=manager_id, role=role,
                    status=EmployeeStatus.ACTIVE, joining_date=date(2023, 1, 1)))
    await db.commit()


def _leave(employee_id: int, day: int, status: LeaveRequestStatus, kind: LeaveType = LeaveType.SICK) -> LeaveRequest:
    return LeaveRequest(employee_id=employee_id, leave_type=kind, start_date=date(Y, M, day), end_date=date(Y, M, day),
                        reason=SECRET_REASON, status=status)


def _wfh(employee_id: int, day: int) -> AttendanceLog:
    return AttendanceLog(employee_id=employee_id, date=date(Y, M, day), clock_in=time(9), status=AttendanceStatus.WFH,
                         work_mode="WFH")


@pytest.fixture
async def calendar_data(db, people):
    """Markers for everyone, plus a Finance peer of MANAGER who does not report to them."""
    await _add_employee(db, FINANCE_PEER, "Fiona Peer", department_id=2, manager_id=None)
    db.add_all([
        _leave(EMPLOYEE.id, 2, LeaveRequestStatus.APPROVED), _wfh(EMPLOYEE.id, 3),
        _leave(EMPLOYEE.id, 4, LeaveRequestStatus.PENDING), _leave(EMPLOYEE.id, 5, LeaveRequestStatus.REJECTED),
        _leave(OUTSIDER.id, 6, LeaveRequestStatus.APPROVED), _wfh(OUTSIDER.id, 7),
        _leave(INACTIVE.id, 8, LeaveRequestStatus.APPROVED), _wfh(INACTIVE.id, 9),
        _leave(MANAGER.id, 10, LeaveRequestStatus.APPROVED, LeaveType.CASUAL),
        _wfh(people.indirect, 11), _wfh(people.report_b, 12), _wfh(FINANCE_PEER, 13), _wfh(ADMIN.id, 14),
    ])
    await db.commit()
    return people


async def _calendar(client, headers, **params) -> dict:
    response = await client.get(URL, headers=headers, params={"year": Y, "month": M, **params})
    assert response.status_code == 200, response.text
    return data(response)


def _people(body: dict) -> dict[int, dict]:
    return {item["employee_id"]: item for item in body["items"]}


# --- authentication -----------------------------------------------------------------------------------------------


async def test_anonymous_is_401(client):
    assert (await client.get(URL, params={"year": Y, "month": M})).status_code == 401


async def test_inactive_viewer_is_403(client, auth):
    response = await client.get(URL, headers=await auth("inactive"), params={"year": Y, "month": M})
    assert response.status_code == 403 and response.json()["detail"]["error"]["code"] == "USER_INACTIVE"


# --- scope per role -----------------------------------------------------------------------------------------------


async def test_employee_sees_only_themselves(client, auth, calendar_data):
    for user, own in (("employee", EMPLOYEE.id), ("outsider", OUTSIDER.id)):
        assert set(_people(await _calendar(client, await auth(user)))) == {own}, user  # no department peers


async def test_employee_with_reports_in_data_still_sees_only_themselves(client, auth, calendar_data):
    """`indirect` reports to EMPLOYEE in the data, but EMPLOYEE is not a manager: the role decides, not manager_id."""
    assert set(_people(await _calendar(client, await auth("employee")))) == {EMPLOYEE.id}


async def test_manager_sees_self_and_active_direct_reports_only(client, auth, calendar_data):
    people = calendar_data
    visible = set(_people(await _calendar(client, await auth("manager"))))
    assert visible == {MANAGER.id, EMPLOYEE.id}
    assert people.indirect not in visible  # reports to EMPLOYEE: an indirect report of MANAGER
    assert FINANCE_PEER not in visible  # same department, not a direct report
    assert OUTSIDER.id not in visible and people.report_b not in visible and people.manager_b not in visible  # other teams
    assert INACTIVE.id not in visible  # direct report, but inactive


async def test_another_manager_sees_only_their_own_team(client, auth, calendar_data):
    people = calendar_data
    body = await _calendar(client, people.headers(people.manager_b))
    assert set(_people(body)) == {people.manager_b, people.report_b}


async def test_admin_sees_every_active_employee(client, auth, calendar_data):
    people = calendar_data
    visible = set(_people(await _calendar(client, await auth("admin"))))
    assert visible == {ADMIN.id, MANAGER.id, EMPLOYEE.id, OUTSIDER.id, FINANCE_PEER,
                       people.manager_b, people.report_b, people.indirect}
    assert INACTIVE.id not in visible


# --- markers ------------------------------------------------------------------------------------------------------


async def test_markers_follow_the_same_scope(client, auth, calendar_data):
    people = calendar_data
    manager = _people(await _calendar(client, await auth("manager")))
    assert manager[EMPLOYEE.id]["markers"] == EMPLOYEE_MARKERS
    assert manager[MANAGER.id]["markers"] == {_day(10): "LEAVE"}
    admin = _people(await _calendar(client, await auth("admin")))
    assert admin[OUTSIDER.id]["markers"] == {_day(6): "LEAVE", _day(7): "WFH"}
    assert admin[people.indirect]["markers"] == {_day(11): "WFH"}
    employee = _people(await _calendar(client, await auth("employee")))
    assert employee[EMPLOYEE.id]["markers"] == EMPLOYEE_MARKERS


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_inactive_people_and_their_markers_never_appear(client, auth, calendar_data, user):
    body = await _calendar(client, await auth(user))
    assert INACTIVE.id not in _people(body)
    all_markers = {day for item in body["items"] for day in item["markers"]}
    assert _day(8) not in all_markers and _day(9) not in all_markers


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_only_approved_leave_and_no_leave_details(client, auth, calendar_data, user):
    response = await client.get(URL, headers=await auth(user), params={"year": Y, "month": M})
    own = {"employee": EMPLOYEE.id, "manager": EMPLOYEE.id, "admin": EMPLOYEE.id}[user]
    markers = _people(data(response))[own]["markers"]
    assert _day(4) not in markers and _day(5) not in markers  # pending and rejected leave never shown
    text = response.text
    for leaked in (SECRET_REASON, "SICK", "CASUAL", "EARNED", "PENDING", "REJECTED", "APPROVED", "leave_type", "reason"):
        assert leaked not in text, (user, leaked)
    assert set(markers.values()) <= {"LEAVE", "WFH"}


# --- response fields ----------------------------------------------------------------------------------------------


async def test_role_field_is_admin_only(client, auth, calendar_data):
    admin = await _calendar(client, await auth("admin"))
    assert all(set(item) == {"employee_id", "employee_name", "employee_role", "markers"} for item in admin["items"])
    for user in ("manager", "employee"):
        body = await _calendar(client, await auth(user))
        assert all(set(item) == {"employee_id", "employee_name", "markers"} for item in body["items"]), user
        assert '"employee_role"' not in str(body).replace("'", '"')
    assert set(admin) == {"year", "month", "days", "items"}  # envelope unchanged


# --- forgery and changes over time --------------------------------------------------------------------------------


async def test_forged_query_parameters_are_ignored(client, auth, calendar_data):
    forged = {"employee_id": OUTSIDER.id, "department_id": 1, "scope": "ORG", "role": "ADMIN", "manager_id": EMPLOYEE.id}
    assert set(_people(await _calendar(client, await auth("employee"), **forged))) == {EMPLOYEE.id}
    assert set(_people(await _calendar(client, await auth("manager"), **forged))) == {MANAGER.id, EMPLOYEE.id}


@pytest.mark.parametrize("subject,expected", [(EMPLOYEE.id, {EMPLOYEE.id}), (MANAGER.id, {MANAGER.id, EMPLOYEE.id})])
async def test_forged_role_claims_are_ignored(client, calendar_data, subject, expected):
    token = create_access_token(subject=str(subject), role="ADMIN")
    body = await _calendar(client, {"Authorization": f"Bearer {token}"})
    assert set(_people(body)) == expected
    assert all("employee_role" not in item for item in body["items"])


async def test_reporting_line_changes_apply_on_the_next_request(client, auth, db, calendar_data):
    headers = await auth("manager")
    assert OUTSIDER.id not in _people(await _calendar(client, headers))
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(manager_id=MANAGER.id))
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(manager_id=None))
    await db.commit()
    assert set(_people(await _calendar(client, headers))) == {MANAGER.id, OUTSIDER.id}


async def test_deactivation_applies_on_the_next_request(client, auth, db, calendar_data):
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    assert set(_people(await _calendar(client, await auth("manager")))) == {MANAGER.id}
    assert EMPLOYEE.id not in _people(await _calendar(client, await auth("admin")))


async def test_department_changes_never_widen_access(client, auth, db, calendar_data):
    await db.execute(update(Employee).where(Employee.id.in_([OUTSIDER.id, EMPLOYEE.id])).values(department_id=2))
    await db.commit()
    assert set(_people(await _calendar(client, await auth("manager")))) == {MANAGER.id, EMPLOYEE.id}  # OUTSIDER now a peer
    assert set(_people(await _calendar(client, await auth("employee")))) == {EMPLOYEE.id}
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(department_id=None))
    await db.commit()
    assert set(_people(await _calendar(client, await auth("employee")))) == {EMPLOYEE.id}  # no department: still self


@pytest.mark.parametrize("params", [{"year": Y, "month": 13}, {"year": 1999, "month": 1}, {"month": 1}, {}])
async def test_malformed_parameters_are_rejected(client, auth, params):
    assert (await client.get(URL, headers=await auth("manager"), params=params)).status_code == 422


# --- unchanged neighbours ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_holidays_and_birthdays_are_unchanged(client, auth, user):
    headers = await auth(user)
    holidays = await client.get("/api/v1/calendar/holidays", headers=headers)
    assert holidays.status_code == 200 and isinstance(data(holidays), list)
    birthdays = await client.get("/api/v1/calendar/birthdays", headers=headers)
    assert birthdays.status_code == 200
    assert all(set(item) == {"name", "team", "date"} for item in data(birthdays))
