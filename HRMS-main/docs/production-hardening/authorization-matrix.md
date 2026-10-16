# Business API authorization matrix (S-01 closure, S-02 admin self-actions)

**Date:** 2026-10-14 · **Scope:** all 60 business routes under `/api/v1` (AI routes are covered by `ai_permissions_matrix.md` and the Phase 2 report).
**Method:** code reading of every handler, plus the automated tests named in the Evidence column.
**Status:** NOT YET PRODUCTION READY. Items marked **DECISION** or **S-02** need approval before any change.

Legend:
- **Role gate:** `any` means any authenticated ACTIVE user. Inactive users are rejected for every route (403 `USER_INACTIVE`, Phase 1).
- **Object scope:** which records the caller can act on.
- **Evidence:** test file under `backend/tests/`. "code" means reviewed by reading only; no dedicated test.

## 1. Route matrix

### Auth and health
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `POST /auth/change-password` | any authenticated | Own account only; current password required; throttled per account (step 4, §2) | `rbac/test_password_change.py` |
| `POST /auth/login`, `POST /auth/token` | public | Inactive accounts get 401 identical to a wrong password; throttled 10/min per IP + account | `characterization/test_auth.py`, `ai/test_limits.py` |
| `GET /health` | public | — | `test_infrastructure.py` |

### Employees (26 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /employees` | ADMIN, MANAGER (EMPLOYEE 403) | **RBAC step 2:** ADMIN all active employees; MANAGER self + direct reports; directory fields only | `rbac/test_directory_scope.py`, `rbac/test_route_permissions.py` |
| `GET /employees/departments` | any | Department catalogue | code |
| `GET /employees/skill-search` | ADMIN, MANAGER (EMPLOYEE 403) | **RBAC step 3:** restricted organisation-wide skill search; fields employee_id, employee_name, department, skill, level; active employees; does not widen list or detail | `rbac/test_skill_search.py`, `ai/sql/test_skill_search_path.py` |
| `GET /employees/{id}` | any (scoped) | **RBAC step 2:** EMPLOYEE own record; MANAGER self + direct reports; ADMIN anyone; out of scope = the same 404 as unknown. Fields: financial / government-ID self + ADMIN; phone / address / blood type also direct manager (B-01) | `characterization/test_employees.py` |
| `POST /employees` | ADMIN | Any role may be assigned | code |
| `PUT /employees/{id}` | ADMIN | Any employee; on own record no role, salary/bank/PAN or password change; last active admin cannot be demoted (**S-02 fixed, step 4**, §2) | `characterization/test_admin_self_actions.py` |
| `DELETE /employees/{id}` (deactivate), `PATCH …/reactivate` | ADMIN | Any employee; deactivating self is prohibited (400) | `characterization/test_employees.py`, `test_admin_self_actions.py` |
| `GET/PUT /employees/me`, profile picture (`/me`), `/me/job-history` | any | Self only. Self may change name, phone, address, blood type, occupancy, DOB | code |
| `GET /employees/{id}/profile-picture` | any | **RBAC step 2:** only employees visible on the org chart (active); inactive/unknown = same 404 | `rbac/test_directory_scope.py` |
| `GET/POST /employees/me/documents`, `DELETE /me/documents/{id}`, `GET /me/documents/{id}/download` | any | Owner only | code |
| `POST /employees/{id}/documents` | ADMIN, MANAGER | Manager: direct reports + self (S-01b). ADMIN: anyone | `characterization/test_employees.py` |
| `POST /employees/{id}/documents/payslip` | ADMIN, MANAGER | Manager: direct reports only, never self (S-01c). ADMIN: anyone except self (**S-02 fixed, step 4**) | `characterization/test_employees.py`, `test_admin_self_actions.py` |
| `PUT /employees/{id}/job-title` | ADMIN, MANAGER | Manager: direct reports only (S-01d). ADMIN: anyone, including self | `characterization/test_employees.py` |
| `GET /employees/{id}/projects` | ADMIN, MANAGER | Manager: self + direct reports (S-01e) | `characterization/test_employees.py` |
| `POST /employees/{id}/projects`, `DELETE …/projects/{pid}` | ADMIN, MANAGER | Manager: self + direct reports (B-05) | `characterization/test_employees.py` |
| `GET/POST /employees/projects/catalog`, `PUT …/catalog/{id}/status` | ADMIN, MANAGER | **Organisation-wide**: any manager creates projects and changes any project's status (**DECISION**, §3) | code |

### Leaves (6 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /leaves/balances/me`, `GET /leaves/requests/me`, `POST /leaves/requests` | any | Self | `characterization/test_leaves.py` |
| `GET /leaves/requests/pending` | any (empty for employees) | What the caller may decide: manager → direct reports; ADMIN → everyone but self | `characterization/test_leaves.py` |
| `POST /leaves/requests/{id}/approve`, `…/reject` | manager / ADMIN logic | Never own; manager outside direct reports → 404 identical to unknown | `characterization/test_leaves.py` |

