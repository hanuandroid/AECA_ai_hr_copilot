"""Admin self-actions (RBAC remediation step 4, approved Decision 2; finding S-02).

Old behaviour (pinned here until 2026-10-16): an ADMIN could change their own role (leaving zero active admins), change
their own salary/bank/PAN, reset their own password through the admin endpoint without the current password, and
upload payslips into their own record. Self-deactivation and self-approval of a pending leave were already refused.

Invariant now (backend, before anything is written):
- no self role change (403 SELF_ROLE_CHANGE_FORBIDDEN);
- no self salary/bank/PAN edit (403 SELF_SENSITIVE_EDIT_FORBIDDEN, whole request refused, nothing persisted);
- no self password reset via the admin endpoint (403 SELF_PASSWORD_RESET_FORBIDDEN; use POST /auth/change-password);
- no self payslip upload, through the payslip endpoint or a PAYSLIP-type self-service document (403 SELF_PAYSLIP_FORBIDDEN);
- the last active administrator can never be demoted or deactivated (tests/rbac/test_last_active_admin.py).
The same operations on OTHER employees keep working.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.employee import Employee
from app.models.employee_document import EmployeeDocument
from app.models.enums import EmployeeStatus, Role
from tests.support.data import ADMIN, EMPLOYEE, OUTSIDER
from tests.support.http import error_code

SENSITIVE_UPDATES = {
    "current_salary_usd": 999999, "bank_name": "Attacker Bank", "bank_account_number": "12345678901",
    "bank_account_name": "Admin Self", "bank_branch": "Main Branch", "bank_ifsc": "ATTK0000001",
    "pan_number": "ABCDE1234F", "pan_name": "Admin Self", "pan_dob": "1990-01-01",
}


async def _row(db, employee_id: int) -> Employee:
    db.expire_all()
    return (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one()


async def _snapshot(db, employee_id: int) -> dict:
    row = await _row(db, employee_id)
    return {column: getattr(row, column) for column in
            ("name", "role", "status", "hashed_password", "phone", *[k for k in SENSITIVE_UPDATES])}


async def _active_admins(db) -> int:
    db.expire_all()
    return (await db.execute(select(func.count(Employee.id)).where(
        Employee.role == Role.ADMIN, Employee.status == EmployeeStatus.ACTIVE))).scalar_one()


# --- existing refusals (unchanged) --------------------------------------------------------------------------------


async def test_admin_cannot_deactivate_own_account(client, auth):
    response = await client.delete(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"))
    assert response.status_code == 400 and error_code(response) == "INVALID_OPERATION"


# --- self role change ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("new_role", ["EMPLOYEE", "MANAGER", "employee"])
async def test_admin_cannot_change_own_role(client, auth, db, new_role):
    before = await _snapshot(db, ADMIN.id)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"role": new_role, "name": "Renamed Admin"})
    assert response.status_code == 403 and error_code(response) == "SELF_ROLE_CHANGE_FORBIDDEN"
    assert await _snapshot(db, ADMIN.id) == before  # the name change in the same request was not applied
    assert await _active_admins(db) == 1


async def test_restating_own_current_role_is_not_a_change(client, auth, db):
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"role": "ADMIN", "name": "Admin Renamed"})
    assert response.status_code == 200 and (await _row(db, ADMIN.id)).name == "Admin Renamed"


async def test_admin_changes_another_employees_role(client, auth, db):
    response = await client.put(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"), json={"role": "MANAGER"})
    assert response.status_code == 200 and (await _row(db, OUTSIDER.id)).role == Role.MANAGER


# --- sensitive self-edits -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", list(SENSITIVE_UPDATES.items()))
async def test_admin_cannot_edit_own_sensitive_field(client, auth, db, field, value):
    before = await _snapshot(db, ADMIN.id)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"), json={field: value})
    assert response.status_code == 403 and error_code(response) == "SELF_SENSITIVE_EDIT_FORBIDDEN"
    assert await _snapshot(db, ADMIN.id) == before


@pytest.mark.parametrize("payload", [
    {"name": "Allowed Part", "phone": "9876543210", "current_salary_usd": 1},  # mixed allowed + forbidden
    {"bank_account_number": None},  # explicit null is still an attempt to change it
    {"pan_number": "ABCDE1234F", "role": "ADMIN"},
])
async def test_mixed_or_null_sensitive_self_edits_are_refused_entirely(client, auth, db, payload):
    before = await _snapshot(db, ADMIN.id)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"), json=payload)
    assert response.status_code == 403 and error_code(response) == "SELF_SENSITIVE_EDIT_FORBIDDEN"
    assert await _snapshot(db, ADMIN.id) == before


async def test_nested_payloads_are_not_fields_and_change_nothing(client, auth, db):
    before = await _snapshot(db, ADMIN.id)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"bank": {"bank_account_number": "12345678901"}, "payroll": {"current_salary_usd": 1}})
    assert response.status_code == 200  # unknown keys are ignored by the schema (existing API contract)
    assert await _snapshot(db, ADMIN.id) == before


async def test_admin_can_edit_own_non_sensitive_fields(client, auth, db):
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"),
                                json={"name": "Admin Prime", "phone": "9876543210"})
    assert response.status_code == 200
    row = await _row(db, ADMIN.id)
    assert (row.name, row.phone) == ("Admin Prime", "9876543210")


async def test_admin_edits_another_employees_sensitive_fields(client, auth, db):
    response = await client.put(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"), json=SENSITIVE_UPDATES)
    assert response.status_code == 200
    row = await _row(db, OUTSIDER.id)
    assert (row.current_salary_usd, row.bank_account_number, row.pan_number) == (999999, "12345678901", "ABCDE1234F")


async def test_forged_role_claim_cannot_reach_the_admin_endpoint(client):
    token = create_access_token(subject=str(EMPLOYEE.id), role="ADMIN")
    response = await client.put(f"/api/v1/employees/{EMPLOYEE.id}", headers={"Authorization": f"Bearer {token}"},
                                json={"current_salary_usd": 1})
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"


# --- password reset via the admin endpoint ------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [{"password": "a-new-password-123"}, {"password": " " * 8},
                                     {"name": "X Y", "password": "a-new-password-123"}])
async def test_admin_cannot_reset_own_password_via_admin_endpoint(client, auth, db, payload):
    before = await _snapshot(db, ADMIN.id)
    response = await client.put(f"/api/v1/employees/{ADMIN.id}", headers=await auth("admin"), json=payload)
    assert response.status_code == 403 and error_code(response) == "SELF_PASSWORD_RESET_FORBIDDEN"
    assert "change-password" in response.text
    assert await _snapshot(db, ADMIN.id) == before


async def test_admin_resets_another_employees_password(client, auth, db):
    response = await client.put(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"),
                                json={"password": "outsider-new-pass"})
    assert response.status_code == 200
    assert verify_password("outsider-new-pass", (await _row(db, OUTSIDER.id)).hashed_password)
    login = await client.post("/api/v1/auth/login", json={"email": OUTSIDER.email, "password": "outsider-new-pass"})
    assert login.status_code == 200


# --- payslips -----------------------------------------------------------------------------------------------------


def _payslip(title: str = "Own payslip") -> dict:
    return {"data": {"title": title, "period": "2026-09"},
            "files": {"file": ("payslip.pdf", b"%PDF-1.4 synthetic payroll 123456", "application/pdf")}}


async def _documents(db) -> int:
    db.expire_all()
    return (await db.execute(select(func.count(EmployeeDocument.id)))).scalar_one()


def _stored_files() -> int:
    root = Path(settings.employee_document_upload_dir)
    return sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0


@pytest.mark.parametrize("user,target", [("admin", ADMIN.id), ("manager", 2)])
async def test_nobody_uploads_a_payslip_into_their_own_record(client, auth, db, user, target):
    documents, files = await _documents(db), _stored_files()
    response = await client.post(f"/api/v1/employees/{target}/documents/payslip", headers=await auth(user), **_payslip())
    assert response.status_code == 403 and error_code(response) == "SELF_PAYSLIP_FORBIDDEN"
    assert "synthetic payroll" not in response.text
    assert (await _documents(db), _stored_files()) == (documents, files)  # refused before storage


async def test_admin_cannot_self_upload_a_payslip_type_document(client, auth, db):
    documents, files = await _documents(db), _stored_files()
    response = await client.post("/api/v1/employees/me/documents", headers=await auth("admin"),
                                 data={"title": "Own payslip", "document_type": "PAYSLIP"},
                                 files={"file": ("p.pdf", b"%PDF-1.4 synthetic", "application/pdf")})
    assert response.status_code == 403 and error_code(response) == "SELF_PAYSLIP_FORBIDDEN"
    assert (await _documents(db), _stored_files()) == (documents, files)


async def test_admin_self_service_non_payslip_documents_still_work(client, auth):
    response = await client.post("/api/v1/employees/me/documents", headers=await auth("admin"),
                                 data={"title": "Own contract", "document_type": "OTHER"},
                                 files={"file": ("c.txt", b"contract", "text/plain")})
    assert response.status_code == 200


async def test_employee_self_service_payslip_documents_follow_existing_policy(client, auth):
    """Documented policy (document_upload_policy.md): employees may upload their own Payslip-type documents.
    Not changed by step 4; recorded as an open decision in the step 4 report."""
    response = await client.post("/api/v1/employees/me/documents", headers=await auth("employee"),
                                 data={"title": "My payslip copy", "document_type": "PAYSLIP"},
                                 files={"file": ("p.pdf", b"%PDF-1.4 synthetic", "application/pdf")})
    assert response.status_code == 200


async def test_admin_uploads_payslips_for_others(client, auth):
    response = await client.post(f"/api/v1/employees/{OUTSIDER.id}/documents/payslip", headers=await auth("admin"),
                                 **_payslip("Outsider payslip"))
    assert response.status_code == 200


async def test_inactive_admin_token_cannot_act(client, db):
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(role=Role.ADMIN, status=EmployeeStatus.INACTIVE))
    await db.commit()
    token = create_access_token(subject=str(OUTSIDER.id), role="ADMIN")
    response = await client.put(f"/api/v1/employees/{EMPLOYEE.id}", headers={"Authorization": f"Bearer {token}"},
                                json={"name": "Nope"})
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"
