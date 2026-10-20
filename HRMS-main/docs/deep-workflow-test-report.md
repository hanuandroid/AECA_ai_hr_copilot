# Deep business-workflow test report

**Runs:** 2026-10-20 on `main` at `1ce24e6` plus uncommitted test changes (§11). Isolated stack
`docker-compose.e2e.yml` (project `hrms-e2e`, own volume, fake AI providers with empty keys, `STEP_UP_ENFORCEMENT=log`),
synthetic data only, Chromium 153 via Playwright 1.63.0. Plan: `deep-workflow-test-plan.md`.
**No live model was called. Fake-model results show workflow correctness and safety, never live answer quality.**

## 1. Baseline (before any new test)

| Suite | Result |
|---|---|
| Playwright role-access suite (fresh stack) | **118 / 118 passed**, 0 skipped, 0 flaky |
| Backend (`scripts/verify-backend.sh`) | 3175 passed, 1 skipped (opt-in live-model test); offline evaluations 139/139; gate tests 25 |

No baseline failures. (A pre-existing Vitest timing flake is recorded in `role-access-test-report.md` F-TEST-1; see §9.)

## 2. Final results

| Suite | Executed | As expected | Unexpected | Skipped |
|---|---:|---:|---:|---:|
| Playwright, full (fresh stack) | **142** | **142** | 0 | 0 |
| — original role-access tests | 118 | 118 passed | 0 | 0 |
| — new workflow tests | 24 | 23 passed + 1 **expected failure** (known defect F-BUG-1) | 0 | 0 |
| Backend (unchanged code) | 3176 | 3175 passed | 0 | 1 (opt-in live test) |
| TypeScript (incl. new specs) | — | 0 errors | — | — |
| Frontend Vitest (no file changed in this phase) | 238 | 237 | 1 (pre-existing flake F-TEST-1, 5.03 s vs the 5 s timeout) | 0 |

## 3. Workflow scenarios and status

Counts below are **scenario** statuses (a test may cover several scenarios).

