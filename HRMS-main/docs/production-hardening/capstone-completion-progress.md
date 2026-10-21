# Capstone Completion Programme — Progress Tracker (Assignment 05)

Started 2026-10-19. Brief: "Master Capstone Completion Program" (workspace file, not committed). Baseline gap inventory:
the G-11 enforcement-readiness review and the Assignment 05 gap assessment (both 2026-10-19, delivered in the session,
not committed). Treat the code and freshly observed results as authoritative; the assessments are the starting list.

**Submission readiness and production readiness are separate.** Release status stays **NOT YET PRODUCTION READY**.

## 0. Baseline (Phase 0, 2026-10-19)

| Item | Observed |
|---|---|
| Repository root / branch | `E:/aiprojects/codebasics/AECA_ai_hr_copilot`, `main` |
| HEAD at start | `29ca184` (G-11 admin UI), parent `d570ab6` (G-11 backend) |
| Remote | `origin` = GitHub `hanuandroid/AECA_ai_hr_copilot`; last fetched `origin/main` = `86001f5`; local `main` is **36 ahead, 0 behind** (not fetched in this programme) |
| Working tree | clean except five untracked, protected `writing-block*.md` files (never edited, staged, moved or deleted) |
| `STEP_UP_ENFORCEMENT` | code default `log`; unchanged by this programme |
| Toolchain on this machine | Docker 29.6.1 (image `hrms-test-py311`, Python 3.11); Node 24.13.0; no local Python 3.11 and no backend `.venv` — backend checks run in a throwaway container |

## 1. Phase plan and status

| Phase | Scope | Status | Commit |
|---|---|---|---|
| 0 | Preflight, baseline, this tracker | DONE | `4e37fb8` |
| 1 | Reconcile status documents (README, phase-status, ai_eval_results, CLAUDE.md, handoff) | DONE | `e9ed364` |
| 2 | Architecture doc corrections + deliverable map | DONE | `d59933a` |
| 3 | G-11 operator documentation | DONE | `721d095` |
| 4 | Live evaluation runner: OpenAI selection + answer-level scoring (offline tests only; never executed live) | DONE | `b3ccc40` |
| 5 | `docs/demo-script.md` | DONE | `3dd2c35` |
| 6 | Step-up failure-mode tests on every protected page | DONE | `5d4a20b` |
| 7 | Browser walkthrough evidence (or NOT_RUN checklist) | DONE (checklist; browser NOT_RUN) | `44603da` |
| 8 | Offline evaluation evidence refresh | DONE | `75ac0c1` |
| 9 | Harness and CI reproducibility | DONE | `165e96e` |
| 10 | Final verification at one HEAD | DONE | `e1f3d5e` |
| 11 | Rubric-to-evidence matrix and submission report | DONE | final docs commit |

## 2. Gates (never crossed without explicit approval)

| Gate | Decision required | Status |
|---|---|---|
| A | Push the local commits to GitHub | DONE 2026-10-20 (owner push; `origin/main` = `2225fe7`, checked with `git ls-remote`); CI results in `ci-and-validation.md` §3 |
| B | Live-model evaluation (provider, model, synthetic test set, spending limit) | PARTLY DONE 2026-10-20: one approved OpenAI `gpt-4o-mini` run, router 33/33, policy 11/11 (`ai_eval_results.md` §4.2); SQL and action suites do not exist |
| C | `STEP_UP_ENFORCEMENT=enforce` | OPEN — stays `log` (readiness review: NOT VERIFIABLE) |
| D | Governance: provider data processing, retention/erasure, health data, salary step-up scope, external monitoring, `braces` advisory exception | OPEN — not decided here |
| E | External infrastructure (proxy, monitoring, hosting) | OPEN |

## 3. Phase log

### Phase 2 — Architecture documentation and deliverable map · DONE (2026-10-19)

