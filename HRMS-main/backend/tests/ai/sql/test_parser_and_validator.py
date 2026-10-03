"""AST-level rules: SELECT-only, statements, tables, columns, stars, joins, functions, parameters,
complexity, literal ids and scope intent. No string matching is used by the implementation."""

import pytest

from app.services.ai.errors import (
    SQLColumnNotAllowedError,
    SQLMutationRejectedError,
    SQLNotAllowedError,
    SQLParseError,
    SQLQueryTooComplexError,
    SQLScopeViolationError,
    SQLTableNotAllowedError,
)
from app.services.ai.sql.parser import parse_select
from app.services.ai.sql.validator import validate_generated_sql
from tests.ai.sql.conftest import MAX_JOINS


async def validate(ctx_for, user_key: str, sql: str):
    return validate_generated_sql(parse_select(sql), await ctx_for(user_key), max_joins=MAX_JOINS)


# --- parser: statements ------------------------------------------------------------------

MUTATIONS = [
    "DELETE FROM employees",
    "UPDATE employees SET status = 'INACTIVE'",
    "INSERT INTO employees (id, name) VALUES (99, 'x')",
    "REPLACE INTO employees (id, name) VALUES (1, 'x')",
    "DROP TABLE employees",
    "TRUNCATE TABLE employees",
    "ALTER TABLE employees ADD COLUMN x INT",
    "CREATE TABLE x (a INT)",
    "MERGE INTO employees USING t ON employees.id = t.id WHEN MATCHED THEN DELETE",
    "WITH gone AS (DELETE FROM employees RETURNING id) SELECT id FROM gone",
    "SELECT * FROM employees; DELETE FROM employees",
    "SELECT 1; DROP TABLE employees",
]
NOT_ALLOWED = [
    "PRAGMA table_info(employees)",
    "ATTACH DATABASE 'x.db' AS x",
    "VACUUM",
    "GRANT SELECT ON employees TO bob",
    "EXEC sp_who",
    "CALL proc()",
    "BEGIN TRANSACTION",
    "SELECT 1; SELECT 2",
]


@pytest.mark.parametrize("sql", MUTATIONS)
def test_mutations_rejected(sql):
    with pytest.raises(SQLMutationRejectedError):
        parse_select(sql)


@pytest.mark.parametrize("sql", NOT_ALLOWED)
def test_non_select_statements_rejected(sql):
    with pytest.raises((SQLNotAllowedError, SQLParseError)):
        parse_select(sql)


@pytest.mark.parametrize("sql", ["", "   ", "SELEC name FROM", "SELECT (", "x" * 5000])
def test_unparseable_sql_rejected(sql):
    with pytest.raises(SQLParseError):
        parse_select(sql)


async def test_comments_cannot_smuggle_statements(ctx_for):
    """Comments never become statements (the AST has one SELECT) and are stripped from executed SQL."""
    from app.services.ai.sql.scope import enforce_scope

    sql = "SELECT id FROM employees /* ; DROP TABLE employees */ WHERE id = :me -- ; DELETE FROM x"
    ctx = await ctx_for("employee")
    validated = validate_generated_sql(parse_select(sql), ctx, max_joins=MAX_JOINS)
    executable = enforce_scope(validated, ctx, max_rows=10, max_joins=MAX_JOINS)
    assert "DROP" not in executable.sql and "DELETE" not in executable.sql and "/*" not in executable.sql


async def test_table_valued_functions_rejected(ctx_for):
    with pytest.raises(SQLNotAllowedError):
        await validate(ctx_for, "admin", "SELECT name FROM pragma_table_info('employees')")


# --- tables ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM attendance_logs",
        "SELECT title FROM hr_policies",
        "SELECT id FROM employee_documents",
        "SELECT name FROM sqlite_master",
        "SELECT id FROM main.employees",
        "SELECT id FROM no_such_table",
    ],
)
async def test_unknown_or_denied_tables_rejected(ctx_for, sql):
    with pytest.raises(SQLTableNotAllowedError):
        await validate(ctx_for, "admin", sql)


@pytest.mark.parametrize("user_key", ["employee", "manager"])
async def test_payroll_table_is_admin_only(ctx_for, user_key):
    with pytest.raises(SQLTableNotAllowedError):
        await validate(ctx_for, user_key, "SELECT month FROM payroll_records WHERE employee_id = :me")


async def test_admin_payroll_amounts_allowed_but_identifiers_not(ctx_for):
    await validate(ctx_for, "admin", "SELECT month, gross, net FROM payroll_records")
    for column in ("pan", "pf_uan", "esi_no", "deductions"):
        with pytest.raises(SQLColumnNotAllowedError):
            await validate(ctx_for, "admin", f"SELECT {column} FROM payroll_records")


