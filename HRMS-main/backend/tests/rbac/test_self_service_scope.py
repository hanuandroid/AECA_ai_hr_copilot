"""Object scope of the self-service endpoints (finding S-04: attendance, finance, calendar neighbours).

Approved rules (authorization-matrix.md §1, page-endpoint-inventory.md §1.1):
- `/attendance/*` and `/finance/*` are SELF-ONLY for every role, ADMIN included: every query is filtered by the caller's
  database id; there is no employee-id parameter and no organisation-wide variant. Client-supplied ids are ignored.
- `/calendar/holidays`: the same list for every role. `/calendar/birthdays`: every role, fields name / team / next date
  only (no birth year, no ids, no DOB) - decision 1 keeps birthdays available to all roles.
- Anonymous: 401. Inactive token: 403 USER_INACTIVE.
- `/calendar/birthdays` lists ACTIVE employees only (S-08, approved 2026-10-17): former employees are excluded in the
  query, for every role, whatever the query parameters or token claims.
The org chart (`/org/tree`) is covered by tests/rbac/test_directory_scope.py; the team calendar by
tests/rbac/test_team_calendar_scope.py.
"""

from datetime import date, time

import pytest
from sqlalchemy import func, select, update

from app.api.v1.endpoints.attendance import _local_now
from app.core.security import create_access_token
from app.models.attendance_log import AttendanceLog
from app.models.employee import Employee
from app.models.enums import AttendanceStatus, EmployeeStatus
from app.models.payroll_record import PayrollRecord
from tests.support.data import ADMIN, EMPLOYEE, EMPLOYEE_SENSITIVE, INACTIVE, MANAGER, OUTSIDER
from tests.support.http import data

USERS = {"employee": EMPLOYEE.id, "manager": MANAGER.id, "admin": ADMIN.id, "outsider": OUTSIDER.id}
OTHER_PAN = "ZZZZZ9999Z"  # a value that only OUTSIDER's payroll row carries
ATTENDANCE_FIELDS = {"id", "date", "clock_in", "clock_out", "status", "work_mode", "punctuality"}


def _log(employee_id: int, day: date, mode: str = "PRESENT") -> AttendanceLog:
    status = AttendanceStatus.WFH if mode == "WFH" else AttendanceStatus.ON_TIME
    return AttendanceLog(employee_id=employee_id, date=day, clock_in=time(8, 30), status=status, work_mode=mode,
                         punctuality="ON_TIME")


@pytest.fixture
async def history(db) -> dict[int, set[int]]:
    """Fixed past dates (no dependency on today) for four people; returns employee id -> own log ids."""
    rows = [_log(employee_id, date(2025, 1, day)) for employee_id in USERS.values() for day in (6, 7, 8)]
    db.add_all(rows)
    await db.commit()
    owned: dict[int, set[int]] = {}
    for row in rows:
        owned.setdefault(row.employee_id, set()).add(row.id)
    return owned


async def _all_own_log_ids(db, employee_id: int) -> set[int]:
    return set((await db.execute(select(AttendanceLog.id).where(AttendanceLog.employee_id == employee_id))).scalars())


# --- attendance ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("user", list(USERS))
async def test_attendance_history_is_self_only_for_every_role(client, auth, db, history, user):
    response = await client.get("/api/v1/attendance/me", headers=await auth(user), params={"limit": 100})
    body = data(response)
    ids = {item["id"] for item in body["items"]}
    own = await _all_own_log_ids(db, USERS[user])
    assert ids == own and body["meta"]["total"] == len(own)  # the total counts only own rows
    others = set().union(*(logs for owner, logs in history.items() if owner != USERS[user]))
    assert not ids & others
    assert all(set(item) == ATTENDANCE_FIELDS for item in body["items"])  # no employee_id or other people's data


async def test_attendance_pagination_never_reaches_other_people(client, auth, db, history):
    headers = await auth("employee")
    own = await _all_own_log_ids(db, EMPLOYEE.id)
    seen = set()
    for offset in range(len(own) + 3):
        body = data(await client.get("/api/v1/attendance/me", headers=headers, params={"limit": 1, "offset": offset}))
        assert body["meta"]["total"] == len(own)
        seen |= {item["id"] for item in body["items"]}
    assert seen == own


@pytest.mark.parametrize("params", [{"employee_id": OUTSIDER.id}, {"user_id": OUTSIDER.id, "scope": "ORG"},
                                    {"employee_id": "1 OR 1=1"}])
async def test_forged_query_parameters_do_not_change_the_owner(client, auth, db, history, params):
    body = data(await client.get("/api/v1/attendance/me", headers=await auth("employee"), params={"limit": 100, **params}))
    assert {item["id"] for item in body["items"]} == await _all_own_log_ids(db, EMPLOYEE.id)


