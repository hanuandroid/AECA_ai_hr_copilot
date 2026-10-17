# Page and endpoint inventory (RBAC remediation Step 10)

**Date:** 2026-10-17 · **Repository state:** `main` after `345ca96` (Step 9) · **Release status: NOT YET PRODUCTION READY**

This is the Step 10 inventory: every API endpoint and every frontend page, the roles allowed, where authorization is
enforced, and how that is verified. The backend is the only authorization boundary; frontend guards are UX.

## Verification classes

| Class | Meaning |
|---|---|
| **AUTO** | Verified by automated tests in this repository (named) that ran green in the Step 10 run |
| **MANUAL** | Checked by reading the code on 2026-10-17 (no dedicated object-level test); what was checked is listed |
| **NOT VERIFIED** | Needs something not available here (real browser, live model, production-like infrastructure) |
| **N/A** | Not applicable |

## 1. API endpoints (79 method + path pairs)

Source: `backend/tests/rbac/test_route_permissions.py`. The table is **generated** from that suite's `CASES`; its
completeness test proves every registered route has exactly one row (0 unclassified). Outcome per role for the suite's
fixture target: `ok` 2xx, `deny` 403, `hidden` 404 identical to an unknown id, `auth` 401, `rejected` 400/409/422 after
the role gate (validation or state, e.g. a fixture that is already published). Role gate: **AUTO** for every row.

