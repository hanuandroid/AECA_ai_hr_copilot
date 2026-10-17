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
| `GET /employees/projects/catalog` | ADMIN, MANAGER | Whole catalogue (unchanged) | code |
| `POST /employees/projects/catalog` | **ADMIN** (step 6; before: ADMIN, MANAGER) | The only creation path; assignment never creates projects | `rbac/test_project_scope.py` |
| `PUT /employees/projects/catalog/{id}/status` | ADMIN, MANAGER | ADMIN: any. MANAGER: only while they or an **active direct report** (one level) are assigned in `employee_projects`, checked per request; else 403 `FORBIDDEN` (step 6, §3) | `rbac/test_project_scope.py` |

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
| `POST /tickets/{id}/assign`, `…/onboarding-tasks` | MANAGER / ADMIN (handler) | Out-of-scope → 404 identical to unknown; manager assignees limited to self or direct reports. Assignment also moves OPEN → IN_PROGRESS | `characterization/test_tickets.py` |
| `POST /tickets/{id}/status` | any (handler) | **RBAC step 5 (decision 4, 2026-10-17):** ADMIN any ticket; MANAGER in S-01a scope (out-of-scope → 404); EMPLOYEE **only as the current assignee**. The EMPLOYEE owner gets 403 `FORBIDDEN` (before: allowed). Role from the DB; extra body fields ignored | `rbac/test_ticket_status.py`, `characterization/test_tickets.py` |
| `POST /tickets/{id}/onboarding-tasks/{task_id}/complete` | any (handler) | Owner or assignee (employee), manager in scope, ADMIN. Unchanged by step 5: completing a task does not change ticket status | `characterization/test_tickets.py` |

### HR policies (now 6 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /hr-policies` | any | PUBLISHED **within the caller's audience** (step 7: EMPLOYEE `ALL`; MANAGER `ALL`+`MANAGERS`; ADMIN everything); managers also see their own submissions; the total uses the same filter; `status` cannot widen it | `characterization/test_hr_policy_publication.py`, `rbac/test_policy_audience.py` |
| `POST /hr-policies/upload` | ADMIN, MANAGER | **Approved 2026-10-14:** ADMIN upload → PUBLISHED; MANAGER upload → SUBMITTED draft. Step 7: optional `audience` (default `ALL`); only an audience that includes the uploader (MANAGER: `ALL`/`MANAGERS`, else 403 `POLICY_AUDIENCE_FORBIDDEN`); unknown → 400 `INVALID_POLICY_AUDIENCE`; both before storage | same, `rbac/test_policy_audience.py` |
| `POST /hr-policies/{id}/publish`, `…/reject` | **ADMIN** | SUBMITTED only (else 409) | same |
| `GET /hr-policies/{id}/events` | ADMIN | Append-only publication audit trail | same |
| `GET /hr-policies/{id}/download` | any | Same visibility as the list, including the audience (step 7); hidden → 404 identical to unknown | same, `characterization/test_hr_policies.py`, `test_framework_security.py`, `rbac/test_policy_audience.py` |

