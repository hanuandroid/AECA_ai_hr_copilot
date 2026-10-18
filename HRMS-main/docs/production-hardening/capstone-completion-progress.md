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
| 0 | Preflight, baseline, this tracker | DONE | (this commit) |
| 1 | Reconcile status documents (README, phase-status, ai_eval_results, CLAUDE.md, handoff) | DONE | see log |
| 2 | Architecture doc corrections + deliverable map | DONE | see log |
| 3 | G-11 operator documentation | DONE | see log |
| 4 | Live evaluation runner: OpenAI selection + answer-level scoring (offline tests only; never executed live) | PENDING | |
| 5 | `docs/demo-script.md` | PENDING | |
| 6 | Step-up failure-mode tests on every protected page | DONE | see log |
| 7 | Browser walkthrough evidence (or NOT_RUN checklist) | PENDING | |
| 8 | Offline evaluation evidence refresh | PENDING | |
| 9 | Harness and CI reproducibility | PENDING | |
| 10 | Final verification at one HEAD | PENDING | |
| 11 | Rubric-to-evidence matrix and submission report | PENDING | |

## 2. Gates (never crossed without explicit approval)

| Gate | Decision required | Status |
|---|---|---|
| A | Push the local commits to GitHub | OPEN — awaiting user approval |
| B | Live-model evaluation (provider, model, synthetic test set, spending limit) | OPEN — runner prepared offline only |
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