async def test_today_shows_only_the_callers_record(client, auth, db):
    today = _local_now().date()
    db.add(_log(OUTSIDER.id, today, "WFH"))
    await db.commit()
    assert data(await client.get("/api/v1/attendance/today", headers=await auth("employee"))) is None
    outsider = data(await client.get("/api/v1/attendance/today", headers=await auth("outsider")))
    assert outsider is not None and outsider["work_mode"] == "WFH"


async def test_clock_in_writes_only_the_callers_row_and_ignores_forged_owners(client, auth, db):
    today = _local_now().date()
    db.add(_log(OUTSIDER.id, today))
    await db.commit()
    before_outsider = await _all_own_log_ids(db, OUTSIDER.id)
    response = await client.post("/api/v1/attendance/clock-in", headers=await auth("employee"),
                                 json={"mode": "WFH", "employee_id": OUTSIDER.id, "date": "2020-01-01"})
    assert response.status_code == 200 and data(response)["work_mode"] == "WFH"
    db.expire_all()
    mine = (await db.execute(select(AttendanceLog).where(AttendanceLog.employee_id == EMPLOYEE.id,
                                                          AttendanceLog.date == today))).scalars().all()
    assert len(mine) == 1 and mine[0].id == data(response)["id"]
    assert await _all_own_log_ids(db, OUTSIDER.id) == before_outsider


async def test_clock_out_never_touches_someone_elses_open_record(client, auth, db):
    today = _local_now().date()
    db.add(_log(OUTSIDER.id, today))
    await db.commit()
    response = await client.post("/api/v1/attendance/clock-out", headers=await auth("manager"))
    assert response.status_code == 400 and response.json()["detail"]["error"]["code"] == "ATTENDANCE_NOT_CLOCKED_IN"
    db.expire_all()
    outsider = (await db.execute(select(AttendanceLog).where(AttendanceLog.employee_id == OUTSIDER.id,
                                                              AttendanceLog.date == today))).scalar_one()
    assert outsider.clock_out is None


# --- finance ------------------------------------------------------------------------------------------------------


@pytest.fixture
async def payroll(db) -> dict[int, set[int]]:
    rows = [PayrollRecord(employee_id=employee_id, month=date(2025, month, 1), gross=5000.0 + employee_id, net=4000.0,
                          deductions={"tax": 1000.0}, pan=OTHER_PAN if employee_id == OUTSIDER.id else f"PAN{employee_id:07d}",
                          pf_uan=f"UAN{employee_id}", esi_no=f"ESI{employee_id}")
            for employee_id in USERS.values() for month in (1, 2)]
    db.add_all(rows)
    await db.commit()
    owned: dict[int, set[int]] = {}
    for row in rows:
        owned.setdefault(row.employee_id, set()).add(row.id)
    return owned


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_payroll_is_self_only_for_every_role(client, auth, db, payroll, user):
    response = await client.get("/api/v1/finance/payroll/me", headers=await auth(user), params={"employee_id": OUTSIDER.id})
    ids = {row["id"] for row in data(response)}
    own = set((await db.execute(select(PayrollRecord.id).where(PayrollRecord.employee_id == USERS[user]))).scalars())
    assert ids and ids <= own  # at most the latest 12 own rows; never another person's
    assert OTHER_PAN not in response.text and not ids & payroll[OUTSIDER.id]


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_statutory_and_profile_describe_only_the_caller(client, auth, payroll, user):
    headers = await auth(user)
    statutory = await client.get("/api/v1/finance/statutory/me", headers=headers, params={"employee_id": OUTSIDER.id})
    profile = await client.get("/api/v1/finance/profile/me", headers=headers, params={"employee_id": OUTSIDER.id})
    assert data(statutory)["employee_id"] == data(profile)["employee_id"] == USERS[user]
    assert OTHER_PAN not in statutory.text + profile.text


async def test_employee_finance_profile_returns_their_own_stored_values(client, auth):
    body = data(await client.get("/api/v1/finance/profile/me", headers=await auth("employee")))
    assert body["bank_account_number"] == EMPLOYEE_SENSITIVE["bank_account_number"]
    assert body["current_salary_usd"] == EMPLOYEE_SENSITIVE["current_salary_usd"]


async def test_forged_admin_claim_does_not_open_other_payroll(client, db, payroll):
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    response = await client.get("/api/v1/finance/payroll/me", headers={"Authorization": f"Bearer {token}"})
    own = set((await db.execute(select(PayrollRecord.id).where(PayrollRecord.employee_id == EMPLOYEE.id))).scalars())
    assert {row["id"] for row in data(response)} <= own and OTHER_PAN not in response.text


# --- calendar neighbours ------------------------------------------------------------------------------------------


