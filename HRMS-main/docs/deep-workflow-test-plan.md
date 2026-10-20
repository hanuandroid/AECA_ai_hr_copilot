# Deep business-workflow test plan (Playwright, isolated E2E stack)

**Date:** 2026-10-20 · Written before implementation, from the code at `1ce24e6` + the uncommitted role-access suite.
Extends `role-access-test-plan.md` (118 tests: pages, navigation, auth, API authorization, step-up dialogs).
Environment, identities and safety rules are the same: `docker-compose.e2e.yml` (project `hrms-e2e`, own volume,
fake AI providers, no keys, `STEP_UP_ENFORCEMENT=log`), synthetic data only, Chromium only.

## 0. What is actually implemented (inspected)

| Area | Real implementation | Consequence for the tests |
|---|---|---|
| Employees | ADMIN create / update / deactivate / reactivate (`endpoints/employees.py`); statuses ACTIVE, INACTIVE; roles ADMIN, MANAGER, EMPLOYEE; `manager_id` **not settable through the API** | new people are created by ADMIN with no manager; team scenarios use the seeded Employee User (reports to Manager User) |
| Deactivation | sets `status=INACTIVE` only; `token_version` is **not** changed | an old token is refused while inactive (403 `USER_INACTIVE`) and works again after reactivation → POLICY_CLARIFICATION_REQUIRED |
| Last-active-admin guard | atomic guard in demote/deactivate; an admin cannot act on self | sequentially unreachable through the API (the acting admin always remains); concurrency covered by `backend/tests/rbac/test_last_active_admin.py` |
| Leaves | `LeaveRequestStatus` PENDING → APPROVED or REJECTED; second decision 400 `LEAVE_NOT_PENDING`; approval deducts the balance; 409 `LEAVE_OVERLAP`; 400 date-range / half-day checks; ADMIN's own leave auto-approved; manager: direct reports only (others 404) | full lifecycle testable in the UI (`/leaves` form, Pending Approvals with Approve / Reject) |
| Tickets | `TicketStatus` OPEN, IN_PROGRESS, RESOLVED (no enforced order); assign: ADMIN anyone, MANAGER self or direct reports; status: ADMIN, MANAGER in scope, or the assignee; owner EMPLOYEE may not change status; onboarding tasks on ONBOARDING tickets | lifecycle testable in `/tickets` (Raise, Assign by id, Start, Resolve, Add Task, Mark Done) |
| HR policies | `PolicyStatus` SUBMITTED → PUBLISHED / REJECTED; ADMIN uploads are published directly; a new version of the same title archives the old one; audiences ALL / MANAGERS / ADMINS; `hr_policy_events` trail; index rebuilt by `scripts/ingest_policies.py` | upload/review/publish testable in `/hr-policies`; re-index run inside the E2E api container |
| Copilot | rule-based refusals and routing; MANAGER skill search answered by a fixed query (no model); policy retrieval below threshold refuses without a model; **every model call fails** with the stack's rule-less fake provider (`AI_INVALID_OUTPUT`) | deterministic success: refusals, skill search, no-answer; deterministic failure: any model-dependent path. Action proposal → confirmation → execution cannot succeed here → NOT_TESTED in E2E (backend: `tests/ai/actions/`) |
| Audit | AI paths → `ai_audit_events` (incl. refusals and denials); policy publication → `hr_policy_events`; business refusals → log lines only | assert only these |

## 1. Workflows

Legend for expected results: **PASS** criteria are listed; scenarios that cannot run in this environment are planned
as NOT_TESTED, unsupported ones NOT_APPLICABLE, unclear rules POLICY_CLARIFICATION_REQUIRED.

