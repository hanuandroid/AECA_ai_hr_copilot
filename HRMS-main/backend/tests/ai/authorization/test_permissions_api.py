"""HTTP boundary: GET /api/v1/chat/permissions uses the real auth + AI context dependency."""

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.config import settings
from app.services.ai.authorization import capabilities_for
from app.models.enums import Role
from tests.support.data import EMPLOYEE, EMPLOYEE_SENSITIVE
from tests.support.http import data, error_code

URL = "/api/v1/chat/permissions"


@pytest.mark.parametrize(
    "user_key,role,scope",
    [("employee", Role.EMPLOYEE, "SELF"), ("manager", Role.MANAGER, "TEAM"), ("admin", Role.ADMIN, "ORG")],
)
async def test_active_users_get_their_role_capabilities(client, auth, user_key, role, scope):
    response = await client.get(URL, headers=await auth(user_key))
    assert response.status_code == 200
    body = data(response)
    assert body["role"] == role.value
    assert body["data_scope"] == scope
    assert set(body["capabilities"]) == {capability.value for capability in capabilities_for(role)}


async def test_inactive_user_rejected(client, auth):
    """R9: existing login still works for INACTIVE users, but the AI boundary rejects them."""
    response = await client.get(URL, headers=await auth("inactive"))
    assert response.status_code == 403
    body = response.json()
    assert body == {
        "success": False,
        "data": None,
        "error": {"code": "AI_USER_INACTIVE", "message": "Your account is not active, so the AI assistant is unavailable."},
    }


async def test_unauthenticated_rejected(client):
    assert (await client.get(URL)).status_code == 401


async def test_frontend_claims_are_ignored(client, auth):
    """Role cookie, custom headers and query params cannot elevate an EMPLOYEE."""
    headers = {**await auth("employee"), "X-Role": "ADMIN", "X-Employee-Id": "1", "X-Authorized": "true"}
    client.cookies.set("hrms_role", "ADMIN")
    response = await client.get(URL, params={"role": "ADMIN", "employee_id": 1}, headers=headers)
    body = data(response)
    assert body["role"] == "EMPLOYEE"
    assert body["employee_id"] == EMPLOYEE.id
    assert "HR_READ_ORG" not in body["capabilities"]


async def test_forged_role_claim_in_token_is_ignored(client):
    forged = jwt.encode(
        {"sub": str(EMPLOYEE.id), "role": "ADMIN", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    body = data(await client.get(URL, headers={"Authorization": f"Bearer {forged}"}))
    assert body["role"] == "EMPLOYEE"
    assert body["data_scope"] == "SELF"


@pytest.mark.parametrize("user_key", ["employee", "manager", "admin", "inactive"])
async def test_response_never_contains_sensitive_values(client, auth, user_key):
    text = (await client.get(URL, headers=await auth(user_key))).text
    for value in EMPLOYEE_SENSITIVE.values():
        assert str(value) not in text
    for marker in ("$2b$", "bank", "salary", "pan_number", "Traceback"):
        assert marker not in text


async def test_existing_business_endpoints_unaffected_by_ai_error_handler(client, auth):
    """The new AIError handler must not change existing error shapes."""
    response = await client.post("/api/v1/leaves/requests/9999/approve", headers=await auth("manager"))
    assert response.status_code == 404
    assert error_code(response) == "LEAVE_NOT_FOUND"
    assert "detail" in response.json()
