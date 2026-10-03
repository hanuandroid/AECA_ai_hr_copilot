"""The SQL registry must classify every table and every column of the real schema (Base.metadata).
A new table or column fails these tests until it is classified - no silent exposure."""

import pytest

import app.models  # noqa: F401
from app.db.base import Base
from app.services.ai.authorization import is_sensitive_field
from app.services.ai.sql.schema import (
    APPROVED_FOREIGN_KEYS,
    DENIED_TABLES,
    TABLES,
    TableKind,
    all_database_columns,
    forbidden_columns,
    is_approved_join,
)

# Explicit expectation, written independently of schema.py.
EXPECTED_FORBIDDEN = {
    "employees": {
        "hashed_password", "date_of_birth", "phone", "address", "blood_type", "profile_photo_path", "profile_photo_mime",
        "current_salary_usd", "bank_name", "bank_account_number", "bank_account_name", "bank_branch", "bank_ifsc",
        "pan_number", "pan_name", "pan_dob",
    },
    "payroll_records": {"pan", "pf_uan", "esi_no", "deductions"},
    "job_history": set(), "leave_balances": set(), "leave_requests": set(), "tickets": set(),
    "employee_projects": set(), "employee_skills": set(), "departments": set(), "skills": set(), "projects": set(),
}


def test_every_database_table_is_classified():
    assert set(Base.metadata.tables) == set(TABLES) | set(DENIED_TABLES)
    assert not set(TABLES) & set(DENIED_TABLES)


@pytest.mark.parametrize("table", sorted(EXPECTED_FORBIDDEN))
def test_every_column_is_classified(table):
    real = set(all_database_columns(table))
    allowed = set(TABLES[table].allowed_columns)
    assert allowed | set(forbidden_columns(table)) == real
    assert set(forbidden_columns(table)) == EXPECTED_FORBIDDEN[table], "new or reclassified column: review it"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_no_allowed_column_is_sensitive(table):
    assert not [c for c in TABLES[table].allowed_columns if is_sensitive_field(c)]


def test_every_forbidden_employee_and_payroll_column_is_sensitive():
    for table in ("employees", "payroll_records"):
        assert all(is_sensitive_field(column) for column in forbidden_columns(table))


def test_sensitive_tables_are_denied():
    for table in ("employee_documents", "hr_policies", "policy_chunks", "poll_responses", "attendance_logs"):
        assert table in DENIED_TABLES
    assert TABLES["payroll_records"].kind == TableKind.PAYROLL


@pytest.mark.parametrize(
    "left,right,approved",
    [
        (("leave_requests", "employee_id"), ("employees", "id"), True),
        (("employees", "manager_id"), ("employees", "id"), True),
        (("employees", "department_id"), ("departments", "id"), True),
        (("employee_projects", "project_id"), ("projects", "id"), True),
        (("employee_skills", "skill_id"), ("skills", "id"), True),
        (("employees", "name"), ("departments", "name"), False),
        (("employees", "department_id"), ("projects", "id"), False),
        (("leave_requests", "id"), ("tickets", "id"), False),
    ],
)
def test_approved_joins(left, right, approved):
    assert is_approved_join(left, right) is approved
    assert is_approved_join(right, left) is approved


def test_foreign_keys_match_models():
    for pair in APPROVED_FOREIGN_KEYS:
        (t1, c1), (t2, c2) = sorted(pair)
        fks = {(fk.parent.table.name, fk.parent.name, fk.column.table.name, fk.column.name)
               for table in Base.metadata.tables.values() for fk in table.foreign_keys}
        assert (t1, c1, t2, c2) in fks or (t2, c2, t1, c1) in fks