### EMP-A Employee creation (ADMIN → new employee)
- **Roles:** ADMIN, the new EMPLOYEE, MANAGER/EMPLOYEE (negative).
- **Setup:** none. **Actions:** ADMIN fills the create form (validation error first, then valid data) → step-up dialog →
  saved. **Expected:** API record has the entered name/email/role EMPLOYEE, status ACTIVE, no manager; the new person
  signs in through the form and sees the EMPLOYEE navigation only (no Employees link), own profile shows own name.
- **Negative:** required fields missing (client message, nothing sent); duplicate email (409 `EMAIL_ALREADY_EXISTS`
  shown, no second record); invalid role / department via API (400 `INVALID_ROLE` / `INVALID_DEPARTMENT`, no account);
  MANAGER/EMPLOYEE create (403, no account).
- **Cleanup:** volume teardown (unique `e2e.` emails per run).

### EMP-B Employee update
- **Actions:** ADMIN edits a synthetic employee's job title and phone in the UI (no step-up); the employee updates own
  phone on `/me`. **Expected:** persisted values via API; the manager's view of a direct report hides financial fields
  that the ADMIN view shows; a MANAGER/EMPLOYEE PUT on another record is refused and nothing changes.

### EMP-C Deactivation and reactivation
- **Actions:** synthetic employee signs in (token kept) → ADMIN deactivates in the UI → sign-in refused (401, same as a
  bad password), old token 403 `USER_INACTIVE`, Employee User still works → ADMIN reactivates → sign-in works again.
- **Policy point:** whether the pre-deactivation token should work again after reactivation (it does) →
  POLICY_CLARIFICATION_REQUIRED (recorded, asserted as current behaviour).

### EMP-D Admin self-actions and the last-active-admin guard
- **Setup:** two synthetic admins A2, A3 (real seeded admin untouched). **Actions:** A2 deactivates A3 (allowed, an
  admin remains); A2 tries to deactivate / demote itself (400 / 403). **Concurrency:** backend test only.

### LV-A Leave approval handoff (EMPLOYEE → MANAGER → EMPLOYEE)
- **Actions:** Employee User submits a one-day leave in the UI → row PENDING (UI + API: owner, dates, type, status) →
  Manager User sees it in Pending Approvals → unrelated synthetic manager cannot approve (404, still PENDING) →
  Manager approves in the UI → APPROVED (UI + API), balance reduced by exactly 1 → Employee User sees APPROVED →
  second approve/reject 400 `LEAVE_NOT_PENDING`, balance unchanged.

### LV-B Leave rejection
- Same handoff with Reject: REJECTED, balance unchanged, the employee sees it; re-approval refused.

### LV-C Leave validation and correction
- End before start (400 `INVALID_DATE_RANGE`, nothing stored) → corrected → stored; overlapping dates 409; half-day
  with different dates 400 (API); EMPLOYEE approving / MANAGER approving another team's leave refused, unchanged.

### TK-A Team ticket lifecycle (EMPLOYEE → MANAGER → EMPLOYEE)
- Employee User raises a ticket in the UI (OPEN, owner = self, no assignee, no Start/Resolve buttons) → Manager sees
  it, assigns it to self in the UI → Start (IN_PROGRESS) → Resolve (RESOLVED); after each step UI and API agree →
  Employee User sees RESOLVED and still cannot change status (UI and API 403) → a synthetic employee outside the team
  cannot see or change it.

### TK-B Assignee handoff across teams; documented Copilot/API differences
- Synthetic employee O raises a ticket → ADMIN assigns it to Employee User → Employee User (assignee) starts it in the
  UI → owner O cannot change status (403) → Manager User cannot see it (owner not in the team).
- **API side of E2:** ADMIN assigns an outside ticket to Manager User → the manager may change its status through the
  business API (assignee rule). **Copilot side of E1/E2:** Employee User's Copilot capabilities never include
  TICKET_STATUS_UPDATE (even while assignee). A Copilot ticket action needs a model proposal → NOT_TESTED in E2E
  (backend: `tests/ai/actions/test_action_parity.py`).

