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
| `POST /auth/login`, `POST /auth/token` | public | Inactive accounts get 401 identical to a wrong password; throttled 10/min per IP + account | `characterization/test_auth.py`, `ai/test_limits.py` |
| `GET /health` | public | — | `test_infrastructure.py` |

### Employees (26 routes)
| Route | Role gate | Object scope | Evidence |
|---|---|---|---|
| `GET /employees`, `GET /employees/departments` | any | Directory fields only, active employees; all employees and emails are listable (directory feature) | code; `characterization/test_employees.py` (fields) |
| `GET /employees/{id}` | any | Financial / government-ID fields: self + ADMIN. Phone / address / blood type: self + ADMIN + direct manager (B-01) | `characterization/test_employees.py` |
| `POST /employees` | ADMIN | Any role may be assigned | code |
| `PUT /employees/{id}` | ADMIN | Any employee, **including self** (**S-02**, §2) | `characterization/test_admin_self_actions.py` |
| `DELETE /employees/{id}` (deactivate), `PATCH …/reactivate` | ADMIN | Any employee; deactivating self is prohibited (400) | `characterization/test_employees.py`, `test_admin_self_actions.py` |
| `GET/PUT /employees/me`, profile picture (`/me`), `/me/job-history` | any | Self only. Self may change name, phone, address, blood type, occupancy, DOB | code |
| `GET /employees/{id}/profile-picture` | any | Any employee's photo (directory feature; business decision, Phase 2) | code |
| `GET/POST /employees/me/documents`, `DELETE /me/documents/{id}`, `GET /me/documents/{id}/download` | any | Owner only | code |
| `POST /employees/{id}/documents` | ADMIN, MANAGER | Manager: direct reports + self (S-01b). ADMIN: anyone | `characterization/test_employees.py` |
| `POST /employees/{id}/documents/payslip` | ADMIN, MANAGER | Manager: direct reports only, never self (S-01c). ADMIN: anyone, **including self** (**S-02**) | `characterization/test_employees.py`, `test_admin_self_actions.py` |
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
| `GET /org/tree` | any | Directory fields | code |

**Coverage gaps (no dedicated tests):** polls, attendance, finance, calendar and org tree are reviewed by code only. Their scopes are self-only or aggregate, but characterization tests are recommended (proposed, not added).

## 2. Admin self-actions (S-02)

| Operation | Current | Proposed control (**needs approval**) |
|---|---|---|
| Deactivate own account | **Prohibited** (400 `INVALID_OPERATION`) | Keep |
| Approve or reject own pending leave | **Prohibited** (403) | Keep |
| Create own leave | **Auto-approved** for ADMINs at creation, with the balance reduced immediately (`leaves.py`; documented in `leave_policy.md`; pinned by `characterization/test_leaves.py::test_admin_leave_is_auto_approved_and_balance_reduced`) | Business decision: keep the documented rule, or route admin leave to another ADMIN (would change the policy text and that test) |
| Change own role | **Allowed, no guard.** Verified: it can leave **zero active admins** | Prohibit self role change; also refuse any change that would leave no active ADMIN (role change or deactivation of the last admin) |
| Change own salary, bank, PAN, UAN, ESI | Allowed, no second approver | Prohibit via `PUT /employees/{self}`, or require a second ADMIN (four-eyes). Record payroll-field changes in an append-only audit |
| Change own email or password via the admin endpoint | Allowed, no re-authentication | Prohibit for self (use a self-service change-password flow with current password) |
| Upload payslip or documents into own record | Allowed (managers are blocked for payslips) | Align with S-01c: no self-payslips for ADMINs either |
| Change own job title; assign self to projects | Allowed | Low risk. Recommend allowing for job title only via another ADMIN (decision) |
| Reactivate | Not applicable to self (an inactive admin cannot log in) | — |

Current behaviour is pinned by `tests/characterization/test_admin_self_actions.py`. Those assertions change only together with an approved control.

## 3. Announcements, polls, project catalogue (proposal; no permissions changed)

| Feature | Business purpose (inferred) | Current permissions | Cross-role exposure | Proposed options for decision |
|---|---|---|---|---|
| Announcements | Company and team news | Create: ADMIN, MANAGER, organisation-wide, no audience field, no edit/delete, no moderation. Read: everyone. Copilot: ADMIN only (`ANNOUNCEMENT_CREATE`) | Any manager broadcasts to the whole company. Content is not used by Policy RAG | (a) Keep. (b) ADMIN-only org-wide, managers department-scoped (needs an audience field and migration). (c) Manager posts as drafts awaiting ADMIN approval (like policies) |
| Polls | Engagement surveys | Create: ADMIN, MANAGER, organisation-wide. Vote: everyone, once. Results: aggregates to everyone at any time | Small-population polls can let readers infer individual answers. No close date | (a) Keep. (b) Results visible to the creator and ADMIN only, or only after closing. (c) Department-scoped audiences |
| Project catalogue | Shared project list for staffing | List / create / status change: ADMIN and **any** MANAGER, organisation-wide, with no owner field. Copilot: create ADMIN-only | A manager can mark another team's project COMPLETED or ON_HOLD | (a) Keep. (b) Add `owner_id`; managers change only projects they own; ADMIN all. (c) Status changes ADMIN-only |

## 4. Findings from this review

| ID | Finding | Severity | Status |
|---|---|---|---|
| S-01f | Managers could publish organisation-wide policies that feed Copilot answers | Medium | **Fixed** (approved policy): migration 0021, publication workflow, retrieval filter, tests |
| S-02 | Admin self-actions without controls (self-demotion to zero admins; own payroll and bank changes; password change without re-authentication; own payslips) | Medium (last-admin lockout: availability; self-dealing: integrity) | **Open, proposal in §2.** Behaviour pinned by tests |
| S-03 | Announcements, polls and catalogue are organisation-wide for any manager | Low to Medium (business) | **Open, decision in §3** |
| S-04 | Polls, attendance, finance, calendar and org tree have no dedicated characterization tests | Low (test gap) | Proposed |
