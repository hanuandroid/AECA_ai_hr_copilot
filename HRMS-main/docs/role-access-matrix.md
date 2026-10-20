# Role-access matrix

**Date:** 2026-10-20 · **Basis:** code at `1ce24e6` plus the uncommitted role-access test changes · **Release status:**
NOT YET PRODUCTION READY.

Every permission below is traced to its implementation and to the tests that exercise it. Nothing is inferred from
role names or from what the UI shows. Sources: `backend/app/services/auth.py`, `backend/app/api/v1/endpoints/*.py`,
`backend/app/services/ai/authorization/policy.py`, `frontend/lib/permissions.ts`, `frontend/middleware.ts`,
`backend/tests/rbac/test_route_permissions.py` (every API route × 4 identities, completeness-checked) and the new
browser suite `frontend/e2e/` (`docs/role-access-test-plan.md`).

**Result** = outcome of the runs on 2026-10-20 (`docs/role-access-test-report.md`): **PASS** (verified by an executed
test), **FAIL**, **NOT_TESTED** (no executed test for that layer), **NOT_APPLICABLE**.
Columns *Backend test* and *Browser/API E2E* name the evidence; "—" means none in that layer.

## 1. Identities and how roles are assigned

| Identity | Defined in | Assigned by | Notes |
|---|---|---|---|
| ADMIN, MANAGER, EMPLOYEE | `models/enums.py::Role`, column `employees.role` | ADMIN via `POST /employees`, `PUT /employees/{id}` (not on self; last active admin protected) | Role is **re-read from the database** on every request (`get_current_user`); the JWT `role` claim and the `hrms_role` cookie are never trusted |
| Team | `employees.manager_id` | not settable through the API (seed data only; ADMIN-created employees have no manager) | A MANAGER's team = active direct reports |
| INACTIVE (status) | `employees.status` | ADMIN deactivate/reactivate | Refused before any role check: login 401 `INVALID_CREDENTIALS` (same as a bad password), existing tokens 403 `USER_INACTIVE`. Since 2026-10-20 deactivation and reactivation also increment `token_version`, so tokens from before the deactivation stay invalid (401 `TOKEN_REVOKED`) after reactivation |
| Anonymous | — | — | 401 on every protected route; pages redirect to `/login` (server side) |

**Tenant / organisation isolation: NOT_APPLICABLE.** The application has one organisation; there is no tenant or
organisation id in any model. Isolation is self / team / organisation scope plus record ownership (sections 3–4).

## 2. Pages (21 `page.tsx` files) — UX only; the backend is the boundary

Client guard: `components/access/access-guard.tsx` + `lib/permissions.ts` (role from `GET /employees/me`). Anonymous
redirect: `middleware.ts` (cookie presence) or `redirect()` in `app/page.tsx`. A denied signed-in user sees an
"Access denied" view at the same URL (client side, no HTTP 403 for pages); the page's API calls are refused by the
backend (column *Backend refusal*).

| Page | ADMIN | MANAGER | EMPLOYEE | Anonymous | Backend refusal for denied roles | Backend test | Browser E2E | Result |
|---|---|---|---|---|---|---|---|---|
| `/` | → /dashboard | → /dashboard | → /dashboard | → /login | — | — | `pages.spec.ts` | PASS |
| `/login` | → /dashboard | → /dashboard | → /dashboard | form | — | — | `pages.spec.ts`, `auth.spec.ts` | PASS |
| `/dashboard`, `/copilot`, `/me`, `/me/documents`, `/finance`, `/attendance`, `/leaves`, `/announcements`, `/hr-policies`, `/polls`, `/team-calendar`, `/tickets`, `/organization` (13) | ✓ | ✓ | ✓ | → /login | n/a (data is scoped per role, §3) | route suite | `pages.spec.ts` (13 × 4) | PASS |
| `/employees` | ✓ | ✓ | denied | → /login | `GET /employees` 403 | route suite, `test_directory_scope.py` | `pages.spec.ts` | PASS |
| `/employees/projects` | ✓ | ✓ | denied | → /login | `GET /employees/projects/catalog` 403 | route suite | `pages.spec.ts` | PASS |
| `/employees/payslips` | ✓ | ✓ | denied | → /login | `GET /employees/{other}/projects` 403 (uploads: §3) | route suite | `pages.spec.ts` | PASS |
| `/employees/new` (create + edit) | ✓ | denied | denied | → /login | `POST /employees` 403 | route suite | `pages.spec.ts` | PASS |
| `/admin/ai-audit`, `/admin/ai-usage` | ✓ | denied | denied | → /login | `GET /ai/audit/metrics` 403 | `tests/ai/audit/` | `pages.spec.ts` | PASS |