### TK-C Onboarding checklist
- Employee User raises an ONBOARDING ticket → Manager adds a task in the UI → owner marks it done → persisted; adding a
  task to a non-onboarding ticket 400.

### POL-A Manager submission, ADMIN review, audience
- Manager uploads a MANAGERS-audience policy in the UI → SUBMITTED, invisible to EMPLOYEE and to other managers' lists
  as published → ADMIN publishes in the UI → `hr_policy_events` shows submitted → published with actors → Manager and
  ADMIN list/download it; EMPLOYEE 404.

### POL-B Indexing and the AI audience filter; new version
- ADMIN uploads an ADMINS-audience policy with a unique synthetic term → index rebuilt (`ingest_policies.py` inside
  the E2E api container) → a Copilot policy question with that term: EMPLOYEE gets the no-answer refusal (retrieval
  filtered, no model call) while ADMIN's retrieval hits the policy (the model call then fails: `AI_INVALID_OUTPUT`) —
  the difference proves the audience filter. New version with the same title → old one ARCHIVED, re-index → the old
  term no longer retrieves for ADMIN either. Feasibility checked at run time; otherwise NOT_TESTED with the reason.

### POL-C Malformed uploads
- Unsupported type, empty file, missing title → refused (400/422), no new policy listed.

### AI-A Authorized deterministic question and conversation ownership
- Manager asks the Copilot (UI) "Which employees know Python?" → answered from the skill-search query (table) →
  conversation persisted and reloadable at `/copilot/{id}` by the owner → EMPLOYEE and ADMIN get 404 for it.

### AI-B Restricted question and audit
- EMPLOYEE asks for another employee's salary/bank (UI) → refusal text, no data → the AI audit trail for that request
  contains the refusal event; ADMIN sees it, other employees do not.

### AI-C Confirmed HR action
- **NOT_TESTED in E2E:** proposals need a model. Executed instead: an action request fails safely (no confirmation,
  no business change); backend tests cover propose → confirm → execute, re-authorization at confirmation, foreign and
  repeated confirmation ids.

### AI-D Failure and recovery
- Model-dependent question → friendly error in the UI, no business change; provider outage simulated by intercepting
  the browser's `/chat` call (503) → friendly error, retry succeeds; archived conversation → 404 and new messages to it
  refused; unknown confirmation id refused.

### INT Data integrity, replay, recovery
| Id | Scenario | Expected |
|---|---|---|
| INT-1 | double-click Submit on the leave form | exactly one leave stored |
| INT-2 | repeated approval / rejection | 400, balance deducted once |
| INT-3 | repeated action confirmation | NOT_TESTED in E2E (needs a proposal); backend replay tests |
| INT-4 | stale Pending Approvals (ADMIN rejects meanwhile) → Manager clicks Approve | error shown, record stays REJECTED, balance unchanged |
| INT-5 | refused mutation then legitimate read | covered by LV-C, TK-A/B, EMP-B |
| INT-6 | network failure while creating an employee (intercepted) | "don't know whether applied" message, no record; retry succeeds once |
| INT-7 | validation failure then correction | LV-C, EMP-A |
| INT-8 | sign out everywhere, then old session | existing `auth.spec.ts` (not duplicated) |
| INT-9 | concurrent last-admin protection | backend `rbac/test_last_active_admin.py` run |

### AUD Audit assertions
- AI refusals/denials appear in `ai_audit_events` for the right employee; business refusals do not (current
  behaviour); policy publication leaves `hr_policy_events`; no credentials, tokens or sensitive values in failure
  evidence (screenshots only, traces off).

## 2. Test architecture

- New spec files `frontend/e2e/workflow-*.spec.ts`; helpers in `e2e/support/workflow.ts` (create synthetic people,
  free leave dates, balances, UI shortcuts). Each test creates what it needs; no test depends on another test's data.