| Id | Workflow | Test | Status |
|---|---|---|---|
| EMP-A | ADMIN creates an employee in the UI (validation first, step-up) → API record ACTIVE EMPLOYEE, no manager → the new person signs in, sees employee navigation only, own profile, Access denied on `/employees` | `workflow-employees` | PASS |
| EMP-A neg | duplicate email in the UI (409 message, no second record, existing not overwritten); invalid role / department / missing fields (400/422, nothing created); MANAGER create (403, nothing created) | `workflow-employees` | PASS |
| EMP-B | ADMIN edits job title (no step-up, phone and bank untouched); self-service phone on `/me`; manager's view of a direct report hides financial fields the ADMIN sees; non-admin role changes refused, unchanged | `workflow-employees` | PASS |
| EMP-B bug | editing a `+91…` phone in the admin edit form | `workflow-employees` (F-BUG-1) | **FAIL — known defect, expected failure** |
| EMP-C | ADMIN deactivates in the UI → sign-in 401 (same as bad password), old token 403 `USER_INACTIVE`, other accounts work → reactivates in the UI → sign-in works | `workflow-employees` | PASS |
| EMP-C policy | the pre-deactivation token works again after reactivation (no `token_version` bump on deactivation) | `workflow-employees` | ~~POLICY_CLARIFICATION_REQUIRED~~ **resolved 2026-10-20 (§13): now refused (401 `TOKEN_REVOKED`)** |
| EMP-D | synthetic admin A2 deactivates A3 (allowed); A2 cannot deactivate or demote itself; seeded admin untouched | `workflow-employees` | PASS |
| EMP-D race | concurrent last-active-admin removal | backend `rbac/test_last_active_admin.py` (in the 3175) | PASS (backend) / NOT_APPLICABLE in E2E (sequentially unreachable) |
| LV-A | EMPLOYEE submits in the UI (PENDING, owner/dates/type stored) → unrelated synthetic manager 404, unchanged → team MANAGER approves in the UI (APPROVED, `approver_id` = manager) → balance −1 → EMPLOYEE sees APPROVED → second approve/reject 400, balance unchanged | `workflow-leave` | PASS |
| LV-B | same with Reject (REJECTED, balance unchanged, employee sees it, re-approval 400) | `workflow-leave` | PASS |
| LV-C | backwards dates, half-day across days, unknown type refused with nothing stored → corrected request stored → overlap 409 → owner and outsider cannot decide it, still PENDING | `workflow-leave` | PASS |
| TK-A | EMPLOYEE raises in the UI (OPEN, no status controls for the owner) → MANAGER assigns to self, Start, Resolve in the UI (API agrees after each step) → EMPLOYEE sees RESOLVED, cannot change it (403) → outsider cannot see, change or assign it | `workflow-tickets` | PASS |
| TK-B | outsider's ticket assigned by ADMIN (UI) to Employee User → the assignee starts it in the UI → owner 403 → MANAGER cannot see it (not in scope); MANAGER cannot assign outside the team | `workflow-tickets` | PASS |
| TK-B2 | E2 API side: manager updates a ticket merely assigned to them; E1: EMPLOYEE Copilot capabilities never include ticket status/assign | `workflow-tickets` | PASS |
| TK-B2 Copilot | Copilot refusing the manager on an assigned-but-not-team-owned ticket (E2) | — | NOT_TESTED in E2E (needs a model proposal; backend `tests/ai/actions/test_action_parity.py`) |
| TK-C | ONBOARDING ticket: MANAGER adds a task in the UI, the owner marks it done (persisted); task on an IT ticket 400; EMPLOYEE cannot add tasks | `workflow-tickets` | PASS |
| POL-A | MANAGER uploads a MANAGERS policy in the UI (SUBMITTED, hidden from employees and other managers, MANAGER cannot publish) → ADMIN publishes in the UI → events SUBMITTED(manager) → PUBLISHED(admin) → managers and ADMIN download, EMPLOYEE 404 | `workflow-policies` | PASS |
| POL-B | ADMINS policy with a unique synthetic term → re-index in the E2E container → EMPLOYEE gets the model-free no-answer (filtered before scoring), ADMIN's retrieval hits it (fails at the fake model: 502) → new version archives the old one (event trail) → after re-index the old wording no longer retrieves, the new one does | `workflow-policies` | PASS |
| POL-C | executable, empty, untitled, bad-audience and non-UTF-8 uploads refused; policy count unchanged | `workflow-policies` | PASS |
| AI-A | MANAGER asks "Which employees know Python?" in the UI → answered from the skill-search query (approved columns only) → conversation stored (user + assistant), reloadable at `/copilot/{id}`; EMPLOYEE and ADMIN 404 | `workflow-copilot` | PASS |
| AI-A | EMPLOYEE (UI) and ADMIN (API) policy question without a matching policy → model-free no-answer | `workflow-copilot` | PASS |
| AI-B | EMPLOYEE asks for a colleague's bank account in the UI → refusal, no numbers → AI audit has the `REFUSAL/REFUSED` event for that employee (polled; written after the response); the employee sees own events; another employee sees none | `workflow-copilot` | PASS |
| AI-C | HR action request: with the fake provider the proposal fails safely (502, friendly error, no confirmation card, no leave created) | `workflow-copilot` | PASS (safe failure) |
| AI-C full | propose → confirm in the UI → execute with the user's token → audit; re-authorization at confirmation; another user's / repeated confirmation id | — | **NOT_TESTED** in E2E (needs a model-made proposal); backend `tests/ai/actions/` (scripted fakes) PASS |
| AI-D | simulated provider outage (intercepted 503) → friendly error → "Try again" succeeds; malformed model output → friendly error; archived conversation → 404 and new messages 404 `AI_CONVERSATION_NOT_FOUND` | `workflow-copilot` | PASS |
| INT-1 | double-click Submit on the leave form → exactly one request | `workflow-integrity` | PASS |
| INT-2/4 | stale Pending Approvals: ADMIN rejects meanwhile → manager's Approve shows "Only pending leave request can be approved", stays REJECTED, balance unchanged; repeated rejection 400 | `workflow-integrity` | PASS |
| INT-3 | repeated action confirmation | — | NOT_TESTED in E2E (backend replay tests PASS) |
| INT-6 | network failure while creating an employee → unknown-outcome message, no record → user retries → exactly one record | `workflow-integrity` | PASS |
| INT-8 | sign out everywhere, then the old session | existing `auth.spec.ts` | PASS |
| AUD | AI refusals audited per employee; business refusals not audited (existing `api.spec.ts`); policy publication trail (`hr_policy_events`) | `workflow-copilot`, `workflow-policies`, `api.spec.ts` | PASS |

