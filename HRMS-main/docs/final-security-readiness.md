# Final security-readiness review — NovaWorks PeopleOps AI Copilot

**Date:** 2026-10-20 · **Code:** `main` at `1ce24e6` plus uncommitted changes (role-access suite, workflow suite,
phone-number fix, session revocation, scripted fake proposals, CI E2E job). **Verdict: NOT YET PRODUCTION READY.**
Passing tests are necessary evidence, not a release decision: the open items in §3 and the release gates in
`production-hardening/release-readiness.md` §6 still apply.

**Classification:** VERIFIED (implementation inspected and an executed test proves it) · PARTIALLY_VERIFIED (proven in
one layer only, or only part of the behaviour) · NOT_TESTED · POLICY_DECISION_REQUIRED · NOT_APPLICABLE.

**Evidence runs (local, 2026-10-20):** backend 3190 passed / 1 skipped (opt-in live test) (3208 passed, 1 skipped after the live-harness tests were added; `verify-backend.sh`, 2026-10-21), offline evaluations 139/139, CI gate tests 25; Vitest 268/268 (277/277 at `6338adc`, 2026-10-21); TypeScript 0 errors; production build PASS; Playwright 152/152 on a fresh isolated stack using the CI e2e job's commands; gitleaks clean (history and all uncommitted files); npm audit runtime 0. GitHub Actions on `6338adc` (2026-10-21, run 39607243952): backend, frontend (incl. `npm run build`), browser E2E, production image builds and secret scan passed; the dependency-advisory gate failed on `braces` (build-time) and Pillow 12.3.0 (runtime) (#18).

## 1. Findings

