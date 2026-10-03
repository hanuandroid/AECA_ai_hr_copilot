"""Router over the REAL Policy RAG, SQL agent and Action agent (only the LLM is scripted).

Proves routing reaches the real services and that their security boundaries (authorization, SQL
read-only validation, scope, target resolution, confirmation) still hold behind the router.
"""

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from app.models.leave_request import LeaveRequest
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.rag.models import PolicyLLMOutput
from app.services.ai.sql.models import SQLGenerationOutput
from tests.ai.orchestration.conftest import JOHN, RAHUL_LEAVE, RAHUL_OUTSIDE_LEAVE, action_proposal, classification, full_llm
from tests.support.data import EMPLOYEE, OUTSIDER
from tests.support.db import business_snapshot


async def leave_status(db, leave_id: int) -> str:
    db.expire_all()
    status = (await db.execute(select(LeaveRequest.status).where(LeaveRequest.id == leave_id))).scalar_one()
    return status.value if hasattr(status, "value") else status


# --- policy / sql ---------------------------------------------------------------------------


async def test_policy_question_reaches_real_rag(real_copilot, ctx_for):
    llm = full_llm()
    response = await real_copilot(llm).answer(await ctx_for("employee"), "What is the sick leave policy?")
    assert response.routes == ["POLICY"] and response.status == "answered"
    assert response.parts[0].data["grounded"] is True and response.parts[0].data["sources"]
    assert llm.count(PolicyLLMOutput) == 1 and llm.count(RouterClassification) == 0


async def test_sql_question_reaches_real_sql_agent_with_self_scope(real_copilot, ctx_for):
    llm = full_llm(sql="SELECT employee_id, remaining FROM leave_balances WHERE employee_id = :me AND leave_type = 'SICK'")
    response = await real_copilot(llm).answer(await ctx_for("employee"), "What is my remaining leave balance?")
    data = response.parts[0].data
    assert response.routes == ["SQL"] and data["scope"] == "SELF"
    assert {row["employee_id"] for row in data["rows"]} == {EMPLOYEE.id}


async def test_policy_plus_sql_with_real_services(real_copilot, ctx_for):
    llm = full_llm(sql="SELECT remaining FROM leave_balances WHERE employee_id = :me AND leave_type = 'SICK'")
    response = await real_copilot(llm).answer(await ctx_for("employee"),
                                              "What is the sick leave policy and how many sick leaves do I have?")
    assert response.routes == ["POLICY", "SQL"] and response.status == "answered"
    assert llm.count(PolicyLLMOutput) == 1 and llm.count(SQLGenerationOutput) == 1
    sql_prompt = next(prompt for schema, prompt in llm.calls if schema == "SQLGenerationOutput")
    assert "how many sick leaves do I have" in sql_prompt and "sick leave policy" not in sql_prompt


@pytest.mark.parametrize("sql,code", [
    ("DELETE FROM employees", "SQL_MUTATION_REJECTED"),
    ("UPDATE leave_requests SET status = 'APPROVED'", "SQL_MUTATION_REJECTED"),
    ("DROP TABLE employees", "SQL_MUTATION_REJECTED"),
    ("SELECT pan_number FROM employees", "SQL_COLUMN_NOT_ALLOWED"),
    ("SELECT bank_account_number FROM employees", "SQL_COLUMN_NOT_ALLOWED"),
    ("SELECT name FROM employees; DELETE FROM employees", "SQL_MUTATION_REJECTED"),
])
async def test_router_cannot_cause_sql_mutation_or_sensitive_reads(real_copilot, ctx_for, sql, code):
    before = business_snapshot()
    llm = full_llm(sql=sql)
    with pytest.raises(Exception) as raised:
        await real_copilot(llm).answer(await ctx_for("admin"), "How many employees are in Engineering?")
    assert raised.value.code == code
    assert business_snapshot() == before


async def test_employee_cannot_read_other_employees_rows_through_router(real_copilot, ctx_for):
    llm = full_llm(sql="SELECT employee_id, remaining FROM leave_balances WHERE employee_id = 4")
    with pytest.raises(Exception) as raised:
        await real_copilot(llm).answer(await ctx_for("employee"), "How many sick leaves does Outsider have left?")
    assert raised.value.code == "SQL_SCOPE_VIOLATION"


# --- actions: propose only, confirmation stays with Phase 5 ----------------------------------


