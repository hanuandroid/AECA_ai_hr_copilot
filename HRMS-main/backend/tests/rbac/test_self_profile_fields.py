"""Self-service profile update cannot change privileged fields (role-access audit 2026-10-20, Phase 5).

`PUT /api/v1/employees/me` accepts only name, phone, address, blood type, occupancy and date of birth
(`schemas.employee.EmployeeMeUpdate`). Today that holds because the schema omits every other field and Pydantic drops
unknown keys. No test pinned it, and the browser suite (frontend/e2e/api.spec.ts) checks only the role field, so this
backend regression test makes the rule explicit for every role: role, status, email, password, manager, department,
salary, bank, PAN and token version sent to the self-service endpoint change nothing, while an allowed field still
updates. Any handler or schema change that starts applying one of them (or starts rejecting extra keys) fails it.
"""

import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.employee import Employee
from tests.support.data import ADMIN, EMPLOYEE, MANAGER, PASSWORD

PRIVILEGED = {
    "role": "ADMIN", "status": "INACTIVE", "email": "attacker@hrms-test.dev", "password": "attacker-chosen-password",
    "manager_id": None, "department_id": 999, "current_salary_usd": 999999, "bank_name": "Attacker Bank",
    "bank_account_number": "999999999999", "bank_ifsc": "ATTK0000001", "pan_number": "ATTKR9999Z", "token_version": 0,
    "is_admin": True,
}
SNAPSHOT_FIELDS = ("role", "status", "email", "hashed_password", "manager_id", "department_id", "current_salary_usd",
                   "bank_name", "bank_account_number", "bank_ifsc", "pan_number", "token_version")


async def _snapshot(db, employee_id: int) -> dict:
    db.expire_all()
    employee = (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one()
    return {field: getattr(employee, field) for field in SNAPSHOT_FIELDS}


@pytest.mark.parametrize("user", [EMPLOYEE, MANAGER, ADMIN], ids=lambda user: user.key)
async def test_self_profile_update_ignores_privileged_fields(client, auth, db, user):
    before = await _snapshot(db, user.id)
    response = await client.put("/api/v1/employees/me", json={**PRIVILEGED, "phone": "9876500001"},
                                headers=await auth(user.key))
    assert response.status_code == 200

    after = await _snapshot(db, user.id)
    assert after == before  # nothing privileged changed
    employee = (await db.execute(select(Employee).where(Employee.id == user.id))).scalar_one()
    assert employee.phone == "9876500001"  # the allowed field did change
    assert verify_password(PASSWORD, employee.hashed_password)
    # The same token still works (token_version untouched) and the role the API reports is unchanged.
    me = await client.get("/api/v1/employees/me", headers=await auth(user.key))
    assert me.status_code == 200 and me.json()["data"]["role"] == user.role.value
