from dataclasses import dataclass

from app.services.ai.authorization.capabilities import Capability, ScopeLevel, TargetRule
from app.services.ai.authorization.context import AIUserContext
from app.services.ai.authorization.policy import grant_for


@dataclass(frozen=True, slots=True)
class TargetEmployee:
    """The employee an operation is about. Target ids may come from untrusted input;
    they are only ever *checked* against server-derived scope, never trusted."""

    employee_id: int

    def __post_init__(self) -> None:
        if type(self.employee_id) is not int or self.employee_id <= 0:
            raise ValueError("employee_id must be a positive int")


@dataclass(frozen=True, slots=True)
class DataScope:
    """Rows a caller may read. `employee_ids is None` means organisation-wide."""

    level: ScopeLevel
    employee_ids: frozenset[int] | None

    def allows(self, employee_id: int) -> bool:
        if self.level == ScopeLevel.NONE:
            return False
        return self.employee_ids is None or employee_id in self.employee_ids


NO_SCOPE = DataScope(ScopeLevel.NONE, frozenset())


def target_allowed(ctx: AIUserContext, rule: TargetRule, employee_id: int) -> bool:
    if rule == TargetRule.NONE:
        return True
    if rule == TargetRule.SELF:
        return employee_id == ctx.employee_id
    if rule == TargetRule.TEAM:
        return employee_id in ctx.team_ids
    if rule == TargetRule.REPORTS:
        return employee_id in ctx.direct_report_ids and employee_id != ctx.employee_id
    if rule == TargetRule.ORG:
        return True
    if rule == TargetRule.ORG_EXCEPT_SELF:
        return employee_id != ctx.employee_id
    return False


def _resolve(ctx: AIUserContext, org: Capability, team: Capability, self_: Capability) -> DataScope:
    if not ctx.is_active:
        return NO_SCOPE
    if grant_for(ctx.role, org) is not None:
        return DataScope(ScopeLevel.ORG, None)
    if grant_for(ctx.role, team) is not None:
        return DataScope(ScopeLevel.TEAM, ctx.team_ids)
    if grant_for(ctx.role, self_) is not None:
        return DataScope(ScopeLevel.SELF, frozenset({ctx.employee_id}))
    return NO_SCOPE


_POLICY_AUDIENCE_GRANTS = (
    (Capability.POLICY_READ, "ALL"),
    (Capability.POLICY_READ_MANAGERS, "MANAGERS"),
    (Capability.POLICY_READ_ADMINS, "ADMINS"),
)


def policy_audiences(ctx: AIUserContext) -> frozenset[str]:
    """Policy audiences the caller may retrieve and cite (RBAC step 7). Empty for inactive users or unknown roles;
    values outside ALL/MANAGERS/ADMINS are never included, so unknown stored audiences match nobody."""
    if not ctx.is_active:
        return frozenset()
    return frozenset(audience for capability, audience in _POLICY_AUDIENCE_GRANTS if grant_for(ctx.role, capability) is not None)


def hr_read_scope(ctx: AIUserContext) -> DataScope:
    """Widest protected-HR-data scope granted by the matrix (never by role name checks)."""
    return _resolve(ctx, Capability.HR_READ_ORG, Capability.HR_READ_TEAM, Capability.HR_READ_SELF)


def sql_read_scope(ctx: AIUserContext) -> DataScope:
    """Row scope the SQL agent must bind into scoped tables (Phase 4)."""
    return _resolve(ctx, Capability.SQL_READ_ORG, Capability.SQL_READ_TEAM, Capability.SQL_READ_SELF)
