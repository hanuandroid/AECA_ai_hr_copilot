---
name: sql-agent
description: Build and modify the read-only NL→SQL agent — schema_service, sql_guardrails (sqlglot validation, allowlists, forbidden columns, scope rewrite, LIMIT), sql_executor (read-only sqlite connection) and sql_agent. Use for any /chat/sql work or when adding tables/columns to the SQL allowlist.
---

> **Implemented layout (checked 2026-10-19):** the module names below are partly the Phase 0 plan. The real locations are listed in `HRMS-main/docs/ai_architecture.md` §2–2.1 (for example `actions/registry.py` + `actions/client.py` instead of `api_tools.py`, `sql/` instead of `sql_*.py`, `rag/` instead of `policy_rag.py`, `/copilot` and `components/copilot/` instead of `/ai-copilot` and `components/ai/`, `ai_audit_events` instead of `ai_audit_logs`, `python -m evals.run_evals [--live]` instead of `--provider`). Follow the real layout.

# SQL Agent

## Purpose
Answer HR data questions with safe, role-scoped, read-only SQL that can't leak forbidden fields even if the LLM or the parser is fooled.

## When to use
Changes to `schema_service.py`, `sql_guardrails.py`, `sql_executor.py`, `sql_agent.py`, the SQL allowlist, or `/api/v1/chat/sql`.

## Workflow
1. **Schema context**: `schema_service.describe_for(ctx)` returns only allowed tables and columns for the role (from `permissions.SQL_POLICY`), plus enum values and short join hints (`employee_projects.project_id → projects.id`, …).
2. **Generate**: the LLM returns structured `{sql, explanation}`. The prompt says SQLite dialect, SELECT only, use only the listed columns, and filter the current user with `:me`.
3. **Guard** (`sql_guardrails.validate_and_rewrite(sql, ctx) -> SafeQuery`):
   1. `sqlglot.parse(sql, read="sqlite")`; there must be exactly 1 statement.
   2. The root must be `exp.Select`, or a `Union`/`Intersect`/`Except` of selects. Walk the **whole** tree and reject any `Insert, Update, Delete, Drop, Alter, Create, Command, Pragma, Attach, Detach, Transaction` node.
   3. Reject functions outside the allowlist (e.g. `load_extension`, `readfile`, `randomblob`…).
   4. Resolve tables. Unknown, `sqlite_*` or non-allowlisted tables are rejected.
   5. Resolve columns (qualify with `sqlglot.optimizer.qualify`). Forbidden columns lead to `AI_SENSITIVE_DATA_BLOCKED`, and unknown ones to `AI_UNSAFE_SQL`.
   6. Rewrite each table reference to `(SELECT <allowed cols> FROM t WHERE <scope>) AS t` with `:me` bound.
   7. Force `LIMIT min(n, AI_SQL_MAX_ROWS)`.
4. **Execute** (`sql_executor`): `sqlite3.connect("file:<db>?mode=ro", uri=True)`, `PRAGMA query_only=ON`, `set_authorizer` (allow SELECT/READ on allowed columns and allowlisted FUNCTION, deny everything else), `set_progress_handler` timeout. Run it in `run_in_threadpool`. Any exception becomes `AI_SQL_FAILED` with a generic message, and the detail is logged redacted.
5. **Summarize**: pass the capped rows to the LLM for a one-paragraph answer. Include `sql` in the response only if the role can view raw SQL.

## Constraints
- Scope is enforced by the rewrite and the bound parameter, never by trusting the LLM's WHERE.
- Never string-format user values into SQL.
- Never return DB error text.

## Examples
- EMPLOYEE: "Show my current project assignments" → `employee_projects` scoped to SELF → rows for self only.
- EMPLOYEE: "leave balance of employee 57" → the scoped table only contains self → 0 rows → "No matching records you can access." (no existence leak).
- `SELECT e.pan_number AS x FROM employees e` → blocked (alias resolves to a forbidden column).
- `SELECT * FROM employees` → expands only to allowed columns.
- `SELECT 1; DROP TABLE employees` → blocked (multi-statement).

## Validation checklist
- [ ] One test per blocked keyword/construct, including inside a CTE and a subquery
- [ ] Alias, `*`, `UNION`, `sqlite_master`, comment-smuggling and case-variation tests
- [ ] Scope tests for each role, with a non-team employee
- [ ] Executor test: a write statement passed straight to the executor (bypassing guardrails) still fails
- [ ] Row limit enforced when the LLM omits LIMIT or asks for 10,000

## Common failure modes
- A keyword blocklist on raw strings instead of an AST walk.
- Checking only the top-level node, so DML hides in a CTE.
- Forgetting `payroll_records.pan`, `pf_uan` and `esi_no`.
- Using the app's async read-write engine for agent SQL.
