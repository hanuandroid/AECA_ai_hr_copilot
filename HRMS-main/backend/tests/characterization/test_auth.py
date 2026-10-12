"""Characterization of the existing authentication mechanism (services/auth.py, core/security.py)."""

from datetime import UTC, datetime, timedelta

import pytest
import jwt

from app.core.config import settings
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER, PASSWORD, USERS
from tests.support.http import data, error_code


def _access_token(subject: object, role: str = "EMPLOYEE", *, minutes: int = 5, **extra) -> str:
    """A correctly signed access token shaped like /auth/login's (type=access); `extra` overrides claims."""
    claims = {"sub": str(subject), "role": role, "type": "access", "exp": datetime.now(UTC) + timedelta(minutes=minutes)}
    claims.update(extra)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def _login_and_me(client, user_key: str):
    user = USERS[user_key]
    response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    tokens = data(response)
    assert tokens["token_type"] == "bearer"
    claims = jwt.decode(tokens["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == str(user.id)
    assert claims["role"] == user.role.value
    assert claims["type"] == "access"  # B-04: access tokens carry an explicit type

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


async def test_inactive_user_cannot_log_in(client):
    """Production hardening B-03 (approved remediation of audit R9).

    Old behaviour (pinned here until 2026-10-11): login and get_current_user ignored Employee.status, so a
    deactivated employee could log in and use every business endpoint. Invariant now: an INACTIVE account gets the
    same generic 401 INVALID_CREDENTIALS as a wrong password, so login reveals nothing about the account's status.
    """
    response = await client.post("/api/v1/auth/login", json={"email": INACTIVE.email, "password": PASSWORD})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_CREDENTIALS"
    assert "access_token" not in response.text


async def test_inactive_user_cannot_obtain_token_via_oauth_form(client):
    response = await client.post("/api/v1/auth/token", data={"username": INACTIVE.email, "password": PASSWORD})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_CREDENTIALS"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/employees/me", "/api/v1/leaves/balances/me", "/api/v1/employees", f"/api/v1/employees/{EMPLOYEE.id}"],
)
async def test_token_issued_before_deactivation_is_rejected(client, path):
    """A still-valid access token for a now-INACTIVE user is rejected on every protected request (403 USER_INACTIVE)."""
    token = _access_token(INACTIVE.id, INACTIVE.role.value)
    response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert error_code(response) == "USER_INACTIVE"


async def test_deactivation_takes_effect_on_the_next_request(client, auth, db):
    from sqlalchemy import update

    from app.models.employee import Employee
    from app.models.enums import EmployeeStatus

    headers = await auth(EMPLOYEE.key)
    assert (await client.get("/api/v1/employees/me", headers=headers)).status_code == 200
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    response = await client.get("/api/v1/employees/me", headers=headers)
    assert response.status_code == 403
    assert error_code(response) == "USER_INACTIVE"


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
    ["not-a-jwt", jwt.encode({"sub": "3", "role": "EMPLOYEE", "type": "access"}, "some-other-secret", algorithm="HS256")],
    ids=["garbage", "wrong-signature"],
)
async def test_invalid_token_rejected(client, token):
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_expired_token_rejected(client):
    token = _access_token(EMPLOYEE.id, minutes=-1)  # correct type: rejected only because it expired
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_role_is_loaded_from_database_not_token_claim(client):
    """A validly-signed token claiming ADMIN for an EMPLOYEE is still treated as EMPLOYEE."""
    forged = _access_token(EMPLOYEE.id, "ADMIN")
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
    token = _access_token(9999, "ADMIN")
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "USER_NOT_FOUND"


# --- Production hardening B-04: explicit access-token type (approved 2026-10-11) -------------------------------
# Old behaviour: any correctly signed token with a `sub` authenticated (the type claim was ignored; refresh tokens were
# rejected only from 2026-10-10). Invariant now: only `type == "access"` authenticates; everything else is 401.


@pytest.mark.parametrize("token_type", [None, "refresh", "ACCESS", "id", ""], ids=["missing", "refresh", "wrong-case", "unknown", "empty"])
@pytest.mark.parametrize("path", ["/api/v1/employees/me", "/api/v1/chat/permissions"], ids=["business", "ai"])
async def test_only_access_type_authenticates(client, token_type, path):
    token = _access_token(EMPLOYEE.id, type=token_type) if token_type is not None else jwt.encode(
        {"sub": str(EMPLOYEE.id), "role": "EMPLOYEE", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
    )
    response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_correct_access_token_authenticates(client):
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {_access_token(EMPLOYEE.id)}"})
    assert response.status_code == 200
    assert data(response)["id"] == EMPLOYEE.id


@pytest.mark.parametrize("subject", ["not-a-number", "3.5", ""], ids=["text", "float", "empty"])
async def test_malformed_subject_rejected_without_server_error(client, subject):
    token = _access_token(subject)
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


@pytest.mark.parametrize("algorithm", ["HS512", "HS384"])
async def test_token_signed_with_another_algorithm_rejected(client, algorithm):
    """Only the configured algorithm is accepted, even with the right secret (algorithm confusion)."""
    claims = {"sub": str(EMPLOYEE.id), "role": "EMPLOYEE", "type": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=algorithm)
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_token_without_expiry_is_rejected(client):
    """PyJWT migration (B-24, Phase 2): `exp` is required. Before, python-jose verified exp only when present, so a
    correctly signed token without exp never expired. Every token the app issues carries exp, so clients are unaffected."""
    token = jwt.encode({"sub": str(EMPLOYEE.id), "role": "EMPLOYEE", "type": "access"}, settings.jwt_secret_key,
                       algorithm=settings.jwt_algorithm)
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


async def test_issued_tokens_decode_with_hs256_only(client):
    """Issued access tokens are HS256 with sub/role/type/exp; decoding with any other algorithm list fails."""
    response = await client.post("/api/v1/auth/login", json={"email": EMPLOYEE.email, "password": PASSWORD})
    token = data(response)["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    assert {"sub", "role", "type", "exp"} <= set(claims)
    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(token, settings.jwt_secret_key, algorithms=["HS512"])


async def test_unsigned_token_rejected(client):
    header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps(
        {"sub": str(EMPLOYEE.id), "role": "ADMIN", "type": "access", "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())}
    ).encode()).rstrip(b"=").decode()
    response = await client.get("/api/v1/employees/me", headers={"Authorization": f"Bearer {header}.{payload}."})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"
