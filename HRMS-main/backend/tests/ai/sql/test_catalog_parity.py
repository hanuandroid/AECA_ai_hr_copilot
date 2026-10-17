"""SQL agent catalog parity (RBAC remediation step 8, AI parity review).

Old behaviour (until 2026-10-17): `projects` and `skills` were unrestricted catalog tables for every scope, so an
EMPLOYEE could list the whole project catalogue (names, descriptions, statuses) and every skill name through the
Copilot. The business API gives an EMPLOYEE only their own projects (GET /employees/{own id}); the catalogue route is
MANAGER/ADMIN only (route-permission suite) and no API lists skill names to employees.

Invariant now (D7: the AI is never broader than the API): under SELF scope, `projects` and `skills` keep only rows
linked to the caller's own assignment rows, through the link table's own scope wrapper (so the final-SQL verifier
proves it). TEAM and ORG scopes (managers, admins) keep the full catalogue, as the API does. `departments` stays
unrestricted for everyone (GET /employees/departments is open to all roles).
"""

import pytest
from sqlalchemy import delete

from app.models.employee_project import EmployeeProject
from app.models.employee_skill import EmployeeSkill
from app.services.ai.errors import SQLNotAllowedError, SQLQueryTooComplexError
from app.services.ai.sql.parser import parse_select
from app.services.ai.sql.scope import enforce_scope, verify_final_sql
from app.services.ai.sql.validator import validate_generated_sql
from tests.ai.sql.conftest import MAX_JOINS, MAX_ROWS
from tests.support.data import EMPLOYEE

ALL_PROJECTS = {"HR Policy Copilot", "Leave Automation Engine", "Workforce Insights Dashboard"}


def _names(answer) -> set[str]:
    return {row["name"] for row in answer.rows}


@pytest.mark.parametrize("sql", [
    "SELECT name FROM projects",
    "SELECT name, description, status FROM projects ORDER BY name",
    "SELECT p.name FROM projects p WHERE p.status IN ('ONGOING', 'COMPLETED', 'ON_HOLD', 'PLANNED')",
    "SELECT name FROM projects WHERE id IN (1, 2, 3)",  # forged ids
    "SELECT name FROM projects UNION SELECT name FROM projects",
])
async def test_employee_sees_only_their_own_projects(run_sql, sql):
    answer, *_ = await run_sql("employee", sql)
    assert _names(answer) == {"HR Policy Copilot"}  # EMPLOYEE is assigned to project 1 only


async def test_employee_catalog_inside_a_cte_is_refused_not_widened(run_sql):
    """The nested own-rows wrapper inside a CTE exceeds the existing subquery-depth guard (3). The query is refused
    (fail closed); the guard is deliberately not relaxed. Documented limitation of step 8."""
    with pytest.raises(SQLQueryTooComplexError):
        await run_sql("employee", "WITH all_projects AS (SELECT name FROM projects) SELECT name FROM all_projects")


@pytest.mark.parametrize("sql", ["SELECT name FROM skills", "SELECT s.name FROM skills s WHERE s.id > 0"])
async def test_employee_sees_only_their_own_skills(run_sql, sql):
    answer, *_ = await run_sql("employee", sql)
    assert _names(answer) == {"Python"}  # FastAPI exists in the catalogue but is not the employee's skill


async def test_employee_without_assignments_sees_no_catalog_rows(run_sql, db):
    await db.execute(delete(EmployeeProject).where(EmployeeProject.employee_id == EMPLOYEE.id))
    await db.execute(delete(EmployeeSkill).where(EmployeeSkill.employee_id == EMPLOYEE.id))
    await db.commit()
    projects, *_ = await run_sql("employee", "SELECT name FROM projects")
    skills, *_ = await run_sql("employee", "SELECT name FROM skills")
    assert projects.row_count == skills.row_count == 0


async def test_another_employees_assignments_never_widen_the_catalogue(run_sql):
    answer, *_ = await run_sql("outsider", "SELECT name FROM projects")
    assert _names(answer) == {"Leave Automation Engine"}  # outsider's own assignment only


async def test_employee_own_project_join_still_works(run_sql):
    sql = ("SELECT p.name, ep.role_on_project FROM employee_projects ep JOIN projects p ON p.id = ep.project_id "
           "WHERE ep.employee_id = :me")
    answer, *_ = await run_sql("employee", sql)
    assert [(row["name"], row["role_on_project"]) for row in answer.rows] == [("HR Policy Copilot", "AI Engineer")]


@pytest.mark.parametrize("user", ["manager", "admin"])
async def test_managers_and_admins_keep_the_full_catalogue(run_sql, user):
    answer, *_ = await run_sql(user, "SELECT name FROM projects")
    assert _names(answer) == ALL_PROJECTS
    skills, *_ = await run_sql(user, "SELECT name FROM skills")
    assert _names(skills) == {"Python", "FastAPI"}


async def test_departments_stay_open_to_employees(run_sql):
    answer, *_ = await run_sql("employee", "SELECT name FROM departments")
    assert answer.row_count >= 1


async def test_self_catalog_wrapper_is_proven_by_the_final_verifier(ctx_for):
    ctx = await ctx_for("employee")
    validated = validate_generated_sql(parse_select("SELECT name FROM projects"), ctx, max_joins=MAX_JOINS)
    executable = enforce_scope(validated, ctx, max_rows=MAX_ROWS, max_joins=MAX_JOINS)
    assert f'"employee_id" IN ({EMPLOYEE.id})' in executable.sql and '"own_link"' in executable.sql
    verify_final_sql(executable.sql, ctx, validated.scope, max_rows=MAX_ROWS, max_joins=MAX_JOINS)  # does not raise
    tampered = executable.sql.replace(f'"employee_id" IN ({EMPLOYEE.id})', '"employee_id" IN (2, 3, 4)')
    with pytest.raises(SQLNotAllowedError):
        verify_final_sql(tampered, ctx, validated.scope, max_rows=MAX_ROWS, max_joins=MAX_JOINS)
