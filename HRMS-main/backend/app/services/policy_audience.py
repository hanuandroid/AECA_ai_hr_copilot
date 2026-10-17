"""HR policy audiences (RBAC remediation step 7, approved decision 3; migration 0023).

The business-API definition, used by api/v1/endpoints/hr_policies.py (list, download, upload). The Copilot derives the
same sets from its own permission matrix (services/ai/authorization: POLICY_READ_MANAGERS / POLICY_READ_ADMINS);
tests/rbac/test_policy_audience.py pins that both agree for every role.

- ALL: every active employee. MANAGERS: managers and admins. ADMINS: admins only (nested levels).
- Only these three values are readable by anyone; any other stored value matches no reader (fail closed). The CHECK
  constraint in migration 0023 already rejects other values at the database.
- An uploader may only choose an audience they belong to (a manager: ALL or MANAGERS). Omitted means ALL, which is the
  behaviour before step 7.
"""

from fastapi import HTTPException, status

from app.core.response import error_response
from app.models.enums import PolicyAudience, Role

_READABLE: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({PolicyAudience.ALL.value, PolicyAudience.MANAGERS.value, PolicyAudience.ADMINS.value}),
    Role.MANAGER: frozenset({PolicyAudience.ALL.value, PolicyAudience.MANAGERS.value}),
    Role.EMPLOYEE: frozenset({PolicyAudience.ALL.value}),
}


def readable_audiences(role: Role) -> frozenset[str]:
    """Audiences whose PUBLISHED policies `role` may read. Unknown roles read nothing."""
    return _READABLE.get(role, frozenset())


def resolve_upload_audience(role: Role, requested: str | None) -> str:
    if requested is None or not requested.strip():
        return PolicyAudience.ALL.value
    try:
        audience = PolicyAudience(requested.strip().upper()).value
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_POLICY_AUDIENCE", "audience must be ALL, MANAGERS or ADMINS"),
        ) from None
    if audience not in readable_audiences(role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response("POLICY_AUDIENCE_FORBIDDEN", "You can only choose an audience that includes you"),
        )
    return audience
