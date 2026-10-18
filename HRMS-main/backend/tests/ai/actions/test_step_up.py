"""AIS-12: step-up re-authentication for high-impact AI actions (deactivation, announcements). Synthetic password only.

A refused password never consumes the confirmation, never executes anything and is never echoed, logged or audited.
Authorization is re-checked first, so an unauthorized caller is denied without a password prompt.
"""

import json

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.models.ai_audit_event import AIAuditEvent
from app.models.ai_pending_action import AIPendingAction
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from app.services.ai.actions.registry import ACTION_REGISTRY
from tests.ai.actions.conftest import ScriptedActionLLM, proposal
from tests.support.data import ADMIN, OUTSIDER, PASSWORD
from tests.support.db import business_snapshot
from tests.support.http import data

URL, CONFIRM = "/api/v1/chat/actions", "/api/v1/chat/actions/confirm"
WRONG = "not-the-password-1"


def test_only_high_impact_actions_require_step_up():
    assert {spec.name.value for spec in ACTION_REGISTRY.values() if spec.step_up} == {"EMPLOYEE_DEACTIVATE",
                                                                                      "ANNOUNCEMENT_CREATE"}


async def _propose_deactivation(client, auth, override_actions):
    override_actions(ScriptedActionLLM(proposal("EMPLOYEE_DEACTIVATE", target_employee_name="Outsider Test")))
    headers = await auth("admin")
    proposed = data(await client.post(URL, json={"question": "Deactivate Outsider Test"}, headers=headers))
    assert proposed["status"] == "confirmation_required" and proposed["step_up_required"] is True
    return headers, proposed["confirmation_id"]


async def _record(db) -> AIPendingAction:
    db.expire_all()
    return (await db.execute(select(AIPendingAction))).scalars().one()


async def _outsider_status(db):
    db.expire_all()
    return (await db.execute(select(Employee.status).where(Employee.id == OUTSIDER.id))).scalar_one()


async def test_ordinary_actions_do_not_ask_for_step_up(client, auth, override_actions):
    override_actions(ScriptedActionLLM(proposal("LEAVE_APPROVE", target_employee_name="Employee Test")))
    proposed = data(await client.post(URL, json={"question": "approve"}, headers=await auth("manager")))
    assert "step_up_required" not in proposed


@pytest.mark.parametrize("password", [None, "", WRONG, "x" * 129, 12345])
async def test_missing_or_wrong_password_is_refused_without_consuming_the_confirmation(client, auth, override_actions, db,
                                                                                       password):
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    before = business_snapshot()
    body = {"confirmation_id": confirmation_id} if password is None else {"confirmation_id": confirmation_id,
                                                                          "password": password}
    response = await client.post(CONFIRM, json=body, headers=headers)
    if isinstance(password, int):  # not a string: rejected by the request schema, nothing executed
        assert response.status_code == 422
    else:
        assert response.status_code == 403 and response.json()["error"]["code"] == "AI_STEP_UP_FAILED"
    assert WRONG not in response.text
    assert business_snapshot() == before and await _outsider_status(db) == EmployeeStatus.ACTIVE
    assert (await _record(db)).status == "PENDING"  # still usable by the right password within the TTL


async def test_correct_password_executes_once_and_replay_still_fails(client, auth, override_actions, db, capfd):
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    refused = await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": WRONG}, headers=headers)
    assert refused.status_code == 403
    done = data(await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD},
                                  headers=headers))
    assert done["status"] == "completed" and await _outsider_status(db) == EmployeeStatus.INACTIVE
    replay = await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD}, headers=headers)
    assert replay.status_code == 400 and replay.json()["error"]["code"] == "AI_CONFIRMATION_INVALID"

    events = (await db.execute(select(AIAuditEvent))).scalars().all()
    rejected = [json.loads(e.metadata_json) for e in events if e.event_type == "ACTION_REJECTED"]
    assert any(m.get("error_code") == "AI_STEP_UP_FAILED" for m in rejected)
    output = capfd.readouterr()
    for secret in (PASSWORD, WRONG):
        assert all(secret not in e.metadata_json for e in events)
        assert secret not in output.out and secret not in output.err


async def test_authorization_is_rechecked_before_the_password(client, auth, override_actions, db):
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(role=Role.ADMIN))  # keep an admin
    await db.execute(update(Employee).where(Employee.id == ADMIN.id).values(role=Role.MANAGER))
    await db.commit()
    response = await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "AI_PERMISSION_DENIED"
    assert await _outsider_status(db) == EmployeeStatus.ACTIVE


async def test_step_up_attempts_are_throttled_before_hashing(client, auth, override_actions, db, monkeypatch):
    monkeypatch.setattr(settings, "auth_login_rate_limit_per_minute", 2)
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    for _ in range(2):
        assert (await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": WRONG},
                                  headers=headers)).status_code == 403
    throttled = await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD}, headers=headers)
    assert throttled.status_code == 429 and throttled.json()["error"]["code"] == "AI_STEP_UP_THROTTLED"
    assert (await _record(db)).status == "PENDING" and await _outsider_status(db) == EmployeeStatus.ACTIVE


