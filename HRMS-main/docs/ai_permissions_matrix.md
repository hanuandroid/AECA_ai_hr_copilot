# AI Permissions Matrix

Status: **Implemented (Phase 2).** The code source of truth is `backend/app/services/ai/authorization/policy.py` (`AI_PERMISSION_MATRIX`). §0 below mirrors it exactly, and `tests/ai/authorization/test_matrix.py` holds an independent copy, so any drift fails the tests.

> **AI authorization is stricter than existing HRMS authorization. AI authorization must never be more permissive.** (Decision D7)

## 0. Implemented capability matrix (authoritative)

The value in each cell is the **target rule**. `—` means denied.

| Capability | ADMIN | MANAGER | EMPLOYEE | Notes |
|---|---|---|---|---|
| POLICY_READ | NONE | NONE | NONE | policy RAG |
| SKILL_SEARCH_ORG | ORG | ORG | — | **Restricted organisation-wide skill search** (RBAC step 3, 2026-10-16; replaces the unused `DIRECTORY_READ`). Approved fields only: employee_id, employee_name, department, skill, level (active employees). Copilot: a MANAGER's pure skill question is answered by the dedicated backend query (`services/ai/sql/skill_search.py`), not model SQL. API: `GET /api/v1/employees/skill-search`. Not a directory: general employee data stays SELF/TEAM/ORG-scoped. EMPLOYEE: "search by skill: Limited" = own skills via SELF-scoped SQL |
| HR_READ_SELF | SELF | SELF | SELF | own leave/balance/projects/tickets/job history |
| HR_READ_TEAM | TEAM | TEAM | — | caller + direct reports |
| HR_READ_ORG | ORG | — | — | |
| SQL_READ_SELF | SELF | SELF | SELF | SQL row scope |
| SQL_READ_TEAM | TEAM | TEAM | — | |
| SQL_READ_ORG | ORG | — | — | |
| SQL_VIEW_RAW | NONE | NONE | — | "View raw SQL: Employee No" |
| PAYROLL_READ_ORG | ORG | — | — | gross/net/month only. PAN/UAN/ESI/deductions stay forbidden for all roles. |
| LEAVE_CREATE | SELF | SELF | SELF | |
| LEAVE_APPROVE | ORG_EXCEPT_SELF | REPORTS | — | **stricter than API (R4)**: manager limited to direct reports, never self |
| LEAVE_REJECT | ORG_EXCEPT_SELF | REPORTS | — | same |
| TICKET_CREATE | SELF | SELF | SELF | |
| TICKET_ASSIGN | ORG | TEAM | — | target = ticket owner. **Stricter than API** (managers: any ticket). |
| TICKET_STATUS_UPDATE | ORG | TEAM | — | **stricter than API** (since RBAC step 5 the API lets only an EMPLOYEE *assignee* update status; owners no longer can). The assignment matrix says "Employee: No". |
| ANNOUNCEMENT_CREATE | NONE | NONE | — | |
| PROJECT_ASSIGN | ORG | TEAM | — | **stricter than API (R4)** |
| PROJECT_CREATE | NONE | — | — | **stricter than API** (managers may create). The assignment lists it as an admin action. |
| EMPLOYEE_DEACTIVATE | ORG_EXCEPT_SELF | — | — | |
| AUDIT_READ_OWN | SELF | SELF | SELF | |
| AUDIT_READ_ORG | ORG | — | — | |

Target rules:

| Rule | Allowed targets |
|---|---|
| `NONE` | no specific employee |
| `SELF` | the caller |
| `TEAM` | the caller and direct reports (`employees.manager_id = caller`) |
| `REPORTS` | direct reports only |
| `ORG` | anyone |
| `ORG_EXCEPT_SELF` | anyone except the caller |

**Deviations from the Phase 2 prompt's draft matrix (decided per D7 and the assignment matrix):**
- TICKET_STATUS_UPDATE for EMPLOYEE: draft Y → **N**. The assignment matrix row "Assign/update ticket" says Employee: No.
- PROJECT_CREATE for MANAGER: draft Y → **N**. The assignment lists "Create projects" under Admin actions only.
- Added DIRECTORY_READ, SQL_VIEW_RAW, PAYROLL_READ_ORG and AUDIT_READ_OWN/ORG to cover assignment matrix rows the draft didn't have. (2026-10-16: DIRECTORY_READ, granted to all roles but never used, was replaced by SKILL_SEARCH_ORG for MANAGER and ADMIN; RBAC step 3.)
- Tickets for MANAGER: the Phase 0 design said "ALL (matches existing API)". It is now **TEAM**, so one team rule applies across all employee-owned data (stricter, allowed by D7).

