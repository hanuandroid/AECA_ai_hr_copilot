"""Characterization of api/v1/endpoints/leaves.py (wrapped by AI tools in Phase 5)."""

import pytest
from sqlalchemy import select

from app.models.enums import LeaveType
from app.models.leave_balance import LeaveBalance
from tests.support.data import (
    ADMIN,
    EMPLOYEE,
    EMPLOYEE_APPROVED_LEAVE_ID,
    EMPLOYEE_PENDING_LEAVE_ID,
    INACTIVE,
    MANAGER,
    OUTSIDER,
    OUTSIDER_PENDING_LEAVE_ID,
    USERS,
    future,
)
from tests.support.http import data, error_code


def leave_payload(**overrides):
    payload = {
        "leave_type": "SICK",
        "start_date": future(30).isoformat(),
        "end_date": future(31).isoformat(),
        "reason": "Fever",
        "is_half_day": False,
        "half_day_period": None,
    }
    payload.update(overrides)
    return payload


async def _remaining(db, employee_id: int, leave_type: LeaveType) -> float:
    row = (
        await db.execute(
            select(LeaveBalance).where(LeaveBalance.employee_id == employee_id, LeaveBalance.leave_type == leave_type)
        )
    ).scalar_one()
    return row.remaining


# --- POST /leaves/requests -------------------------------------------------


async def test_employee_creates_pending_leave(client, auth):
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(), headers=await auth("employee"))
    assert response.status_code == 200
    leave = data(response)
    assert leave["employee_id"] == EMPLOYEE.id
    assert leave["status"] == "PENDING"
    assert leave["approver_id"] is None


async def test_leave_is_always_created_for_the_token_owner(client, auth):
    """employee_id is not accepted from the payload; the token owner is used."""
    response = await client.post(
        "/api/v1/leaves/requests", json=leave_payload(employee_id=OUTSIDER.id), headers=await auth("employee")
    )
    assert response.status_code == 200
    assert data(response)["employee_id"] == EMPLOYEE.id


async def test_admin_leave_is_auto_approved_and_balance_reduced(client, auth, db):
    before = await _remaining(db, ADMIN.id, LeaveType.SICK)
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(), headers=await auth("admin"))
    assert response.status_code == 200
    leave = data(response)
    assert leave["status"] == "APPROVED"
    assert leave["approver_id"] == ADMIN.id
    db.expire_all()
    assert await _remaining(db, ADMIN.id, LeaveType.SICK) == before - 2


async def test_half_day_leave_accepted(client, auth):
    day = future(40).isoformat()
    response = await client.post(
        "/api/v1/leaves/requests",
        json=leave_payload(start_date=day, end_date=day, is_half_day=True, half_day_period="first_half"),
        headers=await auth("employee"),
    )
    assert response.status_code == 200
    assert data(response)["half_day_period"] == "FIRST_HALF"


@pytest.mark.parametrize(
    "overrides,status,code",
    [
        ({"start_date": future(31).isoformat(), "end_date": future(30).isoformat()}, 400, "INVALID_DATE_RANGE"),
        ({"leave_type": "VACATION"}, 400, "INVALID_LEAVE_TYPE"),
        ({"is_half_day": True, "half_day_period": "FIRST_HALF"}, 400, "INVALID_HALF_DAY_RANGE"),
        (
            {"is_half_day": True, "start_date": future(30).isoformat(), "end_date": future(30).isoformat()},
            400,
            "INVALID_HALF_DAY_PERIOD",
        ),
        ({"half_day_period": "FIRST_HALF"}, 400, "INVALID_HALF_DAY_PERIOD"),
        (
            {"start_date": future(10).isoformat(), "end_date": future(10).isoformat()},
            409,
            "LEAVE_OVERLAP",
        ),
    ],
    ids=["reversed-dates", "bad-type", "half-day-multi-day", "half-day-no-period", "period-without-half-day", "overlap"],
)
async def test_leave_validation(client, auth, overrides, status, code):
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(**overrides), headers=await auth("employee"))
    assert response.status_code == status
    assert error_code(response) == code


async def test_leave_reason_too_short_is_422(client, auth):
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(reason="no"), headers=await auth("employee"))
    assert response.status_code == 422


async def test_inactive_user_cannot_create_leave(client, auth, db):
    """Production hardening B-03 (approved remediation of R9).

    Old behaviour (pinned here until 2026-10-11): an INACTIVE employee's token could still create leave requests.
    Invariant now: get_current_user rejects the (pre-deactivation) token with 403 USER_INACTIVE and nothing is written.
    """
    from sqlalchemy import func

    from app.models.leave_request import LeaveRequest

    count = select(func.count(LeaveRequest.id)).where(LeaveRequest.employee_id == INACTIVE.id)
    before = (await db.execute(count)).scalar_one()
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(), headers=await auth("inactive"))
    assert response.status_code == 403
    assert error_code(response) == "USER_INACTIVE"
    assert (await db.execute(count)).scalar_one() == before


