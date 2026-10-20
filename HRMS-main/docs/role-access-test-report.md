# Role-access test report

**Runs:** 2026-10-20, on `main` at `1ce24e6` plus the uncommitted role-access changes (listed in §8). Release status:
**NOT YET PRODUCTION READY**. No live model was called; no claim is made about live-model answer quality.

## 1. What was discovered

- **Identities:** ADMIN, MANAGER, EMPLOYEE (roles), plus the INACTIVE status and anonymous visitors. Tenant isolation:
  NOT_APPLICABLE (single organisation; no tenant id anywhere) — isolation is self / team / organisation scope.
- **Pages:** 21 `page.tsx` files (`role-access-matrix.md` §2). **API:** 80 method + path pairs under `/api/v1`, all
  classified per identity by `backend/tests/rbac/test_route_permissions.py`. **AI capabilities:** 24 (ADMIN 24,
  MANAGER 17, EMPLOYEE 6). **Step-up operations:** 6.

## 2. Results

| Suite | Command | Executed | Passed | Failed | Skipped / NOT_TESTED |
|---|---|---:|---:|---:|---:|
| Backend baseline (before changes) | `sh HRMS-main/scripts/verify-backend.sh` | 3173 | 3172 | 0 | 1 (opt-in live-model test) |
| Backend final (with the new regression test) | same | 3176 | **3175** | 0 | 1 (same) |
| Offline AI evaluations (baseline and final) | same script | 139 | 139 | 0 | live: NOT_RUN |
| CI gate tests (baseline and final) | same script | 25 | 25 | 0 | 0 |
| Frontend Vitest baseline | `npm test` | 238 | 238 | 0 | 0 |
| Frontend Vitest final | `npm test` | 238 | 237 | **1** (pre-existing timing flake, §5) | 0 |
| TypeScript (incl. the new e2e files) | `npx tsc --noEmit` | — | 0 errors | — | — |
| **Playwright E2E (fresh stack)** | `npm run test:e2e` | **118** | **118** | **0** | **0** |
| E2E with credentials removed (skip check) | same, no `E2E_*` values | 19 | 0 | 0 | 19 skipped (= NOT_TESTED, never PASS) |

### Playwright by file (final run, fresh `hrms-e2e` stack, Chromium 153 / Playwright 1.63.0)

| File | Tests | Scope |
|---|---:|---|
| `pages.spec.ts` | 85 | inventory check + 21 pages × 4 sessions (84 combinations) |
| `api.spec.ts` | 15 | direct API: records, directory, refused mutations with unchanged state, self-service escalation, admin self-actions, leaves, tickets, policy audiences, AI capabilities, refusals, conversation and confirmation ownership, AI audit |
| `auth.spec.ts` | 8 | form sign-in ×3, wrong password, inactive account and old token, anonymous 401 on 17 groups, forged role cookie, sign out everywhere |
| `navigation.spec.ts` | 4 | sidebar ×3 roles with click-through; role-dependent controls |
| `step-up.spec.ts` | 6 | all six step-up operations in log mode, TEAM/non-sensitive no-dialog cases, wrong/missing password logged but not blocked |

### Page × session combinations

| Session | Planned | Executed | Passed | Failed | NOT_TESTED |
|---|---:|---:|---:|---:|---:|
| Anonymous | 21 | 21 | 21 | 0 | 0 |
| EMPLOYEE | 21 | 21 | 21 (13 allowed + `/`, `/login` → dashboard; 6 denied with backend refusal checked) | 0 | 0 |
| MANAGER | 21 | 21 | 21 (16 allowed + 2 redirects; 3 denied with backend refusal checked) | 0 | 0 |
| ADMIN | 21 | 21 | 21 (19 allowed + 2 redirects) | 0 | 0 |
| **Total** | **84** | **84** | **84** | **0** | **0** |

## 3. Role-by-role summary (all PASS unless stated)

- **Anonymous:** every page → server-side redirect to `/login`; 17 protected API groups → 401; health 200.
- **EMPLOYEE:** 13 pages; 6 denied (`/employees`, `/employees/projects`, `/employees/payslips`, `/employees/new`, `/admin/ai-audit`, `/admin/ai-usage`) with the backend refusing each page's API (403); own
  records only, others identical to unknown ids; cannot change other employees, approve leave, change ticket status
  as owner, see MANAGERS/ADMINS policies, read others' AI events or metrics; profile update cannot set privileged
  fields; 6 AI capabilities exactly.
- **MANAGER:** team members visible, outsider hidden (also in search); another team's leave hidden and unchanged; a
  direct report's leave approved; no employee create/edit/deactivate, catalogue creation or policy publishing; cannot
  upload ADMINS-audience policies; no admin pages or AI metrics; 17 AI capabilities exactly.
- **ADMIN:** everything above permitted; own role change, own deactivation and own bank edit refused; all six
  step-up dialogs send the password only in `X-Step-Up-Password`; 24 AI capabilities.
- **INACTIVE:** sign-in refused with the same generic error as a bad password; a token issued before deactivation is
  refused (403 `USER_INACTIVE`) on business and AI endpoints.

