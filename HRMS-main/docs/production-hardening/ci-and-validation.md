# Local validation and CI status

**Updated 2026-10-20 (CI automation and final security-readiness review; first version 2026-10-19).** How to
reproduce every check locally, what the CI workflow would run, and what is still unverified. Final security review:
[`../final-security-readiness.md`](../final-security-readiness.md). Release status: **NOT YET PRODUCTION READY**.

## 1. Supported environment

| Component | Version | Declared in |
|---|---|---|
| Python (backend) | 3.11 (verification used 3.11.17) | `backend/Dockerfile` (`python:3.11-slim`), `.github/workflows/ci.yml`, `.python-version` |
| Node.js (frontend) | 24.21.0 for CI and production; local checks on 24.13.0 | `frontend/Dockerfile`, `ci.yml`, `frontend/.nvmrc` |
| Docker | Engine or Desktop with Compose v2 | — |
| Database | SQLite only (no other engine verified) | — |

## 2. Local validation commands

No API key and no `.env` are needed for any of these; tests build a throwaway database and use fake providers.

| Check | Command (from the repository root unless noted) | Notes |
|---|---|---|
| Backend tests + offline evaluations + advisory-gate tests | `sh HRMS-main/scripts/verify-backend.sh` | Throwaway `python:3.11` container; repository mounted read-only and copied without `backend/.env`, `backend/storage`, `node_modules` and workspace notes; provider keys blanked. Override the image with `VERIFY_IMAGE=…`. |
| Same, with a local Python 3.11 | `cd HRMS-main/backend && python -m pytest -q && python -m evals.run_evals && python -m pytest -q ../../.github/scripts` | After `pip install -r requirements-dev.txt` in a 3.11 virtual environment (`development-handoff.md` §6). Some tests need a git checkout. |
| Frontend tests | `cd HRMS-main/frontend && npm ci && npm test` | Vitest + Testing Library |
| TypeScript | `cd HRMS-main/frontend && npx tsc --noEmit` | |
| Production build | `cd HRMS-main/frontend && npm run build` | Writes `.next/` (git-ignored) |
| Secret scan (history) | `docker run --rm -v "$PWD:/repo" -w /repo zricethezav/gitleaks:v8.18.4 detect --source /repo --redact --no-banner` | Same command as CI. On Git Bash use `MSYS_NO_PATHCONV=1` and `$(pwd -W)`. `.gitleaksignore` holds five exact fingerprints of synthetic test fixtures; never add broad rules. |
| Dependency advisories | `cd HRMS-main/frontend && npm audit` / `npm audit --omit=dev`; `pip-audit -r HRMS-main/backend/requirements.txt` | Network calls to the package registries. The pass/fail decision is `.github/scripts/advisory_gate.py`. |
| Live-model evaluation | `python -m evals.run_evals --live` | **Only with approval** (external calls, cost). See `../ai_eval_results.md` §4. |
| Browser E2E (Playwright, Chromium) | from `HRMS-main/`: `docker compose -f docker-compose.e2e.yml up -d --build --wait`, then `cd frontend && cp e2e/e2e.env.example .env.e2e && npx playwright install chromium && npm run test:e2e`, then `docker compose -f docker-compose.e2e.yml down -v` | Disposable stack (project `hrms-e2e`, own volume, no bind mounts, no env file, fake AI providers + scripted proposals, synthetic seed, `STEP_UP_ENFORCEMENT=log`). Rebuild the API image with `--build` or `build setup` (the API image is built by the `setup` service). `frontend/e2e/README.md`. |

## 3. CI workflow (`.github/workflows/ci.yml`)

| Job | Steps |
|---|---|
| backend | security regression suites first, then the rest of the suite; offline evaluations; advisory-gate unit tests |
| frontend | `npm ci`, `npm test`, `npx tsc --noEmit`, `npm run build` |
| dependency-audit | pip-audit and npm audit, then the advisory gate (fails on critical, and on high/unknown without an approved exception) |
| e2e (**added 2026-10-20**) | `npm ci`; `npx playwright install --with-deps chromium`; isolation check of `docker-compose.e2e.yml` (project name, named volumes only, no env file, `STEP_UP_ENFORCEMENT=log`); `up -d --build --wait`; synthetic credentials from `e2e/e2e.env.example`; `npx playwright test`; on failure the API logs and the Playwright results (JSON + screenshots, no traces) are uploaded for 7 days; `down -v` of `hrms-e2e` always |
| docker | production images build; the API image refuses an unsafe configuration; both images run as non-root |
| secret-scan | gitleaks over the full history |

