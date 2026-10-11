"""Characterization of the existing authentication mechanism (services/auth.py, core/security.py)."""

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.config import settings
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER, PASSWORD, USERS
from tests.support.http import data, error_code


async def _login_and_me(client, user_key: str):
    user = USERS[user_key]
    response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    tokens = data(response)
    assert tokens["token_type"] == "bearer"
    claims = jwt.decode(tokens["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == str(user.id)
    assert claims["role"] == user.role.value

    me = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    profile = data(me)
    assert profile["id"] == user.id
    assert profile["role"] == user.role.value
    return profile


async def test_employee_authentication(client):
    await _login_and_me(client, EMPLOYEE.key)


async def test_manager_authentication(client):
    await _login_and_me(client, MANAGER.key)


async def test_admin_authentication(client):
    await _login_and_me(client, ADMIN.key)


async def test_inactive_user_not_rejected_by_existing_auth(client):
    """CURRENT BEHAVIOUR (audit R9): login and get_current_user ignore Employee.status.

    An INACTIVE employee can log in and use the API. This is documented, not changed:
    services/auth.py is out of scope. The AI layer rejects INACTIVE users itself (Phase 2,
    AIUserContext) - that is where `test_inactive_user_rejected` lives.
    """
    profile = await _login_and_me(client, INACTIVE.key)
    assert profile["status"] == "INACTIVE"


async def test_wrong_password_rejected(client):
    response = await client.post("/api/v1/auth/login", json={"email": EMPLOYEE.email, "password": "wrong"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_CREDENTIALS"


async def test_unknown_user_rejected(client):
    response = await client.post("/api/v1/auth/login", json={"email": "nobody@hrms-test.dev", "password": PASSWORD})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_CREDENTIALS"


async def test_missing_token_rejected(client):
    response = await client.get("/api/v1/employees/me")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", jwt.encode({"sub": "3", "role": "EMPLOYEE"}, "some-other-secret", algorithm="HS256")],
    ids=["garbage", "wrong-signature"],
)
async def test_invalid_token_rejected(client, token):
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_expired_token_rejected(client):
    token = jwt.encode(
        {"sub": str(EMPLOYEE.id), "role": "EMPLOYEE", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_role_is_loaded_from_database_not_token_claim(client):
    """A validly-signed token claiming ADMIN for an EMPLOYEE is still treated as EMPLOYEE."""
    forged = jwt.encode(
        {"sub": str(EMPLOYEE.id), "role": "ADMIN", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.post(
        "/api/v1/announcements",
        json={"title": "Forged admin", "body": "Should not be allowed"},
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def _refresh_token_for(client, user_key: str) -> str:
    user = USERS[user_key]
    response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    refresh_token = data(response)["refresh_token"]
    claims = jwt.decode(refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["type"] == "refresh"
    assert "role" not in claims
    return refresh_token


@pytest.mark.parametrize(
    "path",
    ["/api/v1/employees/me", "/api/v1/chat/permissions"],
    ids=["business-endpoint", "ai-endpoint"],
)
async def test_refresh_token_rejected_as_access_token(client, path):
    """Approved remediation (2026-10-10): the 7-day refresh token issued by /auth/login was accepted as a bearer
    access token because get_current_user never checked the `type` claim. get_current_user now rejects
    `type: "refresh"`, which covers the AI endpoints too (get_ai_user_context builds on get_current_user).
    """
    refresh_token = await _refresh_token_for(client, EMPLOYEE.key)
    response = await client.get(path, headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_token_for_deleted_subject_rejected(client):
    token = jwt.encode(
        {"sub": "9999", "role": "ADMIN", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "USER_NOT_FOUND"