async def test_employee_cannot_use_my_team(ctx_for):
    with pytest.raises(SQLTableNotAllowedError):
        await validate(ctx_for, "employee", "SELECT COUNT(*) FROM leave_requests WHERE employee_id IN (SELECT employee_id FROM my_team)")


@pytest.mark.parametrize("name", ["employees", "my_team", "payroll_records", "sqlite_master"])
async def test_cte_cannot_shadow_real_or_virtual_tables(ctx_for, name):
    with pytest.raises(SQLNotAllowedError):
        await validate(ctx_for, "admin", f"WITH {name} AS (SELECT 1 AS id) SELECT id FROM {name}")


# --- columns / SELECT * --------------------------------------------------------------------

SENSITIVE_QUERIES = [
    "SELECT salary FROM payroll_records",
    "SELECT current_salary_usd FROM employees",
    "SELECT name FROM employees ORDER BY current_salary_usd DESC",
    "SELECT name FROM employees WHERE current_salary_usd > 100000",
    "SELECT e.pan_number AS x FROM employees e",
    "SELECT MAX(bank_account_number) FROM employees",
    "SELECT name FROM employees GROUP BY date_of_birth",
    "SELECT hashed_password FROM employees",
    "SELECT d.name FROM departments d JOIN employees e ON e.department_id = d.id WHERE e.bank_ifsc = 'X'",
    "SELECT (SELECT pan_name FROM employees LIMIT 1) AS p FROM departments",
    "WITH x AS (SELECT pan_dob FROM employees) SELECT pan_dob FROM x",
    "SELECT nope FROM employees",
]


@pytest.mark.parametrize("sql", SENSITIVE_QUERIES)
async def test_sensitive_and_unknown_columns_rejected_everywhere(ctx_for, sql):
    with pytest.raises((SQLColumnNotAllowedError, SQLTableNotAllowedError)):
        await validate(ctx_for, "admin", sql)


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM employees", "SELECT * FROM payroll_records", "SELECT e.* FROM employees e", "SELECT * FROM departments",
     "SELECT name, * FROM projects"],
)
async def test_select_star_always_rejected(ctx_for, sql):
    with pytest.raises(SQLColumnNotAllowedError):
        await validate(ctx_for, "admin", sql)


async def test_count_star_is_allowed(ctx_for):
    await validate(ctx_for, "admin", "SELECT COUNT(*) FROM employees")


# --- joins -------------------------------------------------------------------------------


async def test_approved_join_allowed(ctx_for):
    await validate(ctx_for, "admin", "SELECT d.name, COUNT(e.id) FROM employees e JOIN departments d ON d.id = e.department_id GROUP BY d.name")


@pytest.mark.parametrize(
    "sql,error",
    [
        ("SELECT e.id FROM employees e JOIN departments d ON d.name = e.name", SQLNotAllowedError),
        ("SELECT e.id FROM employees e JOIN projects p ON p.id = e.department_id", SQLNotAllowedError),
        ("SELECT e.id FROM employees e JOIN departments d ON d.id = e.department_id OR 1 = 1", SQLNotAllowedError),
        ("SELECT e.id FROM employees e JOIN departments d USING (id)", SQLNotAllowedError),
        ("SELECT e.id FROM employees e, departments d", SQLQueryTooComplexError),
        ("SELECT e.id FROM employees e CROSS JOIN departments d", SQLQueryTooComplexError),
    ],
)
async def test_unapproved_or_cartesian_joins_rejected(ctx_for, sql, error):
    with pytest.raises(error):
        await validate(ctx_for, "admin", sql)


# --- functions / parameters / complexity ------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    ["SELECT load_extension('x')", "SELECT readfile('/etc/passwd')", "SELECT randomblob(1000000000)",
     "SELECT sqlite_version()", "SELECT zeroblob(10) FROM departments"],
)
async def test_dangerous_functions_rejected(ctx_for, sql):
    with pytest.raises(SQLNotAllowedError):
        await validate(ctx_for, "admin", sql)


async def test_common_functions_allowed(ctx_for):
    await validate(
        ctx_for, "admin",
        "SELECT lower(name), strftime('%Y', joining_date), COUNT(DISTINCT department_id), coalesce(occupancy, 'n/a') "
        "FROM employees WHERE name LIKE 'A%' GROUP BY 1, 2, 4",
    )


@pytest.mark.parametrize("sql", ["SELECT id FROM employees WHERE id = :other", "SELECT id FROM employees WHERE id = ?"])
async def test_only_me_parameter_allowed(ctx_for, sql):
    with pytest.raises(SQLNotAllowedError):
        await validate(ctx_for, "admin", sql)


