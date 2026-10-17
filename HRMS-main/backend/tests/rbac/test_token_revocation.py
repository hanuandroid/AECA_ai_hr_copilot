"""Token revocation after password changes (finding B-17, approved 2026-10-17; migration 0024).

Mechanism: `employees.token_version` (database) is incremented by every password write; access tokens carry the value
at issue time as `tv`; `get_current_user` refuses a token whose `tv` differs from the stored value (401 TOKEN_REVOKED).
A token without `tv` predates the field and counts as 0.
Approved policy:
- revoking writes: self-service change (the caller's own session ends too), admin reset of another employee, and
  re-hiring a former employee with a new password;
- failed attempts change nothing and revoke nothing;
- the stored version is the only authority: claims in the token cannot raise, skip or forge it.
Role changes and deactivation were already enforced per request from the database and are unchanged.
"""

import jwt
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.db.session import get_db
from app.main import app
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER, OUTSIDER, PASSWORD
from tests.support.http import error_code

CHANGE = "/api/v1/auth/change-password"
NEW = "brand-new-password-17"
PROBES = ["/api/v1/employees/me", "/api/v1/chat/permissions", "/api/v1/tickets"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


async def _version(db, employee_id: int) -> int:
    db.expire_all()
    return (await db.execute(select(Employee.token_version).where(Employee.id == employee_id))).scalar_one()


async def _hash(db, employee_id: int) -> str:
    db.expire_all()
    return (await db.execute(select(Employee.hashed_password).where(Employee.id == employee_id))).scalar_one()


def _revoked(response) -> bool:
    return response.status_code == 401 and error_code(response) == "TOKEN_REVOKED"


# --- self-service change -------------------------------------------------------------------------------------------


async def test_token_works_before_and_is_revoked_after_a_password_change(client, db):
    token = await _login(client, EMPLOYEE.email, PASSWORD)
    for path in PROBES:
        assert (await client.get(path, headers=_bearer(token))).status_code == 200, path
    changed = await client.post(CHANGE, headers=_bearer(token), json={"current_password": PASSWORD, "new_password": NEW})
    assert changed.status_code == 200 and "signed out" in changed.json()["data"]["existing_tokens"]
    assert await _version(db, EMPLOYEE.id) == 1
    for path in PROBES:  # business API and AI endpoints alike
        assert _revoked(await client.get(path, headers=_bearer(token))), path


async def test_a_new_token_works_after_the_change(client, db):
    token = await _login(client, EMPLOYEE.email, PASSWORD)
    await client.post(CHANGE, headers=_bearer(token), json={"current_password": PASSWORD, "new_password": NEW})
    fresh = await _login(client, EMPLOYEE.email, NEW)
    assert decode_token(fresh)["tv"] == 1
    assert (await client.get("/api/v1/employees/me", headers=_bearer(fresh))).status_code == 200


async def test_every_earlier_session_of_the_user_is_revoked(client, db):
    first, second = await _login(client, EMPLOYEE.email, PASSWORD), await _login(client, EMPLOYEE.email, PASSWORD)
    await client.post(CHANGE, headers=_bearer(first), json={"current_password": PASSWORD, "new_password": NEW})
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(second)))


async def test_other_users_are_unaffected(client, db):
    manager = await _login(client, MANAGER.email, PASSWORD)
    employee = await _login(client, EMPLOYEE.email, PASSWORD)
    await client.post(CHANGE, headers=_bearer(employee), json={"current_password": PASSWORD, "new_password": NEW})
    assert (await client.get("/api/v1/employees/me", headers=_bearer(manager))).status_code == 200
    assert await _version(db, MANAGER.id) == 0


