"""Admin self-actions (production hardening closure review, 2026-10-14; finding S-02).

These tests PIN CURRENT BEHAVIOUR so any change is deliberate. Prohibited today: deactivating one's own account and
deciding one's own leave. Allowed today WITHOUT additional controls (S-02, proposed controls in
docs/production-hardening/authorization-matrix.md; not implemented pending approval):
- an ADMIN can change their own role, which can leave the organisation with no active ADMIN;
- an ADMIN can change their own salary, bank and PAN details (no second approver);
- an ADMIN can change their own email/password through the admin endpoint without re-authentication;
- an ADMIN can upload payroll documents into their own record.
When S-02 controls are approved, these assertions change together with the endpoint, documenting the old behaviour.
"""

from sqlalchemy import func, select

from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from tests.support.data import ADMIN
from tests.support.http import data, error_code


async def _active_admins(db) -> int:
    db.expire_all()
    return (await db.execute(select(func.count(Employee.id)).where(
        Employee.role == Role.ADMIN, Employee.status == EmployeeStatus.ACTIVE))).scalar_one()


async def test_admin_cannot_deactivate_own_account(client, auth):
    response = await client.delete(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"))
    assert response.status_code == 400 and error_code(response) == "INVALID_OPERATION"


async def test_current_behaviour_admin_can_demote_self_leaving_no_admin(client, auth, db):
    assert await _active_admins(db) == 1
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"), json={"role": "EMPLOYEE"})
    assert response.status_code == 200  # S-02: no guard for self-demotion or for removing the last active ADMIN
    assert await _active_admins(db) == 0


async def test_current_behaviour_admin_can_change_own_compensation_and_bank_details(client, auth):
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"current_salary_usd": 999999, "bank_account_number": "12345678901"})
    assert response.status_code == 200  # S-02: no second approver for self-service payroll changes
    assert data(response)["id"] == ADMIN.id


async def test_current_behaviour_admin_can_reset_own_password_without_reauthentication(client, auth):
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"password": "a-new-password-123"})
    assert response.status_code == 200  # S-02: no current-password check on the admin endpoint


async def test_current_behaviour_admin_payslip_upload_into_own_record_is_not_scope_blocked(client, auth):
    files = {"file": ("payslip.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")}
    response = await client.post(f"/api/v1/employees/{ADMIN.id}/documents/payslip", headers=await auth("admin"),
                                 data={"title": "Own payslip", "period": "2026-09"}, files=files)
    assert response.status_code != 403  # S-02: managers are blocked from their own payslips (S-01c); admins are not
