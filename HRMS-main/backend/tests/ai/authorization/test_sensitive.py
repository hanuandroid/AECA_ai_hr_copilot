"""Sensitive-field policy, checked against the real schema (Base.metadata), not only a hand list."""

import pytest

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.db.base import Base
from app.models.enums import Role
from app.services.ai.authorization import is_column_readable, is_sensitive_field
from app.services.ai.guards import strip_sensitive_fields
from tests.ai.authorization.conftest import make_ctx

# Exhaustive classification of the two tables holding sensitive data.
EMPLOYEES_SENSITIVE = {
    "hashed_password", "date_of_birth", "phone", "address", "blood_type", "profile_photo_path",
    "profile_photo_mime", "current_salary_usd", "bank_name", "bank_account_number", "bank_account_name",
    "bank_branch", "bank_ifsc", "pan_number", "pan_name", "pan_dob",
}
EMPLOYEES_SAFE = {"id", "name", "email", "department_id", "manager_id", "role", "status", "joining_date", "occupancy"}
PAYROLL_ALWAYS_SENSITIVE = {"pan", "pf_uan", "esi_no", "deductions"}
PAYROLL_ADMIN_ONLY = {"id", "employee_id", "month", "gross", "net"}

PROMPT_REQUIRED_NAMES = [
    "salary", "bank_account", "account_number", "ifsc", "pan", "date_of_birth", "dob", "pf_number",
    "pf_uan", "esi_no", "password", "password_hash",
]
ALIASES = [
    "Salary", "employee_salary", "salaryUsd", "currentSalaryUsd", "BANK_IFSC", "bankAccountNumber",
    "pan_no", "PAN", "dob2", "birthDate", "hashed_password", "pwd_password", "ssn", "aadhaar_number",
    "uan", "esi", "phone_number", "home_address", "photo_url", "file_path",
]
SAFE_NAMES = [
    "name", "email", "department", "department_id", "project_name", "role_on_project", "skill", "level",
    "leave_type", "remaining", "status", "title", "priority", "joining_date", "company", "span", "expand",
]


def _columns(table: str) -> set[str]:
    return {column.name for column in Base.metadata.tables[table].columns}


def test_schema_classification_is_exhaustive_for_employees():
    assert _columns("employees") == EMPLOYEES_SENSITIVE | EMPLOYEES_SAFE, "new employees column: classify it"
    assert {c for c in _columns("employees") if is_sensitive_field(c)} == EMPLOYEES_SENSITIVE


def test_schema_classification_is_exhaustive_for_payroll():
    assert _columns("payroll_records") == PAYROLL_ALWAYS_SENSITIVE | PAYROLL_ADMIN_ONLY
    assert {c for c in _columns("payroll_records") if is_sensitive_field(c)} == PAYROLL_ALWAYS_SENSITIVE


@pytest.mark.parametrize("table,column", [("employee_documents", "file_path"), ("employee_documents", "checksum"),
                                          ("hr_policies", "file_path"), ("hr_policies", "checksum"),
                                          ("hr_policies", "embedding")])
def test_internal_metadata_columns_are_sensitive(table, column):
    assert column in _columns(table)
    assert is_sensitive_field(column)


@pytest.mark.parametrize("name", PROMPT_REQUIRED_NAMES + ALIASES)
def test_sensitive_names_and_aliases_denied(name):
    assert is_sensitive_field(name)


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_ordinary_fields_allowed(name):
    assert not is_sensitive_field(name)


@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_non_names_are_not_misclassified(value):
    assert is_sensitive_field(value) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("column", sorted(EMPLOYEES_SENSITIVE))
def test_no_role_can_read_sensitive_employee_columns(role, column):
    assert is_column_readable(make_ctx(role), "employees", column) is False


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("column", sorted(PAYROLL_ALWAYS_SENSITIVE))
def test_no_role_can_read_payroll_identifiers(role, column):
    assert is_column_readable(make_ctx(role), "payroll_records", column) is False


@pytest.mark.parametrize("role,allowed", [(Role.ADMIN, True), (Role.MANAGER, False), (Role.EMPLOYEE, False)])
def test_payroll_amounts_admin_only(role, allowed):
    for column in ("gross", "net", "month"):
        assert is_column_readable(make_ctx(role), "payroll_records", column) is allowed


def test_safe_columns_readable_by_active_users_only():
    assert is_column_readable(make_ctx(Role.EMPLOYEE), "employees", "name") is True
    assert is_column_readable(make_ctx(Role.ADMIN, active=False), "employees", "name") is False


def test_strip_sensitive_fields_catches_aliases():
    payload = {"name": "A", "salary": 1, "bankAccountNumber": "x", "nested": [{"dob": "1990", "skill": "Python"}]}
    assert strip_sensitive_fields(payload) == {"name": "A", "nested": [{"skill": "Python"}]}