@pytest.mark.parametrize("body,status", [
    ({"current_password": "wrong-password", "new_password": NEW}, 400),  # wrong current password
    ({"current_password": PASSWORD, "new_password": PASSWORD}, 400),  # unchanged
    ({"current_password": PASSWORD, "new_password": " " * 9}, 400),  # blank
    ({"current_password": PASSWORD, "new_password": "short"}, 422),  # too short
])
async def test_failed_changes_revoke_nothing_and_change_nothing(client, db, body, status):
    token = await _login(client, EMPLOYEE.email, PASSWORD)
    old_hash = await _hash(db, EMPLOYEE.id)
    assert (await client.post(CHANGE, headers=_bearer(token), json=body)).status_code == status
    assert await _version(db, EMPLOYEE.id) == 0 and await _hash(db, EMPLOYEE.id) == old_hash
    assert (await client.get("/api/v1/employees/me", headers=_bearer(token))).status_code == 200


# --- admin flows ---------------------------------------------------------------------------------------------------


async def test_admin_reset_revokes_the_employees_tokens_only(client, db):
    employee = await _login(client, EMPLOYEE.email, PASSWORD)
    admin = await _login(client, ADMIN.email, PASSWORD)
    reset = await client.put(f"/api/v1/employees/{EMPLOYEE.id}", headers=_bearer(admin), json={"password": "admin-set-pass-1"})
    assert reset.status_code == 200
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(employee)))
    assert (await client.get("/api/v1/employees/me", headers=_bearer(admin))).status_code == 200
    assert await _version(db, EMPLOYEE.id) == 1 and await _version(db, ADMIN.id) == 0
    assert (await client.get("/api/v1/employees/me", headers=_bearer(await _login(client, EMPLOYEE.email, "admin-set-pass-1")))).status_code == 200


async def test_admin_edits_without_a_password_revoke_nothing(client, db):
    employee = await _login(client, EMPLOYEE.email, PASSWORD)
    admin = await _login(client, ADMIN.email, PASSWORD)
    for body in ({"name": "Employee Renamed"}, {"password": None}, {"password": "   "}):
        response = await client.put(f"/api/v1/employees/{EMPLOYEE.id}", headers=_bearer(admin), json=body)
        assert response.status_code in (200, 422), (body, response.text)
    assert await _version(db, EMPLOYEE.id) == 0
    assert (await client.get("/api/v1/employees/me", headers=_bearer(employee))).status_code == 200


async def test_admin_self_reset_stays_refused_and_revokes_nothing(client, db):
    admin = await _login(client, ADMIN.email, PASSWORD)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=_bearer(admin), json={"password": "new-admin-pass"})
    assert response.status_code == 403 and error_code(response) == "SELF_PASSWORD_RESET_FORBIDDEN"
    assert await _version(db, ADMIN.id) == 0
    assert (await client.get("/api/v1/employees/me", headers=_bearer(admin))).status_code == 200


async def test_rehiring_a_former_employee_with_a_new_password_revokes_old_tokens(client, db):
    stale = create_access_token(subject=str(INACTIVE.id), role="EMPLOYEE")  # issued while they were active (tv 0)
    assert (await client.get("/api/v1/employees/me", headers=_bearer(stale))).status_code == 403  # inactive today
    admin = await _login(client, ADMIN.email, PASSWORD)
    rehire = await client.post("/api/v1/employees", headers=_bearer(admin), json={
        "name": "Inactive Test", "email": INACTIVE.email, "password": "rehire-pass-123", "department_id": 1,
        "joining_date": "2026-01-05"})
    assert rehire.status_code == 200, rehire.text
    db.expire_all()
    row = (await db.execute(select(Employee).where(Employee.id == INACTIVE.id))).scalar_one()
    assert row.status == EmployeeStatus.ACTIVE and row.token_version == 1
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(stale)))  # not resurrected by the re-hire
    fresh = await _login(client, INACTIVE.email, "rehire-pass-123")
    assert (await client.get("/api/v1/employees/me", headers=_bearer(fresh))).status_code == 200


