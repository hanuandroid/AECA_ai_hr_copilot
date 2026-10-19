# Capstone submission report — Assignment 05, AI HR Operations Copilot

**Date:** 2026-10-19 · **Verified commit:** `165e96e` (branch `main`) · Programme record:
[`production-hardening/capstone-completion-progress.md`](production-hardening/capstone-completion-progress.md)

## 1. Verdicts

| Question | Verdict |
|---|---|
| **Submission readiness** | **Ready for academic submission on offline evidence, once published.** Every rubric area has an implementation, tests and a demo scenario, and every offline check passes at one commit (§3). Three things a reviewer should know: the work is only local until a push is approved (Gate A); no live-model evaluation has been run, so real-model answer quality is unmeasured (runner ready; Gate B); the browser walkthrough has not been executed (checklist ready). |
| **Production readiness** | **NOT YET PRODUCTION READY.** One of seven release gates is met (`production-hardening/release-readiness.md` §6). Governance decisions, live evaluation, CI on GitHub, proxy/monitoring, retention/backup and browser checks remain. |

## 2. Rubric-to-evidence matrix

Tests are under `HRMS-main/backend/tests/` unless they start with `frontend/`. Demo ids refer to
[`demo-script.md`](demo-script.md) and [`production-hardening/browser-walkthrough.md`](production-hardening/browser-walkthrough.md).

