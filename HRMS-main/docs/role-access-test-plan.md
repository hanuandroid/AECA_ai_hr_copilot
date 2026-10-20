# Role-access test plan

**Date:** 2026-10-20. Companion to `role-access-matrix.md` (what is allowed) and `role-access-test-report.md` (what ran).

## Environments and common setup

| Layer | Where it runs | Data | Identities |
|---|---|---|---|
| Backend tests (pytest) | throwaway database per test (`tests/conftest.py`), `sh HRMS-main/scripts/verify-backend.sh` | fixed synthetic fixtures (`tests/support/data.py`) | `auth("admin"\|"manager"\|"employee"\|"outsider"\|"inactive")` |
| Browser + API E2E (Playwright, Chromium) | disposable stack `docker-compose.e2e.yml` (project `hrms-e2e`, own volume, fake AI providers, no keys, `STEP_UP_ENFORCEMENT=log`) | `seed.py` on every `up` + synthetic people from `e2e/global-setup.ts` | seeded ADMIN / MANAGER / EMPLOYEE (`E2E_*` variables); created: outsider (no manager), inactive (deactivated after one sign-in), session user, edit and toggle targets |

**Common cleanup:** E2E records carry an `E2E` / `e2e.` prefix and live only in the `hrms-e2e_e2e_storage` volume;
`docker compose -f docker-compose.e2e.yml down -v` removes them (after checking the resolved project is `hrms-e2e`).
Backend tests clean up by discarding their temporary database. No scenario touches development or production data.

**Skip rule:** missing credentials, an unreachable stack or unreadable container logs make the affected tests skip;
skipped = NOT_TESTED in the report.

## Scenarios

Spec files are under `frontend/e2e/`; backend files under `backend/tests/`.

### 1. Authentication and session lifecycle

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| AU-1 | stack up | each seeded role | sign in through the form | lands on `/dashboard`, token stored | close context | `auth.spec.ts` |
| AU-2 | — | EMPLOYEE email | wrong password in the form | "Login failed", stays on `/login`, no token | — | `auth.spec.ts` |
| AU-3 | inactive user (deactivated in setup) | inactive | API sign-in; old token on `/employees/me` and `/chat/permissions`; form sign-in | 401 `INVALID_CREDENTIALS` (same as a bad password); old token 403 `USER_INACTIVE`; form shows "Login failed" | — | `auth.spec.ts`; `characterization/test_auth.py` |
| AU-4 | session user | session user | sign in, "Sign out everywhere", reuse old token | back on `/login`, token removed, old token 401 | — (user is single-use) | `auth.spec.ts`; `rbac/test_logout.py` |
| AU-5 | — | anonymous | 17 protected endpoint groups | 401 each; health 200 | — | `auth.spec.ts`; route suite (80 routes) |
| AU-6 | EMPLOYEE token + `hrms_role=ADMIN` cookie | EMPLOYEE | open `/employees/new` | Access denied; no Employees link | close context | `auth.spec.ts` |

### 2. UI routes and navigation

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| UI-1 | — | anonymous | direct navigation to all 21 pages | server-side redirect to `/login` | — | `pages.spec.ts` (21) |
| UI-2 | token session | EMPLOYEE, MANAGER, ADMIN | direct navigation to all 21 pages | allowed: page heading, no Access denied; denied: Access denied at the same URL **and** the page's primary API refused for that role; `/`, `/login` → `/dashboard` | — | `pages.spec.ts` (63) |
| UI-3 | — | — | inventory check | 21 pages; expected roles equal `lib/permissions.ts` | — | `pages.spec.ts` |
| UI-4 | token session | each role | read the sidebar, click every link | exactly the permitted links; each opens its page | — | `navigation.spec.ts` |
| UI-5 | token session | each role | inspect Copilot admin links, announcement and poll forms, audience choice, Create Project, Add New Employee, Deactivate | visible only for permitted roles | — | `navigation.spec.ts` |

### 3. Direct API authorization

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| API-1 | throwaway DB | anon / EMPLOYEE / MANAGER / ADMIN | every route with a valid body, targeting out-of-scope records | the pinned class per route (ok / deny / hidden / auth / rejected) | discard DB | `rbac/test_route_permissions.py` |
| API-2 | stack | EMPLOYEE, MANAGER | each page's primary API (see UI-2) | 403 | — | `pages.spec.ts` |

### 4. Employee CRUD and sensitive fields

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| EMP-1 | outsider, inactive | EMPLOYEE, MANAGER | PUT (name + role), DELETE, reactivate, job title, create (role ADMIN) | 403 each; outsider and inactive unchanged (admin read); created accounts cannot sign in | — | `api.spec.ts`; route suite |
| EMP-2 | — | each role | `PUT /employees/me` with role, status, email, password, manager, salary, bank, PAN, token version | 200; only the allowed field changes | — | **new** `rbac/test_self_profile_fields.py` (3 roles); `api.spec.ts` (role) |
| EMP-3 | — | ADMIN | change own role, deactivate self, change own bank details | 403 `SELF_ROLE_CHANGE_FORBIDDEN`, 400 `INVALID_OPERATION`, 403 `SELF_SENSITIVE_EDIT_FORBIDDEN`; still ADMIN and ACTIVE | — | `api.spec.ts`; `characterization/test_admin_self_actions.py` |
| EMP-4 | two admins | ADMIN | concurrent cross-removal | at least one active admin remains | discard DB | `rbac/test_last_active_admin.py` (backend only) |