### Tickets (6 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /tickets`, `POST /tickets` | any | Employee: raised by or assigned to self. Manager: raised by or assigned to self, or raised by direct reports (S-01a). ADMIN: all | `characterization/test_tickets.py` |
| `POST /tickets/{id}/assign`, `…/status`, `…/onboarding-tasks`, `…/complete` | role logic in handler | Out-of-scope → 404 identical to unknown; manager assignees limited to self or direct reports | `characterization/test_tickets.py` |

### HR policies (now 6 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /hr-policies` | any | PUBLISHED for everyone; managers also see their own submissions; ADMIN sees all (optional `status` filter cannot widen visibility) | `characterization/test_hr_policy_publication.py` |
| `POST /hr-policies/upload` | ADMIN, MANAGER | **Approved 2026-10-14:** ADMIN upload → PUBLISHED; MANAGER upload → SUBMITTED draft | same |
| `POST /hr-policies/{id}/publish`, `…/reject` | **ADMIN** | SUBMITTED only (else 409) | same |
| `GET /hr-policies/{id}/events` | ADMIN | Append-only publication audit trail | same |
| `GET /hr-policies/{id}/download` | any | Same visibility as the list; hidden → 404 identical to unknown | same, `characterization/test_hr_policies.py`, `test_framework_security.py` |

