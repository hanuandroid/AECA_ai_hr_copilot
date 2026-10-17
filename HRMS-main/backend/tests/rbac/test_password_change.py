"""Self-service password change: POST /api/v1/auth/change-password (RBAC remediation step 4, Decision 2).

Contract: authenticated (DB-loaded, active) caller; {current_password, new_password}; new password 8-128 characters,
not blank, different from the current one; hashed with the existing passlib context; throttled per account with the
login limiter; nothing about passwords is returned or logged. Sessions (B-17, 2026-10-17): a successful change
revokes every token issued before it, including the caller's (tests/rbac/test_token_revocation.py has the full matrix).
Old behaviour (until 2026-10-17): earlier access tokens stayed valid until they expired.
"""

import json

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.employee import Employee
from tests.support.data import EMPLOYEE, INACTIVE, PASSWORD
from tests.support.http import error_code

URL = "/api/v1/auth/change-password"
NEW = "brand-new-password-42"


async def _hash(db, employee_id: int = EMPLOYEE.id) -> str:
    db.expire_all()
    return (await db.execute(select(Employee.hashed_password).where(Employee.id == employee_id))).scalar_one()


async def _login(client, password: str) -> int:
    return (await client.post("/api/v1/auth/login", json={"email": EMPLOYEE.email, "password": password})).status_code


async def test_anonymous_request_is_rejected(client):
    assert (await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW})).status_code == 401


async def test_successful_change(client, auth, db):
    old_hash = await _hash(db)
    with capture_logs() as logs:
        response = await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW},
                                     headers=await auth("employee"))
    assert response.status_code == 200 and response.json()["data"]["password_changed"] is True
    for secret in (PASSWORD, NEW, old_hash, "$2b$", "hashed_password"):
        assert secret not in response.text
    new_hash = await _hash(db)
    assert new_hash != old_hash and new_hash.startswith("$2") and verify_password(NEW, new_hash)  # existing bcrypt context
    assert await _login(client, PASSWORD) == 401 and await _login(client, NEW) == 200
    rendered = json.dumps(logs, default=str)
    assert "auth.password_changed" in rendered and PASSWORD not in rendered and NEW not in rendered


async def test_wrong_current_password_changes_nothing(client, auth, db):
    old_hash = await _hash(db)
    response = await client.post(URL, json={"current_password": "not-my-password", "new_password": NEW},
                                 headers=await auth("employee"))
    assert response.status_code == 400 and error_code(response) == "INVALID_CURRENT_PASSWORD"
    assert await _hash(db) == old_hash and await _login(client, PASSWORD) == 200


@pytest.mark.parametrize("new_password,status,code", [
    ("short1", 422, None), ("x" * 129, 422, None), ("        ", 400, "INVALID_NEW_PASSWORD"),
    (PASSWORD, 400, "PASSWORD_UNCHANGED"),
])
async def test_invalid_new_passwords_are_rejected(client, auth, db, new_password, status, code):
    old_hash = await _hash(db)
    response = await client.post(URL, json={"current_password": PASSWORD, "new_password": new_password},
                                 headers=await auth("employee"))
    assert response.status_code == status
    if code:
        assert error_code(response) == code
    assert await _hash(db) == old_hash


@pytest.mark.parametrize("body", [{}, {"current_password": PASSWORD}, {"new_password": NEW},
                                  {"current_password": PASSWORD, "new_password": NEW, "employee_id": 1}])
async def test_request_shape(client, auth, db, body):
    response = await client.post(URL, json=body, headers=await auth("employee"))
    if set(body) >= {"current_password", "new_password"}:  # extra keys cannot retarget another account
        assert response.status_code == 200
        assert await _hash(db, 1) != await _hash(db)  # the admin's hash was not touched
    else:
        assert response.status_code == 422


async def test_inactive_account_is_rejected(client):
    token = create_access_token(subject=str(INACTIVE.id), role=INACTIVE.role.value)
    response = await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW},
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"


async def test_refresh_token_is_not_accepted(client):
    from app.core.security import create_refresh_token

    token = create_refresh_token(subject=str(EMPLOYEE.id))
    response = await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW},
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("correct", [True, False])
async def test_attempts_are_throttled_per_account(client, auth, db, monkeypatch, correct):
    monkeypatch.setattr(settings, "auth_login_rate_limit_per_minute", 3)
    headers = await auth("employee")
    statuses = []
    current = PASSWORD
    for attempt in range(4):
        new = f"rotated-password-{attempt}"
        body = {"current_password": current if correct else "wrong-password", "new_password": new}
        response = await client.post(URL, json=body, headers=headers)
        statuses.append(response.status_code)
        if response.status_code == 200:
            current = new
            # B-17: the change revoked this session; sign in again with the new password, as a real client must.
            login = await client.post("/api/v1/auth/login", json={"email": EMPLOYEE.email, "password": new})
            headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    assert statuses[:3] == ([200, 200, 200] if correct else [400, 400, 400])
    assert statuses[3] == 429
    assert error_code(response) == "TOO_MANY_ATTEMPTS"
    other = await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW}, headers=await auth("manager"))
    assert other.status_code == 200  # the limit is per account


async def test_tokens_issued_before_the_change_are_revoked(client, auth):
    """B-17 (approved 2026-10-17). Old behaviour (until 2026-10-17): this request still returned 200 until the token
    expired (stateless JWT, no revocation). Now the caller's own session ends with the change."""
    headers = await auth("employee")
    assert (await client.post(URL, json={"current_password": PASSWORD, "new_password": NEW}, headers=headers)).status_code == 200
    response = await client.get("/api/v1/employees/me", headers=headers)
    assert response.status_code == 401 and error_code(response) == "TOKEN_REVOKED"


async def test_wrong_password_attempt_is_logged_without_secrets(client, auth):
    with capture_logs() as logs:
        await client.post(URL, json={"current_password": "guess-123456", "new_password": NEW}, headers=await auth("employee"))
    rendered = json.dumps(logs, default=str)
    assert "auth.password_change_failed" in rendered
    assert "guess-123456" not in rendered and NEW not in rendered and "Bearer" not in rendered