| Method | Path | Anonymous | EMPLOYEE | MANAGER | ADMIN | Note |
|---|---|---|---|---|---|---|
| `POST` | `/api/v1/auth/login` | ok | ok | ok | ok |  |
| `POST` | `/api/v1/auth/token` | ok | ok | ok | ok |  |
| `POST` | `/api/v1/auth/change-password` | auth | ok | ok | ok | Step 4: self-service password change for any authenticated active user (own account only) |
| `GET` | `/api/v1/health` | ok | ok | ok | ok |  |
| `GET` | `/api/v1/employees/departments` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/employees` | auth | deny | ok | ok | Decision 1 (step 2): EMPLOYEE denied (was ok); MANAGER sees self + direct reports only |
| `GET` | `/api/v1/employees/{employee_id}` | auth | hidden | hidden | ok | Decision 1 (step 2): another employee is hidden from EMPLOYEE and from a non-managing MANAGER (was ok) |
| `GET` | `/api/v1/employees/skill-search` | auth | deny | ok | ok | Step 3: restricted org-wide skill search (approved fields only); not a directory |
| `GET` | `/api/v1/employees/me` | auth | ok | ok | ok |  |
| `PUT` | `/api/v1/employees/me` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/employees/me/profile-picture` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/employees/me/profile-picture` | auth | hidden | hidden | hidden |  |
| `GET` | `/api/v1/employees/{employee_id}/profile-picture` | auth | hidden | hidden | hidden | no photo seeded; directory photo rules are covered by step 2 tests |
| `GET` | `/api/v1/employees/me/job-history` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/employees/me/documents` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/employees/me/documents` | auth | ok | ok | ok |  |
| `DELETE` | `/api/v1/employees/me/documents/{document_id}` | auth | hidden | hidden | hidden |  |
| `GET` | `/api/v1/employees/me/documents/{document_id}/download` | auth | hidden | hidden | hidden |  |
| `POST` | `/api/v1/employees/{employee_id}/documents` | auth | deny | deny | ok |  |
| `POST` | `/api/v1/employees/{employee_id}/documents/payslip` | auth | deny | deny | ok |  |
| `POST` | `/api/v1/employees` | auth | deny | deny | ok |  |
| `PUT` | `/api/v1/employees/{employee_id}` | auth | deny | deny | ok |  |
| `DELETE` | `/api/v1/employees/{employee_id}` | auth | deny | deny | ok |  |
| `PATCH` | `/api/v1/employees/{employee_id}/reactivate` | auth | deny | deny | ok |  |
| `PUT` | `/api/v1/employees/{employee_id}/job-title` | auth | deny | deny | ok |  |
| `GET` | `/api/v1/employees/projects/catalog` | auth | deny | ok | ok |  |
| `POST` | `/api/v1/employees/projects/catalog` | auth | deny | deny | ok | step 6 (decision 5): catalogue creation is admin-only |
| `PUT` | `/api/v1/employees/projects/catalog/{project_id}/status` | auth | deny | deny | ok | step 6 (X-3): manager only while they or a direct report are assigned; nobody is in the fixture |
| `GET` | `/api/v1/employees/{employee_id}/projects` | auth | deny | deny | ok |  |
| `POST` | `/api/v1/employees/{employee_id}/projects` | auth | deny | deny | ok |  |
| `DELETE` | `/api/v1/employees/{employee_id}/projects/{project_id}` | auth | deny | deny | hidden | admin: no such assignment |
| `GET` | `/api/v1/attendance/me` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/attendance/today` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/attendance/clock-in` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/attendance/clock-out` | auth | rejected | rejected | rejected | not clocked in on a fresh DB |
| `GET` | `/api/v1/finance/payroll/me` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/finance/statutory/me` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/finance/profile/me` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/leaves/balances/me` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/leaves/requests/me` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/leaves/requests` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/leaves/requests/pending` | auth | deny | ok | ok |  |
| `POST` | `/api/v1/leaves/requests/{request_id}/approve` | auth | deny | hidden | ok | manager: outside team -> identical not-found (B-05) |
| `POST` | `/api/v1/leaves/requests/{request_id}/reject` | auth | deny | hidden | ok |  |
| `GET` | `/api/v1/announcements` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/announcements` | auth | deny | ok | ok | step 6 (decision 5): a manager's announcement is TEAM-scoped; asking for ORG is 403 |
| `GET` | `/api/v1/polls` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/polls` | auth | deny | ok | ok |  |
| `POST` | `/api/v1/polls/{poll_id}/vote` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/polls/{poll_id}/results` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/calendar/team` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/calendar/holidays` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/calendar/birthdays` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/org/tree` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/tickets` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/tickets` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/tickets/{ticket_id}/assign` | auth | deny | ok | ok |  |
| `POST` | `/api/v1/tickets/{ticket_id}/status` | auth | deny | ok | ok | A-07 fixed (step 5, decision 4): the EMPLOYEE owner may not change status; assignees may |
| `POST` | `/api/v1/tickets/{ticket_id}/onboarding-tasks` | auth | deny | rejected | rejected | IT ticket, not ONBOARDING |
| `POST` | `/api/v1/tickets/{ticket_id}/onboarding-tasks/{task_id}/complete` | auth | hidden | hidden | hidden |  |
| `GET` | `/api/v1/hr-policies` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/hr-policies/upload` | auth | deny | ok | ok | MANAGER upload is a SUBMITTED draft (ADMIN-only publication) |
| `POST` | `/api/v1/hr-policies/{policy_id}/publish` | auth | deny | deny | rejected | policy 1 is already PUBLISHED |
| `POST` | `/api/v1/hr-policies/{policy_id}/reject` | auth | deny | deny | rejected |  |
| `GET` | `/api/v1/hr-policies/{policy_id}/events` | auth | deny | deny | ok |  |
| `GET` | `/api/v1/hr-policies/{policy_id}/download` | auth | ok | ok | ok |  |
| `POST` | `/api/v1/chat` | auth | rejected | rejected | rejected |  |
| `POST` | `/api/v1/chat/policy` | auth | rejected | rejected | rejected |  |
| `POST` | `/api/v1/chat/sql` | auth | rejected | rejected | rejected |  |
| `POST` | `/api/v1/chat/actions` | auth | rejected | rejected | rejected |  |
| `POST` | `/api/v1/chat/actions/confirm` | auth | rejected | rejected | rejected |  |
| `GET` | `/api/v1/chat/permissions` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/chat/conversations` | auth | ok | ok | ok |  |
| `GET` | `/api/v1/chat/conversations/{conversation_id}` | auth | hidden | hidden | hidden |  |
| `DELETE` | `/api/v1/chat/conversations/{conversation_id}` | auth | hidden | hidden | hidden |  |
| `POST` | `/api/v1/chat/sessions` | auth | rejected | rejected | rejected |  |
| `POST` | `/api/v1/chat/sessions/{session_id}/messages` | auth | rejected | rejected | rejected |  |
| `GET` | `/api/v1/ai/audit` | auth | ok | ok | ok | own events for non-admins |
| `GET` | `/api/v1/ai/audit/metrics` | auth | deny | deny | ok |  |

