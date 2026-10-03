"""AI user context (from DB) and team/resource scoping (R4: managers restricted to their team)."""

import dataclasses
import inspect

import pytest

from app.models.enums import Role
from app.services.ai.authorization import (
    Capability,
    DenyReason,
    ScopeLevel,
    TargetEmployee,
    authorize,
    build_ai_user_context,
    hr_read_scope,
    sql_read_scope,
)
from tests.ai.authorization.conftest import make_ctx
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER, OUTSIDER

C = Capability

# --- Context -------------------------------------------------------------------


async def test_context_built_from_database_state(ctx_for):
    manager = await ctx_for("manager")
    assert manager.employee_id == MANAGER.id
    assert manager.role == Role.MANAGER
    assert manager.is_active is True
    # Direct reports come from employees.manager_id, including the INACTIVE report.
    assert manager.direct_report_ids == {EMPLOYEE.id, INACTIVE.id}
    assert manager.team_ids == {MANAGER.id, EMPLOYEE.id, INACTIVE.id}

    employee = await ctx_for("employee")
    assert employee.manager_id == MANAGER.id
    assert employee.direct_report_ids == frozenset()

    inactive = await ctx_for("inactive")
    assert inactive.is_active is False


async def test_context_is_immutable(ctx_for):
    ctx = await ctx_for("employee")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.role = Role.ADMIN  # type: ignore[misc]
    with pytest.raises(AttributeError):
        ctx.direct_report_ids.add(OUTSIDER.id)  # type: ignore[attr-defined]


def test_context_builder_accepts_no_untrusted_inputs():
    """Only the DB Employee row and a session - no message, role, or claims parameter exists."""
    assert list(inspect.signature(build_ai_user_context).parameters) == ["employee", "db"]


# --- Team scoping (resource checks) --------------------------------------------


@pytest.mark.parametrize(
    "user_key,capability,target,allowed",
    [
        # manager -> own team: allowed
        ("manager", C.HR_READ_TEAM, EMPLOYEE.id, True),
        ("manager", C.HR_READ_TEAM, MANAGER.id, True),
        ("manager", C.LEAVE_APPROVE, EMPLOYEE.id, True),
        ("manager", C.LEAVE_REJECT, EMPLOYEE.id, True),
        ("manager", C.PROJECT_ASSIGN, EMPLOYEE.id, True),
        ("manager", C.TICKET_ASSIGN, EMPLOYEE.id, True),
        # manager -> outside team: denied (stricter than existing API, R4)
        ("manager", C.HR_READ_TEAM, OUTSIDER.id, False),
        ("manager", C.LEAVE_APPROVE, OUTSIDER.id, False),
        ("manager", C.LEAVE_REJECT, OUTSIDER.id, False),
        ("manager", C.PROJECT_ASSIGN, OUTSIDER.id, False),
        ("manager", C.TICKET_STATUS_UPDATE, OUTSIDER.id, False),
        ("manager", C.HR_READ_TEAM, ADMIN.id, False),
        ("manager", C.HR_READ_TEAM, 9999, False),  # non-existent: same answer as out-of-team
        # manager may not approve own leave (REPORTS excludes self)
        ("manager", C.LEAVE_APPROVE, MANAGER.id, False),
        # employee -> another employee: denied
        ("employee", C.HR_READ_SELF, EMPLOYEE.id, True),
        ("employee", C.HR_READ_SELF, OUTSIDER.id, False),
        ("employee", C.HR_READ_SELF, MANAGER.id, False),
        ("employee", C.LEAVE_CREATE, OUTSIDER.id, False),
        ("employee", C.TICKET_CREATE, OUTSIDER.id, False),
        # admin -> organisation: allowed where defined
        ("admin", C.HR_READ_ORG, OUTSIDER.id, True),
        ("admin", C.LEAVE_APPROVE, EMPLOYEE.id, True),
        ("admin", C.PROJECT_ASSIGN, OUTSIDER.id, True),
        ("admin", C.EMPLOYEE_DEACTIVATE, OUTSIDER.id, True),
        ("admin", C.EMPLOYEE_DEACTIVATE, ADMIN.id, False),  # never self
        ("admin", C.LEAVE_APPROVE, ADMIN.id, False),  # segregation of duties
    ],
)
async def test_team_and_resource_scoping(ctx_for, user_key, capability, target, allowed):
    result = authorize(await ctx_for(user_key), capability, TargetEmployee(target))
    assert result.allowed is allowed
    if not allowed:
        assert result.reason in {DenyReason.OUT_OF_SCOPE, DenyReason.CAPABILITY_NOT_GRANTED}


async def test_out_of_scope_and_nonexistent_targets_are_indistinguishable(ctx_for):
    ctx = await ctx_for("manager")
    outsider = authorize(ctx, C.HR_READ_TEAM, TargetEmployee(OUTSIDER.id))
    missing = authorize(ctx, C.HR_READ_TEAM, TargetEmployee(424242))
    assert (outsider.allowed, outsider.reason, outsider.message) == (missing.allowed, missing.reason, missing.message)


async def test_scope_ignores_role_name_manager_without_reports_has_only_self(db):
    """Scope derives from employees.manager_id, not from WHERE role='MANAGER'."""
    lone_manager = make_ctx(Role.MANAGER, employee_id=77, reports=set())
    assert authorize(lone_manager, C.HR_READ_TEAM, TargetEmployee(EMPLOYEE.id)).allowed is False
    assert hr_read_scope(lone_manager).employee_ids == {77}


# --- Data scopes (consumed by SQL agent in Phase 4) ------------------------------


async def test_data_scopes_per_role(ctx_for):
    employee_scope = sql_read_scope(await ctx_for("employee"))
    assert employee_scope.level == ScopeLevel.SELF
    assert employee_scope.employee_ids == {EMPLOYEE.id}
    assert not employee_scope.allows(OUTSIDER.id)

    manager_scope = sql_read_scope(await ctx_for("manager"))
    assert manager_scope.level == ScopeLevel.TEAM
    assert manager_scope.employee_ids == {MANAGER.id, EMPLOYEE.id, INACTIVE.id}
    assert manager_scope.allows(EMPLOYEE.id)
    assert not manager_scope.allows(OUTSIDER.id)

    admin_scope = sql_read_scope(await ctx_for("admin"))
    assert admin_scope.level == ScopeLevel.ORG
    assert admin_scope.employee_ids is None
    assert admin_scope.allows(OUTSIDER.id)

    assert hr_read_scope(await ctx_for("manager")) == manager_scope


async def test_inactive_user_has_no_data_scope(ctx_for):
    scope = sql_read_scope(await ctx_for("inactive"))
    assert scope.level == ScopeLevel.NONE
    assert not scope.allows(INACTIVE.id)


@pytest.mark.parametrize("bad", [0, -1, "3", 3.0, True, None])
def test_target_employee_rejects_non_int_ids(bad):
    with pytest.raises(ValueError):
        TargetEmployee(bad)  # type: ignore[arg-type]