Inactive users are denied **every** capability, regardless of role.

## Principles

1. **Deterministic only.** Permissions are decided by application code from the DB-loaded `Employee.role`, `Employee.id` and `Employee.manager_id`. The LLM never decides, and nothing the user or a document says can change a decision.
2. **Never looser than the app.** The AI may be *stricter* than an existing endpoint (e.g. team-only approvals) but never grants access the normal UI/API would deny. Mutations always go through the existing endpoint, which re-applies its own checks.
3. **Deny by default.** Unknown capability, table, column, or tool → deny.
4. **No existence leaks.** Refusals never confirm that a record exists ("You do not have permission to view another employee's payroll information." — not "I found it but can't show it").
5. **INACTIVE users are denied** all AI capabilities (stricter than the existing `get_current_user`).

## Scopes

| Scope | Definition (evaluated in code, bound as SQL parameters) |
|---|---|
| `SELF` | `employee_id = :current_user_id` |
| `TEAM` | `SELF` ∪ direct reports: `employee_id IN (SELECT id FROM employees WHERE manager_id = :current_user_id)` |
| `ALL` | no row filter |
| `NONE` | inaccessible |

> Team = **direct reports only** (single level). Decision rationale: it is the only hierarchy signal in the schema and is unambiguous. In seed data every employee reports to Manager User, so tests must create a non-team employee to prove scoping.

## 1. Assignment table → capabilities (Phase 0 design; §0 is authoritative where they differ)

| AI Capability | EMPLOYEE | MANAGER | ADMIN | Enforced by |
|---|---|---|---|---|
| Ask HR policy questions | ✅ | ✅ | ✅ | `policy.ask` |
| Own leave balance | ✅ SELF | ✅ SELF | ✅ SELF | SQL scope / `get_my_leave_balance` tool |
| Another employee's leave balance | ❌ | ✅ TEAM | ✅ ALL | SQL scope on `leave_balances` |
| Own project assignments | ✅ SELF | ✅ | ✅ | SQL scope on `employee_projects` |
| All project assignments | ❌ | ⚠ TEAM ("Limited") | ✅ ALL | SQL scope |
| Search employees by skill | ⚠ directory fields only ("Limited") | ✅ | ✅ | SQL column allowlist |
| Generate SQL over HR data | ⚠ scoped | ⚠ scoped | ✅ ALL (minus forbidden) | SQL guardrails |
| View raw SQL | ❌ | ✅ | ✅ | response formatter (`sql.view_raw`) |
| Create own leave request | ✅ | ✅ | ✅ | tool allowlist + existing API |
| Approve/reject leave | ❌ | ✅ TEAM | ✅ ALL | tool allowlist + team pre-check + existing API |
| Create ticket | ✅ | ✅ | ✅ | tool allowlist + existing API |
| Assign/update ticket | ❌ | ✅ | ✅ | tool allowlist + existing API |
| Create announcement | ❌ | ✅ | ✅ | tool allowlist + existing API |
| Assign employee to project | ❌ | ✅ TEAM | ✅ ALL | tool allowlist + team pre-check + existing API |
| Create project | ❌ | ❌ | ✅ | tool allowlist (API allows MANAGER; AI is stricter per assignment) |
| Deactivate / reactivate employee | ❌ | ❌ | ✅ | tool allowlist + existing API (ADMIN-only) |
| Payroll data via AI | ❌ (use Finance page) | ❌ ("Restricted" → blocked) | ✅ aggregate fields only | SQL table/column allowlist |
| Bank / PAN / password / DOB / salary fields | ❌ | ❌ | ❌ | global forbidden-column set |
| View AI audit log (`GET /api/v1/ai/audit`) | own events | own events | all events (+ filter by employee) | AUDIT_READ_OWN / AUDIT_READ_ORG (Phase 8) |
| AI usage dashboard and metrics (`/admin/ai-usage`, `GET /api/v1/ai/audit/metrics`) | ❌ 403 | ❌ 403 | ✅ | AUDIT_READ_ORG (Phase 8/10) |
| Copilot conversations (`/api/v1/chat/conversations`) | own only | own only | own only (no admin view) | ownership check (Phase 7) |
| Role-specific Copilot mode / suggested prompts | employee mode | manager mode | admin mode | UX only: prompts filtered by `GET /chat/permissions`; grants nothing (Phase 10) |

