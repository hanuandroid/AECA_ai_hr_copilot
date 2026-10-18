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
