# Role-access browser suite (Playwright)

Real-browser and direct-API authorization tests for ADMIN, MANAGER, EMPLOYEE and anonymous sessions. They run only
against the disposable stack in `HRMS-main/docker-compose.e2e.yml` (synthetic data, fake AI providers, no API keys).
Matrix, plan and latest results: `HRMS-main/docs/role-access-matrix.md`, `role-access-test-plan.md`,
`role-access-test-report.md`.

## Run

From `HRMS-main/`:

```bash
docker compose -f docker-compose.e2e.yml config        # check: project hrms-e2e, volume hrms-e2e_e2e_storage, no bind mounts
docker compose -f docker-compose.e2e.yml up -d --build --wait   # build, migrate, seed, index, start (127.0.0.1:18000 / :13000)
cd frontend
cp e2e/e2e.env.example .env.e2e                        # synthetic seeded accounts only; or export the E2E_* variables
npx playwright install chromium                         # once per machine
npm run test:e2e                                        # = npx playwright test
cd ..
docker compose -f docker-compose.e2e.yml down -v        # removes only the hrms-e2e containers and volume
```

Every `up` re-runs migrations, the seed and the policy index on the E2E volume, so each run starts from the same
synthetic data. Global setup (`global-setup.ts`) then creates the extra synthetic people the scenarios need (an
employee outside the manager's team, an inactive employee, a sign-out user and two step-up targets) through the admin
API, with random passwords kept only in `e2e/.runtime/` (git-ignored).

## Rules

- Never point `E2E_WEB_URL` / `E2E_API_URL` anywhere but this local stack (the suite refuses non-loopback hosts).
- Missing credentials or an unreachable stack make tests **skip** (report them as NOT_TESTED), never pass.
- Failure evidence is screenshots only. Traces are off because they record request headers (tokens and the step-up
  password); `E2E_TRACE=1` turns them on for local debugging of this synthetic stack - never share such a trace.
- `STEP_UP_ENFORCEMENT` stays `log` here; blocking behaviour is covered by `backend/tests/rbac/test_step_up_api.py`.
- No live model is called: AI checks use deterministic paths (permissions, rule-based refusals, ownership, audit).