Sidebar (13 links; EMPLOYEE has no "Employees"), role-dependent controls (Post Announcement, audience choice, Create
poll, Create Project, Add New Employee, Deactivate Employee, AI audit links): `navigation.spec.ts` — **PASS**.
A forged `hrms_role=ADMIN` cookie with an EMPLOYEE token does not open admin pages: `auth.spec.ts` — **PASS**.

## 3. Business API (80 method + path pairs) by feature

Outcome per identity: ok (2xx), deny (403), hidden (404, identical to an unknown id), auth (401), rejected
(400/409/422 after the role gate). The full per-route table is generated from the route suite in
`production-hardening/page-endpoint-inventory.md` §1; this section groups it and adds the record-level rules.

| Feature / operation | Endpoint | ADMIN | MANAGER | EMPLOYEE | Anon | Implementation | Backend test | Browser/API E2E | Result |
|---|---|---|---|---|---|---|---|---|---|
| Sign in / token | `POST /auth/login`, `/auth/token` | ok | ok | ok | ok | `endpoints/auth.py` (inactive = same 401) | `characterization/test_auth.py` | `auth.spec.ts` | PASS |
| Sign out everywhere | `POST /auth/logout` | ok (own) | ok (own) | ok (own) | auth | `auth.py`, `token_version` | `rbac/test_logout.py`, `test_token_revocation.py` | `auth.spec.ts` (old token → 401) | PASS |
| Change own password | `POST /auth/change-password` | own | own | own | auth | `auth.py` | `rbac/test_password_change.py` | — | PASS (backend) |
| Own profile read/update | `GET/PUT /employees/me` | own | own | own | auth | `employees.py::update_me` (fixed field list) | route suite; **new** `rbac/test_self_profile_fields.py` (privileged fields ignored) | `api.spec.ts` (role field ignored) | PASS |
| Directory list / search | `GET /employees` | all | self + reports | deny | auth | `employees.py` directory scope | `rbac/test_directory_scope.py` | `api.spec.ts` | PASS |
| Employee record | `GET /employees/{id}` | all | self + reports; others hidden | own only; others hidden | auth | same | same | `api.spec.ts` (incl. unknown id = out-of-scope) | PASS |
| Skill search | `GET /employees/skill-search` | ok | ok | deny | auth | `employees.py`, approved fields | `rbac/test_skill_search.py` | — | PASS (backend) |
| Create employee | `POST /employees` | ok (+ step-up) | deny | deny | auth | `require_roles(ADMIN)` | route suite, `rbac/test_step_up_api.py` | `api.spec.ts` (no account created), `step-up.spec.ts` | PASS |
| Update employee | `PUT /employees/{id}` | ok (step-up if password/role/email/bank/PAN change) | deny | deny | auth | `require_roles(ADMIN)`, self-action guards | route suite, `characterization/test_admin_self_actions.py`, `test_step_up_api.py` | `api.spec.ts` (state unchanged; admin self role/bank refused), `step-up.spec.ts` | PASS |
| Deactivate / reactivate | `DELETE /employees/{id}`, `PATCH …/reactivate` | ok (+ step-up; not self; last admin protected) | deny | deny | auth | `require_roles(ADMIN)`, `_remove_admin_guarded` | route suite, `rbac/test_last_active_admin.py`, `test_step_up_api.py` | `api.spec.ts`, `step-up.spec.ts` | PASS (last-admin race: backend only) |
| Job title | `PUT /employees/{id}/job-title` | ok | reports only | deny | auth | `employees.py` | route suite, `characterization/test_employees.py` | `api.spec.ts` (outsider refused) | PASS |
| Documents / payslips for others | `POST /employees/{id}/documents[/payslip]` | ok | reports only (no self payslip) | deny | auth | `employees.py` | route suite, `test_admin_self_actions.py` | — | PASS (backend) |
| Own documents | `/employees/me/documents*` | own | own | own | auth | `employees.py` | route suite | — | PASS (backend) |
| Projects catalogue | `GET/POST /employees/projects/catalog`, `PUT …/status` | ok | read; status only when the team is assigned; no create | deny | auth | `employees.py` | `rbac/test_project_scope.py` | `navigation.spec.ts` (Create Project hidden for MANAGER) | PASS |
| Project assignment | `GET/POST/DELETE /employees/{id}/projects*` | ok | self + reports | deny | auth | `employees.py` | route suite, `test_project_scope.py` | `pages.spec.ts` (EMPLOYEE refused) | PASS |
| Leave request / balances | `/leaves/requests`, `/leaves/*/me` | own | own | own | auth | `leaves.py` | `characterization/test_leaves.py` | `api.spec.ts` | PASS |
| Leave approve / reject | `POST /leaves/requests/{id}/approve|reject` | anyone but self | direct reports; others hidden | deny | auth | `leaves.py` | route suite, `test_leaves.py` | `api.spec.ts` (outsider leave hidden for MANAGER, still PENDING; team leave approved) | PASS |
| Tickets list / create | `GET/POST /tickets` | all | own + team scope | own | auth | `tickets.py` (S-01a) | `characterization/test_tickets.py` | `api.spec.ts` | PASS |
| Ticket assign / status | `POST /tickets/{id}/assign|status` | ok | team scope | owner: deny; assignee: status ok | auth | `tickets.py` | `rbac/test_ticket_status.py` | `api.spec.ts` (owner and outsider refused; status unchanged) | PASS |
| Announcements / polls create | `POST /announcements`, `/polls` | ORG (+ step-up) or TEAM | TEAM only (ORG 403) | deny | auth | `announcements.py`, `polls.py`, `content_audience.py` | `rbac/test_content_audience.py`, `test_step_up_api.py` | `navigation.spec.ts`, `step-up.spec.ts` | PASS |
| Announcements / polls read, vote | `GET …`, `POST /polls/{id}/vote` | all | ORG + own team | ORG + manager's team | auth | same | same | `pages.spec.ts` | PASS |
| HR policies list / download | `GET /hr-policies`, `…/{id}/download` | ALL+MANAGERS+ADMINS | ALL+MANAGERS | ALL | auth | `hr_policies.py`, `policy_audience.py` | `rbac/test_policy_audience.py` | `api.spec.ts` (list + download hidden outside audience) | PASS |
| HR policy upload / publish | `POST /hr-policies/upload`, `…/publish|reject`, `GET …/events` | upload = published; publish/reject | upload (audience must include self), review by ADMIN | deny | auth | same | `characterization/test_hr_policy_publication.py` | `api.spec.ts` (MANAGER → ADMINS 403; MANAGER publish 403) | PASS |
| Attendance, finance | `/attendance/*`, `/finance/*` | own only | own only | own only | auth | self-filtered queries | `rbac/test_self_service_scope.py` | `pages.spec.ts` | PASS |
| Calendar, org chart | `/calendar/*`, `/org/tree` | per S-06 / S-08 rules | same | same | auth | `team_calendar.py`, `org.py` | `test_team_calendar_scope.py`, `test_directory_scope.py` | `pages.spec.ts` | PASS |

