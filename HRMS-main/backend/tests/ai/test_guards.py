"""AI-layer data protection guards (independent of existing endpoint behaviour, incl. R1)."""

import re
from pathlib import Path

import pytest

from app.services.ai.errors import AIPermissionDeniedError
from app.services.ai.guards import SENSITIVE_FIELDS, assert_route_allowed, is_route_forbidden, strip_sensitive_fields
from app.services.ai.redaction import redact

AI_PACKAGE = Path(__file__).resolve().parents[2] / "app" / "services" / "ai"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/employees/3"),  # R1
        ("GET", "/api/v1/employees/3/"),
        ("GET", "/api/v1/employees/3?include=all"),
        ("PUT", "/api/v1/employees/3"),
        ("GET", "/api/v1/finance/profile/me"),
        ("GET", "/api/v1/finance/payroll/me"),
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/employees/me/documents"),
        ("POST", "/api/v1/employees/3/documents/payslip"),
        ("GET", "/api/v1/employees/3/profile-picture"),
    ],
)
def test_forbidden_routes_blocked(method, path):
    assert is_route_forbidden(method, path)
    with pytest.raises(AIPermissionDeniedError):
        assert_route_allowed(method, path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/leaves/requests"),
        ("POST", "/api/v1/leaves/requests/1/approve"),
        ("POST", "/api/v1/tickets"),
        ("POST", "/api/v1/tickets/1/status"),
        ("POST", "/api/v1/announcements"),
        ("POST", "/api/v1/employees/3/projects"),
        ("POST", "/api/v1/employees/projects/catalog"),
        ("DELETE", "/api/v1/employees/3"),
        ("GET", "/api/v1/employees"),
    ],
)
def test_wrappable_routes_allowed(method, path):
    assert not is_route_forbidden(method, path)
    assert_route_allowed(method, path)


def test_strip_sensitive_fields_removes_r1_fields_recursively():
    payload = {
        "id": 3,
        "name": "Employee Test",
        "current_salary_usd": 72000,
        "bank_account_number": "00031000000002",
        "PAN_NUMBER": "CFJNR1002V",
        "projects": [{"id": 1, "name": "HR Policy Copilot", "pf_uan": "1002"}],
        "payroll": {"gross": 1, "deductions": {"pf": 1}, "pan": "X"},
    }
    assert strip_sensitive_fields(payload) == {
        "id": 3,
        "name": "Employee Test",
        "projects": [{"id": 1, "name": "HR Policy Copilot"}],
        "payroll": {"gross": 1},
    }


def test_sensitive_fields_cover_assignment_forbidden_list():
    assignment_list = {
        "hashed_password", "bank_account_number", "bank_account_name", "bank_branch", "bank_ifsc",
        "pan_number", "pan_name", "pan_dob", "date_of_birth", "current_salary_usd",
        "profile_photo_path", "profile_photo_mime",
    }
    audit_additions = {"pan", "pf_uan", "esi_no"}  # payroll_records (audit R8)
    assert assignment_list | audit_additions <= SENSITIVE_FIELDS


def test_no_ai_module_references_forbidden_routes():
    """Static guard: only guards.py may mention forbidden route prefixes.

    Phase 5: actions/registry.py legitimately declares `DELETE /api/v1/employees/{employee_id}` (the existing
    deactivation route), which this text heuristic cannot tell apart from the forbidden GET. The registry is
    therefore checked method-aware below (test_action_registry_routes_pass_the_deny_list) instead of by text.
    """
    pattern = re.compile(r"/finance/|/auth/|/documents|/profile-picture|/employees/\{[a-z_]*\}[\"'?]")
    offenders = [
        path.relative_to(AI_PACKAGE).as_posix()
        for path in AI_PACKAGE.rglob("*.py")
        if path.name != "guards.py"
        and path.relative_to(AI_PACKAGE).as_posix() != "actions/registry.py"
        and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_action_registry_routes_pass_the_deny_list():
    from app.services.ai.actions.registry import ACTION_REGISTRY

    for spec in ACTION_REGISTRY.values():
        concrete = spec.path.format(leave_id=7, ticket_id=7, employee_id=7)
        assert not is_route_forbidden(spec.method, concrete), spec.name
        assert "/finance" not in concrete and "/auth" not in concrete and "/documents" not in concrete
    # The R1 route stays forbidden for every method the AI could otherwise use to read/replace a profile.
    assert is_route_forbidden("GET", "/api/v1/employees/7") and is_route_forbidden("PUT", "/api/v1/employees/7")


def test_ai_package_does_not_import_business_endpoints():
    offenders = [
        path.relative_to(AI_PACKAGE).as_posix()
        for path in AI_PACKAGE.rglob("*.py")
        if "app.api.v1.endpoints" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --- Redaction ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,secret,marker",
    [
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIzIn0.abc_DEF-123 here", "eyJhbGci", "[REDACTED_TOKEN]"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi", "[REDACTED_TOKEN]"),
        ("my PAN is CFJNR1002V", "CFJNR1002V", "[REDACTED_PAN]"),
        ("IFSC HDFC0001234", "HDFC0001234", "[REDACTED_IFSC]"),
        ("account 00031000000002 please", "00031000000002", "[REDACTED_NUMBER]"),
        ("key sk-ant-api03-abcdefghijklmnop", "sk-ant-api03", "[REDACTED_KEY]"),
    ],
)
def test_redact_removes_secrets_and_pii(text, secret, marker):
    result = redact(text)
    assert secret not in result
    assert marker in result


def test_redact_keeps_ordinary_text_and_truncates():
    assert redact("Apply sick leave from May 6 to May 7") == "Apply sick leave from May 6 to May 7"
    assert redact(None) == ""
    assert redact("x" * 50, max_length=10).startswith("x" * 10)
    assert len(redact("x" * 50, max_length=10)) < 50