## 4. Security findings and fixes

| Id | Finding | Severity | Action |
|---|---|---|---|
| T-1 | No backend test pinned that `PUT /employees/me` ignores privileged fields (role, status, email, password, manager, salary, bank, PAN, token version). The behaviour was already correct (fixed field list in the handler). | Test gap | **Added** `backend/tests/rbac/test_self_profile_fields.py` (3 roles, passes). Deliberate-regression check: injecting an email change into the handler made all 3 fail. No application code changed. |
| — | No backend authorization gap was found by the browser or API tests. | — | — |

No authorization implementation was changed and no existing test was weakened.

## 5. Other findings (not security; documented, not fixed — outside the approved scope)

| Id | Finding | Evidence |
|---|---|---|
| F-UX-1 | The login page shows "Login failed" instead of the API's message: it reads `error.message`, while the API nests it under `detail.error`. Still generic (no account disclosure). | `auth.spec.ts` |
| F-UX-2 | After deactivate/reactivate, the success note ("Employee deactivated.") is cleared immediately by the list reload (`loadData` calls `setSuccess("")`). | `step-up.spec.ts` |
| F-TEST-1 | **(Resolved 2026-10-20: per-file 15 s timeout, see deep-workflow-test-report.md §12.) Pre-existing flaky Vitest tests:** the four employee-creation failure-path tests in `frontend/tests/step-up-admin.test.tsx` take 4.6–5.0 s against Vitest's default 5 s timeout. One timed out in 3 of 6 full runs today (and in 2 of 2 single-file runs). The tests' assertions are correct; they need a longer per-test timeout. Not changed here (not a role-access file). | Vitest runs, §2 |
| F-DOC-1 | E2E email domains must be real-looking: the backend rejects reserved `.test` domains (422); the suite uses `hrms-e2e.dev`. | global setup |

## 6. Limitations and what was not verified

- **Live-model behaviour: NOT_TESTED.** The E2E stack uses fake model and embedding providers with empty keys. AI
  checks were limited to deterministic paths (capabilities, rule-based refusals, ownership, audit). RAG audience
  filtering inside answers, SQL scoping of generated SQL, and cross-user confirmation of a real proposal are covered
  by backend tests with scripted models only.
- **Step-up blocking (`enforce`): not exercised in a browser** — the stack keeps `log` (as required); blocking is
  covered by `backend/tests/rbac/test_step_up_api.py`.
- **Last-active-admin race:** backend tests only (`rbac/test_last_active_admin.py`).
- **Browser coverage:** Chromium only (as approved); desktop viewport only (the sidebar is hidden on small screens).
- **Business-API refusals are not stored for audit** (log lines only) — current behaviour, documented in the matrix.
- The E2E stack raises the login rate limit (also the step-up attempt limit) to 600/min so the suite can sign in
  repeatedly; throttling is covered by backend tests.
- Failure evidence is screenshots only; traces are off by default because they would record tokens and the step-up
  password (`E2E_TRACE=1` enables them locally). The JSON results contain no passwords or tokens (checked).

## 7. Reproduce

```bash
# From HRMS-main/
sh scripts/verify-backend.sh                                   # backend + offline evals + gate tests (from the repo root: sh HRMS-main/scripts/verify-backend.sh)
docker compose -f docker-compose.e2e.yml config                # isolation check: project hrms-e2e, volume hrms-e2e_e2e_storage, no bind mounts
docker compose -f docker-compose.e2e.yml up -d --build --wait
cd frontend
cp e2e/e2e.env.example .env.e2e                                # synthetic seeded accounts of the E2E stack only
npx playwright install chromium
npm run test:e2e                                               # 118 tests; results in test-results/results.json
npm test && npx tsc --noEmit
cd .. && docker compose -f docker-compose.e2e.yml down -v      # removes only the hrms-e2e containers and volume
```

## 8. Changed files (uncommitted, unstaged)

- New: `HRMS-main/docker-compose.e2e.yml`; `frontend/playwright.config.ts`; `frontend/e2e/` (`README.md`,
  `e2e.env.example`, `global-setup.ts`, `pages.spec.ts`, `navigation.spec.ts`, `auth.spec.ts`, `api.spec.ts`,
  `step-up.spec.ts`, `support/{env,api,session,suite}.ts`); `backend/tests/rbac/test_self_profile_fields.py`;
  `docs/role-access-matrix.md`, `docs/role-access-test-plan.md`, `docs/role-access-test-report.md`.
- Modified: `frontend/package.json` and `package-lock.json` (`@playwright/test` 1.63.0 exact, `test:e2e` script);
  `frontend/vitest.config.mts` (comment only); `frontend/.dockerignore` (keep e2e out of the web image);
  `HRMS-main/.gitignore` (Playwright outputs, `e2e/.runtime/`, `frontend/.env.e2e`).
- Local, git-ignored: `frontend/.env.e2e` (synthetic demo accounts), `frontend/e2e/.runtime/`, `frontend/test-results/`.