# --- GET /leaves/requests/pending -----------------------------------------


async def test_employee_cannot_list_pending_approvals(client, auth):
    response = await client.get("/api/v1/leaves/requests/pending", headers=await auth("employee"))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


PENDING = "/api/v1/leaves/requests/pending"
OUTSIDER_REASON = "Medical appointment"  # seeded reason of the outsider's pending request


async def test_manager_sees_only_direct_reports_pending_leaves(client, auth):
    """Production hardening B-05 (approved follow-up, remediation of R4).

    Old behaviour (pinned here until 2026-10-12): every manager saw ALL pending requests, including other teams'
    leave reasons. Invariant now: the approval queue holds only requests the caller may decide - direct reports
    for managers; everyone but themselves for admins - and counts/pagination reveal nothing else.
    """
    response = await client.get(PENDING, params={"limit": 100}, headers=await auth("manager"))
    assert response.status_code == 200
    body = data(response)
    ids = {item["id"] for item in body["items"]}
    assert EMPLOYEE_PENDING_LEAVE_ID in ids and OUTSIDER_PENDING_LEAVE_ID not in ids
    assert {item["employee_id"] for item in body["items"]} <= {EMPLOYEE.id, INACTIVE.id}  # MANAGER's direct reports
    assert body["meta"]["total"] == len(body["items"])
    assert OUTSIDER_REASON not in response.text


async def test_manager_pagination_does_not_reveal_out_of_scope_requests(client, auth):
    headers = await auth("manager")
    total = data(await client.get(PENDING, params={"limit": 100}, headers=headers))["meta"]["total"]
    seen = []
    for offset in range(0, total + 3):
        page = data(await client.get(PENDING, params={"limit": 1, "offset": offset}, headers=headers))
        assert page["meta"]["total"] == total
        seen += [item["id"] for item in page["items"]]
    assert OUTSIDER_PENDING_LEAVE_ID not in seen and len(seen) == total


async def test_admin_sees_other_employees_pending_leaves_but_not_own(client, auth, db):
    from app.models.enums import LeaveRequestStatus
    from app.models.leave_request import LeaveRequest

    own = LeaveRequest(employee_id=ADMIN.id, leave_type=LeaveType.CASUAL, start_date=future(45), end_date=future(45),
                       reason="Admin personal", status=LeaveRequestStatus.PENDING)
    db.add(own)
    await db.commit()
    body = data(await client.get(PENDING, params={"limit": 100}, headers=await auth("admin")))
    ids = {item["id"] for item in body["items"]}
    assert {EMPLOYEE_PENDING_LEAVE_ID, OUTSIDER_PENDING_LEAVE_ID} <= ids
    assert own.id not in ids  # admins cannot decide their own leave, so it is not in their queue


async def test_manager_does_not_see_own_pending_leave(client, auth, db):
    from app.models.enums import LeaveRequestStatus
    from app.models.leave_request import LeaveRequest

    own = LeaveRequest(employee_id=MANAGER.id, leave_type=LeaveType.CASUAL, start_date=future(46), end_date=future(46),
                       reason="Manager personal", status=LeaveRequestStatus.PENDING)
    db.add(own)
    await db.commit()
    body = data(await client.get(PENDING, params={"limit": 100}, headers=await auth("manager")))
    assert own.id not in {item["id"] for item in body["items"]}


# --- POST /leaves/requests/{id}/approve | reject --------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_employee_cannot_decide_leave(client, auth, action):
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_PENDING_LEAVE_ID}/{action}", headers=await auth("employee")
    )
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_employee_cannot_approve_own_leave(client, auth):
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_PENDING_LEAVE_ID}/approve", headers=await auth("employee")
    )
    assert response.status_code == 403


async def test_manager_approves_team_leave_and_balance_is_reduced(client, auth, db):
    before = await _remaining(db, EMPLOYEE.id, LeaveType.CASUAL)
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_PENDING_LEAVE_ID}/approve", headers=await auth("manager")
    )
    assert response.status_code == 200
    leave = data(response)
    assert leave["status"] == "APPROVED"
    assert leave["approver_id"] == MANAGER.id
    db.expire_all()
    assert await _remaining(db, EMPLOYEE.id, LeaveType.CASUAL) == before - 2