Note: the assignment allows EMPLOYEE to update tickets they own. The matrix says "Assign/update ticket: Employee No", so the AI follows the matrix (stricter). Employees can still update their own tickets in the normal UI.

## 2. SQL Agent — table & column access (implemented in Phase 4)

> **The LLM is never an authorization authority. SQL validation is enforced independently of the LLM prompt.**

Code source of truth: `backend/app/services/ai/sql/schema.py` (`TABLES`, `DENIED_TABLES`). `tests/ai/sql/test_schema_registry.py` fails if any table or column in the models is unclassified.

Global **forbidden columns** (rejected at validation time for every role, and absent from every scoped view):

```text
employees.hashed_password
employees.bank_name, bank_account_number, bank_account_name, bank_branch, bank_ifsc
employees.pan_number, pan_name, pan_dob, date_of_birth
employees.current_salary_usd
employees.profile_photo_path, profile_photo_mime
employees.phone, employees.address, employees.blood_type        (personal PII — not needed for HR intelligence)
payroll_records.pan, payroll_records.pf_uan, payroll_records.esi_no, payroll_records.deductions
```

Tables **never** exposed to the SQL agent: `employee_documents, hr_policies (RAG handles it), attendance_logs, polls, poll_responses, announcements, onboarding_tasks, holidays, policy_chunks, alembic_version, sqlite_*` and the AI-owned tables `ai_pending_actions`, `ai_conversations`, `ai_conversation_messages`, `ai_audit_events`. Also rejected: schema-qualified names (`main.x`) and table-valued functions (`pragma_table_info(...)`).

Rows are limited by the Phase 2 SQL capability (`SQL_READ_SELF/TEAM/ORG`): EMPLOYEE → SELF, MANAGER → TEAM (self + `employees.manager_id = caller`), ADMIN → ORG.

| Table | Allowed columns | EMPLOYEE rows | MANAGER rows | ADMIN rows |
|---|---|---|---|---|
| `employees` | id, name, email, department_id, manager_id, role, status, joining_date, occupancy | SELF | TEAM | ALL |
| `departments` | id, name, location | ALL (catalog) | ALL | ALL |
| `skills` | id, name, normalized_name | ALL (catalog) | ALL | ALL |
| `employee_skills` | id, employee_id, skill_id, level | SELF | TEAM | ALL |
| `projects` | id, name, description, status | ALL (catalog) | ALL | ALL |
| `employee_projects` | id, employee_id, project_id, role_on_project | SELF | TEAM | ALL |
| `job_history` | id, employee_id, designation, business_unit, department, start_date, end_date, is_current | SELF | TEAM | ALL |
| `leave_balances` | id, employee_id, leave_type, total, used, remaining | SELF | TEAM | ALL |
| `leave_requests` | id, employee_id, leave_type, start_date, end_date, is_half_day, half_day_period, reason, status, approver_id | SELF | TEAM | ALL |
| `tickets` | id, employee_id, assignee_id, title, description, category, priority, status, created_at | SELF (raised **or** assigned) | TEAM (raised by team **or** assigned to caller; stricter than existing `GET /tickets`) | ALL |
| `payroll_records` | id, employee_id, month, gross, net | NONE | NONE | ALL |

**Phase 4 changes vs the Phase 0 design (all stricter, per D7 and the Phase 4 spec):**
- `employees` for EMPLOYEE is now **SELF**, not ALL. An employee's `SELECT COUNT(*) FROM employees` is refused, as the spec requires. The directory remains available in the normal UI. Consequence: "search employees by skill" through SQL is limited to the caller's own scope for EMPLOYEE.
- `employee_skills` is now scoped (SELF/TEAM/ORG), not ALL.
- `SELECT *` / `t.*` is **always refused** (explicit columns are required), even for tables without forbidden columns. `COUNT(*)` is allowed.

