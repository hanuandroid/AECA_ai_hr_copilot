"""Announcement and poll audience rules (RBAC remediation step 6, approved decision 5 / X-2; migration 0022).

The ONE definition, shared by api/v1/endpoints/announcements.py and polls.py:
- ORG: visible to everyone. Only ADMIN publishes it.
- TEAM: visible to the team manager (team_manager_id), that person's direct reports (employees.manager_id, one level,
  no indirect reports) and every ADMIN. The team is always the creator's own team, taken from the authenticated,
  database-loaded user; a client can never name a team, manager or author.
- MANAGER creates TEAM only (asking for ORG is refused, never downgraded silently). ADMIN chooses either; the default
  for an omitted audience is ORG for ADMIN (unchanged behaviour) and TEAM for MANAGER.
- Unknown audience values are refused. The CHECK constraint in migration 0022 enforces the same pair at the database.
"""

from fastapi import HTTPException, status
from sqlalchemy import or_, true

from app.core.response import error_response
from app.models.employee import Employee
from app.models.enums import ContentAudience, Role


def visible_to(model, user: Employee):
    """WHERE clause for the announcements or polls `user` may see (lists, counts, votes, results)."""
    if user.role == Role.ADMIN:
        return true()
    clauses = [model.audience == ContentAudience.ORG.value, model.team_manager_id == user.id]
    if user.manager_id is not None:
        clauses.append(model.team_manager_id == user.manager_id)
    return or_(*clauses)


def resolve_audience(user: Employee, requested: str | None) -> tuple[str, int | None]:
    """(audience, team_manager_id) for new content created by `user` (already checked to be MANAGER or ADMIN)."""
    if requested is None or not requested.strip():
        audience = ContentAudience.ORG if user.role == Role.ADMIN else ContentAudience.TEAM
    else:
        try:
            audience = ContentAudience(requested.strip().upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response("INVALID_AUDIENCE", "audience must be ORG or TEAM"),
            ) from None
    if audience == ContentAudience.ORG and user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response("AUDIENCE_FORBIDDEN", "Only admins can publish to the whole organisation; use TEAM"),
        )
    return audience.value, (user.id if audience == ContentAudience.TEAM else None)