### Announcements, polls, attendance, finance, calendar, org
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /announcements` | any | **Audience-filtered** (step 6): ORG + TEAM items of the caller's own team or their manager's team; ADMIN all. Total counts the same filter | `rbac/test_content_audience.py` |
| `POST /announcements` | ADMIN, MANAGER (in handler) | ADMIN: ORG (default) or TEAM. MANAGER: TEAM only (default); ORG → 403 `AUDIENCE_FORBIDDEN`. Team = creator's own | `rbac/test_content_audience.py` |
| `GET /polls`, `POST /polls/{id}/vote` | any | **Audience-filtered** like announcements; one vote per employee. Voting on a hidden poll = the same 404 as an unknown id | `rbac/test_content_audience.py` |
| `POST /polls` | ADMIN, MANAGER (in handler) | Same audience rule as announcements | `rbac/test_content_audience.py` |
| `GET /polls/{id}/results` | any | Aggregates only (unchanged); a TEAM poll's results only for that team and admins, otherwise the unknown-id 404 | `rbac/test_content_audience.py` |
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

## 3. Announcements, polls, project catalogue (implemented 2026-10-17, RBAC step 6)

Approved decision 5 with plan items X-2 and X-3. Migration `0022_content_audience`. Tests: `tests/rbac/test_content_audience.py`,
`test_content_audience_migration.py`, `test_project_scope.py`, `tests/ai/actions/test_announcement_audience_ai.py`,
frontend `tests/content-audience.test.tsx`.

**Audience model.** `announcements` and `polls` get `audience` (`ORG` | `TEAM`, server default `ORG`) and
`team_manager_id` (FK `employees.id`). A CHECK constraint requires ORG ⇔ no team and TEAM ⇔ a team, and rejects any other
value. Existing rows were backfilled to ORG, so nothing visible before became hidden.

| | Before | Now |
|---|---|---|
| Create ORG announcement / poll | ADMIN, MANAGER | ADMIN only (ADMIN default). MANAGER asking for ORG → 403 `AUDIENCE_FORBIDDEN`, nothing saved |
| Create TEAM item | — | MANAGER (default) and ADMIN. The team is always the creator: `team_manager_id = caller`. Client `team_manager_id`, `author_id`, `created_by` are ignored |
| See / count | everything | ORG; TEAM where `team_manager_id` is the caller or the caller's manager (direct reports, one level; no indirect reports); ADMIN all. Membership is read from `employees.manager_id` on every request |
| Vote / results of a TEAM poll | anyone with the id | that team and admins; others get the same 404 as an unknown id (checked before option validation). Results stay aggregate; individual votes are never returned |
| Update / delete / publish | no such routes | no such routes (unchanged) |
| Copilot | `ANNOUNCEMENT_CREATE` ADMIN, MANAGER, org-wide | grant is the audience: ADMIN → ORG, MANAGER → TEAM; the model cannot set it; the API re-checks at confirmation. No AI poll or project-status action; the SQL agent cannot read `announcements`, `polls` or `poll_responses` |
| Catalogue create | ADMIN, MANAGER | ADMIN only (API and Copilot) |
| Project status | ADMIN, any MANAGER | ADMIN any; MANAGER only while they or an active direct report are assigned (X-3 interpretation; no owner field) |

**Limits and notes.**
- ADMIN TEAM items go to the admin's own direct reports; an admin cannot target another manager's team (not in the approved plan).
- Visibility follows the current reporting line: when an employee changes manager, the old team's TEAM items disappear for
  them and the new team's appear (TEAM items are not snapshotted to a member list).
- The X-3 rule counts **active** direct reports only; an inactive report's leftover assignment does not grant status changes.
- Downgrading 0022 keeps the rows but the older code shows them to everyone; review TEAM items before any rollback.
- Verified on SQLite only (the supported database). The project-status check and the write are in one request but not one
  statement; a concurrent assignment removal can race a status change (no stronger guarantee is claimed).

### Original proposal (kept for the record)

| Feature | Business purpose (inferred) | Current permissions | Cross-role exposure | Proposed options for decision |
|---|---|---|---|---|
| Announcements | Company and team news | Create: ADMIN, MANAGER, organisation-wide, no audience field, no edit/delete, no moderation. Read: everyone. Copilot: MANAGER and ADMIN (`ANNOUNCEMENT_CREATE`; corrected 2026-10-16, this row previously said ADMIN only) | Any manager broadcasts to the whole company. Content is not used by Policy RAG | (a) Keep. (b) ADMIN-only org-wide, managers department-scoped (needs an audience field and migration). (c) Manager posts as drafts awaiting ADMIN approval (like policies) |
| Polls | Engagement surveys | Create: ADMIN, MANAGER, organisation-wide. Vote: everyone, once. Results: aggregates to everyone at any time | Small-population polls can let readers infer individual answers. No close date | (a) Keep. (b) Results visible to the creator and ADMIN only, or only after closing. (c) Department-scoped audiences |
| Project catalogue | Shared project list for staffing | List / create / status change: ADMIN and **any** MANAGER, organisation-wide, with no owner field. Copilot: create ADMIN-only | A manager can mark another team's project COMPLETED or ON_HOLD | (a) Keep. (b) Add `owner_id`; managers change only projects they own; ADMIN all. (c) Status changes ADMIN-only |

## 3a. HR policy audiences (implemented 2026-10-17, RBAC step 7)

Approved decision 3. Migration `0023_policy_audience`. Tests: `tests/rbac/test_policy_audience.py`,
`tests/ai/rag/test_policy_audience_rag.py`, `tests/ai/conversation/test_policy_replay.py`, frontend
`tests/policy-audience.test.tsx`.

| Audience | Readers | Notes |
|---|---|---|
| `ALL` | everyone (active) | default; every existing policy was backfilled to `ALL` |
| `MANAGERS` | managers and admins | nested levels |
| `ADMINS` | admins only | |
| anything else | nobody | rejected by a CHECK constraint; code never matches it (never indexed, never retrieved, never listed to non-admins) |

| Path | Enforcement |
|---|---|
| List, total, download | `app/services/policy_audience.py`; hidden = unknown-id 404. ADMIN sees all rows; a manager also sees their own submissions |
| Upload | an uploader may only pick an audience that includes them; default `ALL` |
| Indexing | only PUBLISHED policies with a known audience are indexed; the audience is **not** copied into chunks |
| Retrieval | `policy_audiences(ctx)` from the AI matrix (`POLICY_READ`, `POLICY_READ_MANAGERS`, `POLICY_READ_ADMINS`) is a **required** argument; the vector store joins `hr_policies` and filters status + audience **before scoring**, so hidden chunks cannot affect IDF, ranks or the threshold, and a reclassification applies on the next request without re-indexing |
| Citations | sources only come from the filtered retrieval; each carries `policy_id` (no paths, checksums or uploader ids) |
| Conversation replay | assistant replies store the cited `source_policy_ids`; replay and follow-up classifier context replace a reply with a neutral notice if any cited policy is not readable by the caller **now** (audience changed, role changed, policy missing) |
| Cache | none exists in the RAG path |
| SQL agent | `hr_policies` was already a denied table |

Synthetic negative tests: title, verbatim quotation, synonym paraphrase, codeword and forged-citation questions for
EMPLOYEE (vs MANAGERS and ADMINS policies) and MANAGER (vs ADMINS); the restricted text never reaches the retrieval
result, the LLM prompt, the answer, the sources or the HTTP response. A parity test pins the business rule and the AI
matrix to the same sets for every role. Mutation checks: removing the retrieval filter fails 7 tests; disabling replay
withholding fails 8.

**Limits.** Admins cannot change a policy's audience after upload (re-upload a new version instead); no route was
added for it. The upload response's version number can reveal that a same-title policy exists in a hidden audience
(pre-existing behaviour for drafts too). Replies stored before migration 0023 have no recorded citations and are
replayed unchanged. Verified on SQLite only. Downgrading 0023 makes restricted policies readable by everyone again.

## 4. Findings from this review

| ID | Finding | Severity | Status |
|---|---|---|---|
| S-01f | Managers could publish organisation-wide policies that feed Copilot answers | Medium | **Fixed** (approved policy): migration 0021, publication workflow, retrieval filter, tests |
| S-02 | Admin self-actions without controls (self-demotion to zero admins; own payroll and bank changes; password change without re-authentication; own payslips) | Medium (last-admin lockout: availability; self-dealing: integrity) | **Fixed (RBAC step 4, 2026-10-16)**, §2; open: employee self-service PAYSLIP-type documents |
| S-03 | Announcements, polls and catalogue are organisation-wide for any manager | Low to Medium (business) | **Fixed (RBAC step 6, 2026-10-17)**, §3 |
| S-04 | Polls, attendance, finance, calendar and org tree have no dedicated characterization tests | Low (test gap) | Proposed |
