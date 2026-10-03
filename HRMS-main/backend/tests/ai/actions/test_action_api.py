"""POST /api/v1/chat/actions and /actions/confirm over HTTP (real auth -> agent -> real business API)."""

import pytest
from sqlalchemy import select

from app.models.enums import LeaveRequestStatus
from app.models.leave_request import LeaveRequest
from tests.ai.actions.conftest import ScriptedActionLLM, proposal
from tests.support.data import EMPLOYEE_PENDING_LEAVE_ID, MANAGER
from tests.support.db import business_snapshot
from tests.support.http import data

URL, CONFIRM = "/api/v1/chat/actions", "/api/v1/chat/actions/confirm"


async def test_full_http_flow_propose_then_confirm(client, auth, override_actions, db):
    override_actions(ScriptedActionLLM(proposal("LEAVE_APPROVE", target_employee_name="Employee Test")))
    headers = await auth("manager")
    proposed = data(await client.post(URL, json={"question": "Approve Employee Test's pending leave"}, headers=headers))
    assert proposed["status"] == "confirmation_required" and proposed["action"] == "LEAVE_APPROVE"
    assert proposed["confirmation_id"] and proposed["expires_at"].endswith("Z")
    confirmed = data(await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"]}, headers=headers))
    assert confirmed == {**confirmed, "status": "completed", "message": "Leave request approved."}
    db.expire_all()
    leave = (await db.execute(select(LeaveRequest).where(LeaveRequest.id == EMPLOYEE_PENDING_LEAVE_ID))).scalar_one()
    assert (leave.status, leave.approver_id) == (LeaveRequestStatus.APPROVED, MANAGER.id)

    replay = await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"]}, headers=headers)
    assert replay.status_code == 400 and replay.json()["error"]["code"] == "AI_CONFIRMATION_INVALID"


async def test_confirm_accepts_only_the_token(client, auth, override_actions):
    override_actions(ScriptedActionLLM(proposal("LEAVE_APPROVE", target_employee_name="Employee Test")))
    headers = await auth("manager")
    proposed = data(await client.post(URL, json={"question": "approve"}, headers=headers))
    before = business_snapshot()
    for body in (
        {"confirmation_id": proposed["confirmation_id"], "action": "EMPLOYEE_DEACTIVATE", "employee_id": 1},
        {"confirmation_id": proposed["confirmation_id"], "leave_id": 2},
        {"action": "LEAVE_APPROVE"},
        {},
    ):
        assert (await client.post(CONFIRM, json=body, headers=headers)).status_code == 422
    assert business_snapshot() == before


async def test_cross_user_confirmation_over_http(client, auth, override_actions):
    override_actions(ScriptedActionLLM(proposal("LEAVE_APPROVE", target_employee_name="Employee Test")))
    proposed = data(await client.post(URL, json={"question": "approve"}, headers=await auth("manager")))
    response = await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"]}, headers=await auth("employee"))
    assert response.status_code == 400 and response.json()["error"]["code"] == "AI_CONFIRMATION_INVALID"


@pytest.mark.parametrize("url", [URL, CONFIRM])
async def test_inactive_and_unauthenticated_rejected(client, auth, override_actions, url):
    llm = ScriptedActionLLM(proposal("LEAVE_CREATE"))
    override_actions(llm)
    body = {"question": "apply leave"} if url == URL else {"confirmation_id": "x" * 43}
    inactive = await client.post(url, json=body, headers=await auth("inactive"))
    assert inactive.status_code == 403 and inactive.json()["error"]["code"] == "AI_USER_INACTIVE"
    assert (await client.post(url, json=body)).status_code == 401
    assert llm.calls == []


@pytest.mark.parametrize(
    "user_key,payload,status,code",
    [
        ("employee", proposal("LEAVE_APPROVE", resource_id=EMPLOYEE_PENDING_LEAVE_ID), 403, "AI_PERMISSION_DENIED"),
        ("manager", proposal("PROJECT_CREATE", arguments={"project_name": "Shadow"}), 403, "AI_PERMISSION_DENIED"),
        ("manager", proposal("LEAVE_APPROVE", target_employee_name="Outsider Test"), 404, "AI_TARGET_NOT_FOUND"),
        ("employee", {**proposal("LEAVE_CREATE"), "authorized": True}, 502, "AI_INVALID_OUTPUT"),
    ],
)
async def test_rejections_use_ai_envelope(client, auth, override_actions, user_key, payload, status, code):
    override_actions(ScriptedActionLLM(payload))
    before = business_snapshot()
    response = await client.post(URL, json={"question": "do it"}, headers=await auth(user_key))
    assert response.status_code == status
    body = response.json()
    assert body["success"] is False and body["error"]["code"] == code
    assert "Outsider" not in body["error"]["message"] and "Traceback" not in response.text
    assert business_snapshot() == before


async def test_clarification_over_http(client, auth, override_actions):
    override_actions(ScriptedActionLLM(proposal("LEAVE_APPROVE", target_employee_name="Ravi")))
    body = data(await client.post(URL, json={"message": "Approve Ravi's leave"}, headers=await auth("manager")))
    assert body["status"] == "clarification_required" and "confirmation_id" not in body


async def test_invalid_question(client, auth, override_actions):
    llm = ScriptedActionLLM(proposal("LEAVE_CREATE"))
    override_actions(llm)
    response = await client.post(URL, json={"question": "  "}, headers=await auth("employee"))
    assert response.status_code == 400 and response.json()["error"]["code"] == "AI_INVALID_REQUEST"
    assert llm.calls == []


async def test_unconfigured_provider_returns_503(client, auth, monkeypatch):
    from app.core.config import settings
    from app.services.ai import providers

    monkeypatch.setattr(settings, "ai_llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(providers, "_llm_cache", {})
    response = await client.post(URL, json={"question": "apply leave tomorrow"}, headers=await auth("employee"))
    assert response.status_code == 503 and response.json()["error"]["code"] == "AI_UNAVAILABLE"
