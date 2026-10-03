"""Scope rewrite, final re-validation, LIMIT enforcement, read-only executor, DB immutability."""

import pytest
import sqlglot

from app.services.ai.errors import SQLExecutionError, SQLNotAllowedError
from app.services.ai.sql.executor import ReadOnlySQLExecutor
from app.services.ai.sql.parser import parse_select
from app.services.ai.sql.scope import apply_row_limit, enforce_scope, verify_final_sql, wrapper_sql
from app.services.ai.sql.validator import validate_generated_sql
from tests.ai.sql.conftest import MAX_JOINS, MAX_ROWS, db_snapshot
from tests.conftest import TEST_DB_PATH
from tests.support.data import EMPLOYEE, INACTIVE, MANAGER


async def build(ctx_for, user_key, sql, max_rows=MAX_ROWS):
    ctx = await ctx_for(user_key)
    validated = validate_generated_sql(parse_select(sql), ctx, max_joins=MAX_JOINS)
    return ctx, validated, enforce_scope(validated, ctx, max_rows=max_rows, max_joins=MAX_JOINS)


# --- rewrite -------------------------------------------------------------------------------


async def test_every_table_is_wrapped_with_backend_scope(ctx_for):
    ctx, validated, executable = await build(
        ctx_for, "manager",
        "SELECT p.name FROM employee_projects ep JOIN projects p ON p.id = ep.project_id WHERE ep.employee_id IN (SELECT employee_id FROM my_team)",
    )
    team = ", ".join(str(i) for i in sorted({MANAGER.id, EMPLOYEE.id, INACTIVE.id}))
    assert f'"employee_id" IN ({team})' in executable.sql  # server-derived team ids
    assert 'FROM "projects"' in executable.sql and '"projects" WHERE' not in executable.sql  # catalog: no row filter
    assert 'WITH "my_team" AS' in executable.sql or "WITH my_team AS" in executable.sql
    assert executable.params == {"me": MANAGER.id}
    assert wrapper_sql("employees", validated.scope, ctx).count("pan_number") == 0


async def test_self_scope_wrappers_and_tickets(ctx_for):
    _, _, executable = await build(ctx_for, "employee", "SELECT id FROM tickets WHERE employee_id = :me")
    assert f'("employee_id" IN ({EMPLOYEE.id}) OR "assignee_id" = {EMPLOYEE.id})' in executable.sql


async def test_org_scope_projects_columns_without_row_filter(ctx_for):
    _, _, executable = await build(ctx_for, "admin", "SELECT COUNT(*) FROM employees")
    assert 'FROM "employees")' in executable.sql and "WHERE" not in executable.sql
    assert "hashed_password" not in executable.sql


# --- defence in depth: the rewrite alone confines rows even if the intent check were bypassed ---


@pytest.mark.parametrize(
    "user_key,allowed_ids",
    [("employee", {EMPLOYEE.id}), ("manager", {MANAGER.id, EMPLOYEE.id, INACTIVE.id})],
)
async def test_rewrite_confines_rows_without_intent_check(ctx_for, user_key, allowed_ids):
    from app.services.ai.authorization import sql_read_scope
    from app.services.ai.sql.validator import ValidatedQuery, _qualify

    ctx = await ctx_for(user_key)
    # Deliberately unconstrained query that the intent check would reject.
    unconstrained = ValidatedQuery(
        tree=_qualify(parse_select("SELECT id FROM employees ORDER BY id")),
        scope=sql_read_scope(ctx),
        tables=frozenset({"employees"}),
        uses_my_team=False,
    )
    executable = enforce_scope(unconstrained, ctx, max_rows=MAX_ROWS, max_joins=MAX_JOINS)
    executor = ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3, max_rows=MAX_ROWS)
    result = await executor.execute(executable.sql, executable.params, allowed_tables=executable.tables)
    assert {row["id"] for row in result.rows} == allowed_ids

    leaves = ValidatedQuery(
        tree=_qualify(parse_select("SELECT employee_id FROM leave_requests")),
        scope=sql_read_scope(ctx),
        tables=frozenset({"leave_requests"}),
        uses_my_team=False,
    )
    executable = enforce_scope(leaves, ctx, max_rows=MAX_ROWS, max_joins=MAX_JOINS)
    result = await executor.execute(executable.sql, executable.params, allowed_tables=executable.tables)
    assert {row["employee_id"] for row in result.rows} <= allowed_ids


# --- LIMIT -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT id FROM departments", "LIMIT 100"),
        ("SELECT id FROM departments LIMIT 100000", "LIMIT 100"),
        ("SELECT id FROM departments LIMIT 5", "LIMIT 5"),
        ("SELECT id FROM departments LIMIT (SELECT 100000)", "LIMIT 100"),
        ("SELECT id FROM departments UNION SELECT id FROM projects LIMIT 999", "LIMIT 100"),
    ],
)
async def test_row_limit_enforced(ctx_for, sql, expected):
    _, _, executable = await build(ctx_for, "admin", sql)
    assert executable.sql.endswith(expected)