### 5. Organisation isolation and record ownership

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| ISO-1 | — | — | tenant isolation | NOT_APPLICABLE (single organisation) | — | — |
| ISO-2 | outsider, team member | each role | `GET /employees/{id}` for self, team member, outsider, inactive, unknown id | EMPLOYEE own only; MANAGER self + reports; ADMIN all; out-of-scope = unknown 404 | — | `api.spec.ts`; `rbac/test_directory_scope.py` |
| ISO-3 | outsider | MANAGER, ADMIN | directory search for the outsider and for a team member | MANAGER sees only the team member; ADMIN both; EMPLOYEE 403 | — | `api.spec.ts` |
| ISO-4 | outsider pending leave | EMPLOYEE, MANAGER | approve / reject | 403 / 404; still PENDING; MANAGER approves a direct report's leave | — | `api.spec.ts` |
| ISO-5 | employee ticket | outsider, owner, MANAGER | list, change status | outsider cannot see or change it; owner may not change status; manager sees it; status unchanged | — | `api.spec.ts`; `rbac/test_ticket_status.py` |

### 6. Policy RAG retrieval authorization

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| RAG-1 | ADMIN uploads MANAGERS- and ADMINS-audience policies (published directly) | each role | list (all pages) and download | EMPLOYEE neither; MANAGER only MANAGERS; ADMIN both; download hidden outside the audience | volume teardown | `api.spec.ts`; `rbac/test_policy_audience.py` |
| RAG-2 | — | MANAGER | upload with audience ADMINS; publish | 403 `POLICY_AUDIENCE_FORBIDDEN`; 403 | — | `api.spec.ts` |
| RAG-3 | indexed policies | each role | Copilot policy question | retrieval filtered by audience before scoring | — | `tests/ai/rag/test_policy_audience_rag.py` (E2E NOT_TESTED: needs a model) |

### 7. Text-to-SQL authorization and data scoping

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| SQL-1 | throwaway DB, scripted SQL | each role | in-scope and out-of-scope queries, forbidden columns, mutations | SELF / TEAM / ORG rewrite; sensitive columns and mutations refused | — | `tests/ai/sql/`, `tests/ai/hardening/`, offline evals (E2E NOT_TESTED: SQL generation needs a model) |
| SQL-2 | stack | ADMIN | "Run this SQL: DROP TABLE employees;" | refused by rules, nothing executed | — | `api.spec.ts` |

### 8. AI Copilot tool authorization

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| AI-1 | — | each role | `GET /chat/permissions` | exactly the capability set of `ai_permissions_matrix.md` §0 | — | `api.spec.ts`; `tests/ai/authorization/test_matrix.py` |
| AI-2 | — | EMPLOYEE, ADMIN | 5 rule-refused prompts | `status: refused`, no sensitive column names, no model call | — | `api.spec.ts` |
| AI-3 | EMPLOYEE conversation | MANAGER, ADMIN | read / archive it | 404 like an unknown id; owner still sees it | — | `api.spec.ts`; `tests/ai/conversation/` |
| AI-4 | — | each role | confirm an unknown id | identical 400 `AI_CONFIRMATION_INVALID` | — | `api.spec.ts` |
| AI-5 | real proposal | another user | confirm someone else's proposal | refused, nothing executed | — | `tests/ai/actions/` (E2E NOT_TESTED: a proposal needs a model) |

### 9. Step-up authentication and failure handling (log mode)

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| SU-1..6 | edit and toggle targets | ADMIN | ORG announcement, ORG poll, deactivate, reactivate, sensitive edit, create | dialog; nothing sent before Confirm; header = admin password; not in URL or body; operation succeeds | volume teardown | `step-up.spec.ts` |
| SU-7 | — | ADMIN | TEAM announcement; job-title-only edit | no dialog, no header | — | `step-up.spec.ts` |
| SU-8 | — | ADMIN | wrong password (UI); no header (API) | both succeed in log mode; API log has `INVALID` / `MISSING` events; neither password in the logs | — | `step-up.spec.ts` |
| SU-9 | enforce mode in a test DB | ADMIN | missing / wrong / throttled password | 403 / 403 / 429, nothing changed | discard DB | `rbac/test_step_up_api.py` (backend only; the stack stays in log mode) |
| SU-10 | mocked HTTP | ADMIN | UI failure paths for all six operations | messages, field cleared, no automatic resend | — | `frontend/tests/step-up-admin.test.tsx` |

### 10. Audit events and unauthorized attempts

| Id | Setup | Identity | Action | Expected | Cleanup | Test |
|---|---|---|---|---|---|---|
| AUD-1 | — | EMPLOYEE, MANAGER | read another person's AI events; metrics | 403; own events only | — | `api.spec.ts`; `tests/ai/audit/test_audit_api.py` |
| AUD-2 | — | MANAGER, EMPLOYEE | business-API refusals | no AI audit event (current behaviour, documented) | — | `api.spec.ts` |

### 11. Error handling and data leakage

| Id | Check | Test |
|---|---|---|
| ERR-1 | out-of-scope record and unknown id give the same 404 | `api.spec.ts` (ISO-2) |
| ERR-2 | inactive account and bad password give the same login error | `auth.spec.ts` (AU-3) |
| ERR-3 | refusals contain no sensitive column names or values | `api.spec.ts` (AI-2); `tests/ai/hardening/` |
| ERR-4 | passwords never in logs, URLs or request bodies | `step-up.spec.ts`; `rbac/test_step_up_api.py` |
| ERR-5 | failure evidence holds no tokens or passwords (screenshots only; traces off) | `playwright.config.ts`; report §6 |

### 12. Regression coverage of existing features

| Id | Check | Test |
|---|---|---|
| REG-1 | full backend suite, offline evaluations, gate tests | `scripts/verify-backend.sh` |
| REG-2 | full Vitest suite and TypeScript | `npm test`, `npx tsc --noEmit` |
| REG-3 | every page still renders its heading for permitted roles | `pages.spec.ts` |