- Seeded team members are used only where a manager relationship is required (cannot be created through the API).
- Persisted state is always checked through the API after UI actions; no sleeps; Chromium only.
- Every `up` re-seeds the volume, so balances and dates start fresh; tests also tolerate repeated runs on one stack.

## 3. Copilot action confirmation end to end (added 2026-10-20)

**Audit.** Proposal (`/chat` router or `/chat/actions`): imperative verbs route to ACTION by rule (no model); the model
must return an `ActionProposal`; the backend checks the capability, resolves and validates the target and parameters
(untrusted hints such as `resource_id` are verified), stores a single-use, owner-bound, expiring confirmation.
Confirm (`/chat/actions/confirm`): owner-only load (others: 400 `AI_CONFIRMATION_INVALID`, same as unknown/used/expired),
capability + target re-check, password for ANNOUNCEMENT_CREATE / EMPLOYEE_DEACTIVATE, atomic claim, `ACTION_CONFIRMED`
audit written fail-closed, then the existing business endpoint with the **user's own token**; COMPLETED / FAILED /
UNKNOWN recorded; audit events `ACTION_PROPOSED`, `ACTION_CONFIRMATION_REQUESTED`, `ACTION_CONFIRMED`,
`ACTION_EXECUTED`, `ACTION_REJECTED`, `AUTHORIZATION_DENIED`.

**Test-only accommodation.** The E2E stack's fake provider has no rules, so no proposal can be produced. Added: a
schema-aware scripted fake (`llm/fake.py::ScriptedFakeLLM`) loaded from `AI_FAKE_LLM_SCRIPT` (JSON) **only when
`AI_LLM_PROVIDER=fake`**; production refuses both the fake provider (existing) and the script setting (new check). Rules
name the output schema, a phrase to match in the request, and the output (optionally one value captured from the
question, e.g. a leave id or a ticket title). Everything after the proposal is the real application flow.

| Id | Scenario | Roles | Expected |
|---|---|---|---|
| ACT-1 | ticket request in the Copilot UI → card → nothing created yet → Confirm in the UI | synthetic EMPLOYEE | completed; ticket persisted with that employee as owner (proves the user's token); UI shows the result; audit has ACTION_CONFIRMED + ACTION_EXECUTED for that employee |
| ACT-2 | EMPLOYEE asks to approve a leave | EMPLOYEE | 403 `AI_PERMISSION_DENIED`, no confirmation, leave unchanged, `AUTHORIZATION_DENIED` audited |
| ACT-3 | another user confirms someone's confirmation id | two synthetic users | 400 `AI_CONFIRMATION_INVALID`; nothing created; the owner can still confirm |
| ACT-4 | the same id confirmed again after success | EMPLOYEE | 400; exactly one ticket |
| ACT-5 | duplicate confirmation (two concurrent API calls; double-click in the UI) | EMPLOYEE | one execution, one ticket |
| ACT-6 | expired confirmation | — | NOT_TESTED in E2E (300 s TTL, no clock control); backend `test_confirmation_expires` |
| ACT-7 | permission removed between proposal and confirmation (synthetic ADMIN proposes a leave approval, is demoted, confirms) | ADMIN → EMPLOYEE | 403 `AI_PERMISSION_DENIED`; leave still PENDING; the confirmation is spent |
| ACT-8 | record changed between proposal and confirmation (the manager approves directly first) | ADMIN, MANAGER | `failed` with the business error; leave stays as the manager left it |
| ACT-9 | invalid action parameters (priority URGENT) | EMPLOYEE | clarification, no confirmation id, nothing created |
| ACT-10 | password-protected action (MANAGER announcement): wrong password, then correct | MANAGER | wrong: refused, still pending, nothing posted; correct: completed, TEAM announcement persisted |
| ACT-11 | business API failure during execution | — | covered by ACT-8 (real refusal); an outage of the in-process API cannot be simulated from the browser: backend `test_backend_unavailable_is_not_retried` |
