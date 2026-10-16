"""THE single source of truth for AI authorization (docs/ai_permissions_matrix.md mirrors this).

Decision D7: AI authorization >= existing application authorization. Entries here may be
stricter than the existing endpoints, never more permissive. A capability absent for a role
is denied. Deliberate deviations from existing endpoint behaviour:

- LEAVE_APPROVE/REJECT: MANAGER limited to direct reports (existing API: any leave, R4).
- PROJECT_ASSIGN: MANAGER limited to own team (existing API: anyone, R4).
- TICKET_ASSIGN/STATUS_UPDATE: MANAGER limited to tickets raised by own team; EMPLOYEE denied
  (existing API lets owners update status; assignment matrix says "Employee: No").
- PROJECT_CREATE: ADMIN only (since RBAC step 6 the API is ADMIN-only too).
- ANNOUNCEMENT_CREATE: ADMIN -> ORG audience, MANAGER -> TEAM audience (RBAC step 6, same rule as the API).
- Every capability is denied to INACTIVE users (existing auth does not check status, R9).
"""

from types import MappingProxyType

from app.models.enums import Role
from app.services.ai.authorization.capabilities import Capability as C
from app.services.ai.authorization.capabilities import TargetRule as T

A, M, E = Role.ADMIN, Role.MANAGER, Role.EMPLOYEE

_MATRIX: dict[C, dict[Role, T]] = {
    C.POLICY_READ: {A: T.NONE, M: T.NONE, E: T.NONE},
    C.SKILL_SEARCH_ORG: {A: T.ORG, M: T.ORG},  # RBAC step 3 (replaces the unused DIRECTORY_READ granted to all)
    C.HR_READ_SELF: {A: T.SELF, M: T.SELF, E: T.SELF},
    C.HR_READ_TEAM: {A: T.TEAM, M: T.TEAM},
    C.HR_READ_ORG: {A: T.ORG},
    C.SQL_READ_SELF: {A: T.SELF, M: T.SELF, E: T.SELF},
    C.SQL_READ_TEAM: {A: T.TEAM, M: T.TEAM},
    C.SQL_READ_ORG: {A: T.ORG},
    C.SQL_VIEW_RAW: {A: T.NONE, M: T.NONE},
    C.PAYROLL_READ_ORG: {A: T.ORG},
    C.LEAVE_CREATE: {A: T.SELF, M: T.SELF, E: T.SELF},
    C.LEAVE_APPROVE: {A: T.ORG_EXCEPT_SELF, M: T.REPORTS},
    C.LEAVE_REJECT: {A: T.ORG_EXCEPT_SELF, M: T.REPORTS},
    C.TICKET_CREATE: {A: T.SELF, M: T.SELF, E: T.SELF},
    C.TICKET_ASSIGN: {A: T.ORG, M: T.TEAM},
    C.TICKET_STATUS_UPDATE: {A: T.ORG, M: T.TEAM},
    C.ANNOUNCEMENT_CREATE: {A: T.ORG, M: T.TEAM},  # RBAC step 6: ADMIN publishes org-wide, MANAGER to own team
    C.PROJECT_ASSIGN: {A: T.ORG, M: T.TEAM},
    C.PROJECT_CREATE: {A: T.NONE},
    C.EMPLOYEE_DEACTIVATE: {A: T.ORG_EXCEPT_SELF},
    C.AUDIT_READ_OWN: {A: T.SELF, M: T.SELF, E: T.SELF},
    C.AUDIT_READ_ORG: {A: T.ORG},
}

AI_PERMISSION_MATRIX: MappingProxyType[C, MappingProxyType[Role, T]] = MappingProxyType(
    {capability: MappingProxyType(grants) for capability, grants in _MATRIX.items()}
)


def grant_for(role: Role, capability: C) -> T | None:
    """Target rule granted to `role` for `capability`, or None when not granted."""
    return AI_PERMISSION_MATRIX.get(capability, MappingProxyType({})).get(role)


def capabilities_for(role: Role) -> frozenset[C]:
    return frozenset(capability for capability, grants in AI_PERMISSION_MATRIX.items() if role in grants)
