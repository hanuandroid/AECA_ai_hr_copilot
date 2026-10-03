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


async def test_inactive_user_can_still_create_leave(client, auth):
    """CURRENT BEHAVIOUR (R9): INACTIVE employees are not blocked by the existing API."""
    response = await client.post("/api/v1/leaves/requests", json=leave_payload(), headers=await auth("inactive"))
    assert response.status_code == 200
    assert data(response)["employee_id"] == INACTIVE.id


# --- GET /leaves/requests/pending -----------------------------------------


async def test_employee_cannot_list_pending_approvals(client, auth):
    response = await client.get("/api/v1/leaves/requests/pending", headers=await auth("employee"))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


async def test_manager_sees_all_pending_leaves_not_only_team(client, auth):
    """CURRENT BEHAVIOUR (R4): no team scoping - the outsider's pending leave is visible to MANAGER."""
    response = await client.get("/api/v1/leaves/requests/pending", headers=await auth("manager"))
    assert response.status_code == 200
    ids = {item["id"] for item in data(response)["items"]}
    assert {EMPLOYEE_PENDING_LEAVE_ID, OUTSIDER_PENDING_LEAVE_ID} <= ids


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


async def test_manager_can_approve_leave_outside_team(client, auth):
    """CURRENT BEHAVIOUR (R4): the existing API does not check the manager relationship.

    The AI layer must be stricter (D7): approve_leave will pre-check team membership.
    """
    response = await client.post(
        f"/api/v1/leaves/requests/{OUTSIDER_PENDING_LEAVE_ID}/approve", headers=await auth("manager")
    )
    assert response.status_code == 200
    assert data(response)["status"] == "APPROVED"


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