async def test_action_is_proposed_not_executed(real_copilot, ctx_for, db):
    before = business_snapshot()
    llm = full_llm(action=action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Mehta"))
    response = await real_copilot(llm).answer(await ctx_for("manager"), "Approve Rahul's leave.")
    assert response.status == "confirmation_required" and response.code == "AI_CONFIRMATION_REQUIRED"
    assert response.parts[0].data["confirmation_id"] and response.parts[0].data["action"] == "LEAVE_APPROVE"
    assert await leave_status(db, RAHUL_LEAVE) == "PENDING"
    assert business_snapshot() == before


async def test_router_proposal_is_confirmed_only_through_the_phase5_mechanism(real_copilot, ctx_for, db, login):
    llm = full_llm(action=action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Mehta"))
    copilot = real_copilot(llm)
    ctx = await ctx_for("manager")
    response = await copilot.answer(ctx, "Approve Rahul's leave.")
    token = response.parts[0].data["confirmation_id"]
    result = await copilot._actions.confirm(ctx, token, await login("manager"))
    assert result.status == "completed" and await leave_status(db, RAHUL_LEAVE) == "APPROVED"
    with pytest.raises(Exception) as replay:
        await copilot._actions.confirm(ctx, token, await login("manager"))
    assert replay.value.code == "AI_CONFIRMATION_INVALID"


async def test_policy_plus_action_answers_policy_and_leaves_the_action_pending(real_copilot, ctx_for, db):
    before = business_snapshot()
    llm = full_llm(action=action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Mehta"))
    response = await real_copilot(llm).answer(await ctx_for("manager"), "Tell me the leave policy and approve Rahul's leave.")
    assert response.routes == ["POLICY", "ACTION"] and response.status == "confirmation_required"
    assert response.parts[0].status == "answered"
    action_prompt = next(prompt for schema, prompt in llm.calls if schema == "ActionProposal")
    assert "approve Rahul's leave" in action_prompt and "leave policy" not in action_prompt.split("approve")[0][-40:]
    assert await leave_status(db, RAHUL_LEAVE) == "PENDING" and business_snapshot() == before


@pytest.mark.parametrize("proposal", [
    action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Verma"),
    action_proposal("LEAVE_APPROVE", resource_id=RAHUL_OUTSIDE_LEAVE),
    action_proposal("LEAVE_APPROVE", target_employee_id=21),
])
async def test_manager_outside_team_is_still_refused(real_copilot, ctx_for, db, proposal):
    before = business_snapshot()
    with pytest.raises(Exception) as raised:
        await real_copilot(full_llm(action=proposal)).answer(await ctx_for("manager"), "Approve Rahul's leave.")
    assert raised.value.code == "AI_TARGET_NOT_FOUND"
    assert await leave_status(db, RAHUL_OUTSIDE_LEAVE) == "PENDING" and business_snapshot() == before


@pytest.mark.parametrize("proposal", [
    action_proposal("EMPLOYEE_DEACTIVATE", target_employee_id=JOHN),
    action_proposal("LEAVE_APPROVE", resource_id=RAHUL_LEAVE),
    action_proposal("ANNOUNCEMENT_CREATE", arguments={"title": "Hello", "body": "Hello everyone"}),
])
async def test_employee_cannot_gain_manager_or_admin_actions_through_router(real_copilot, ctx_for, db, proposal):
    before = business_snapshot()
    with pytest.raises(Exception) as raised:
        await real_copilot(full_llm(action=proposal)).answer(await ctx_for("employee"), "Approve Rahul's leave.")
    assert raised.value.code in ("AI_PERMISSION_DENIED", "AI_TARGET_NOT_FOUND")
    assert business_snapshot() == before


async def test_admin_deactivation_is_only_proposed(real_copilot, ctx_for, db):
    llm = full_llm(action=action_proposal("EMPLOYEE_DEACTIVATE", target_employee_id=JOHN))
    response = await real_copilot(llm).answer(await ctx_for("admin"), "Deactivate John Mathew.")
    assert response.status == "confirmation_required"
    db.expire_all()
    status = (await db.execute(select(Employee.status).where(Employee.id == JOHN))).scalar_one()
    assert status == EmployeeStatus.ACTIVE


async def test_action_proposal_with_invented_fields_is_rejected(real_copilot, ctx_for):
    proposal = {**action_proposal("LEAVE_APPROVE", target_employee_name="Rahul Mehta"), "url": "/api/v1/employees/3",
                "method": "DELETE"}
    with pytest.raises(Exception) as raised:
        await real_copilot(full_llm(action=proposal)).answer(await ctx_for("manager"), "Approve Rahul's leave.")
    assert raised.value.code == "AI_INVALID_OUTPUT"


async def test_llm_routed_request_still_hits_real_authorization(real_copilot, ctx_for):
    """Even if the classifier picks ACTION for an employee, the action agent authorizes independently."""
    llm = full_llm(router=classification("ACTION", 0.99),
                   action=action_proposal("EMPLOYEE_DEACTIVATE", target_employee_id=OUTSIDER.id))
    before = business_snapshot()
    with pytest.raises(Exception) as raised:
        await real_copilot(llm).answer(await ctx_for("employee"), "I need Outsider gone")
    assert raised.value.code == "AI_PERMISSION_DENIED" and business_snapshot() == before