**Enforcement (not prompt-based):**
1. **AST validation.** Allowed tables, columns and functions only. No star projections, placeholders other than `:me`, CTEs shadowing tables, recursive CTEs or cartesian/USING joins. Joins are limited to the approved relationships. A non-ORG user may not compare employee-id columns to literals.
2. **Scope intent.** For non-ORG users, every scoped table in every SELECT must be constrained by `:me` (`owner = :me`, `employees.manager_id = :me`) or `my_team` (`owner IN (SELECT employee_id FROM my_team)` / `JOIN my_team`), directly or via an equality join to a constrained table, and only in AND-conjuncts. Otherwise the query gets `SQL_SCOPE_VIOLATION` (denied, not silently narrowed).
3. **Scope rewrite (the security boundary).** Every table `T` becomes `(SELECT <allowed cols> FROM T WHERE <owner> IN (<server ids>)) AS T`. For tickets the predicate is `employee_id IN (ids) OR assignee_id = caller`. ORG and catalog tables get the projection only. `my_team` is a backend CTE built from `AIUserContext`.
4. **Row limit.** The outermost `LIMIT` is set to `min(literal, AI_SQL_MAX_ROWS)`, or `AI_SQL_MAX_ROWS` when it is missing or non-literal.
5. **Final re-validation** of the regenerated SQL: every table must sit in exactly the expected wrapper, `my_team` must match, and the LIMIT must be present.
6. **Read-only executor.** Separate `mode=ro` connection, `query_only`, and a per-query authorizer that allows SELECT, allowlisted column reads for the query's tables and allowlisted functions. Everything else is denied.

## 3. Action Agent — tool registry

### 3.0 Implemented (Phase 5) — authoritative

> **The LLM proposes actions; the backend authorizes and executes them. All data-changing AI actions require explicit confirmation. The Action Agent can call only registered HRMS APIs.**

Code source of truth: `backend/app/services/ai/actions/registry.py` (`ACTION_REGISTRY`). Each action name is identical to its Phase 2 capability, and availability per role comes only from `AI_PERMISSION_MATRIX`. There is no permission table inside the action package.

| Action / capability | Existing endpoint | ADMIN | MANAGER | EMPLOYEE | Target resolution (within scope only) |
|---|---|---|---|---|---|
| LEAVE_CREATE | POST `/api/v1/leaves/requests` | self | self | self | caller (another target is denied) |
| LEAVE_APPROVE | POST `/api/v1/leaves/requests/{id}/approve` | anyone except self | direct reports | — | leave by id, or the employee's single PENDING leave |
| LEAVE_REJECT | POST `/api/v1/leaves/requests/{id}/reject` | anyone except self | direct reports | — | same |
| TICKET_CREATE | POST `/api/v1/tickets` | self | self | self | caller |
| TICKET_ASSIGN | POST `/api/v1/tickets/{id}/assign` | any ticket | team-raised tickets | — | ticket by id/title, plus assignee (ACTIVE, within HR read scope) |
| TICKET_STATUS_UPDATE | POST `/api/v1/tickets/{id}/status` | any ticket | team-raised tickets | — (D7) | ticket by id/title; status ∈ OPEN/IN_PROGRESS/RESOLVED |
| ANNOUNCEMENT_CREATE | POST `/api/v1/announcements` | ✓ | ✓ | — | — |
| PROJECT_ASSIGN | POST `/api/v1/employees/{id}/projects` | anyone | team | — | employee (ACTIVE) + project from the catalog |
| PROJECT_CREATE | POST `/api/v1/employees/projects/catalog` | ✓ | — (D7: the API allows it) | — | — |
| EMPLOYEE_DEACTIVATE | DELETE `/api/v1/employees/{id}` | anyone except self | — | — | employee |

Every action requires confirmation (D6): a server-side, single-use token bound to the user, action, target and payload, with a TTL of `AI_ACTION_CONFIRMATION_TTL_SECONDS` (300).

**Changes vs the Phase 0 design below:**
- Read-only tools such as `get_my_leave_balance` and `list_pending_leaves` are not part of the Action Agent. Reads go through the SQL agent (Phase 4), so the registry contains only mutations.
- Phase 0 named the confirmation table `ai_pending_actions`; it is implemented under that name in migration 0018.

### 3.1 Phase 0 design (historical)

All mutating tools require **explicit user confirmation** (a server-issued, single-use, user-bound, 5-minute confirmation id). The assignment requires this for high-impact actions. We extend it to every mutation because LLM-extracted parameters (dates, ids) must be shown to the user before execution.