### 1.1 Object-level scope evidence (beyond the role gate)

| Endpoint group | Object rule | Evidence | Class |
|---|---|---|---|
| `/auth/*` | login throttle; password change: own account, current password, throttle | `characterization/test_auth.py`, `rbac/test_password_change.py`, `test_framework_security.py` | AUTO |
| `GET /employees`, `GET /employees/{id}`, profile pictures | EMPLOYEE own record; MANAGER self + direct reports; ADMIN all; out of scope = unknown 404 | `rbac/test_directory_scope.py` | AUTO |
| `GET /employees/skill-search` | MANAGER/ADMIN, approved fields only, active employees | `rbac/test_skill_search.py` | AUTO |
| `PUT /employees/{id}`, `DELETE /employees/{id}`, payslip and document uploads | no self role/salary/bank/PAN/password/payslip; last active admin protected | `characterization/test_admin_self_actions.py`, `rbac/test_last_active_admin.py` | AUTO (concurrency on SQLite only) |
| `/employees/{id}/projects*`, `/employees/projects/catalog*` | manager self + reports; catalogue create ADMIN; status: manager or an active direct report assigned | `characterization/test_employees.py`, `rbac/test_project_scope.py` | AUTO |
| `/employees/me*` | caller only | route suite (other ids hidden), `characterization/test_employees.py` | AUTO |
| `/leaves/*` | self; approve/reject: manager direct reports, admin anyone but self | `characterization/test_leaves.py` | AUTO |
| `/tickets/*` | S-01a manager scope; status: assignee, manager in scope, admin (step 5) | `characterization/test_tickets.py`, `rbac/test_ticket_status.py` | AUTO |
| `/announcements`, `/polls*` | ORG/TEAM audience; hidden poll = unknown 404 | `rbac/test_content_audience.py`, `rbac/test_content_audience_migration.py` | AUTO |
| `/hr-policies*` | publication workflow; ALL/MANAGERS/ADMINS audience | `characterization/test_hr_policy_publication.py`, `characterization/test_hr_policies.py`, `rbac/test_policy_audience.py` | AUTO |
| `/attendance/*`, `/finance/*` | every query filtered by `employee_id == current_user.id` | code read 2026-10-17 (`attendance.py`, `finance.py`); role gate in the route suite | MANUAL (S-04: no dedicated tests) |
| `GET /org/tree` | active employees only; id, name, role, manager_id | code read 2026-10-17 (`org.py`) | MANUAL (S-04) |
| `GET /calendar/team` | **ADMIN and MANAGER: every employee; EMPLOYEE: own department (self if none)**; names, roles, approved-leave and WFH markers | code read 2026-10-17 (`team_calendar.py`) | MANUAL; **finding S-06** (wider than decision 1's manager scope; needs a product decision) |
| `GET /calendar/birthdays`, `/calendar/holidays` | birthdays: name, department, next date (no birth year); includes inactive employees | code read 2026-10-17 | MANUAL (S-04) |
| `/chat`, `/chat/policy`, `/chat/sql`, `/chat/actions*`, `/chat/conversations*`, `/chat/permissions` | AI authorization boundary, per-role API tests, conversation ownership, policy audiences, action parity, confirmation re-check | `tests/ai/**`, e.g. `actions/test_action_api.py`, `actions/test_action_parity.py`, `actions/test_confirmation_parity.py`, `rag/test_policy_audience_rag.py`, `conversation/test_policy_replay.py`, `sql/test_read_path_parity.py` | AUTO (offline, scripted models) |
| `/ai/audit*` | non-admins: own events only; metrics ADMIN | `tests/ai/audit/test_audit_api.py` | AUTO |

The `rejected` cells for `/chat*` in the generated table are the suite's minimal request bodies being refused by
validation; the role outcomes of those endpoints are covered by the AI API tests above.

## 2. Frontend pages (21 `page.tsx` files)

Source: `frontend/lib/permissions.ts` (one map; `frontend/tests/page-access.test.tsx` fails if any `app/**/page.tsx`
has no row). Frontend guard: **AUTO** (component tests in jsdom). Real-browser behaviour: **NOT VERIFIED** (no
Playwright; Phase 3 workstream 3.7 not approved). The backend column is what actually protects the data.

| Page | EMPLOYEE | MANAGER | ADMIN | Primary API (backend rule, AUTO via §1) |
|---|---|---|---|---|
| `/`, `/login` | public | public | public | `POST /auth/login` |
| `/dashboard` | ✓ | ✓ | ✓ | self-service endpoints |
| `/copilot`, `/copilot/{conversation}` | ✓ | ✓ | ✓ | `/chat*` (AI boundary) |
| `/me`, `/me/documents` | ✓ | ✓ | ✓ | `/employees/me*` |
| `/finance`, `/attendance`, `/leaves` | ✓ | ✓ | ✓ | self-only endpoints |
| `/announcements`, `/polls` | ✓ | ✓ | ✓ | audience-filtered lists (step 6) |
| `/hr-policies` | ✓ | ✓ | ✓ | audience-filtered list and download (step 7) |
| `/team-calendar` | ✓ | ✓ | ✓ | `GET /calendar/team` (see S-06) |
| `/tickets` | ✓ | ✓ | ✓ | scoped ticket list (S-01a) |
| `/organization` | ✓ | ✓ | ✓ | `GET /org/tree` (limited chart) |
| `/employees` | — | ✓ | ✓ | `GET /employees` (EMPLOYEE 403) |
| `/employees/projects` | — | ✓ | ✓ | catalogue and assignments (MANAGER/ADMIN) |
| `/employees/payslips` | — | ✓ | ✓ | payslip upload (scoped; no self upload) |
| `/employees/new` (create, edit) | — | — | ✓ | `POST /employees`, `PUT /employees/{id}` (ADMIN) |
| `/admin/ai-audit`, `/admin/ai-usage` | — | — | ✓ | `/ai/audit`, `/ai/audit/metrics` (org-wide: ADMIN) |

Guard behaviour (AUTO): restricted pages never mount for other roles; the role comes from the server profile (a forged
`hrms_role` cookie is ignored); failure to load the role denies; the sidebar uses the same map; the middleware login
check now covers `/organization`.

## 3. Copilot capabilities vs the business API

Authoritative: `docs/ai_permissions_matrix.md` §0 (capabilities) and §3.0a (action parity). Evidence: AUTO
(`tests/ai/authorization/test_matrix.py`; `tests/ai/actions/test_action_parity.py` with the invariant *Copilot allowed
⇒ API allowed*; `tests/ai/actions/test_confirmation_parity.py`; `tests/ai/sql/test_read_path_parity.py`;
`tests/ai/sql/test_catalog_parity.py`; the API/Copilot policy-audience parity test in `tests/rbac/test_policy_audience.py`).

Approved deviations preserved: **E1** (no Copilot ticket-status change for any EMPLOYEE, including the assignee),
**E2** (owner-based manager ticket scope), and the employee project-catalogue restriction (assignment eval
`asg-sql-01` updated 2026-10-17).

## 4. Step 10 verification run (2026-10-17)

Backend 2778 passed (Python 3.11.17 container); offline evals 139/139; advisory-gate tests 25; frontend 152/152,
TypeScript 0 errors, Next.js build PASS; gitleaks full history PASS; migrations to head PASS (downgrade below `0016`
fails: S-07); Docker production smoke PASS; dependency advisory gate FAIL by design (`braces`). Details:
`release-readiness.md` §1, §2, §5 and §5a.

## 5. Not verified here

- Real-browser end-to-end flows (NOT VERIFIED: Playwright not approved).
- Live-model routing, RAG, SQL and action quality (NOT VERIFIED: no approved key or budget; offline evals use scripted models).
- Databases other than SQLite, and more than one API process (NOT VERIFIED; the app refuses multi-worker start-up).
- Production infrastructure: TLS proxy, monitoring, backups in the target environment (NOT VERIFIED).