# Production hardening B-05 (approved remediation of R4). Old behaviour (pinned here until 2026-10-11): any manager
# could approve or reject any employee's leave, including their own. Invariant now: nobody decides their own leave;
# managers decide only for direct reports (employees.manager_id); admins decide for anyone else. Denials change nothing.


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_manager_cannot_decide_leave_outside_team(client, auth, db, action):
    headers = await auth("manager")
    before = await _remaining(db, OUTSIDER.id, LeaveType.CASUAL)
    response = await client.post(f"/api/v1/leaves/requests/{OUTSIDER_PENDING_LEAVE_ID}/{action}", headers=headers)
    unknown = await client.post(f"/api/v1/leaves/requests/999999/{action}", headers=headers)
    # Indistinguishable from an unknown id: request ids cannot be probed for existence.
    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()
    assert error_code(response) == "LEAVE_NOT_FOUND"
    db.expire_all()
    assert await _remaining(db, OUTSIDER.id, LeaveType.CASUAL) == before
    pending = await client.get("/api/v1/leaves/requests/pending", params={"limit": 100}, headers=await auth("admin"))
    assert OUTSIDER_PENDING_LEAVE_ID in {item["id"] for item in data(pending)["items"]}  # still pending


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_admin_can_decide_leave_for_another_employee(client, auth, action):
    response = await client.post(
        f"/api/v1/leaves/requests/{OUTSIDER_PENDING_LEAVE_ID}/{action}", headers=await auth("admin")
    )
    assert response.status_code == 200
    assert data(response)["approver_id"] == ADMIN.id


@pytest.mark.parametrize("decider", ["manager", "admin"])
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_nobody_decides_their_own_leave(client, auth, db, decider, action):
    from app.models.enums import LeaveRequestStatus
    from app.models.leave_request import LeaveRequest

    # A PENDING request of the decider's own (admin leave created via the API is auto-approved, so seed it directly).
    own = LeaveRequest(employee_id=USERS[decider].id, leave_type=LeaveType.CASUAL, start_date=future(40),
                       end_date=future(40), reason="Personal", status=LeaveRequestStatus.PENDING)
    db.add(own)
    await db.commit()
    response = await client.post(f"/api/v1/leaves/requests/{own.id}/{action}", headers=await auth(decider))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"
    await db.refresh(own)
    assert own.status == LeaveRequestStatus.PENDING and own.approver_id is None


async def test_admin_rejects_leave_without_changing_balance(client, auth, db):
    before = await _remaining(db, EMPLOYEE.id, LeaveType.CASUAL)
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_PENDING_LEAVE_ID}/reject", headers=await auth("admin")
    )
    assert response.status_code == 200
    assert data(response)["status"] == "REJECTED"
    assert data(response)["approver_id"] == ADMIN.id
    db.expire_all()
    assert await _remaining(db, EMPLOYEE.id, LeaveType.CASUAL) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_deciding_non_pending_leave_fails(client, auth, action):
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_APPROVED_LEAVE_ID}/{action}", headers=await auth("manager")
    )
    assert response.status_code == 400
    assert error_code(response) == "LEAVE_NOT_PENDING"


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_deciding_missing_leave_is_404(client, auth, action):
    response = await client.post(f"/api/v1/leaves/requests/9999/{action}", headers=await auth("manager"))
    assert response.status_code == 404
    assert error_code(response) == "LEAVE_NOT_FOUND"


async def test_approval_blocked_when_balance_insufficient(client, auth, db):
    balance = (
        await db.execute(
            select(LeaveBalance).where(LeaveBalance.employee_id == EMPLOYEE.id, LeaveBalance.leave_type == LeaveType.CASUAL)
        )
    ).scalar_one()
    balance.remaining = 1.0
    await db.commit()
    response = await client.post(
        f"/api/v1/leaves/requests/{EMPLOYEE_PENDING_LEAVE_ID}/approve", headers=await auth("manager")
    )
    assert response.status_code == 400
    assert error_code(response) == "LEAVE_INSUFFICIENT_BALANCE"


# --- Own-data reads --------------------------------------------------------


async def test_my_leave_requests_only_returns_own(client, auth):
    response = await client.get("/api/v1/leaves/requests/me", headers=await auth("employee"))
    assert response.status_code == 200
    assert {item["employee_id"] for item in data(response)["items"]} == {EMPLOYEE.id}


async def test_my_leave_balances(client, auth):
    response = await client.get("/api/v1/leaves/balances/me", headers=await auth("employee"))
    assert response.status_code == 200
    by_type = {row["leave_type"]: row for row in data(response)}
    assert by_type["SICK"]["total"] == 10
    assert by_type["CASUAL"]["total"] == 12
    assert by_type["EARNED"]["total"] == 15