**Scenario totals:** PASS 27 · FAIL 1 (known defect F-BUG-1, pinned as an expected failure) · NOT_TESTED 3 (AI-C
full flow, Copilot side of E2, INT-3 — all need a model-made proposal; covered by backend tests) ·
NOT_APPLICABLE 1 (sequential last-admin trigger) · POLICY_CLARIFICATION_REQUIRED 1 (token after reactivation).

## 4. Tests added per workflow file (24)

`workflow-employees.spec.ts` 6 (incl. the F-BUG-1 expected-failure test) · `workflow-leave.spec.ts` 3 ·
`workflow-tickets.spec.ts` 4 · `workflow-policies.spec.ts` 3 · `workflow-copilot.spec.ts` 5 ·
`workflow-integrity.spec.ts` 3. Helpers: `e2e/support/workflow.ts`.

## 5. Role handoffs and state transitions (all verified in UI **and** API)

| Handoff | Transition | Actor recorded | Next role sees | Unrelated users |
|---|---|---|---|---|
| ADMIN → new EMPLOYEE | account created ACTIVE | — | new person signs in, employee view | MANAGER/EMPLOYEE cannot create |
| EMPLOYEE → MANAGER → EMPLOYEE (leave) | PENDING → APPROVED / REJECTED | `approver_id` = manager | employee sees decision | unrelated manager 404, owner/outsider 403 |
| EMPLOYEE → MANAGER → EMPLOYEE (ticket) | OPEN → assigned → IN_PROGRESS → RESOLVED | assignee = manager | employee sees RESOLVED | outsider cannot see/change/assign |
| outsider → ADMIN → EMPLOYEE (ticket) | assigned → IN_PROGRESS by the assignee | assignee = Employee User | assignee and owner see IN_PROGRESS | owner 403; manager cannot see |
| MANAGER → ADMIN → audiences (policy) | SUBMITTED → PUBLISHED | events record both actors | managers + admin read | employee 404 |
| ADMIN → Copilot index → roles | indexed → retrievable by audience | — | admin retrieval hits | employee filtered out |

## 6. Backend/API evidence

Every UI step is followed by an API read with a real token from the E2E backend (records, leave rows and balances,
ticket rows and tasks, policy lists and event trails, conversations, AI audit events). The backend suite (3175) was
re-run unchanged; no application code was modified in this phase.

## 7. Data integrity and cleanup

- All records use `E2E` / `e2e.` prefixes with a per-run random tag; tests create their own people and do not depend
  on each other's data. The seeded Employee User / Manager User are used only where a manager relationship is needed
  (it cannot be created through the API); free leave dates are looked up so repeated runs on one stack still work.
- Cleanup = `docker compose -f docker-compose.e2e.yml down -v` after checking the project is `hrms-e2e`; nothing is
  ever written to the development database or storage.
- Failure evidence: screenshots only (traces off); the results JSON was checked for passwords and tokens (none).

## 8. Failures and reproducible steps

**F-BUG-1 (new defect, not fixed — application behaviour is out of scope):** editing a phone number in the admin edit
form corrupts `+91` numbers.
1. Create an employee whose phone is `+919000000123` (UI create stores `+91` + digits).
2. ADMIN opens `/employees/new?employee_id=<id>`: the Local number field shows `000000123` (should be `9000000123`).
3. Type `9000000999`, Continue, Save Changes → stored phone `+9199000000999`.
- **Cause:** `app/employees/new/page.tsx` derives the country code with `/^\+\d{1,3}/` (greedy: `+919`).
- **Proposed minimal fix:** pick the longest matching value from the existing `countryCodes` list (e.g.
  `countryCodes.map(c => c.value).sort((a, b) => b.length - a.length).find(code => phone.startsWith(code)) ?? "+91"`).
- **Regression test:** `workflow-employees.spec.ts` "F-BUG-1 regression" (currently an expected failure; it reports an
  unexpected pass once fixed, so the `test.fail` marker must then be removed).
- Saving without editing the phone keeps the stored value (verified in EMP-B), so existing data is not corrupted by
  unrelated edits.

No other failures.

## 9. Existing versus new failures

