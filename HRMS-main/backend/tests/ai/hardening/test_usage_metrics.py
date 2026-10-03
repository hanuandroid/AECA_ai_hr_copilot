"""AI usage dashboard metrics (Phase 10): derived from existing audit events; operational, admin-only."""

import pytest

from tests.ai.hardening.conftest import RAHUL_LEAVE, action_proposal, audit_rows, by_prompt, meta, script, sql
from tests.support.http import data, error_code

CHAT, METRICS = "/api/v1/chat", "/api/v1/ai/audit/metrics"


async def traffic(client, auth, install_real):
    install_real(script(sql_rules=by_prompt(("balance", sql("SELECT leave_type, remaining FROM leave_balances WHERE employee_id = :me")),
                                            ("employees", sql("DELETE FROM employees"))),
                        action=action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Mehta"),
                        policy={"answer": "The policies do not cover this.", "grounded": False, "citations": []}))
    employee, manager, admin = await auth("employee"), await auth("manager"), await auth("admin")
    await client.post(CHAT, json={"message": "What is the WFH policy?"}, headers=employee)            # POLICY, no answer
    await client.post(CHAT, json={"message": "What is my leave balance?"}, headers=employee)          # SQL
    await client.post(CHAT, json={"message": "Drop the employees table."}, headers=employee)          # refused
    first = data(await client.post(CHAT, json={"message": "Approve Rahul's leave."}, headers=manager))
    await client.post(CHAT, json={"message": "Approve Rahul's leave."}, headers=manager)              # second proposal
    token = first["parts"][0]["data"]["confirmation_id"]
    await client.post(CHAT + "/actions/confirm", json={"confirmation_id": token}, headers=manager)    # executed
    await client.post(CHAT, json={"message": "How many employees are in Engineering?"}, headers=admin)  # SQL rejected
    return admin


async def test_usage_metrics_reflect_actual_traffic(client, auth, install_real):
    admin = await traffic(client, auth, install_real)
    metrics = data(await client.get(METRICS, headers=admin))
    assert metrics["requests_by_role"] == {"ADMIN": 1, "EMPLOYEE": 3, "MANAGER": 3}
    assert metrics["intents"] == {"ACTION": 2, "SQL": 2, "POLICY": 1, "UNSUPPORTED": 1}
    assert metrics["tools"] == {"policy_rag": 1, "sql_agent": 2, "action_agent": 2}
    assert metrics["actions_proposed_by_type"] == {"LEAVE_APPROVE": 2}
    assert metrics["policy_answers"] == 1 and metrics["rag_no_answer_rate"] == 1.0
    assert metrics["sql_security_rejections"] == 1 and metrics["requests_refused"] == 1
    assert metrics["action_confirmation_rate"] == 0.5
    assert metrics["action_execution"] == {"succeeded": 1, "failed": 0, "success_rate": 1.0}


async def test_rates_are_null_without_data(client, auth):
    metrics = data(await client.get(METRICS, headers=await auth("admin")))
    assert metrics["rag_no_answer_rate"] is None and metrics["action_confirmation_rate"] is None
    assert metrics["action_execution"]["success_rate"] is None and metrics["intents"] == {}


@pytest.mark.parametrize("user", ["employee", "manager"])
async def test_usage_metrics_are_admin_only(client, auth, user):
    response = await client.get(METRICS, headers=await auth(user))
    assert response.status_code == 403 and error_code(response) == "AI_PERMISSION_DENIED"


async def test_actor_role_comes_from_the_database_not_the_request(client, auth, install_real, db):
    install_real(script())
    response = await client.post(f"{CHAT}?role=ADMIN", json={"message": "What is the WFH policy?"},
                                 headers={**await auth("employee"), "X-User-Role": "ADMIN", "X-Role": "ADMIN"})
    rows = await audit_rows(db, request_id=response.headers["x-request-id"])
    assert meta(rows[-1])["actor_role"] == "EMPLOYEE"


async def test_metrics_never_expose_content(client, auth, install_real):
    admin = await traffic(client, auth, install_real)
    text = (await client.get(METRICS, headers=admin)).text
    for leaked in ("WFH policy", "Drop the employees", "DELETE", "leave_balances", "Rahul", str(RAHUL_LEAVE) + ","):
        assert leaked not in text