**e2e job cost and dependencies:** about 15-20 minutes per run on a standard `ubuntu-24.04` runner (two uncached
image builds, Chromium with system packages, ~4 minutes of tests); downloads from Docker Hub, PyPI (inside the image
build), the npm registry, the Playwright CDN and apt mirrors; **no secrets and no paid services**. Its commands were
executed locally on 2026-10-20 (same isolation check, stack, credentials file and test command).

**Status:** the workflow has **never run on GitHub** (the local commits are not pushed; Gate A). Remote CI needs a push,
which is not approved, so no remote result exists. Branch protection is
not configured. Every job's commands have local equivalents above; their latest local results are in
`capstone-completion-progress.md`. The `dependency-audit` job is **expected to fail** until the `braces` decision (§5).

## 4. Pinning

| Item | Current | Proposal (not applied) |
|---|---|---|
| GitHub actions | major tags: `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` | pin each to the full commit SHA of the release in use, with the tag as a comment; needs the SHAs looked up on GitHub and one CI run to confirm |
| gitleaks image | version tag `zricethezav/gitleaks:v8.18.4` | pin by image digest (`@sha256:…`) after pulling and recording it |
| Tool versions | Python 3.11, Node 24.21.0, `pip-audit==2.9.0` | already exact |
| Dependencies | exact pins in `requirements*.txt` and `package-lock.json`; `frontend/tests/dependency-pins.test.ts` | — |

The SHA and digest pins were not applied here: they cannot be verified without network lookups and a CI run, and a
wrong SHA would break CI silently. They remain a release-gate item (`release-readiness.md` §6, gate 3).

## 5. The `braces` advisory (decision required; not decided here)

Evidence from `npm audit` on 2026-10-19:

| Scope | Result |
|---|---|
| Runtime dependencies (`npm audit --omit=dev`) | **0** vulnerabilities |
| All dependencies | 5 high, 2 moderate, all through the dev dependency `tailwindcss@3.4.19` |

Dependency path: `tailwindcss@3.4.19` → `chokidar@3.6.0` and `micromatch@4.0.8` → `braces@3.0.3`. Advisory
GHSA-vfj7-8cjw-p6xm (high): stack-exhaustion denial of service from deeply nested brace patterns; affected range
`<=3.0.3`; npm offers no patched `braces`, only `tailwindcss@4.3.3` (a major upgrade). `chokidar`, `micromatch`,
`fast-glob` and `tailwindcss` are reported high only because they depend on `braces`. Separately,
`postcss-selector-parser` (GHSA-rj75-hqrm-r3gf, moderate, through tailwind) is reported but does not fail the gate.

Exposure: build time only. The glob patterns come from the repository's own Tailwind configuration, not from users,
and the package is not in the production image's runtime dependencies.

Options for the owner (`.github/security/advisory-exceptions.json` holds a PROPOSED draft):
1. **Approve the exception:** name an owner and an approver and set `status: APPROVED` (expiry at most 180 days). The
   gate then passes; revisit at expiry.
2. **Upgrade to Tailwind 4:** removes the path; needs a configuration and CSS migration and a visual check of every
   page. A separate, approved change.
3. **An npm override for `braces`:** not possible today, because no fixed `braces` release exists.
4. **Leave it:** the dependency-audit job stays red, by design.

No exception was approved and no advisory was suppressed in this programme (Gate D).

## 6. Local verification of 2026-10-20 (the same commands the CI jobs run)

| Check | Baseline (end of the previous phase) | Final (this phase) |
|---|---|---|
| Backend `scripts/verify-backend.sh` | 3190 passed, 1 skipped; evaluations 139/139; gate 25 | 3190 passed, 1 skipped; evaluations 139/139; gate 25 |
| Vitest | 268/268 | 268/268 |
| TypeScript | 0 errors | 0 errors |
| Production build | PASS (earlier run) | PASS |
| Playwright (fresh isolated stack, CI job commands) | 152/152 | 152/152 (0 skipped, 0 flaky), run with the e2e job's commands |
| gitleaks history / uncommitted files | clean / clean | clean (80 commits) / clean (46 files) |
| npm audit | runtime 0; all: 5 high + 2 moderate (`tailwindcss@3` chain, `braces`) | unchanged |
| pip-audit | not run locally (CI job) | NOT_TESTED locally |
| GitHub CI | NOT_TESTED (never run) | NOT_TESTED (needs a push; not approved) |
