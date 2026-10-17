"""Global sign-out: POST /api/v1/auth/logout (B-17 follow-up, decision L1, approved 2026-10-17).

The endpoint increments the authenticated caller's own employees.token_version (one atomic UPDATE), so every token
issued to that user so far is refused from the next request on, on every device. No parameters: it can never touch
another user. A retry with the same token gets 401 TOKEN_REVOKED. The response carries no version or token.
"""

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.employee import Employee
from tests.support.data import ADMIN, EMPLOYEE, MANAGER, OUTSIDER, PASSWORD
from tests.support.http import error_code

LOGOUT = "/api/v1/auth/logout"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


async def _version(db, employee_id: int) -> int:
    db.expire_all()
    return (await db.execute(select(Employee.token_version).where(Employee.id == employee_id))).scalar_one()


async def test_logout_requires_authentication(client, db):
    assert (await client.post(LOGOUT)).status_code == 401
    assert await _version(db, EMPLOYEE.id) == 0


async def test_inactive_tokens_are_refused(client, auth):
    response = await client.post(LOGOUT, headers=await auth("inactive"))
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"


@pytest.mark.parametrize("email,employee_id", [(EMPLOYEE.email, EMPLOYEE.id), (MANAGER.email, MANAGER.id), (ADMIN.email, ADMIN.id)])
async def test_logout_revokes_every_session_of_the_caller(client, db, email, employee_id):
    first, second = await _login(client, email), await _login(client, email)
    response = await client.post(LOGOUT, headers=_bearer(first))
    assert response.status_code == 200
    assert response.json()["data"] == {"signed_out": True, "scope": "all_sessions"}  # no version, no token
    assert await _version(db, employee_id) == 1
    for token in (first, second):  # this device and the "other device"
        revoked = await client.get("/api/v1/employees/me", headers=_bearer(token))
        assert revoked.status_code == 401 and error_code(revoked) == "TOKEN_REVOKED"
    fresh = await _login(client, email)
    assert (await client.get("/api/v1/employees/me", headers=_bearer(fresh))).status_code == 200


async def test_logout_never_affects_other_users(client, db):
    manager = await _login(client, MANAGER.email)
    employee = await _login(client, EMPLOYEE.email)
    await client.post(LOGOUT, headers=_bearer(employee))
    assert (await client.get("/api/v1/employees/me", headers=_bearer(manager))).status_code == 200
    assert (await _version(db, MANAGER.id), await _version(db, OUTSIDER.id)) == (0, 0)


@pytest.mark.parametrize("body", [{"employee_id": MANAGER.id}, {"user_id": ADMIN.id, "all": True}])
async def test_body_or_query_cannot_target_another_user(client, db, body):
    token = await _login(client, EMPLOYEE.email)
    response = await client.post(LOGOUT, headers=_bearer(token), json=body, params={"employee_id": MANAGER.id})
    assert response.status_code == 200
    assert (await _version(db, EMPLOYEE.id), await _version(db, MANAGER.id), await _version(db, ADMIN.id)) == (1, 0, 0)


async def test_repeated_logout_is_predictable(client, db):
    token = await _login(client, EMPLOYEE.email)
    assert (await client.post(LOGOUT, headers=_bearer(token))).status_code == 200
    again = await client.post(LOGOUT, headers=_bearer(token))
    assert again.status_code == 401 and error_code(again) == "TOKEN_REVOKED"
    assert await _version(db, EMPLOYEE.id) == 1  # the refused retry changed nothing
    fresh = await _login(client, EMPLOYEE.email)
    assert (await client.post(LOGOUT, headers=_bearer(fresh))).status_code == 200
    assert await _version(db, EMPLOYEE.id) == 2


async def test_forged_role_claim_logs_out_only_the_real_subject(client, db):
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    assert (await client.post(LOGOUT, headers=_bearer(token))).status_code == 200
    assert (await _version(db, EMPLOYEE.id), await _version(db, ADMIN.id)) == (1, 0)


async def test_logout_ends_ai_sessions_too(client, db):
    token = await _login(client, MANAGER.email)
    assert (await client.get("/api/v1/chat/permissions", headers=_bearer(token))).status_code == 200
    await client.post(LOGOUT, headers=_bearer(token))
    response = await client.get("/api/v1/chat/permissions", headers=_bearer(token))
    assert response.status_code == 401 and error_code(response) == "TOKEN_REVOKED"
