"""Centralised sensitive-field policy for the AI layer (enforced in code, not by prompts).

Two complementary checks:
1. SENSITIVE_COLUMNS: exact column names found in the actual schema (employees, payroll_records,
   employee_documents, hr_policies) plus the assignment's forbidden list.
2. Token matching on field names, so aliases produced by an LLM or other APIs
   ("salary", "dob", "account_number", "pf_number", "password_hash", ...) are also caught.
Payroll amounts (payroll_records.gross/net) are not globally forbidden but the whole payroll
table requires PAYROLL_READ_ORG (ADMIN only).
"""

import re

from app.services.ai.authorization.capabilities import Capability
from app.services.ai.authorization.context import AIUserContext
from app.services.ai.authorization.policy import grant_for

SENSITIVE_COLUMNS: frozenset[str] = frozenset(
    {
        # employees
        "hashed_password",
        "date_of_birth",
        "phone",
        "address",
        "blood_type",
        "profile_photo_path",
        "profile_photo_mime",
        "current_salary_usd",
        "bank_name",
        "bank_account_number",
        "bank_account_name",
        "bank_branch",
        "bank_ifsc",
        "pan_number",
        "pan_name",
        "pan_dob",
        # payroll_records
        "pan",
        "pf_uan",
        "esi_no",
        "deductions",
        # internal metadata (employee_documents, hr_policies)
        "file_path",
        "checksum",
        "embedding",
        # generic aliases
        "password",
    }
)

SENSITIVE_TOKENS: frozenset[str] = frozenset(
    {
        "password", "passwd", "hashed", "secret", "token",
        "salary", "salaries", "compensation", "ctc",
        "bank", "account", "ifsc", "iban", "swift",
        "pan", "aadhaar", "ssn",
        "dob", "birth", "birthday",
        "pf", "uan", "esi", "deductions",
        "phone", "address", "blood",
        "photo", "path", "checksum", "embedding",
    }
)

PAYROLL_TABLES: frozenset[str] = frozenset({"payroll_records"})

_SPLIT_RE = re.compile(r"[^a-z0-9]+|(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")


def _tokens(name: str) -> set[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)  # camelCase -> camel_Case
    return {token for token in _SPLIT_RE.split(snake.lower()) if token}


def is_sensitive_field(name: str) -> bool:
    """True if a field/column/alias name must never be exposed through AI, for any role."""
    if not isinstance(name, str) or not name.strip():
        return False
    lowered = name.strip().lower()
    if lowered in SENSITIVE_COLUMNS:
        return True
    return bool(_tokens(name.strip()) & SENSITIVE_TOKENS)


def is_column_readable(ctx: AIUserContext, table: str, column: str) -> bool:
    """Table-aware column check used by the SQL agent (Phase 4)."""
    if not ctx.is_active or is_sensitive_field(column):
        return False
    if table.lower() in PAYROLL_TABLES:
        return grant_for(ctx.role, Capability.PAYROLL_READ_ORG) is not None
    return True