Inactive users: every row above returns 403 `USER_INACTIVE` for an existing token (`auth.spec.ts`,
`characterization/test_auth.py`) — **PASS**. Anonymous 401 on 17 representative protected groups — `auth.spec.ts`
**PASS**; all 80 routes — route suite **PASS**.

## 4. AI Copilot (stricter than the business API by design, D7)

| Capability / operation | ADMIN | MANAGER | EMPLOYEE | Implementation | Backend test | API E2E | Result |
|---|---|---|---|---|---|---|---|
| Capability set (`GET /chat/permissions`) | 24 | 17 | 6 | `authorization/policy.py` | `tests/ai/authorization/test_matrix.py` | `api.spec.ts` (exact sets) | PASS |
| Policy RAG audience | ALL+MANAGERS+ADMINS | ALL+MANAGERS | ALL | `policy_audiences(ctx)` filter before scoring | `tests/ai/rag/test_policy_audience_rag.py` | — (answer needs a model) | PASS (backend); E2E NOT_TESTED |
| Text-to-SQL scope | ORG | TEAM | SELF | `sql/scope.py` rewrite + read-only executor | `tests/ai/sql/` | — (SQL generation needs a model) | PASS (backend); E2E NOT_TESTED |
| Rule-based refusals (salary, bank, payroll, DROP, delete all) | refused | refused | refused | `orchestration/classifier.py` | `tests/ai/hardening/`, offline evals | `api.spec.ts` (5 prompts, no model call) | PASS |
| Action proposal / confirmation | per §0 matrix | per matrix | per matrix | `actions/`, re-check at confirm, existing API with user token | `tests/ai/actions/` (parity, replay, foreign confirmation) | unknown id identical for all roles (`api.spec.ts`); real cross-user confirmation NOT_TESTED in E2E (needs a model proposal) | PASS (backend) |
| Conversation ownership | own only | own only | own only | `ConversationService.get_user_conversation` | `tests/ai/conversation/` | `api.spec.ts` (owner ok; MANAGER and ADMIN 404; archive by another user 404) | PASS |
| AI audit read | all events | own events | own events | `ai_audit.py::audit_scope_for` | `tests/ai/audit/test_audit_api.py` | `api.spec.ts` (another person's events 403; own only) | PASS |
| AI usage metrics | ok | deny | deny | AUDIT_READ_ORG | same | `api.spec.ts`, `pages.spec.ts` | PASS |
| Documented Copilot/API differences | — | E2: owner-based ticket scope | E1: no ticket status change even as assignee | `policy.py` | `tests/ai/actions/test_action_parity.py` | capability sets (`api.spec.ts`) | PASS |

Answer quality of any live model: **NOT_TESTED** (no live model was called).

## 5. Step-up (G-11, `STEP_UP_ENFORCEMENT=log`)

| Operation | UI dialog | Header sent | Log mode outcome | Enforce outcome | Backend test | Browser E2E | Result |
|---|---|---|---|---|---|---|---|
| Create employee | yes (after form validation) | yes | succeeds | 403/429 without the right password | `rbac/test_step_up_api.py` | `step-up.spec.ts` | PASS |
| Deactivate / reactivate | yes | yes | succeeds | same | same | `step-up.spec.ts` | PASS |
| Sensitive employee edit | only when password/role/email/bank/PAN change | yes | succeeds | same | same | `step-up.spec.ts` (job-title change: no dialog, no header) | PASS |
| ORG announcement / poll | yes; TEAM: no | yes; TEAM: no | wrong or missing password **still succeeds**, logged `auth.step_up.not_satisfied` (`INVALID` / `MISSING`) | same | same | `step-up.spec.ts` (log lines checked; password never in logs) | PASS |

## 6. Audit of unauthorized attempts (current behaviour, documented — not a requirement found in the docs)

| Attempt | Recorded where | Evidence | Result |
|---|---|---|---|
| AI-path refusal or capability denial | `ai_audit_events` (e.g. `AUTHORIZATION_DENIED`, refusals) | `tests/ai/audit/`; `api.spec.ts` (refusals appear in the employee's own events) | PASS |
| Business-API 403/404 (e.g. MANAGER deactivating someone) | **not** in any table; some are log lines (`security.self_action_rejected`, `security.last_admin_protected`, `auth.step_up.not_satisfied`, `auth.password_change_failed`) | `api.spec.ts` (AI audit counts unchanged after business refusals) | PASS (behaviour as implemented) |

## 7. Ambiguous or open policy points (documented, not decided)

1. Business-API refusals are not persisted for audit (only log lines). No requirement says they must be.
2. In `log` mode a wrong step-up password does not block (approved rollout setting; G-11 enforce not approved).
3. Approved Copilot/API differences E1, E2 and `asg-sql-01` (employees see only their own projects via the Copilot).
4. Employee self-service PAYSLIP-type documents: open question in `production-hardening/authorization-matrix.md`.
5. Pages are protected client-side (Access denied view) plus server-side cookie presence; a signed-in user without
   the role gets no HTTP 403 for the page itself — the data calls are refused by the backend (verified).
6. The team relation (`manager_id`) cannot be set through the API; ADMIN-created employees have no manager.