async def test_creating_a_new_employee_starts_at_version_zero(client, db):
    admin = await _login(client, ADMIN.email, PASSWORD)
    created = await client.post("/api/v1/employees", headers=_bearer(admin), json={
        "name": "New Starter", "email": "new.starter@hrms-test.dev", "password": "starter-pass-1", "department_id": 1,
        "joining_date": "2026-01-05"})
    assert created.status_code == 200, created.text
    new_id = created.json()["data"]["id"]
    assert await _version(db, new_id) == 0
    assert decode_token(await _login(client, "new.starter@hrms-test.dev", "starter-pass-1"))["tv"] == 0


# --- forged and malformed tokens -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tv", [0, 2, 99, -1, "1", True, 1.0, None, [1]])
async def test_forged_versions_are_refused(client, db, tv):
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(token_version=1))
    await db.commit()
    token = jwt.encode({"sub": str(EMPLOYEE.id), "role": "ADMIN", "type": "access", "tv": tv, "exp": 4102444800},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(token)))


async def test_the_matching_version_is_accepted_and_the_role_claim_still_ignored(client, db):
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(token_version=1))
    await db.commit()
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN", token_version=1)
    assert (await client.get("/api/v1/employees/me", headers=_bearer(token))).status_code == 200
    assert (await client.get("/api/v1/employees", headers=_bearer(token))).status_code == 403  # still an EMPLOYEE


async def test_tokens_without_a_version_stop_working_after_the_first_change(client, db):
    legacy = jwt.encode({"sub": str(EMPLOYEE.id), "role": "EMPLOYEE", "type": "access", "exp": 4102444800},
                        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    assert (await client.get("/api/v1/employees/me", headers=_bearer(legacy))).status_code == 200  # version 0
    await client.post(CHANGE, headers=_bearer(legacy), json={"current_password": PASSWORD, "new_password": NEW})
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(legacy)))


async def test_a_token_signed_with_another_key_is_invalid(client):
    token = jwt.encode({"sub": str(EMPLOYEE.id), "type": "access", "tv": 0, "exp": 4102444800}, "another-secret-key-0123456789abcdef",
                       algorithm=settings.jwt_algorithm)
    response = await client.get("/api/v1/employees/me", headers=_bearer(token))
    assert response.status_code == 401 and error_code(response) == "INVALID_TOKEN"


async def test_refresh_tokens_carry_the_version_but_never_authenticate(client):
    login = await client.post("/api/v1/auth/login", json={"email": EMPLOYEE.email, "password": PASSWORD})
    data = login.json()["data"]
    assert decode_token(data["refresh_token"])["tv"] == 0 and decode_token(data["access_token"])["tv"] == 0
    response = await client.get("/api/v1/employees/me", headers=_bearer(create_refresh_token(str(EMPLOYEE.id))))
    assert response.status_code == 401 and error_code(response) == "INVALID_TOKEN"


async def test_oauth2_token_endpoint_issues_the_current_version(client, db):
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(token_version=3))
    await db.commit()
    response = await client.post("/api/v1/auth/token", data={"username": OUTSIDER.email, "password": PASSWORD})
    assert response.status_code == 200 and decode_token(response.json()["access_token"])["tv"] == 3


# --- persistence and failure ---------------------------------------------------------------------------------------


async def test_revocation_survives_a_restart_of_the_database_engine(client, db):
    from app.db import session

    token = await _login(client, EMPLOYEE.email, PASSWORD)
    await client.post(CHANGE, headers=_bearer(token), json={"current_password": PASSWORD, "new_password": NEW})
    await session.engine.dispose()  # drop every pooled connection, as a process restart would
    assert _revoked(await client.get("/api/v1/employees/me", headers=_bearer(token)))
    assert await _version(db, EMPLOYEE.id) == 1  # stored in the database, not in memory


async def test_an_unreadable_user_record_never_authenticates(client):
    token = await _login(client, EMPLOYEE.email, PASSWORD)

    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise OperationalError("SELECT", {}, Exception("database unavailable"))

    async def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        with pytest.raises(OperationalError):  # surfaces as a server error; it never authenticates (no fail-open)
            await client.get("/api/v1/employees/me", headers=_bearer(token))
    finally:
        app.dependency_overrides.pop(get_db, None)