| # | Area | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | Authentication: login, generic failure for bad password and inactive account, signed tokens, role re-read from the database, forged role cookie ignored | VERIFIED | `backend/tests/characterization/test_auth.py`, `rbac/test_token_revocation.py`; `frontend/e2e/auth.spec.ts` | Tokens are kept in `localStorage`; a strict script CSP is still pending (B-17 note) → see #22 |
| 2 | Role permissions for every API route (80 routes × anonymous/EMPLOYEE/MANAGER/ADMIN) and every page (21 × 4) | VERIFIED | `rbac/test_route_permissions.py` (completeness-checked); `e2e/pages.spec.ts`, `navigation.spec.ts`, `api.spec.ts` | Pages are guarded client-side; the backend refuses every page's data (checked per denied page) |
| 3 | Record ownership and team scope (employees, leaves, tickets, onboarding tasks, policies, conversations) | VERIFIED | `rbac/test_directory_scope.py`, `characterization/test_leaves.py`, `test_tickets.py`, `rbac/test_ticket_status.py`, `rbac/test_policy_audience.py`; `e2e/api.spec.ts`, `workflow-leave/tickets/policies.spec.ts` | Out-of-scope records answer like unknown ids |
| 4 | Tenant / organisation isolation | NOT_APPLICABLE | single-organisation data model (no tenant id) | |
| 5 | Session revocation on deactivation; reactivation does not revive old tokens | VERIFIED | `rbac/test_deactivation_sessions.py` (7); `e2e/workflow-employees.spec.ts` EMP-C | Fixed 2026-10-20 with the existing `token_version` mechanism |
| 6 | Role changes and sessions | POLICY_DECISION_REQUIRED | behaviour VERIFIED: demotion applies on the next request, `token_version` unchanged (`test_deactivation_sessions.py`) | Decide whether a role change must also force a new sign-in |
| 7 | Sensitive employee data: field visibility (financial fields self/ADMIN only), privileged fields ignored in self-service, admin self-edit limits, AI forbidden columns | VERIFIED | `rbac/test_self_profile_fields.py`, `characterization/test_admin_self_actions.py`, `tests/ai/sql/`, `tests/ai/hardening/`; `e2e/workflow-employees.spec.ts` EMP-B | |
| 8 | Phone-number handling (F-BUG-1) | VERIFIED | `frontend/tests/phone.test.ts`, `employee-phone.test.tsx`; `e2e/workflow-employees.spec.ts` | Country-code list hard-coded in the frontend and no backend phone format validation → #19 |
| 9 | Policy RAG audience filtering | VERIFIED (retrieval) / PARTIALLY_VERIFIED (answers) | `tests/ai/rag/test_policy_audience_rag.py`; `e2e/workflow-policies.spec.ts` POL-B (model-free retrieval contrast) | Answer generation in the browser needs a model: not covered |
| 10 | Text-to-SQL scoping and read-only execution | PARTIALLY_VERIFIED | `tests/ai/sql/` (scripted SQL, one test per blocked construct), offline evals 139/139; E2E: MANAGER skill-search query only | No browser test of model-generated SQL (no script rule) |
| 11 | Copilot actions: proposal, review, owner-bound and single-use confirmation, authorization re-check, execution with the user's token through the business API, persistence, audit | VERIFIED | `tests/ai/actions/` (flow, API, parity, step-up); `e2e/workflow-copilot-actions.spec.ts` ACT-1..5, 7..11 | Browser proposals come from a test-only script (#24) |
| 12 | Confirmation expiry | PARTIALLY_VERIFIED | backend `test_confirmation_expires` (controlled clock) | Browser: NOT_TESTED (300 s TTL, no clock control) |
| 13 | Business-API outage during in-process execution | PARTIALLY_VERIFIED | backend `test_backend_unavailable_is_not_retried`; browser: lost confirm request (ACT-11) | An in-process outage cannot be simulated from the browser |
| 14 | Copilot manager ticket scope (stricter than the API, E2) | PARTIALLY_VERIFIED | backend `tests/ai/actions/test_action_parity.py`; E2E: API side and EMPLOYEE capabilities only | No script rule for ticket status proposals |
| 15 | Step-up (G-11) | PARTIALLY_VERIFIED · POLICY_DECISION_REQUIRED | `rbac/test_step_up_api.py` (enforce mode on a test DB), `tests/ai/actions/test_step_up.py`; `e2e/step-up.spec.ts`, ACT-10 | Runs in `log` mode: a wrong or missing password is logged, **not blocked**, on the business API (the Copilot's own password check does block). `enforce` needs approval; readiness review: NOT VERIFIABLE (no proxy, unknown external clients, no observation period) |
| 16 | AI audit events (requests, refusals, denials, action confirmation/execution) | VERIFIED | `tests/ai/audit/`; `e2e/workflow-copilot.spec.ts` AI-B, ACT-1/2 | Written right after the response (tests poll) |
| 17 | Business-API refusals and employee deactivation/reactivation in an audit store | POLICY_DECISION_REQUIRED | behaviour VERIFIED: log lines only, nothing persisted (`e2e/api.spec.ts`) | Decide whether these need persistent audit records |
| 18 | Dependency security | PARTIALLY_VERIFIED · POLICY_DECISION_REQUIRED | npm audit 2026-10-20: runtime 0; all 5 high + 2 moderate via build-time `tailwindcss@3` (`braces`, no patched release; absent from the production web image). **Pillow 12.3.0 (runtime):** eight advisories published 2026-10-20, fixed only in 13.0.0, which `fastembed` does not allow (investigation 2026-10-21; `ci-and-validation.md` §5); pip-audit itself: CI only | `braces` exception or Tailwind 4, and a Pillow exception with a pin or a `fastembed` replacement: owner decisions; the CI dependency gate fails until then; nothing approved |
| 19 | Phone validation policy (country codes, backend format) | POLICY_DECISION_REQUIRED | frontend list of 7 codes in `lib/phone.ts`; the backend checks length only (update 6–20, create 10–20 characters) | |
| 20 | Concurrency | VERIFIED (SQLite, single worker) | last-active-admin race `rbac/test_last_active_admin.py`; duplicate confirmation ACT-5; double submission INT-1 | Other databases and multi-worker: NOT_TESTED (production refuses more than one worker) |
| 21 | Failure recovery in the UI (network loss, stale screens, validation then correction, provider outage) | VERIFIED | `e2e/workflow-integrity.spec.ts`, `workflow-copilot.spec.ts` AI-D, ACT-11; `frontend/tests/step-up-admin.test.tsx` | |
| 22 | Token storage and browser hardening | POLICY_DECISION_REQUIRED | tokens in `localStorage`; security headers present; strict `script-src` CSP not adopted | Existing B-17 / CSP decision |
| 23 | Data governance (provider data processing, retention, erasure, health data, backups) | POLICY_DECISION_REQUIRED | gates G-01..G-10, `release-readiness.md` §6 | |
| 24 | Test-only scripted fake model cannot reach production | VERIFIED | `tests/ai/test_scripted_fake.py` (production refusal, fake-only selection, fail-closed script, schema-aware rules) | Production also refuses the fake provider itself |
| 25 | Real-model answer and action-extraction quality | PARTIALLY_VERIFIED | one live run, 2026-10-20, OpenAI `gpt-4o-mini`: router 33/33, policy answers 11/11 (`ai_eval_results.md` §4.2) | Action extraction, SQL generation and injection resistance with a real model: NOT_TESTED |
| 26 | Deployment: reverse proxy, TLS, header logging, monitoring and alerts, backups and restore drill | NOT_TESTED | runbook and alert catalogue exist (`production-hardening/`) | No target environment. GitHub CI itself runs (run 39471328944; all jobs pass except the advisory gate, #18) |
| 27 | Production configuration guard (secrets, hosts, workers, fake providers, tracing, scripted fake) | VERIFIED | `tests/ai/hardening/test_production_config.py`, `test_scripted_fake.py`; CI `docker` job command | |
| 28 | Secrets in the repository | VERIFIED | gitleaks: full history and every uncommitted file clean (2026-10-20) | `.gitleaksignore`: five exact synthetic-fixture fingerprints |

## 2. Coverage by layer

| Layer | Evidence |
|---|---|
| Backend unit/integration | `scripts/verify-backend.sh` (full suite, offline evaluations, gate tests) |
| Browser (Chromium) | 152 Playwright tests: 21 pages × 4 sessions, navigation, auth/session lifecycle, direct API authorization, step-up dialogs, employee/leave/ticket/policy/Copilot workflows, Copilot action lifecycle, integrity and recovery |
| API (direct, real tokens) | `e2e/api.spec.ts` and the API checks inside every workflow test |
| Concurrency | backend last-admin race; duplicate confirmation; double submission |
| Failure recovery | network loss, stale data, simulated provider outage, business refusal during execution |
| Live model | one run (2026-10-20): routing and policy answers only; SQL and actions NOT_TESTED |

## 3. Open decisions and gaps (not decided here)

1. Branch protection; pin actions to commit SHAs (GitHub CI runs; only the advisory gate fails, #18).
2. Live-model evaluation of SQL generation and action extraction (Gate B; routing and policy answers ran on 2026-10-20).
3. G-11 `enforce` mode (Gate C) after proxy/header, client inventory, observation and alerting evidence.
4. Role changes forcing a new sign-in (#6); persistent audit of business refusals and of deactivation/reactivation (#17).
5. Phone validation policy and country-code list (#19).
6. `braces` advisory exception or Tailwind 4 upgrade; Pillow runtime advisories (#18).
7. Token storage / strict CSP (#22); data-governance gates (#23).
8. Browser coverage gaps: confirmation expiry, in-process API outage, Copilot manager ticket scope, model-generated SQL
   (all covered by backend tests only).
9. Deployment and operations evidence: proxy, TLS, monitoring, backups and restore drill in a target environment (#26).