- `docs/ai_architecture.md`: §2 rewritten from the actual tree. The old list mixed implemented modules with
  never-built plan entries (`api_tools.py`, `action_agent.py`, `audit.py`, `models/ai_audit_log.py`,
  `0018_ai_audit_logs.py`, `/chat/router`, `GET /audit/recent`). New §2.1 maps each assignment deliverable to its
  real location and tests (no compatibility modules added); §2.2 explains the provider abstraction and the AIS-02
  boundary; §2.3 states that authorization and mutation controls are backend-enforced. AI-owned table list
  corrected (§1). Audit-message privacy deviation explained (§3.5). The Phase 0 design diagram and notes stay, marked
  historical and superseded. Streaming note: decided under FastAPI 0.116, not re-probed on 0.133.1. Config block:
  `openai` provider variables added.
- `README.md`: requirement → package / endpoint / tests table in the AI Copilot section.
- Check: every path named in §2, §2.1 and the README table exists (`ls`); remaining mentions of old names are only
  in the mapping table and the sections marked historical.

### Phase 3 — G-11 operator documentation · DONE (2026-10-19)

- `docs/production-hardening/deployment-runbook.md` §2.1 (new): the three modes, the default and approved `log`, the
  covered operations with their log `operation` names, what is not covered (salary, PF/ESI, DOB, policy publication,
  payslips: open scope decision), precedence (process env > `.env` file > code default), lower-case values only
  (other values stop startup), read-once-at-startup with the exact recreate command, a command that prints only the
  mode, rollback to `log`, throttling coupling to `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE`, header hygiene (unverified: no
  proxy chosen), and the readiness blockers.
- `backend/production.env.example`: explicit `STEP_UP_ENFORCEMENT=log` with comments; `backend/.env.example`:
  commented entry. `monitoring-alerts.md`: proposed alert A19 and `X-Step-Up-Password` added to the never-forward rule.
  `README.md`: short G-11 section linking the runbook.
- Facts checked against code: `config.py` (`Literal["off","log","enforce"] = "log"`, pydantic-settings env > `.env`),
  `step_up.py` (log event fields, codes), endpoint operation names, `rate_limit.py` (limit ≤ 0 disables).
  `STEP_UP_ENFORCEMENT` itself was not changed anywhere.

### Fresh baseline (run during Phases 0–3, 2026-10-19 01:01–01:08 IST)

Throwaway `hrms-test-py311` container (Python 3.11.17, FastAPI 0.133.1), API keys blanked, started at `4e37fb8`
(backend code identical to `29ca184`): backend **3154 passed**, 0 failed (344.7 s); offline evaluations
**139/139** (policy 11, SQL 20, router 33, conversation 25, actions 20, assignment 30), `LIVE_MODEL_EVAL = NOT_RUN`;
CI gate tests `.github/scripts` **25 passed**.

### Phase 6 — Step-up failure-mode tests · DONE (2026-10-19)

