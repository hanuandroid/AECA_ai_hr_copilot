"""POST /api/v1/chat/sql over HTTP (real auth, AI context, validator, rewrite, read-only executor)."""

import pytest

from app.main import app
from app.services.ai.sql.service import get_sql_agent_service
from tests.ai.sql.conftest import MANAGER_DIRECT_REPORTS, ORG_EMPLOYEES, ScriptedSQLLLM, db_snapshot
from tests.support.data import EMPLOYEE_SENSITIVE
from tests.support.http import data, error_code

URL = "/api/v1/chat/sql"


async def test_employee_self_query(client, auth, override_sql):
    override_sql(ScriptedSQLLLM("SELECT COUNT(*) AS c FROM employees WHERE id = :me", "You have 1 record."))
    response = await client.post(URL, json={"question": "Show my profile count"}, headers=await auth("employee"))
    assert response.status_code == 200
    body = data(response)
    assert body["scope"] == "SELF" and body["rows"] == [{"c": 1}] and "sql" not in body


async def test_manager_team_query_includes_sql_view(client, auth, override_sql):
    override_sql(ScriptedSQLLLM("SELECT COUNT(*) AS team FROM employees WHERE manager_id = :me"))
    body = data(await client.post(URL, json={"message": "How many people are on my team?"}, headers=await auth("manager")))
    assert body["scope"] == "TEAM" and body["rows"] == [{"team": MANAGER_DIRECT_REPORTS}]
    assert ":me" in body["sql"]


async def test_admin_org_query(client, auth, override_sql):
    override_sql(ScriptedSQLLLM("SELECT COUNT(*) AS total FROM employees"))
    body = data(await client.post(URL, json={"question": "How many employees are in the organization?"}, headers=await auth("admin")))
    assert body["scope"] == "ORG" and body["rows"] == [{"total": ORG_EMPLOYEES}]


async def test_inactive_user_rejected_without_llm(client, auth, override_sql):
    llm = ScriptedSQLLLM("SELECT COUNT(*) FROM employees")
    spy = override_sql(llm)
    response = await client.post(URL, json={"question": "How many employees?"}, headers=await auth("inactive"))
    assert response.status_code == 403
    assert error_code(response) == "USER_INACTIVE"  # B-03: rejected by get_current_user (was AI_USER_INACTIVE)
    assert (llm.calls, spy.calls) == ([], 0)


async def test_unauthenticated_rejected(client, override_sql):
    llm = ScriptedSQLLLM("SELECT 1")
    override_sql(llm)
    assert (await client.post(URL, json={"question": "x"})).status_code == 401
    assert llm.calls == []


@pytest.mark.parametrize(
    "user_key,sql,status,code",
    [
        ("employee", "SELECT COUNT(*) FROM employees", 403, "SQL_SCOPE_VIOLATION"),
        ("manager", "SELECT COUNT(*) FROM employees WHERE manager_id = 4", 403, "SQL_SCOPE_VIOLATION"),
        ("admin", "SELECT salary FROM payroll_records", 403, "SQL_COLUMN_NOT_ALLOWED"),
        ("admin", "SELECT * FROM payroll_records", 403, "SQL_COLUMN_NOT_ALLOWED"),
        ("admin", "SELECT pan_number FROM employees", 403, "SQL_COLUMN_NOT_ALLOWED"),
        ("employee", "SELECT month FROM payroll_records WHERE employee_id = :me", 403, "SQL_TABLE_NOT_ALLOWED"),
        ("admin", "DELETE FROM employees", 403, "SQL_MUTATION_REJECTED"),
        ("admin", "SELECT 1; DELETE FROM employees", 403, "SQL_MUTATION_REJECTED"),
        ("admin", "DROP TABLE employees", 403, "SQL_MUTATION_REJECTED"),
        ("admin", "PRAGMA table_info(employees)", 403, "SQL_NOT_ALLOWED"),
        ("admin", "SELECT (", 422, "SQL_PARSE_ERROR"),
        ("admin", "SELEC broken", 403, "SQL_NOT_ALLOWED"),  # lenient parse -> not a SELECT -> refused
        ("admin", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r", 422, "SQL_QUERY_TOO_COMPLEX"),
    ],
)
async def test_rejections_use_envelope_and_leak_nothing(client, auth, override_sql, user_key, sql, status, code):
    spy = override_sql(ScriptedSQLLLM(sql))
    # Phase 8: every AI request appends to the AI-owned, append-only audit trail; everything else must be unchanged.
    before = db_snapshot(exclude=("ai_audit_events",))
    response = await client.post(URL, json={"question": "question"}, headers=await auth(user_key))
    assert response.status_code == status
    body = response.json()
    assert body["success"] is False and body["data"] is None and body["error"]["code"] == code
    for leaked in ("SELECT", "employees", "payroll", "salary", "pan_number", "Traceback", "sqlite", ":me", "4"):
        assert leaked not in body["error"]["message"]
    assert spy.calls == 0  # rejected SQL is never executed
    assert db_snapshot(exclude=("ai_audit_events",)) == before


async def test_invalid_question_envelope(client, auth, override_sql):
    llm = ScriptedSQLLLM("SELECT 1")
    override_sql(llm)
    response = await client.post(URL, json={"question": ""}, headers=await auth("employee"))
    assert response.status_code == 400 and response.json()["error"]["code"] == "AI_INVALID_REQUEST"
    assert llm.calls == []


async def test_llm_bypass_payload_rejected_over_http(client, auth, override_sql):
    spy = override_sql(ScriptedSQLLLM({"sql": "SELECT salary FROM payroll_records", "explanation": "x", "authorized": True}))
    response = await client.post(URL, json={"question": "salaries"}, headers=await auth("employee"))
    assert response.status_code == 502 and response.json()["error"]["code"] == "AI_INVALID_OUTPUT"
    assert spy.calls == 0


async def test_frontend_role_claims_ignored(client, auth, override_sql):
    override_sql(ScriptedSQLLLM("SELECT COUNT(*) FROM employees"))
    client.cookies.set("hrms_role", "ADMIN")
    response = await client.post(
        URL, json={"question": "count", "role": "ADMIN", "employee_id": 1}, headers={**await auth("employee"), "X-Role": "ADMIN"}
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "SQL_SCOPE_VIOLATION"


async def test_response_contains_no_sensitive_values(client, auth, override_sql):
    override_sql(ScriptedSQLLLM("SELECT id, name, email, role, status, joining_date FROM employees ORDER BY id"))
    text = (await client.post(URL, json={"question": "list"}, headers=await auth("admin"))).text
    for value in EMPLOYEE_SENSITIVE.values():
        assert str(value) not in text
    assert "$2b$" not in text


async def test_unconfigured_provider_returns_503(client, auth, monkeypatch):
    from app.core.config import settings
    from app.services.ai import providers

    monkeypatch.setattr(settings, "ai_llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(providers, "_llm_cache", {})
    assert get_sql_agent_service not in app.dependency_overrides
    response = await client.post(URL, json={"question": "How many employees?"}, headers=await auth("admin"))
    assert response.status_code == 503 and response.json()["error"]["code"] == "AI_UNAVAILABLE"
