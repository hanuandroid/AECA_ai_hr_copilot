"""Role x capability matrix. EXPECTED is written out independently of policy.py and mirrors
docs/ai_permissions_matrix.md §1, so any drift between code and docs fails a test."""

import pytest

from app.models.enums import Role
from app.services.ai.authorization import (
    AI_PERMISSION_MATRIX,
    Capability,
    DenyReason,
    TargetRule,
    authorize,
    capabilities_for,
    grant_for,
)
from tests.ai.authorization.conftest import make_ctx

C, T = Capability, TargetRule
A, M, E = Role.ADMIN, Role.MANAGER, Role.EMPLOYEE

# capability: (ADMIN, MANAGER, EMPLOYEE); None = denied
EXPECTED: dict[Capability, tuple[TargetRule | None, TargetRule | None, TargetRule | None]] = {
    C.POLICY_READ: (T.NONE, T.NONE, T.NONE),
    C.DIRECTORY_READ: (T.NONE, T.NONE, T.NONE),
    C.HR_READ_SELF: (T.SELF, T.SELF, T.SELF),
    C.HR_READ_TEAM: (T.TEAM, T.TEAM, None),
    C.HR_READ_ORG: (T.ORG, None, None),
    C.SQL_READ_SELF: (T.SELF, T.SELF, T.SELF),
    C.SQL_READ_TEAM: (T.TEAM, T.TEAM, None),
    C.SQL_READ_ORG: (T.ORG, None, None),
    C.SQL_VIEW_RAW: (T.NONE, T.NONE, None),
    C.PAYROLL_READ_ORG: (T.ORG, None, None),
    C.LEAVE_CREATE: (T.SELF, T.SELF, T.SELF),
    C.LEAVE_APPROVE: (T.ORG_EXCEPT_SELF, T.REPORTS, None),
    C.LEAVE_REJECT: (T.ORG_EXCEPT_SELF, T.REPORTS, None),
    C.TICKET_CREATE: (T.SELF, T.SELF, T.SELF),
    C.TICKET_ASSIGN: (T.ORG, T.TEAM, None),
    C.TICKET_STATUS_UPDATE: (T.ORG, T.TEAM, None),
    C.ANNOUNCEMENT_CREATE: (T.NONE, T.NONE, None),
    C.PROJECT_ASSIGN: (T.ORG, T.TEAM, None),
    C.PROJECT_CREATE: (T.NONE, None, None),
    C.EMPLOYEE_DEACTIVATE: (T.ORG_EXCEPT_SELF, None, None),
    C.AUDIT_READ_OWN: (T.SELF, T.SELF, T.SELF),
    C.AUDIT_READ_ORG: (T.ORG, None, None),
}

CASES = [
    (capability, role, rules[index])
    for capability, rules in EXPECTED.items()
    for index, role in enumerate((A, M, E))
]


def test_matrix_covers_every_capability():
    assert set(EXPECTED) == set(Capability)
    assert set(AI_PERMISSION_MATRIX) == set(Capability)


def test_matrix_only_uses_known_roles():
    for grants in AI_PERMISSION_MATRIX.values():
        assert set(grants) <= set(Role)


@pytest.mark.parametrize("capability,role,expected_rule", CASES, ids=lambda v: getattr(v, "value", str(v)))
def test_role_capability_matrix(capability, role, expected_rule):
    assert grant_for(role, capability) == expected_rule
    result = authorize(make_ctx(role), capability)
    assert result.allowed is (expected_rule is not None)
    if expected_rule is None:
        assert result.reason == DenyReason.CAPABILITY_NOT_GRANTED


@pytest.mark.parametrize("capability,role,_rule", CASES, ids=lambda v: getattr(v, "value", str(v)))
def test_inactive_user_denied_every_capability(capability, role, _rule):
    result = authorize(make_ctx(role, active=False), capability)
    assert result.allowed is False
    assert result.reason == DenyReason.INACTIVE_USER


def test_capabilities_for_matches_matrix():
    for index, role in enumerate((A, M, E)):
        expected = {capability for capability, rules in EXPECTED.items() if rules[index] is not None}
        assert capabilities_for(role) == expected


def test_employee_is_strictly_least_privileged():
    assert capabilities_for(E) < capabilities_for(M) < capabilities_for(A)


def test_matrix_is_immutable_at_runtime():
    with pytest.raises(TypeError):
        AI_PERMISSION_MATRIX[C.PROJECT_CREATE] = {M: T.NONE}  # type: ignore[index]
    with pytest.raises(TypeError):
        AI_PERMISSION_MATRIX[C.PROJECT_CREATE][M] = T.NONE  # type: ignore[index]


@pytest.mark.parametrize(
    "capability,role",
    [
        # D7: stricter than the existing endpoints (characterized in tests/characterization/).
        (C.TICKET_STATUS_UPDATE, E),  # existing API lets ticket owners update status
        (C.PROJECT_CREATE, M),  # existing API lets managers create projects
    ],
)
def test_ai_is_stricter_than_existing_api_where_documented(capability, role):
    assert authorize(make_ctx(role), capability).allowed is False