async def test_holidays_are_the_same_for_every_role(client, auth):
    bodies = [data(await client.get("/api/v1/calendar/holidays", headers=await auth(user))) for user in ("employee", "manager", "admin")]
    assert bodies[0] == bodies[1] == bodies[2]


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_birthdays_expose_no_year_ids_or_dob(client, auth, db, user):
    response = await client.get("/api/v1/calendar/birthdays", headers=await auth(user))
    items = data(response)
    assert items and all(set(item) == {"name", "team", "date"} for item in items)
    dob = EMPLOYEE_SENSITIVE["date_of_birth"]
    assert dob not in response.text  # the full date of birth (with year) never appears
    employee = next(item for item in items if item["name"] == "Employee Test")
    assert employee["date"][5:] == dob[5:] and employee["date"][:4] != dob[:4]  # month-day only; year is the next occurrence


# --- S-08: birthdays of active employees only ----------------------------------------------------------------------

BIRTHDAYS = "/api/v1/calendar/birthdays"
# Fixed past dates of birth with distinctive month-days; the response only ever carries the next occurrence.
DOBS = {OUTSIDER.id: date(1985, 3, 15), INACTIVE.id: date(1988, 7, 4), MANAGER.id: date(1980, 11, 30)}


@pytest.fixture
async def birthdays(db):
    for employee_id, dob in DOBS.items():
        await db.execute(update(Employee).where(Employee.id == employee_id).values(date_of_birth=dob))
    await db.commit()


def _names(response) -> set[str]:
    return {item["name"] for item in data(response)}


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_birthdays_list_active_employees_only(client, auth, birthdays, user):
    response = await client.get(BIRTHDAYS, headers=await auth(user))
    assert response.status_code == 200
    names = _names(response)
    assert {"Employee Test", "Outsider Test", "Manager Test"} <= names  # active people with a date of birth
    assert "Inactive Test" not in names
    assert not any(item["date"].endswith("-07-04") for item in data(response))  # not even the former employee's date
    for leaked in ("1985", "1988", "1980", str(INACTIVE.id)):
        assert f'"{leaked}' not in response.text and f"-{leaked}" not in response.text, leaked


async def test_birthday_departments_are_unchanged(client, auth, db, birthdays):
    items = {item["name"]: item for item in data(await client.get(BIRTHDAYS, headers=await auth("employee")))}
    assert (items["Outsider Test"]["team"], items["Manager Test"]["team"]) == ("Engineering", "Finance")
    assert items["Manager Test"]["date"][5:] == "11-30" and items["Outsider Test"]["date"][5:] == "03-15"
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(department_id=None))
    await db.commit()
    items = {item["name"]: item for item in data(await client.get(BIRTHDAYS, headers=await auth("employee")))}
    assert items["Outsider Test"]["team"] == "Unassigned"


@pytest.mark.parametrize("params", [{"include_inactive": "true"}, {"status": "INACTIVE"}, {"employee_id": INACTIVE.id},
                                    {"limit": 500, "all": "1"}])
async def test_query_parameters_cannot_bring_back_former_employees(client, auth, birthdays, params):
    assert "Inactive Test" not in _names(await client.get(BIRTHDAYS, headers=await auth("admin"), params=params))


async def test_forged_admin_claim_cannot_bring_back_former_employees(client, birthdays):
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    assert "Inactive Test" not in _names(await client.get(BIRTHDAYS, headers={"Authorization": f"Bearer {token}"}))


async def test_status_changes_apply_on_the_next_request(client, auth, db, birthdays):
    headers = await auth("manager")
    assert "Outsider Test" in _names(await client.get(BIRTHDAYS, headers=headers))
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(status=EmployeeStatus.INACTIVE))
    await db.execute(update(Employee).where(Employee.id == INACTIVE.id).values(status=EmployeeStatus.ACTIVE))
    await db.commit()
    names = _names(await client.get(BIRTHDAYS, headers=headers))
    assert "Outsider Test" not in names and "Inactive Test" in names


# --- anonymous and inactive ---------------------------------------------------------------------------------------

SELF_SERVICE = [("GET", "/api/v1/attendance/me"), ("GET", "/api/v1/attendance/today"), ("POST", "/api/v1/attendance/clock-in"),
                ("POST", "/api/v1/attendance/clock-out"), ("GET", "/api/v1/finance/payroll/me"),
                ("GET", "/api/v1/finance/statutory/me"), ("GET", "/api/v1/finance/profile/me"),
                ("GET", "/api/v1/calendar/holidays"), ("GET", "/api/v1/calendar/birthdays")]


@pytest.mark.parametrize("method,url", SELF_SERVICE)
async def test_anonymous_401_and_inactive_403(client, auth, db, method, url):
    rows = (await db.execute(select(func.count(AttendanceLog.id)))).scalar_one()
    assert (await client.request(method, url, json={} if method == "POST" else None)).status_code == 401
    inactive = await client.request(method, url, headers=await auth("inactive"), json={} if method == "POST" else None)
    assert inactive.status_code == 403 and inactive.json()["detail"]["error"]["code"] == "USER_INACTIVE"
    db.expire_all()
    assert (await db.execute(select(func.count(AttendanceLog.id)))).scalar_one() == rows  # nothing written