- Existing: none in the Playwright or backend baselines. Pre-existing Vitest timing flake F-TEST-1
  (`frontend/tests/step-up-admin.test.tsx`, employee-creation cases near the 5 s timeout) — untouched; see
  `role-access-test-report.md`.
- New: F-BUG-1 (defect in application code, found by this phase; pinned, not fixed).
- Vitest: one run during this phase reported 6 failures with two test durations of ~2,290 s (the machine stalled
  during the run); a clean rerun right after gave 237/238 with only F-TEST-1. Neither involves files changed here.

## 10. Remaining policy questions and gaps

1. **POLICY_CLARIFICATION_REQUIRED:** should deactivation revoke existing sessions permanently (bump
   `token_version`)? Today a token issued before deactivation works again after reactivation.
2. Copilot action execution (propose/confirm/execute, re-authorization, replay, foreign ids) cannot run in the browser
   with the rule-less fake provider: NOT_TESTED in E2E; backend tests cover it. A scripted provider selectable in the
   E2E stack would be an application change and was not made.
3. Business-API refusals are log lines only, not audit records (documented; no requirement found).
4. Chromium desktop only; no live-model quality claims.
5. Leave balances on one stack shrink with repeated runs (a fresh `up` re-seeds); tests pick free dates but assume
   enough balance (12 / 10 / 15 days seeded).

## 11. Reproduce

```bash
# From HRMS-main/
sh scripts/verify-backend.sh                                    # (from the repo root: sh HRMS-main/scripts/verify-backend.sh)
docker compose -f docker-compose.e2e.yml config                 # project hrms-e2e, volume hrms-e2e_e2e_storage, no bind mounts
docker compose -f docker-compose.e2e.yml up -d --build --wait
cd frontend
cp e2e/e2e.env.example .env.e2e                                 # synthetic seeded accounts of the E2E stack only
npx playwright install chromium
npm run test:e2e                                                # 142 tests (118 role-access + 24 workflow)
npx playwright test e2e/workflow-*.spec.ts                      # workflow tests only
cd .. && docker compose -f docker-compose.e2e.yml down -v
```

**Changed in this phase (uncommitted, unstaged):** new `frontend/e2e/workflow-{employees,leave,tickets,policies,
copilot,integrity}.spec.ts`, `frontend/e2e/support/workflow.ts`, `docs/deep-workflow-test-plan.md`,
`docs/deep-workflow-test-report.md`. No application, backend, configuration or dependency change.

## 12. Update 2026-10-20: F-BUG-1 fixed

- **Root cause:** `app/employees/new/page.tsx` split a stored phone with `/^\+\d{1,3}/` (greedy), so `+919000000123`
  loaded as country code `+919` and local `000000123`; editing the local number saved `+9199000000999`.
- **Fix:** new `frontend/lib/phone.ts` (`splitStoredPhone`, `composePhone`, the existing `COUNTRY_CODES` list moved
  there unchanged). The longest supported code that the stored value starts with (followed by digits only) wins. A
  value without a supported code (other countries, or seeded numbers without `+`) is shown as stored, flagged with a
  short note, and **saved back unchanged unless the admin edits the phone fields** (no invented `+91`). An untouched
  phone is always sent back exactly as loaded. Storage format, validation, create mode and the backend are unchanged.
- **Ambiguity documented:** the shipped list (+91, +1, +44, +971, +65, +61, +49) contains no code that is a prefix of
  another; the longest-match rule is pinned with a synthetic list (`+1` / `+1242`) and a test fails if the shipped
  list ever gains such a pair without review.
- **Tests:** `tests/phone.test.ts` (22 unit cases), `tests/employee-phone.test.tsx` (8 form cases: +91 load, +971 not
  +91, unchanged save, local-number change, country-code change, unsupported `+33…` and `9876543210` kept and flagged,
  edited unsupported number). Against the old page 5 of the 8 form cases fail (the cases the old parser got wrong).
  Playwright: the F-BUG-1 test reported an unexpected pass with the fixed build (proof of the fix), then its
  expected-failure marker was removed; it now also reopens the employee and checks the persisted number; a new test
  covers an unsupported stored number.
- **Results after the fix:** Vitest 268/268 (3 consecutive full runs); TypeScript 0 errors; Playwright full suite on a
  fresh stack **143/143** (118 role-access + 25 workflow), 0 unexpected, 0 skipped, 0 flaky.