def test_non_literal_offset_rejected():
    with pytest.raises(SQLNotAllowedError):
        apply_row_limit(sqlglot.parse_one("SELECT 1 LIMIT 5 OFFSET (SELECT 3)", read="sqlite"), 100)


# --- final re-validation (tamper detection) ---------------------------------------------------


@pytest.mark.parametrize(
    "tamper",
    [
        lambda sql: sql.replace(f' WHERE "id" IN ({EMPLOYEE.id})', ""),  # scope predicate removed
        lambda sql: sql.replace(f"IN ({EMPLOYEE.id})", "IN (3, 4)"),  # scope widened
        lambda sql: sql.replace('"name", "email"', '"name", "pan_number", "email"'),  # forbidden column added
        lambda sql: sql.replace(" LIMIT 100", ""),  # limit removed
        lambda sql: sql + "; DELETE FROM employees",  # statement appended
    ],
)
async def test_final_verification_detects_tampering(ctx_for, tamper):
    ctx, validated, executable = await build(ctx_for, "employee", "SELECT name FROM employees WHERE id = :me")
    tampered = tamper(executable.sql)
    assert tampered != executable.sql
    with pytest.raises(Exception):
        verify_final_sql(tampered, ctx, validated.scope, max_rows=MAX_ROWS, max_joins=MAX_JOINS)


# --- read-only executor (independent of the parser) -------------------------------------------

EMPLOYEE_TABLES = frozenset({"employees"})


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM employees",
        "UPDATE employees SET status = 'INACTIVE'",
        "INSERT INTO departments (id, name, location) VALUES (99, 'x', 'y')",
        "DROP TABLE employees",
        "DELETE FROM leave_requests",
        "CREATE TABLE evil (a INT)",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE 'evil.db' AS evil",
        "SELECT pan_number FROM employees",  # forbidden column
        "SELECT id FROM attendance_logs",  # table not in this query's allow-list
        "SELECT sqlite_version()",  # function not allow-listed
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r LIMIT 5) SELECT n FROM r",
    ],
)
async def test_executor_blocks_writes_and_forbidden_reads_even_without_validation(sql):
    before = db_snapshot()
    executor = ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3, max_rows=MAX_ROWS)
    with pytest.raises(SQLExecutionError) as caught:
        await executor.execute(sql, {}, allowed_tables=EMPLOYEE_TABLES)
    assert "sqlite" not in caught.value.message.lower() and "employees" not in caught.value.message
    assert db_snapshot() == before


async def test_executor_payroll_denied_unless_query_allows_it():
    executor = ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3, max_rows=MAX_ROWS)
    with pytest.raises(SQLExecutionError):
        await executor.execute("SELECT gross FROM payroll_records", {}, allowed_tables=EMPLOYEE_TABLES)
    result = await executor.execute("SELECT gross FROM payroll_records", {}, allowed_tables=frozenset({"payroll_records"}))
    assert result.rows


async def test_executor_timeout():
    executor = ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=-1, max_rows=MAX_ROWS)
    with pytest.raises(SQLExecutionError):
        await executor.execute(
            "SELECT COUNT(*) FROM employees a JOIN employees b ON 1 = 1 JOIN employees c ON 1 = 1 JOIN employees d ON 1 = 1",
            {},
            allowed_tables=EMPLOYEE_TABLES,
        )


async def test_executor_row_cap_and_truncation():
    executor = ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3, max_rows=2)
    result = await executor.execute("SELECT id FROM employees ORDER BY id", {}, allowed_tables=EMPLOYEE_TABLES)
    assert [row["id"] for row in result.rows] == [1, 2]
    assert result.truncated is True


# --- DB immutability through the whole agent -----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM employees",
        "UPDATE employees SET status = 'INACTIVE'",
        "INSERT INTO employees (id, name) VALUES (99, 'x')",
        "DROP TABLE employees",
        "TRUNCATE TABLE leave_requests",
        "SELECT id FROM employees; DELETE FROM employees",
        "WITH x AS (DELETE FROM tickets RETURNING id) SELECT id FROM x",
        "UPDATE leave_requests SET status = 'APPROVED' WHERE employee_id = :me",
        "UPDATE payroll_records SET net = 0",
    ],
)
@pytest.mark.parametrize("user_key", ["employee", "manager", "admin"])
async def test_no_business_data_changes_through_the_agent(run_sql, user_key, sql):
    before = db_snapshot()
    with pytest.raises(Exception) as caught:
        await run_sql(user_key, sql)
    assert getattr(caught.value, "code", "").startswith("SQL_")
    assert db_snapshot() == before