- `frontend/tests/step-up-admin.test.tsx`: new table-driven block runs four failure paths on **every** protected
  operation (ORG announcement, ORG poll, deactivation, reactivation, sensitive employee edit, employee creation):
  wrong password (dialog stays, field cleared, server text hidden, only a new user attempt resends, header on the
  retry), throttling (message, nothing retried), network failure (dialog closes, "don't know whether this change was
  applied", never resubmitted), Escape (closes, nothing sent, typed value dropped). Each ends with the existing
  `assertPasswordOnlyInHeader` check (not in URLs, bodies, storage, cookies, console or DOM). The create-form filling
  steps moved into `fillNewEmployee` (shared by the existing create test; its assertions unchanged).
  ORG/TEAM, sensitive/non-sensitive and backend-fallback cases were already covered and are unchanged.
- Results: focused file 53 passed (29 → 53); full frontend **238 passed** (16 files); `tsc` 0 errors; gitleaks
  `--no-git` on the test file: no leaks. Deliberate regression (dialog no longer clears the password on submit):
  13 tests failed, then the file was restored unchanged.

### Phase 1 — Status documents reconciled · DONE (2026-10-19)

- `README.md` (AI Copilot banner), `docs/phase-status.md` (top banner + an end-of-file entry for the work recorded
  in other trackers since 2026-10-14), `docs/ai_eval_results.md` (header marks the 2026-10-03 tables historical),
  `CLAUDE.md` (status bullets) and `docs/development-handoff.md` (dated note; §10 marked historical) now agree:
  assignment complete; hardening, RBAC, AI security remediation and G-11 done locally; **NOT YET PRODUCTION READY**;
  latest fresh results = the 2026-10-19 baseline above plus frontend 238 / `tsc` 0 at `5d4a20b`; live-model
  evaluation, browser walkthrough and GitHub CI **not run**; local commits unpublished. This tracker is named as the
  single source of current status so the other files do not need new numbers each time.
- "Ready for final submission" (2026-10-03) removed from the README and the phase-status banner. Old counts (1685,
  86, 2778, 152) remain only in dated historical entries. `ai_eval_results.md` §3 is replaced in Phase 8.

### Phase 4 — Live evaluation runner: Anthropic and OpenAI, answer-level scoring · DONE (2026-10-19; never executed live)

- `evals/run_evals.py`: `select_live_provider(env)` reads the provider, key and model **names** from the process
  environment: `anthropic` (default) needs `ANTHROPIC_API_KEY` (model `AI_LLM_MODEL` or the app default); `openai`
  needs `OPENAI_API_KEY` + `AI_OPENAI_MODEL` (no default model assumed); `fake`/missing values → NOT_RUN; anything else
  → CONFIG_ERROR; no subprocess and no call unless READY. The chosen provider is passed explicitly to every live
  subprocess (no fallback), and each suite must report the provider it actually used, otherwise the run is FAILED.
  A requested live run that fails or is misconfigured sets exit code 1; NOT_RUN does not.
- Live suites: `router_live` (now with provider, model and per-case outcomes) and new `policy_live`
  (`tests/ai/rag/test_policy_eval_live.py`, skipped unless the runner sets `AI_LIVE_EVAL=1`): real Policy RAG answers
  on the 11 policy cases, scored by the offline scorer (retrieval, citation validity, grounding with the expected
  fact, refusal). Calls go through `PolicyRagService` → `observe` → `provider_policy` (boundary kept); each question
  has its own AIS-10 request budget; a provider error is a failed case, never a correct refusal; usage is summed from
  provider-reported numbers and the estimated cost is null unless prices are configured and every call reported usage.
  Helpers: `evals/live_policy_eval.py`. Report: provider, model, start/finish timestamps, per-suite metrics, usage, cost.
- Tests (offline, no network): `tests/ai/audit/test_eval_reporting.py` +15 test functions, +18 collected cases (selection table incl. "a key for the other
  provider never causes a switch" and "no key is echoed"; explicit provider in every subprocess; provider-switch and
  incomplete-suite → FAILED; no subprocess unless READY; exit codes; usage meter never invents cost; provider error =
  failed case; one budget per question; live test is opt-in). One existing test was adapted: `test_live_eval_is_never_faked`
  now passes its environment explicitly (same Anthropic expectation; additionally asserts that the harness's `fake`
  provider is never treated as live).
- Executed: focused files 34 passed / 1 skipped; `tests/ai/audit` + `tests/ai/rag` with `--network none`: 368 passed,
  1 skipped (the opt-in live test); offline `run_evals` 139/139; `--live` without a key → NOT_RUN (exit 0); OpenAI
  without a model → NOT_RUN. Offline wiring probe of the live policy path (`--network none`, quoting stand-in named
  `openai`): 11 cases scored, provider recorded, 8 calls, usage unreported → cost null. gitleaks `--no-git` on the
  changed files: no leaks. **No external model was called** (Gate B).
- Docs: `ai_eval_results.md` §4 and limits, README, `CLAUDE.md`, `ai-components-architecture-and-production-readiness.md`,
  `architecture/phase-8-audit-observability.md` updated.
- Limits: live SQL generation and action extraction are not covered by a live suite; live retrieval uses the offline
  hash embedder; the per-user rate limit and daily token budget (HTTP dependencies) do not apply to the evaluation.

### Phase 5 — Demo script · DONE (2026-10-19)

- New `docs/demo-script.md` (linked from the README): disposable checkout (the dev stack's DB is
  `backend/storage/hrms.db` and `seed.py` recreates it), prerequisites, provider choice and its privacy/cost
  implications (no key → `AI_UNAVAILABLE`; `fake` is not for demos), configuration, build/migrate/seed/ingest commands,
  a read-only synthetic-data check (employee count 1003 = 3 fixed + 1000 Faker), the three seeded accounts, role-based
  prompts with expected outcomes (policy with citations and an unanswerable question, SQL with scope limits, actions
  with confirmation and the Copilot password step, security refusals), audit/usage pages, the G-11 dialogs and what
  `log` mode does (wrong password logged, not blocked; `enforce` not to be switched on), teardown, and the no-key
  automated evidence.
- Every UI label, route, account, prompt and expectation was checked against the code, `seed.py`,
  `assignment_eval.json` and the README. The commands were **not executed** in this phase (the document says so).

### Phase 7 — Browser walkthrough evidence · DONE as a checklist; browser execution **NOT_RUN** (2026-10-19)

- Tooling check: no Playwright, Puppeteer or Cypress in `frontend/package.json` or `node_modules`; adding Playwright
  is workstream 3.7 (not approved); no browser was available to this session. Nothing was installed.
- New `docs/production-hardening/browser-walkthrough.md`: 38 scenarios (setup; ≥5 policy questions; SQL and scope;
  action proposal/confirmation/cancel/authorization; negative security prompts; admin-page access; every G-11 dialog,
  `log`-mode behaviour with the log line, and why blocking is not exercised in a browser), each with expected outcome,
  empty *Actual*, Result **NOT_RUN**, and the automated test that covers the same behaviour (references checked).
  `demo-script.md` points to it. No defects recorded because nothing was run.
- Remaining: a human (or an approved Playwright workstream) must run it; Copilot rows need an approved provider key.

### Phase 8 — Offline evaluation evidence refreshed · DONE (2026-10-19)

- Runs: `python -m evals.run_evals` at `4e37fb8` (baseline) and on the phase 4 tree (`b3ccc40`, `--network none`):
  both **139/139**, all metrics 100%: policy 11 (retrieval, citation validity, grounding, refusal), SQL 20
  (classification, security rejection, scope, no forbidden columns, no mutation), router 33, conversation 25,
  actions 20, assignment 30 (policy, SQL, action, security and conversation categories; no mutation; not executed; no
  sensitive values). `LIVE_MODEL_EVAL = NOT_RUN`. Dataset sizes counted from the JSON files match (139); no case was
  added, removed or changed.
- `docs/ai_eval_results.md`: header and §2 state the fresh run, its environment, and that offline means scripted
  model output; §3 gives the current totals and keeps the 2026-10-03 breakdown as history; the capstone phase 6
  deliberate regression added to the mutation table; new §5 "Known limitations": no live result, 6 pinned false
  refusals on the benign set (G-10), SQL over-refusal, no entailment check in the grounding guard, the deliberate
  deviations, live-suite coverage limits.

### Phase 9 — Harness engineering and CI reproducibility · DONE (2026-10-19)

- **Committed verification script** `HRMS-main/scripts/verify-backend.sh` (replaces developer-specific scratchpad
  scripts): throwaway `python:3.11` container, repository mounted read-only and copied without `backend/.env`,
  `backend/storage`, `node_modules`, `.next` and the workspace notes; provider keys blanked; runs the backend suite,
  offline evaluations and advisory-gate tests. Executed with the default `python:3.11` image: Python 3.11.17,
  **3172 passed, 1 skipped** (the opt-in live test), evaluations **139/139**, gate tests **25 passed**, 7 min 10 s.
  The handoff's old container command (which mounted `backend/.env` and did not blank keys) now points to the script.
- **New `docs/production-hardening/ci-and-validation.md`:** supported environment, every local validation command
  (backend, evaluations, frontend, `tsc`, build, gitleaks, dependency audits, live evaluation with approval), the CI
  job map, CI status (**never run on GitHub; no branch protection**) [corrected 2026-10-20: GitHub runs existed since 2026-10-14; `ci-and-validation.md` §3], pinning status and a proposal (actions to commit
  SHAs, gitleaks image to a digest — not applied: unverifiable without network lookups and a CI run), and the
  `braces` analysis.
- **`braces` evidence (npm audit, 2026-10-19):** runtime dependencies 0 vulnerabilities; all dependencies 5 high +
  2 moderate, all via the dev dependency `tailwindcss@3.4.19` → `chokidar@3.6.0` / `micromatch@4.0.8` →
  `braces@3.0.3` (GHSA-vfj7-8cjw-p6xm, high, `<=3.0.3`, no patched release; only fix offered is `tailwindcss@4`,
  major) and `postcss-selector-parser` (GHSA-rj75-hqrm-r3gf, moderate). Options documented (approve the exception,
  Tailwind 4, no override possible, leave CI red). **Not decided; nothing suppressed** (Gate D). pip-audit not run here.
- **Harness drift fixed:** `.claude/rules/ai-safety.md`, `architecture.md`, `testing.md` named modules and a flag that
  do not exist (`api_tools.py`, `sql_executor.py`, `ai_audit_logs`, `--provider real`); corrected to the real
  locations. Three agents and five skills that still describe the Phase 0 plan got a pointer to the implemented layout
  (`ai_architecture.md` §2.1; §8.2 since the 2026-10-21 restructure) instead of a rewrite. `CLAUDE.md` names the script and the validation document.
- Review of the rest: phase workflow with approval gates, characterization tests, negative tests per rule, deliberate
  regression checks, per-phase local commits and the trackers are in place; resumability now runs through
  `capstone-completion-progress.md` plus the handoff.

### Phase 10 — Final verification at one commit · DONE (2026-10-19, HEAD `165e96e`)

| Check | Command | Result at `165e96e` |
|---|---|---|
| Backend suite (includes step-up, SQL, RAG, authorization, actions, audit-privacy, hardening, characterization) | `sh HRMS-main/scripts/verify-backend.sh` (`python:3.11` container, Python 3.11.17, FastAPI 0.133.1, keys blanked, no `.env`) | **3172 passed, 1 skipped** (opt-in live test), 0 failed, 313.9 s |
| Offline AI evaluations | same script (`python -m evals.run_evals`) | **139/139**; `LIVE_MODEL_EVAL = NOT_RUN` |
| CI advisory-gate tests | same script (`pytest .github/scripts`) | **25 passed** |
| Frontend suite | `npx vitest run` (host Node 24.13.0) | **238 passed** (16 files) |
| TypeScript | `npx tsc --noEmit` | 0 errors |
| Production build | `npm run build` | PASS |
| Secret scan, history | gitleaks v8.18.4 `detect` (78 commits) | no leaks |
| Secret scan, programme files | gitleaks `--no-git` on the 35 files changed since `29ca184` | no leaks |
| Live-model evaluation | — | **NOT_RUN** (Gate B) |
| Browser walkthrough | — | **NOT_RUN** (no browser tooling; checklist ready) |
| CI on GitHub, pip-audit | — | **NOT_RUN** (not pushed, Gate A; pip-audit runs in CI) [corrected 2026-10-20: GitHub runs existed since 2026-10-14; `ci-and-validation.md` §3] |

Cumulative diff review `29ca184..165e96e` (35 files, +1496/−132): **no change** under `backend/app/`, `alembic/`,
`frontend/app|components|lib/` or `storage/` — so no authorization, business-endpoint or migration change; changes are
evaluation tooling and its tests, one frontend test file, documentation, the env templates (`STEP_UP_ENFORCEMENT=log`,
the existing default; `config.py` default still `log`), the harness files and the verification script. The live
evaluation paths call the model only through `observe(...)` (provider boundary kept). Passwords in tests are synthetic
and only in headers or form fields; no real personal data; no protected `writing-block*.md` file is tracked or
changed. Documentation claims were checked against code during each phase.

### Phase 11 — Submission package · DONE (2026-10-19)

- New `docs/capstone-submission-report.md` (linked from the README): verdicts (submission readiness: ready on offline
  evidence once published; production readiness: **NOT YET PRODUCTION READY**), the rubric-to-evidence matrix for the
  nine areas (implementation, tests, demo scenario, status, remaining limitation), verified results at `165e96e`,
  historical results, NOT_RUN checks with what unblocks them, open governance decisions, missing external evidence.
- README, `CLAUDE.md` and `phase-status.md` status lines now quote the final results at `165e96e`.

## 4. Final state (2026-10-19; browser evidence and current counts superseded by §5)

- Verified commit `165e96e`; later commits are documentation only. All work is local (`main` ahead of `origin/main`).
- Completed: Phases 0–11. Browser walkthrough and live-model evaluation are prepared but **NOT_RUN**.
- Gates still open: **A** push (needs approval), **B** live evaluation (provider, model, synthetic set, budget),
  **C** G-11 `enforce` (stays `log`), **D** governance incl. `braces` and salary step-up scope, **E** infrastructure.

## 5. Update (2026-10-20): browser E2E, phone fix, session revocation

Work after the programme above, on `1ce24e6` plus uncommitted changes (committed together with this update):
Playwright role-access and business-workflow suite (`frontend/e2e/`, isolated `docker-compose.e2e.yml` stack),
employee phone-number fix (F-BUG-1), session revocation on deactivation/reactivation, test-only scripted fake proposals
(`AI_FAKE_LLM_SCRIPT`, refused in production), CI `e2e` job. Reports: `../role-access-test-report.md`,
`../deep-workflow-test-report.md`, `../final-security-readiness.md`; commands: `ci-and-validation.md` §6.

| Check | 2026-10-19 (§4, `165e96e`) | 2026-10-20 (local) |
|---|---|---|
| Backend (`scripts/verify-backend.sh`) | 3172 passed, 1 skipped | **3190 passed, 1 skipped** |
| Offline evaluations / CI gate tests | 139/139 / 25 | **139/139 / 25** (scripted model output) |
| Frontend Vitest | 238 | **268** |
| TypeScript / production build | 0 / PASS | **0 / PASS** |
| Automated browser E2E (Playwright, Chromium, fake AI providers) | NOT_RUN (no tooling) | **152 passed**, 0 failed, 0 skipped, 0 flaky |
| Manual browser walkthrough (`browser-walkthrough.md`) | NOT_RUN | **NOT_RUN** (not executed by a person; the automated suite covers most scenarios, but not live-model answers) |
| gitleaks | no leaks (78 commits) | no leaks (80 commits; 46 uncommitted files) |
| Live-model evaluation | NOT_RUN | **router 33/33, policy 11/11** (OpenAI `gpt-4o-mini`, 2026-10-20 20:13–20:14 UTC; routing and policy only) |
| CI on GitHub | NOT_RUN | run 39471328944 on `2225fe7`, and later runs up to 39607243952 on `6338adc` (2026-10-21): backend, frontend, e2e, docker, secret-scan **success**; dependency advisories **failure** (`braces`; Pillow since 2026-10-20) |

Gate A done (push, 2026-10-20); gate B partly done (live routing and policy run); gates C–E unchanged. Release status: **NOT YET PRODUCTION READY**.