async def test_too_many_joins_rejected(ctx_for):
    sql = ("SELECT e.id FROM employees e JOIN employees m ON m.id = e.manager_id JOIN departments d ON d.id = e.department_id "
           "JOIN leave_requests l ON l.employee_id = e.id JOIN tickets t ON t.employee_id = e.id "
           "JOIN employee_projects ep ON ep.employee_id = e.id")
    with pytest.raises(SQLQueryTooComplexError):
        await validate(ctx_for, "admin", sql)


async def test_deep_nesting_and_recursion_rejected(ctx_for):
    deep = "SELECT id FROM departments WHERE id IN (SELECT id FROM departments WHERE id IN (SELECT id FROM departments WHERE id IN (SELECT id FROM departments WHERE id IN (SELECT id FROM departments))))"
    with pytest.raises(SQLQueryTooComplexError):
        await validate(ctx_for, "admin", deep)
    with pytest.raises(SQLQueryTooComplexError):
        await validate(ctx_for, "admin", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r")


# --- literal ids + scope intent (non-ORG) ------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM leave_requests WHERE employee_id = 4",
        "SELECT id FROM leave_requests WHERE employee_id = :me OR employee_id = 4",
        "SELECT id FROM leave_requests WHERE employee_id IN (3, 4)",
        "SELECT id FROM employees WHERE id BETWEEN 1 AND 5",
        "SELECT id FROM employees WHERE manager_id = 1",
    ],
)
@pytest.mark.parametrize("user_key", ["employee", "manager"])
async def test_literal_employee_ids_rejected_for_non_org(ctx_for, user_key, sql):
    with pytest.raises(SQLScopeViolationError):
        await validate(ctx_for, user_key, sql)


async def test_admin_may_use_literal_ids(ctx_for):
    await validate(ctx_for, "admin", "SELECT id FROM leave_requests WHERE employee_id = 4")


@pytest.mark.parametrize(
    "user_key,sql",
    [
        ("employee", "SELECT id FROM employees WHERE id = :me"),
        ("employee", "SELECT l.id FROM leave_requests l JOIN employees e ON e.id = l.employee_id WHERE e.id = :me"),
        ("employee", "SELECT t.id FROM tickets t WHERE t.assignee_id = :me"),
        ("employee", "SELECT p.name FROM employee_projects ep JOIN projects p ON p.id = ep.project_id WHERE ep.employee_id = :me"),
        ("manager", "SELECT id FROM employees WHERE manager_id = :me"),
        ("manager", "SELECT l.id FROM leave_requests l JOIN my_team t ON t.employee_id = l.employee_id"),
        ("manager", "SELECT id FROM leave_requests WHERE employee_id IN (SELECT id FROM employees WHERE manager_id = :me)"),
        ("employee", "SELECT name FROM departments"),  # catalog: no scope predicate needed
    ],
)
async def test_scope_constrained_queries_accepted(ctx_for, user_key, sql):
    await validate(ctx_for, user_key, sql)


@pytest.mark.parametrize(
    "user_key,sql",
    [
        ("employee", "SELECT COUNT(*) FROM employees"),
        ("employee", "SELECT id FROM leave_requests WHERE status = 'PENDING'"),
        ("employee", "SELECT id FROM leave_requests WHERE employee_id = :me OR status = 'PENDING'"),
        ("employee", "SELECT l.id FROM leave_requests l JOIN employees e ON e.id = l.approver_id WHERE e.id = :me"),
        ("employee", "SELECT m.name FROM employees e JOIN employees m ON m.id = e.manager_id WHERE e.id = :me"),
        ("manager", "SELECT COUNT(*) FROM employees"),
        ("manager", "SELECT id FROM leave_requests WHERE employee_id IN (SELECT manager_id FROM employees WHERE id = :me)"),
        ("manager", "SELECT id FROM employees WHERE manager_id = :me UNION SELECT id FROM employees"),
    ],
)
async def test_unconstrained_queries_rejected(ctx_for, user_key, sql):
    with pytest.raises(SQLScopeViolationError):
        await validate(ctx_for, user_key, sql)


async def test_validated_scope_comes_from_context(ctx_for):
    assert (await validate(ctx_for, "employee", "SELECT id FROM employees WHERE id = :me")).scope.level.value == "SELF"
    assert (await validate(ctx_for, "manager", "SELECT id FROM employees WHERE manager_id = :me")).scope.level.value == "TEAM"
    assert (await validate(ctx_for, "admin", "SELECT id FROM employees")).scope.level.value == "ORG"