| Tool | Backend call | EMPLOYEE | MANAGER | ADMIN | Extra deterministic check | Confirm |
|---|---|---|---|---|---|---|
| `get_my_leave_balance` | GET `/leaves/balances/me` | ✅ | ✅ | ✅ | — | no |
| `list_my_leave_requests` | GET `/leaves/requests/me` | ✅ | ✅ | ✅ | — | no |
| `list_my_tickets` | GET `/tickets?mine=true` | ✅ | ✅ | ✅ | — | no |
| `find_employee` | GET `/employees?q=` | ✅ | ✅ | ✅ | directory fields only | no |
| `list_pending_leaves` | GET `/leaves/requests/pending` | ❌ | ✅ (filtered to TEAM) | ✅ | — | no |
| `create_leave_request` | POST `/leaves/requests` | ✅ | ✅ | ✅ | self only (API uses token) | **yes** |
| `create_ticket` | POST `/tickets` | ✅ | ✅ | ✅ | enum validation | **yes** |
| `approve_leave` | POST `/leaves/requests/{id}/approve` | ❌ | ✅ | ✅ | MANAGER: leave.employee ∈ TEAM and ≠ self | **yes** |
| `reject_leave` | POST `/leaves/requests/{id}/reject` | ❌ | ✅ | ✅ | same as approve | **yes** |
| `assign_ticket` | POST `/tickets/{id}/assign` | ❌ | ✅ | ✅ | — | **yes** |
| `update_ticket_status` | POST `/tickets/{id}/status` | ❌ | ✅ | ✅ | — | **yes** |
| `create_announcement` | POST `/announcements` | ❌ | ✅ | ✅ | — | **yes** |
| `assign_employee_to_project` | POST `/employees/{id}/projects` | ❌ | ✅ | ✅ | MANAGER: employee ∈ TEAM | **yes** |
| `create_project` | POST `/employees/projects/catalog` | ❌ | ❌ | ✅ | — | **yes** |
| `deactivate_employee` | DELETE `/employees/{id}` | ❌ | ❌ | ✅ | not self | **yes** |

**Never exposed as tools:** `GET /employees/{id}` (leaks bank/PAN/salary — Audit R1), `PUT /employees/{id}`, any `/finance/*`, any document upload/download/delete, `/auth/*`, profile photo endpoints.

**Not supported (no backing API):** editing/cancelling a leave request (`update_leave_request` in the assignment). The agent replies that this isn't available and does not implement it with SQL.

The tools available to the LLM are **filtered by role before the LLM call**. A tool the user can't use is never offered to the model. The permission check runs again at execution and at confirmation time.

## 4. Standard refusal messages

| Code | Message |
|---|---|
| `AI_PERMISSION_DENIED` | You do not have permission to perform this action. |
| `AI_SENSITIVE_DATA_BLOCKED` | I can't provide bank, PAN, password, date-of-birth or salary information. |
| `AI_PAYROLL_DENIED` | You do not have permission to view payroll information through the assistant. |
| `AI_OUT_OF_SCOPE` | You can only view information about yourself (EMPLOYEE) / your team (MANAGER). |
| `AI_UNSAFE_SQL` | *(Phase 0 name; implemented as the `SQL_*` codes below)* |
| `SQL_SCOPE_VIOLATION` (403) | You can only ask about data within your authorized scope: your own records, or your team's if you manage a team. |
| `SQL_COLUMN_NOT_ALLOWED` / `SQL_TABLE_NOT_ALLOWED` (403) | That information is not available through the assistant. |
| `SQL_MUTATION_REJECTED` (403) | The assistant can only read data. It cannot create, change or delete records. |
| `SQL_NOT_ALLOWED` (403) | That query uses an operation the assistant is not allowed to run. |
| `SQL_PARSE_ERROR` / `SQL_QUERY_TOO_COMPLEX` (422) | The assistant could not build a valid query… / That question needs a query that is too complex… |
| `SQL_EXECUTION_ERROR` (500) | The assistant could not run the query. Please try again. |
| `AI_ACTION_NOT_SUPPORTED` | That action isn't supported by the assistant. |
| `AI_INSUFFICIENT_CONTEXT` | I couldn't find this in the HR policy library. Please contact HR. |
| `AI_CONFIRMATION_INVALID` (400) | This confirmation is invalid or has expired. Please ask again. *(identical for unknown, other user's, used, expired or tampered)* |
| `AI_TARGET_NOT_FOUND` (404) | I couldn't find a matching record that you're allowed to act on. *(identical for non-existent and out-of-scope)* |
| `AI_BACKEND_UNAVAILABLE` (502) | The HR system could not complete this action right now. Please try again later. |