| # | Requirement | Implementation | Tests and evaluations | Demo scenario | Status | Remaining limitation |
|---|---|---|---|---|---|---|
| 1 | Policy RAG (ingestion, chunking, embeddings, vector search, grounded answers, sources, insufficient context) | `services/ai/rag/`, `services/ai/embeddings.py`, `scripts/ingest_policies.py`; `POST /api/v1/chat/policy` | `ai/rag/` (incl. guard and audience tests); `evals/policy_eval.json` 11/11 and the 5 policy cases of `assignment_eval.json` (offline) | demo §4.1; walkthrough B1–B7 | Implemented; offline-verified | Real-model answer quality not measured (live suite ready, not run); no entailment check in the grounding guard |
| 2 | Safe read-only Text-to-SQL | `services/ai/sql/` (sqlglot parse, validator, schema registry, scope rewrite with bound parameters, read-only executor with `query_only` and an authorizer); `POST /api/v1/chat/sql` | `ai/sql/` (one test per blocked construct), `ai/hardening/`; `evals/sql_eval.json` 20/20 | demo §4.2, §4.4; walkthrough C1–C6, E6 | Implemented; offline-verified | Live SQL-generation accuracy not measured; some legitimate unusual queries are refused |
| 3 | HR task automation through backend APIs | `services/ai/actions/` (registry of existing routes, typed params, single-use user-bound confirmation, `client.py` calling the existing endpoint with the user's token; password for 2 high-impact actions); `POST /chat/actions`, `/chat/actions/confirm` | `ai/actions/` (registry, parity with business endpoints, confirmation replay, step-up); `evals/action_eval.json` 20/20 | demo §4.3; walkthrough D1–D7 | Implemented; offline-verified | Live action-extraction accuracy not measured |
| 4 | RBAC and backend authorization | `services/ai/authorization/` (deterministic matrix, DB-loaded role/team, INACTIVE rejected); business-route RBAC remediation; G-11 password step-up (`services/step_up.py`, `log` mode) | `ai/authorization/`, `rbac/` (incl. route-permission matrix, step-up API), `characterization/` | demo §4.4, §6; walkthrough D7, E1–E7, F1–F9 | Implemented; verified | `enforce` mode for G-11 not approved; frontend role checks are UX only by design |
| 5 | Frontend workflow | `frontend/app/copilot/`, `components/copilot/` (chat, sources, data tables, confirmation cards with cancel and password), `/admin/ai-usage`, `/admin/ai-audit`; step-up dialog `components/security/step-up-dialog.tsx` | `frontend/tests/` (Copilot shell and scenarios, page access, step-up dialogs for all six protected operations) | demo §4–6; walkthrough all | Implemented; verified with mocked HTTP | **Browser walkthrough NOT_RUN**; no browser automation (Playwright not approved) |
| 6 | Audit logging | `services/ai/audit/`, append-only table `ai_audit_events` (migration 0020; triggers); `GET /api/v1/ai/audit[/metrics]` | `ai/audit/` (append-only, no prompt/answer/PII persisted, metrics, eval reporting) | demo §5; walkthrough E7 | Implemented; verified | Deliberate deviation: no message text in audit (length + keyed fingerprint; redacted text only in the owner's conversation store) |
| 7 | AI evaluation and security testing | `evals/` (6 offline suites, 139 cases; live runner for Anthropic/OpenAI), `tests/ai/hardening/` (adversarial matrix, red-team corpus, PII corpus) | `evals/run_evals.py` 139/139 offline; `ai/hardening/`, `ai/test_pii_detection_corpus.py`, `ai/test_provider_policy.py`; deliberate-regression checks all caught | demo §8 | Offline: complete. Live: NOT_RUN | No live-model result; 6 pinned false refusals on a benign set (G-10) |
| 8 | Observability and operational readiness | request ids, provider-call audit (tokens, latency, cost when priced), `/admin/ai-usage`, budgets and limits (`limits.py`), production configuration checks, retention `--check`, runbook, alert catalogue | `ai/test_limits.py`, `ai/hardening/test_production_config.py`, `ai/test_retention.py`, `ai/audit/` | demo §5; runbook | Implemented locally | No monitoring tool, proxy or alert owners (G-08, Gate E); external tracing deliberately off; single worker only |
| 9 | Reproducibility and documentation | README (deliverable map), `ai_architecture.md` §2.1, `ai_permissions_matrix.md`, `ai_eval_results.md`, `demo-script.md`, `ci-and-validation.md`, `scripts/verify-backend.sh`, CI workflow | this report §3 | demo §1–2, §7 | Documented and locally reproducible | CI never ran on GitHub; dependency gate fails by design until the `braces` decision |

## 3. Verified results (fresh, at `165e96e`)

| Check | Command | Result |
|---|---|---|
| Backend suite (all areas, incl. step-up, SQL, RAG, authorization, actions, audit privacy) | `sh HRMS-main/scripts/verify-backend.sh` (Python 3.11.17 container, keys blanked, no `.env`) | **3172 passed, 1 skipped** (the opt-in live test) |
| Offline AI evaluations | same script | **139/139** (policy 11, SQL 20, router 33, conversation 25, actions 20, assignment 30) |
| CI advisory-gate tests | same script | 25 passed |
| Frontend suite | `npm test` | **238 passed** |
| TypeScript / production build | `npx tsc --noEmit` / `npm run build` | 0 errors / PASS |
| Secret scan | gitleaks v8.18.4, full history (78 commits) and the files changed in this programme | no leaks |

Offline evaluations use scripted model output: they verify pipelines and security boundaries, not live-model quality.
Commands and environment: `production-hardening/ci-and-validation.md`. Commits after `165e96e` change documentation only.

## 4. Historical results (not re-run in this programme)

- Docker production smoke test (2026-10-17): unsafe configuration refused, non-root images, migrations to head on a
  fresh volume, read-only health, spoofed Host 400 (`development-handoff.md` §10).
- Live OpenAI provider smoke test, 10 checks (2026-10-16, `gpt-4o-mini`): not a quality evaluation
  (`ai_eval_results.md` §4.1).
- Migration downgrade checks and the RAG retrieval calibration proposal (`development-handoff.md` §10,
  `rag-calibration.md`).

## 5. NOT_RUN checks

| Check | Why | What unblocks it |
|---|---|---|
| Live-model evaluation (`run_evals --live`) | external calls and cost need approval (Gate B / G-06) | approved provider, model, synthetic test set and spending limit |
| Browser walkthrough | no browser automation; not executed by a person yet | a manual run of `browser-walkthrough.md`, or approval of Playwright (3.7) |
| CI on GitHub | commits not pushed (Gate A) | approval to push, then one workflow run |
| G-11 `enforce` in a real deployment | not approved (Gate C) | proxy/header evidence, client inventory, `log`-mode observation, alerts |
| pip-audit | needs registry access during package installation; CI runs it | a CI run |

## 6. Governance decisions still open (Gate D; not decided in this programme)

- G-01/G-02: provider data-processing terms; whether health-related text may reach an external model.
- G-03/G-04: retention periods and erasure; backup encryption and destination.
- G-05: Anthropic server-side fallback beta; G-07: pricing and cost budget; G-08: monitoring tooling and owners.
- G-09: conversation storage minimisation; G-10: false-refusal tuning.
- G-11: `enforce` mode, and whether salary, PF/ESI, DOB, policy publication and payslip uploads need step-up.
- The `braces` advisory exception or a Tailwind 4 upgrade (`ci-and-validation.md` §5).

## 7. External evidence still missing

Reverse-proxy configuration and its header logging; the monitoring stack with alert owners; API clients outside this
repository; a `log`-mode observation period; a GitHub CI run; a browser run; live-model results; a timed restore drill
in the target environment.