- **F-TEST-1:** the long employee-form Vitest tests took 2–4 s alone but neared the 5 s default under full-suite
  parallel load; adding the new form tests made that consistent. `vi.setConfig({ testTimeout: 15_000 })` was added to
  `tests/step-up-admin.test.tsx` and `tests/employee-phone.test.tsx` (timeout only, no assertion changed).

## 13. Update 2026-10-20: deactivation revokes sessions; reactivation does not revive them

- **Original behaviour:** `get_current_user` refuses a non-ACTIVE account (403 `USER_INACTIVE`) and a token whose `tv`
  differs from `employees.token_version` (401 `TOKEN_REVOKED`). Deactivation and reactivation changed only `status`, so
  a token issued before the deactivation was blocked only while the account was inactive and **worked again after
  reactivation**. (Refresh tokens are issued at login but no endpoint accepts them, so nothing else could revive.)
- **Policy applied (the phase brief's default):** deactivation invalidates existing sessions immediately; every token
  issued before it stays invalid after reactivation; reactivation allows a fresh sign-in only; role changes keep the
  current per-request behaviour (role re-read from the database, no revocation); inactive accounts cannot sign in.
- **Root cause:** the deactivate / reactivate handlers in `backend/app/api/v1/endpoints/employees.py` did not use the
  existing B-17 revocation mechanism (`token_version`).
- **Change (backend only, no migration, no new mechanism):** `DELETE /employees/{id}` increments `token_version`
  (atomic SQL `token_version + 1`; for an ADMIN target inside the same guarded UPDATE as the last-active-admin check, so
  that guard's concurrency safety is unchanged), and `PATCH /employees/{id}/reactivate` increments it again. The second
  increment also covers accounts that were deactivated before this change (their version was never bumped). Refused
  attempts (self-deactivation 400, already inactive 400, non-admin 403, step-up refusals in `enforce`) happen before
  any write and change nothing. While inactive the status check still answers first (403 `USER_INACTIVE`, unchanged);
  after reactivation an old token gets 401 `TOKEN_REVOKED`; the frontend then ends the session
  (`/login?reason=session-ended`).
- **Backend tests:** new `tests/rbac/test_deactivation_sessions.py` (7): token works before; 403 immediately after
  deactivation; 401 `TOKEN_REVOKED` on four endpoints after reactivation; fresh sign-in works with employee rights only;
  sign-out everywhere still revokes the fresh session; another ADMIN's sessions (guarded path) revoked; a legacy
  deactivation (status only) does not revive on reactivation; non-admins and the employee themself cannot deactivate or
  reactivate (no status or version change; an inactive token is refused before any handler); refused deactivations do
  not bump versions; role changes keep the per-request policy (demotion applies at once, version untouched). Against
  the old code 3 of the 7 failed (the lifecycle ones); all pass with the change. Focused run (new + token revocation,
  logout, last-active-admin, auth and employee characterization, admin self-actions, step-up API, route permissions,
  Copilot actions): **864 passed**.
- **Playwright:** EMP-C rewritten to the full sequence: the employee's browser session works → ADMIN deactivates (UI)
  → the old token is refused (API 403 `USER_INACTIVE`) and the browser no longer shows the person's data → ADMIN
  reactivates (UI) → the same token is refused on three endpoints (401 `TOKEN_REVOKED`) and the browser is sent to
  sign in (token cleared) → fresh sign-in through the form works with employee pages and APIs only → ADMIN and an
  unrelated employee keep working. The previous assertion (old token 200 after reactivation) was the documented old
  behaviour and is replaced, not weakened.
- **Remaining limitations:** role changes still do not revoke sessions (by design: the role is re-read per request);
  refresh tokens carry `tv` but are not usable anywhere; tokens remain in `localStorage` (B-17 note). The deactivation
  and reactivation are not written to an audit table (business endpoints have no audit trail).

## 14. Update 2026-10-20: Copilot action confirmation end to end

- **Before:** the E2E stack's fake model has no rules, so no proposal could be produced; AI-C, the Copilot side of E2
  and INT-3 were NOT_TESTED in the browser.
- **Test-only accommodation (backend, active only with `AI_LLM_PROVIDER=fake`):** `llm/fake.py::ScriptedFakeLLM`,
  selected in `providers.build_llm_provider` when `AI_FAKE_LLM_SCRIPT` (new setting, `core/config.py`) holds JSON rules.
  Rules are **schema-aware** (a rule answers only its named output model, e.g. `ActionProposal`; the router and
  policy calls still fail exactly as before), match only inside the prompt's `<request>` block (never the listed action
  descriptions), and may copy one captured value (a leave id or a title) into the output. Unmatched or invalid output
  fails like the rule-less fake; a malformed script fails closed (503 `AI_UNAVAILABLE`); `core/production_checks.py`
  refuses the setting in production (the fake provider itself was already refused). The script lives in
  `docker-compose.e2e.yml` (4 rules). No endpoint, authorization, confirmation or business rule changed.
- **New Playwright file `workflow-copilot-actions.spec.ts` (9 tests):**

| Id | Scenario | Result |
|---|---|---|
| ACT-1 | synthetic EMPLOYEE requests a ticket in the Copilot UI → card with the server summary → no ticket before Confirm → Confirm in the UI (exactly one confirm request) → `completed` → the ticket exists with **that employee as owner** (executed with the user's own token through `POST /tickets`), HIGH / IT / OPEN, id = result id → UI "Done:" → audit `ACTION_CONFIRMED` + `ACTION_EXECUTED` for that employee and request | PASS |
| ACT-4 | the same confirmation id again → 400 `AI_CONFIRMATION_INVALID`, still one ticket | PASS |
| ACT-2 | EMPLOYEE asks to approve a leave → 403 `AI_PERMISSION_DENIED`, error shown, no card, leave PENDING, `AUTHORIZATION_DENIED` audited for that employee | PASS |
| ACT-3 | another employee, MANAGER and ADMIN confirm the owner's id → 400 `AI_CONFIRMATION_INVALID` each, nothing created; the owner then confirms → completed, one ticket owned by the owner | PASS |
| ACT-5 | two concurrent API confirms → one 200, one 400, one ticket; double-click Confirm in the UI → one request, one ticket | PASS |
| ACT-6 | expired confirmation | NOT_TESTED in E2E (300 s TTL, no clock control); backend `tests/ai/actions/test_action_flow.py::test_confirmation_expires` PASS |
| ACT-7 | synthetic ADMIN proposes approving a leave, is demoted before confirming → 403 `AI_PERMISSION_DENIED` at confirmation, leave PENDING; after re-promotion the spent id → 400, leave still PENDING | PASS |
| ACT-8 | synthetic ADMIN's card in the UI; the manager approves the leave directly meanwhile → Confirm → `failed` / `LEAVE_NOT_PENDING` from the business API, UI "Not completed:", leave APPROVED by the manager | PASS |
| ACT-9 | invalid parameters (priority URGENT) → the real validator asks for a valid priority; no card, no ticket | PASS |
| ACT-10 | MANAGER announcement: Confirm disabled until a password is typed; wrong password → 403 `AI_STEP_UP_FAILED`, card stays pending with an alert, nothing posted; right password → completed, TEAM announcement by the manager persisted | PASS |
| ACT-11 | the confirm request is lost on the network (intercepted) → UI says the outcome is unknown, one attempt only, no ticket; the server-side confirmation is still valid and can be confirmed deliberately | PASS |

- **Backend regression:** confirmation ownership, single use, expiry, re-checks at confirmation (role, team, target,
  deactivation, state), tampering, execution through the business API, unavailable backend and audit were already
  covered (`tests/ai/actions/test_action_flow.py`, `test_action_api.py`, `test_confirmation_parity.py`,
  `test_step_up.py`); new `tests/ai/test_scripted_fake.py` (8) pins the accommodation (schema-awareness, request-only
  matching, captures, fail-closed script, provider selection, production refusal).
- **Remaining limits:** proposals in the browser come from a script, so no live-model extraction quality is claimed;
  expiry and in-process API outages are backend-only; the Copilot side of E2 (manager limited to team-owned tickets)
  is still backend-only (no script rule for ticket status changes was added).
- **Reproduce:** `docker compose -f docker-compose.e2e.yml up -d --build --wait` (the API image is built by the `setup`
  service: use `--build` or `build setup`), then `npx playwright test e2e/workflow-copilot-actions.spec.ts`.