async def test_announcements_require_step_up_too(client, auth, override_actions, db):
    override_actions(ScriptedActionLLM(proposal("ANNOUNCEMENT_CREATE", arguments={"title": "Townhall",
                                                                                  "body": "Townhall moved to 5 PM."})))
    headers = await auth("admin")
    proposed = data(await client.post(URL, json={"question": "announce it"}, headers=headers))
    assert proposed["step_up_required"] is True
    before = business_snapshot()
    refused = await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"]}, headers=headers)
    assert refused.status_code == 403 and business_snapshot() == before
    done = data(await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"], "password": PASSWORD},
                                  headers=headers))
    assert done["status"] == "completed"


# --- review fixes (L1, L2) ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{"password": "Synthetic-Probe-Pass-9"},
                                  {"confirmation_id": "x" * 30, "password": 987654},
                                  {"confirmation_id": "x" * 30, "password": "Synthetic-Probe-Pass-9", "role": "ADMIN"}])
async def test_validation_errors_on_ai_paths_never_echo_the_password(client, auth, body):
    response = await client.post(CONFIRM, json=body, headers=await auth("admin"))
    assert response.status_code == 422 and response.json()["detail"]  # still a normal, located validation error
    assert "Synthetic-Probe-Pass-9" not in response.text and "987654" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


async def test_business_validation_errors_keep_the_framework_default(client, auth):
    response = await client.post("/api/v1/tickets", json={"title": 12345}, headers=await auth("employee"))
    assert response.status_code == 422 and any("input" in error for error in response.json()["detail"])


async def test_sign_in_throttling_cannot_exhaust_step_up(client, auth, override_actions, db, monkeypatch):
    """A sign-in key shaped like a step-up key ({ip}|{identifier} = "ai-step-up|<id>") lives in another limiter."""
    from app.core.rate_limit import login_limiter
    monkeypatch.setattr(settings, "auth_login_rate_limit_per_minute", 1)
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    assert login_limiter.allow(f"ai-step-up|{ADMIN.id}", 1) and not login_limiter.allow(f"ai-step-up|{ADMIN.id}", 1)
    done = data(await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD},
                                  headers=headers))
    assert done["status"] == "completed"


# --- G-11: compatibility with business-API step-up (STEP_UP_ENFORCEMENT=enforce) ----------------------------------

async def test_copilot_deactivation_still_works_when_the_api_enforces_step_up(client, auth, override_actions, db,
                                                                              monkeypatch, capfd):
    monkeypatch.setattr(settings, "step_up_enforcement", "enforce")
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    capfd.readouterr()
    done = data(await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD},
                                  headers=headers))
    assert done["status"] == "completed" and await _outsider_status(db) == EmployeeStatus.INACTIVE
    record = await _record(db)
    rows = (await db.execute(select(AIAuditEvent))).scalars().all()
    output = capfd.readouterr()
    for stored in (record.payload_json, record.summary, *(row.metadata_json for row in rows), output.out, output.err):
        assert PASSWORD not in stored
    assert "auth.step_up.not_satisfied" not in output.out  # the forwarded password satisfied the API


async def test_copilot_org_announcement_still_works_when_the_api_enforces_step_up(client, auth, override_actions,
                                                                                  monkeypatch):
    monkeypatch.setattr(settings, "step_up_enforcement", "enforce")
    override_actions(ScriptedActionLLM(proposal("ANNOUNCEMENT_CREATE", arguments={"title": "Townhall",
                                                                                  "body": "Townhall moved to 5 PM."})))
    headers = await auth("admin")
    proposed = data(await client.post(URL, json={"question": "announce it to everyone"}, headers=headers))
    done = data(await client.post(CONFIRM, json={"confirmation_id": proposed["confirmation_id"], "password": PASSWORD},
                                  headers=headers))
    assert done["status"] == "completed"


async def test_the_api_also_protects_the_copilot_path_on_its_own(client, auth, override_actions, db, monkeypatch):
    """Negative control: if the Copilot ever stopped forwarding the verified password, the business endpoint itself
    refuses and nothing changes (defence in depth, not reliance on the AI layer)."""
    from app.services.ai.actions.client import HRMSActionClient

    monkeypatch.setattr(settings, "step_up_enforcement", "enforce")
    original = HRMSActionClient.execute

    async def without_forwarding(self, prepared, *, step_up_password=None):
        return await original(self, prepared, step_up_password=None)

    monkeypatch.setattr(HRMSActionClient, "execute", without_forwarding)
    headers, confirmation_id = await _propose_deactivation(client, auth, override_actions)
    result = data(await client.post(CONFIRM, json={"confirmation_id": confirmation_id, "password": PASSWORD},
                                    headers=headers))
    # The action client reports every business 401/403 as a generic FORBIDDEN (existing contract, actions/client._outcome).
    assert result["status"] == "failed" and result["error_code"] == "FORBIDDEN"
    assert await _outsider_status(db) == EmployeeStatus.ACTIVE and (await _record(db)).status == "FAILED"