### Announcements, polls, attendance, finance, calendar, org
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /announcements` | any | All announcements, with author name | `characterization/test_announcements.py` |
| `POST /announcements` | ADMIN, MANAGER (in handler) | **Organisation-wide** (**DECISION**, §3) | `characterization/test_announcements.py` |
| `GET /polls`, `POST /polls/{id}/vote` | any | All polls; one vote per employee | code |
| `POST /polls` | ADMIN, MANAGER (in handler) | **Organisation-wide** (**DECISION**, §3) | code |
| `GET /polls/{id}/results` | any | Aggregate counts per option, at any time; no individual votes | code |
| `GET /attendance/me`, `/today`, `POST /clock-in`, `/clock-out` | any | Self | code |
| `GET /finance/payroll/me`, `/statutory/me`, `/profile/me` | any | Self | code |
| `GET /calendar/team` | any | Manager / ADMIN: own department; employee: self. Approved-leave markers only (Phase 1 review) | code |
| `GET /calendar/holidays`, `/calendar/birthdays` | any | Holidays; birthdays without year | code |
| `GET /org/tree` | any | **RBAC step 2:** active employees only; id, name, role, manager_id | `rbac/test_directory_scope.py` |

**Coverage gaps (no dedicated tests):** polls, attendance, finance, calendar and org tree are reviewed by code only. Their scopes are self-only or aggregate, but characterization tests are recommended (proposed, not added).

## 2. Admin self-actions (S-02)

**Implemented 2026-10-16** (RBAC step 4, Decision 2). Every check runs in the backend before anything is written. The actor is the authenticated, database-loaded user.

| Operation (ADMIN on own record) | Before step 4 | Now |
|---|---|---|
| Deactivate own account | Prohibited (400 `INVALID_OPERATION`) | Unchanged |
| Approve or reject own pending leave | Prohibited (403) | Unchanged |
| Create own leave | Auto-approved (documented in `leave_policy.md`) | Unchanged (business decision; not in step 4 scope) |
| Change own role (`PUT /employees/{self}` with a different role) | Allowed, could leave zero admins | **403 `SELF_ROLE_CHANGE_FORBIDDEN`**. Restating the current role is not a change. |
| Change own salary, bank or PAN fields (`current_salary_usd`, `bank_*`, `pan_number`, `pan_name`, `pan_dob`) | Allowed | **403 `SELF_SENSITIVE_EDIT_FORBIDDEN`**. The whole request is refused if any of these fields is present, including an explicit `null` or when mixed with allowed fields; nothing is persisted. Another admin can still change them. |
| Reset own password via `PUT /employees/{self}` | Allowed, no re-authentication | **403 `SELF_PASSWORD_RESET_FORBIDDEN`**, pointing to `POST /auth/change-password`. Resets for other employees are unchanged. |
| Upload a payslip into own record (`POST /employees/{self}/documents/payslip`) | Allowed | **403 `SELF_PAYSLIP_FORBIDDEN`** for every role, before the file is read or stored |
| Self-service PAYSLIP-type document (`POST /employees/me/documents`, `document_type=PAYSLIP`) | Allowed | **403 `SELF_PAYSLIP_FORBIDDEN` for ADMIN**. Employees and managers keep the documented self-service rule (open decision, below). |
| Change own email, job title; assign self to projects | Allowed | Unchanged (not part of Decision 2) |

**Last-active-admin invariant (system-wide; the app is single-tenant):**
- At least one employee with role ADMIN and status ACTIVE always remains.
- Every demotion (`PUT` role) and deactivation (`DELETE`) of an ADMIN runs one conditional `UPDATE … WHERE id = :target AND (SELECT count(*) of other active admins) >= 1`, so the check and the write can't be split by a concurrent request.
  - A refusal returns **409 `LAST_ACTIVE_ADMIN`**.
  - A write conflict returns 409 `CONCURRENT_UPDATE`.
  - Either way the whole request is rolled back.
- AI actions (`EMPLOYEE_DEACTIVATE`) reach the same HTTP handler. Reactivation and creation can only add admins.
- Tested with simultaneous cross-removals by two admins (deactivate, demote and mixed; 15 rounds): exactly one succeeded and the other got `LAST_ACTIVE_ADMIN` every time.
- Not verified for other databases or several API processes, which is not a supported deployment.

**Self-service password change (`POST /api/v1/auth/change-password`):**
- **Request and checks:** any authenticated active user sends `{current_password, new_password}`. The current password is verified. The new one must be 8–128 characters (the existing schema rule), not blank, and different from the current one.
- **Storage:** hashed with the existing passlib/bcrypt context.
- **Errors:**
  - 400 `INVALID_CURRENT_PASSWORD`, `INVALID_NEW_PASSWORD` or `PASSWORD_UNCHANGED`;
  - 422 when the schema rules aren't met;
  - 403 `USER_INACTIVE`; 401 when unauthenticated.
- **Throttling:** `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` attempts per account per minute (default 10), successful and failed alike, giving 429 `TOO_MANY_ATTEMPTS`. The limiter is in-process, so it holds for the supported single-worker deployment only.
- **Sessions:** JWTs are stateless and there is no revocation list. Access tokens issued before the change **stay valid until they expire** (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 15). The old password stops working for login immediately. Refresh tokens are issued at login but there is no refresh endpoint, and they're rejected as bearer tokens.
- **Logging:** structured events only (`auth.password_changed`, `auth.password_change_failed`, `security.self_action_rejected`, `security.last_admin_protected`), with the employee id and a reason code. No passwords, hashes, tokens or field values.

**Open decision:** any employee may upload a PAYSLIP-type document into their own record through self-service. This is documented policy in `document_upload_policy.md`, and only ADMINs are blocked as of step 4. Decide whether payslip-type documents should be restricted to payroll uploads for everyone.

Tests:
- `tests/characterization/test_admin_self_actions.py`
- `tests/rbac/test_last_active_admin.py`
- `tests/rbac/test_password_change.py`
- `tests/rbac/test_route_permissions.py`

## 3. Announcements, polls, project catalogue (proposal; no permissions changed)

| Feature | Business purpose (inferred) | Current permissions | Cross-role exposure | Proposed options for decision |
|---|---|---|---|---|
| Announcements | Company and team news | Create: ADMIN, MANAGER, organisation-wide, no audience field, no edit/delete, no moderation. Read: everyone. Copilot: MANAGER and ADMIN (`ANNOUNCEMENT_CREATE`; corrected 2026-10-16, this row previously said ADMIN only) | Any manager broadcasts to the whole company. Content is not used by Policy RAG | (a) Keep. (b) ADMIN-only org-wide, managers department-scoped (needs an audience field and migration). (c) Manager posts as drafts awaiting ADMIN approval (like policies) |
| Polls | Engagement surveys | Create: ADMIN, MANAGER, organisation-wide. Vote: everyone, once. Results: aggregates to everyone at any time | Small-population polls can let readers infer individual answers. No close date | (a) Keep. (b) Results visible to the creator and ADMIN only, or only after closing. (c) Department-scoped audiences |
| Project catalogue | Shared project list for staffing | List / create / status change: ADMIN and **any** MANAGER, organisation-wide, with no owner field. Copilot: create ADMIN-only | A manager can mark another team's project COMPLETED or ON_HOLD | (a) Keep. (b) Add `owner_id`; managers change only projects they own; ADMIN all. (c) Status changes ADMIN-only |

## 4. Findings from this review

| ID | Finding | Severity | Status |
|---|---|---|---|
| S-01f | Managers could publish organisation-wide policies that feed Copilot answers | Medium | **Fixed** (approved policy): migration 0021, publication workflow, retrieval filter, tests |
| S-02 | Admin self-actions without controls (self-demotion to zero admins; own payroll and bank changes; password change without re-authentication; own payslips) | Medium (last-admin lockout: availability; self-dealing: integrity) | **Fixed (RBAC step 4, 2026-10-16)**, §2; open: employee self-service PAYSLIP-type documents |
| S-03 | Announcements, polls and catalogue are organisation-wide for any manager | Low to Medium (business) | **Open, decision in §3** |
| S-04 | Polls, attendance, finance, calendar and org tree have no dedicated characterization tests | Low (test gap) | Proposed |
