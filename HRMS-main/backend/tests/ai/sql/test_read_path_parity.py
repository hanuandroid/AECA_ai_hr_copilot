"""Non-mutating Copilot reads vs the business API (RBAC remediation step 8; D7: AI results are a subset of the API's).

Read paths: the SQL agent (scope-wrapped tables), the restricted skill search (step 3), Policy RAG + replay (step 7,
tests/ai/rag and tests/ai/conversation) and GET /chat/permissions (UX metadata only). Announcements, polls, poll
responses, HR policies, documents and attendance are not readable by the SQL agent at all, and no action reads them.
This module pins the parity of the directory (step 2) and project data (step 6) between the two paths, and that the
Copilot has no route to the step 4 self-action endpoints.
"""

import pytest

from app.services.ai.actions.registry import ACTION_REGISTRY
from app.services.ai.errors import SQLScopeViolationError
from app.services.ai.sql.schema import DENIED_TABLES, TABLES
from tests.support.data import EMPLOYEE, MANAGER, OUTSIDER
from tests.support.http import data


SCOPED_DIRECTORY_SQL = {
    "employee": "SELECT id FROM employees WHERE id = :me",
    "manager": "SELECT id FROM employees WHERE id IN (SELECT employee_id FROM my_team)",
    "admin": "SELECT id FROM employees",
}


@pytest.mark.parametrize("user", ["employee", "manager", "admin"])
async def test_every_sql_directory_row_is_readable_through_the_api(client, auth, run_sql, user):
    """Step 2 parity: each employee the Copilot returns is one GET /employees/{id} also shows this user."""
    answer, *_ = await run_sql(user, SCOPED_DIRECTORY_SQL[user])
    ids = {row["id"] for row in answer.rows}
    assert ids, user
    for employee_id in ids:
        response = await client.get(f"/api/v1/employees/{employee_id}", headers=await auth(user))
        assert response.status_code == 200, (user, employee_id)


@pytest.mark.parametrize("user,forged", [("employee", OUTSIDER.id), ("employee", MANAGER.id), ("manager", OUTSIDER.id)])
async def test_forged_ids_return_nothing_outside_scope(client, auth, run_sql, user, forged):
    api = await client.get(f"/api/v1/employees/{forged}", headers=await auth(user))
    assert api.status_code == 404  # hidden through the API (step 2)
    try:
        answer, *_ = await run_sql(user, f"SELECT id, name, email FROM employees WHERE id = {forged}")
    except SQLScopeViolationError:
        return  # refused before execution
    assert answer.row_count == 0  # or scope-wrapped to nothing


async def test_employee_project_reads_match_their_own_api_view(client, auth, run_sql):
    detail = await client.get(f"/api/v1/employees/{EMPLOYEE.id}", headers=await auth("employee"))
    assert detail.status_code == 200
    api_projects = {project["name"] for project in data(detail)["projects"]}
    answer, *_ = await run_sql("employee", "SELECT name FROM projects")
    assert {row["name"] for row in answer.rows} == api_projects == {"HR Policy Copilot"}


def test_content_tables_are_not_readable_by_the_sql_agent():
    for table in ("announcements", "polls", "poll_responses", "hr_policies", "employee_documents", "attendance_logs"):
        assert table not in TABLES and table in DENIED_TABLES, table


def test_no_ai_action_reaches_step_4_self_action_routes():
    routes = {(spec.method, spec.path) for spec in ACTION_REGISTRY.values()}
    forbidden = {("PUT", "/api/v1/employees/{employee_id}"), ("POST", "/api/v1/auth/change-password"),
                 ("POST", "/api/v1/employees/{employee_id}/documents/payslip"), ("POST", "/api/v1/employees/me/documents"),
                 ("PATCH", "/api/v1/employees/{employee_id}/reactivate"), ("PUT", "/api/v1/employees/me")}
    assert not routes & forbidden
    assert not any("password" in path or "payslip" in path or "salary" in path for _, path in routes)
