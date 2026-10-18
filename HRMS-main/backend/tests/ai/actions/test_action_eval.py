import copy
import json

from sqlalchemy import select

from app.models.leave_request import LeaveRequest
from evals.action_agent_eval import evaluate, load_cases
from evals.reporting import from_actions, publish
from tests.support.data import EMPLOYEE_PENDING_LEAVE_ID, PASSWORD
from tests.support.db import business_snapshot

REQUIRED_VALID = {"LEAVE_CREATE", "LEAVE_APPROVE", "LEAVE_REJECT", "TICKET_CREATE", "TICKET_ASSIGN",
                  "TICKET_STATUS_UPDATE", "ANNOUNCEMENT_CREATE", "PROJECT_ASSIGN", "EMPLOYEE_DEACTIVATE"}


def test_dataset_shape_and_coverage():
    cases = load_cases()
    for case in cases:
        assert {"question", "role", "expected_action", "expected_capability", "expected_authorization",
                "confirmation_required", "expected_result"} <= set(case)
    completed = {c["expected_action"] for c in cases if c["expected_result"] == "completed"}
    assert REQUIRED_VALID <= completed
    notes = json.dumps(cases)
    for scenario in ("ambiguous employee", "ambiguous leave", "ambiguous project"):
        assert scenario in notes
    assert any(c["role"] == "MANAGER" and c["expected_action"] == "PROJECT_CREATE" and c["expected_authorization"] == "DENY" for c in cases)
    assert any(c["expected_action"] == "EMPLOYEE_DEACTIVATE" and c["proposal"].get("target_employee_id") == 1 for c in cases)


async def test_action_eval_passes(agent, login, db):
    pending_start = (await db.execute(select(LeaveRequest.start_date).where(LeaveRequest.id == EMPLOYEE_PENDING_LEAVE_ID))).scalar_one()
    cases = json.loads(json.dumps(load_cases()).replace("__EMPLOYEE_PENDING_START__", pending_start.isoformat()))
    services = {}

    async def propose(role_key, proposal, question):
        service, _, ctx = await agent(role_key, copy.deepcopy({"needs_clarification": False, **proposal}))
        services[role_key] = (service, ctx)
        return await service.propose(ctx, question)

    async def confirm(role_key, confirmation_id):
        service, ctx = services[role_key]
        # AIS-12: the authorized user re-enters their (synthetic) password; ignored for non-step-up actions.
        return await service.confirm(ctx, confirmation_id, await login(role_key), password=PASSWORD)

    report = await evaluate(cases, propose, confirm, business_snapshot)
    publish(from_actions(report))
    failures = [r for r in report.results if not r.passed]
    assert failures == [], failures
    assert all(value == 1.0 for value in report.metrics.values()), report.metrics
    assert len(report.results) == 20
