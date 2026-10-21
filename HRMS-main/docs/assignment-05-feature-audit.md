# Assignment 05 feature audit and gap analysis

**Date:** 2026-10-20 · **Code:** `main` at `2225fe7` (local and `origin/main` identical at the time of the check) ·
**Type:** read-only audit (Phase A); no application code, test, CI or configuration was changed.
**Source of requirements:** `assignment_05_ai_hr_copilot.md` (workspace root). Related records:
[`capstone-submission-report.md`](capstone-submission-report.md) (submission verdict),
[`final-security-readiness.md`](final-security-readiness.md) (28 security findings),
[`production-hardening/ci-and-validation.md`](production-hardening/ci-and-validation.md) (commands).

**Status vocabulary:** VERIFIED (demonstrated by suitable tests or reproducible execution) · PARTIALLY VERIFIED
(an important integration, error path or real-provider behaviour is untested) · MISSING · FAILING · NOT TESTED.
"Offline" means fake or scripted model output; it proves pipelines and guardrails, never real-model quality.

## 1. Executive summary

- Every mandatory capability of the assignment is implemented, inside the existing CB Nest application, with
  deterministic authorization and no AI write path to business tables. No automatic-failure condition was found.
- **Real CI evidence exists.** GitHub Actions ran for `2225fe7` on 2026-10-20 (run `39471328944`): backend, frontend,
  production image builds, secret scan and the new browser E2E job **passed**; the dependency-advisory gate **failed**
  (the known `braces` advisory). The workflow had also run on 2026-10-14 (`c3d6928`) and 2026-10-15 (`86001f5`) with
  the same pattern. Several documents still say CI "never ran on GitHub": that statement is **wrong** (§8).
- The weakest mandatory evidence is real-model quality. "Policy RAG answers at least 5 common HR policy questions
  correctly" rests on an offline extractive model (8/8 answerable cases, covering all five suggested prompts), real-embedding retrieval
  measurements (Recall@4 = 1.000) and **one** real-model policy answer (OpenAI smoke test, 2026-10-16). A small live
  policy evaluation is prepared but has never run. **Update (later on 2026-10-20):** it has now run once (OpenAI
  `gpt-4o-mini`): router 33/33 and policy 11/11, including all five suggested prompts (`ai_eval_results.md` §4.2).
- **Verdict: ready for academic submission after one documentation fix (P1-2), with a live policy evaluation (P1-1)
  strongly recommended.** Production readiness: **NOT YET PRODUCTION READY** (separate checklist, §13).

## 2. Rubric and requirement matrix

Rubric (verified against the assignment): Policy RAG quality 20%, SQL agent correctness and safety 20%, backend API
tool calling 20%, role-based access control 20%, full-stack integration 10%, code quality 10%.

Paths are under `HRMS-main/backend/app/` (code) and `HRMS-main/backend/tests/` (tests) unless marked `frontend/`.

| ID | Requirement (faithful wording) | Implementation | Verification and evidence | Status | Gap | Pri |
|---|---|---|---|---|---|---|
| RAG-1 | Load policies from DB/files, chunk, embed, store, retrieve | `services/ai/rag/` (`vector_store.py`, `retriever.py`: hybrid cosine + IDF lexical), `services/ai/embeddings.py` (FastEmbed `bge-small-en-v1.5`), `scripts/ingest_policies.py`; only PUBLISHED policies | `ai/rag/`; real-embedding calibration 2026-10-14: Recall@4 1.000, MRR 0.964 holdout (`production-hardening/rag-calibration.md`) | VERIFIED | — | — |
| RAG-2 | Grounded answer from retrieved context only; source references (title, category, filename) | `rag/service.py`, `rag/guard.py` (grounding guard, cited ids must be retrieved) | offline `policy_eval.json` 11/11 (ExtractiveFakeLLM); browser: retrieval contrast only (POL-B); real model: live run 2026-10-20, 11/11 with `gpt-4o-mini` (8 answered, 3 refused by threshold) | VERIFIED (one live run) | one run, synthetic policies, hash-embedding retrieval, automatic scoring | P2 |
| RAG-3 | Refuse when context is insufficient | `AI_RAG_MIN_SCORE` 0.35 + guard | offline 3/3 refusals; smoke test #2b | VERIFIED (offline) | at 0.35 one clear negative in nine reaches the model (calibration); proposal 0.475 pending HR review | P2 |
| RAG-4 | Documents are data, not instructions; no model memory; no hidden metadata | delimited `<policy_document>` blocks, fixed system prompt, response schema without paths/checksums | `ai/rag/` injection tests, `ai/hardening/` red-team corpus (scripted model) | VERIFIED (offline) | document-injection resistance of a real model untested | P2 |
| SQL-1 | Schema-aware NL→SQL, read-only SELECT only | `services/ai/sql/` (prompt from the registry in `schema.py`) | `sql_eval.json` 20/20 (scripted SQL); smoke test #3a (one live query) | PARTIALLY VERIFIED | live SQL-generation accuracy unmeasured | P2 |
| SQL-2 | Block INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/REPLACE/TRUNCATE/PRAGMA/ATTACH/DETACH; parse before execution; one statement | `sql/parser.py`, `validator.py` (sqlglot AST), `executor.py` (`mode=ro`, `query_only`, authorizer) | `ai/sql/` one test per construct and per exfiltration trick; adversarial matrix | VERIFIED | — | — |
| SQL-3 | Forbidden columns never exposed; payroll restricted | `sql/schema.py` registry, global forbidden set | `test_schema_registry.py::EXPECTED_FORBIDDEN`; PII corpus | VERIFIED | — | — |
| SQL-4 | Row limit, role filters, no raw DB errors | scope rewrite to derived tables with bound parameters (`sql/scope.py`); `AI_SQL_MAX_ROWS`; error mapping | `ai/sql/`, `ai/authorization/` | VERIFIED | — | — |
| ACT-1 | Actions only through existing backend APIs with the user's token | `actions/registry.py` (10 routes), `actions/client.py` (httpx, in-process ASGI transport) | `ai/actions/test_action_parity.py`; E2E ACT-1..5, 7..11 (`frontend/e2e/workflow-copilot-actions.spec.ts`) | VERIFIED | — | — |
| ACT-2 | Intent extraction, structured tool schemas, result summaries | `actions/models.py` (`extra="forbid"`), `resolver.py`, `service.py` | `action_eval.json` 20/20 (scripted proposals) | PARTIALLY VERIFIED | live action-extraction accuracy unmeasured | P2 |
| ACT-3 | Supported-action list (employee/manager/admin) | 10 mutations; reads (balance, ticket status, own projects, team leave, skill search, analytics) go through the SQL agent; policy questions and summaries through RAG | §4 | VERIFIED for mutations | mapping of the assignment's list to agents is not written down; policy **upload** is UI-only (not an AI action) | P2 |
| ACT-4 | Employee cannot approve leave or assign projects | `authorization/policy.py` matrix + business API checks | `ai/authorization/` (24 capabilities × roles), `ai/actions/`, E2E ACT-3 and `api.spec.ts` | VERIFIED | — | — |
| ACT-5 | Confirmation before high-impact actions (bonus) | `actions/confirmation.py` (single-use, owner-bound, 300 s TTL, re-check) | backend expiry/replay tests; E2E duplicate confirm ACT-5, lost request ACT-11 | VERIFIED (expiry: backend only) | — | — |
| RBAC-1 | AI follows the app's authorization; never looser | `services/ai/authorization/` (DB role, INACTIVE rejected) | `ai/authorization/`, `rbac/test_route_permissions.py` (80 routes × 4 identities) | VERIFIED | — | — |
| RBAC-2 | No access to others' sensitive data; bank/PAN/password never; non-leaking refusals | forbidden set, field visibility, uniform 404/refusal texts | `rbac/test_self_profile_fields.py`, `ai/hardening/`, E2E EMP-B | VERIFIED | — | — |
| RBAC-3 | Authorization not only in the frontend | backend checks on every route | `frontend/e2e/api.spec.ts` (direct API with real tokens) | VERIFIED | — | — |
| AUD-1 | Log every AI interaction: user id, role, message, intent, tool/API, status, timestamp; no secrets | `services/ai/audit/` table `ai_audit_events` (append-only triggers): `employee_id`, `actor_role`, `intents`, `capability`/`route`/`endpoint`, `status`, `created_at`, `action`, `target_employee_id`, `resource_id` | `ai/audit/` (privacy: no prompt, answer or PII persisted) | VERIFIED, with a deliberate deviation | the **message** is stored as length + keyed fingerprint, not text (redacted text lives only in the owner's conversation store); documented | — |
| FE-1 | Chat input, history, loading, error, sources, SQL table, action confirmation, role-aware UI | `frontend/app/copilot/[[...conversation]]`, `components/copilot/*`, `lib/copilot-*.ts` | Vitest (mocked HTTP); Playwright 152 incl. Copilot workflows and action lifecycle | PARTIALLY VERIFIED | a grounded policy answer **with sources** is not shown end-to-end in the browser (needs a model or a scripted rule) | P2 |
| FE-2 | Recommended section "Recent AI Actions" | `/admin/ai-audit` (ADMIN); `GET /api/v1/ai/audit` already returns a user's own events | — | MISSING for non-admin users (recommended, not a minimum) | no per-user view | P2 |
| DOC-1 | `ai_architecture.md`, `ai_permissions_matrix.md`, `ai_eval_results.md`, README updates | present; deliverable-name map in `ai_architecture.md` §2.1 | read during this audit | VERIFIED | inaccuracies in §8 | P1/P2 |
| CQ-1 | Modular design, typed schemas, clean errors | one package per concern, Pydantic models, `AI_*` error codes | 3190 backend tests; CI backend job green | VERIFIED | — | — |

**Minimum passing requirements:** ≥5 policy questions answered correctly: VERIFIED (offline, plus one live
run with 8/8 answerable cases correct; P1-1 done) · SQL read-only: VERIFIED · actions through APIs: VERIFIED · no access to others' sensitive data:
VERIFIED · employee cannot approve leave or assign projects: VERIFIED · no direct AI database writes: VERIFIED ·
usable frontend flow: VERIFIED (browser suite, CI e2e job green).
**Automatic-failure conditions:** none observed (no AI write path; destructive SQL blocked; no forbidden field
exposure in any test; backend authorization; existing HRMS suites green; gitleaks clean over history).

## 3. Feature inventory

| Area | Endpoints / pages |
|---|---|
| Copilot router (LangGraph) | `POST /api/v1/chat` (the assignment's optional `/chat/router`) |
| Policy RAG | `POST /api/v1/chat/policy` |
| SQL agent | `POST /api/v1/chat/sql` |
| Action agent | `POST /api/v1/chat/actions`, `POST /api/v1/chat/actions/confirm` |
| Conversations | `GET/DELETE /api/v1/chat/conversations[/{id}]` (owner-scoped) |
| Role modes (UX only) | `GET /api/v1/chat/permissions` |
| Legacy session API | `POST /api/v1/chat/sessions[/{id}/messages]` |
| Audit and usage | `GET /api/v1/ai/audit`, `GET /api/v1/ai/audit/metrics` |
| Frontend | `/copilot`, `/admin/ai-usage`, `/admin/ai-audit`; step-up dialog on protected admin operations |

Bonuses: LangGraph router, human-in-the-loop confirmation, formal evaluation set, document-injection tests,
role-specific modes, usage dashboard, token/latency tracking in audit metadata. Not implemented: streaming
(deliberate, documented in `phase-10-final-report.md`), LangSmith/OpenTelemetry tracing.

## 4. Assignment action list → implementation

| Assignment action | Role | How it is served |
|---|---|---|
| Apply for leave | E/M/A | action `LEAVE_CREATE` |
| Check leave balance, check ticket status, view own projects | E/M/A | SQL agent, SELF scope |
| Ask policy questions | all | Policy RAG |
| Approve/reject leave | M (reports), A | actions `LEAVE_APPROVE` / `LEAVE_REJECT` |
| Assign or update tickets | M (team), A | actions `TICKET_ASSIGN` / `TICKET_STATUS_UPDATE` |
| View team leave, search by skill, check project assignments | M/A | SQL agent, TEAM/ORG scope |
| Create announcements | M (team audience), A | action `ANNOUNCEMENT_CREATE` (password step-up) |
| Create projects | A | action `PROJECT_CREATE` |
| Assign employees to projects | M (team), A | action `PROJECT_ASSIGN` |
| Upload or summarize HR policies | A | summarize: Policy RAG (policy wording routes to RAG); **upload: existing HR policies page only, no AI action** |
| Manage employee lifecycle | A | action `EMPLOYEE_DEACTIVATE` (password step-up); reactivation is UI-only |
| View broader HRMS analytics | A | SQL agent ORG scope; AI usage dashboard |

## 5. Missing and partially implemented

- **Real-model quality (P2):** policy answers and routing were evaluated live once (2026-10-20, 44 cases, all passed);
  SQL generation, action extraction and injection resistance with a real model remain unmeasured.
- **Browser display of a grounded policy answer with sources (P2):** covered by mocked Vitest only.
- **Per-user "Recent AI Actions" (P2):** recommended UI section; backend support already exists.
- **Policy upload through chat (P3):** not built; the existing upload page covers the business need, and a chat
  upload path would add a file-handling attack surface. Document as a scope decision rather than build it.
- **Streaming and tracing (P3):** optional bonuses, not implemented.

## 6. Test and evaluation evidence

| Evidence | Where it ran | Result |
|---|---|---|
| Backend suite | local, `scripts/verify-backend.sh` (2026-10-20, log re-checked) | 3190 passed, 1 skipped |
| Offline evaluations (6 suites, 139 cases) | local, same script | 139/139 (scripted/extractive model) |
| CI advisory-gate unit tests | local | 25 passed |
| Vitest / TypeScript / build | local (2026-10-20) | 268 / 0 errors / PASS (later: Vitest 277 at `6338adc`; build passed in CI run 39607243952) |
| Playwright (Chromium, isolated stack) | local `results.json` (2026-10-20T16:27Z) | 152 expected, 0 unexpected, 0 skipped, 0 flaky |
| GitHub Actions run `39471328944` on `2225fe7` | GitHub, 2026-10-20 18:02–18:11 UTC | backend, frontend, docker, secret-scan, e2e: success; dependency advisories: failure |
| Real-embedding retrieval calibration | local, 2026-10-14 | Recall@4 1.000; proposal only |
| Live OpenAI smoke test (10 checks) | local dev stack, 2026-10-16, `gpt-4o-mini` | all PASS; not a quality evaluation |
| Live-model evaluation (`run_evals --live`) | owner's terminal, throwaway container, 2026-10-20 20:13–20:14 UTC, OpenAI `gpt-4o-mini` | router 33/33 (3 cases reached the model), policy 11/11; 8 calls, 5,460 input / 798 output tokens |
| Manual browser walkthrough | — | **NOT_RUN** |

The CI e2e job's Playwright step ran 122 s and succeeded; per-test counts are in the job log, which needs an
authenticated GitHub session and was not read. The unit and Playwright counts above are local results.

## 7. Security and privacy findings

No new security defect was found in this audit. The 28 findings in `final-security-readiness.md` stand; the
capstone-relevant ones are all VERIFIED or PARTIALLY_VERIFIED there. Privacy notes: audit records hold identifiers,
counts and keyed fingerprints, never prompts, answers, SQL text, rows or tokens; conversations store redacted user text
and are owner-scoped; retention periods are an open governance decision (G-03/G-04), so no compliance claim is made.

## 8. Documentation gaps

| # | Gap | Files | Pri |
|---|---|---|---|
| D-1 | "CI never ran on GitHub" / "NOT_RUN" is false: runs exist on 2026-10-14, 2026-10-15 and 2026-10-20 (all green except the advisory gate) | `capstone-submission-report.md` (§1, §3, §5, §7), `ci-and-validation.md` §3/§6, `final-security-readiness.md` (#26, header), `README.md`, `production-hardening/capstone-completion-progress.md` §5, `release-readiness.md`, `development-handoff.md`, workspace `CLAUDE.md` | P1 |
| D-2 | Assignment action list not mapped to agents (§4 above) | `ai_permissions_matrix.md` | P2 |
| D-3 | Matrix §1 says "Deactivate / reactivate employee" via AI; reactivation is not an AI action (`ai_architecture.md` §3.3 says so) | `ai_permissions_matrix.md` §1 | P3 |
| D-4 | Broken sentence "The original router harness It never calls the services…" | `ai_eval_results.md` §4 | P3 |

**Update 2026-10-20 (later the same day):** D-1, D-3 and D-4 corrected in the listed files. Not changed: the
comment on line 2 of `.github/workflows/ci.yml` ("Not yet run on GitHub"; CI files were out of scope) and dated history
(`phase-2-report.md`, the Phase 9/10 entries of `capstone-completion-progress.md`). D-2 is deferred.

Historical phase reports (e.g. `phase-2-report.md`) are dated records and should not be rewritten.

## 9. GitHub CI results

| Run | Commit | Date (UTC) | backend | frontend | docker | secret-scan | e2e | dependency advisories | Overall |
|---|---|---|---|---|---|---|---|---|---|
| `38524047626` | `c3d6928` | 2026-10-14 | success | success | success | success | (job not yet defined) | failure | failure |
| `38792377654` | `86001f5` | 2026-10-15 | success | success | success | success | (job not yet defined) | failure | failure |
| `39471328944` | `2225fe7` | 2026-10-20 | success | success | success | success | **success** | failure | failure |
| `39493375853` | `e149cfc` | 2026-10-20 | success | success | success | success | success | failure | failure |
| `39607243952` | `6338adc` | 2026-10-21 | success | success | success | success | success | failure | failure |

Unresolved CI failure: the advisory gate (`tailwindcss@3` → `braces`, GHSA-vfj7-8cjw-p6xm, high; build-time only;
runtime `npm audit` 0). **Update 2026-10-21:** the gate now also blocks on Pillow 12.3.0 (runtime; eight advisories
published 2026-10-20, fixed only in Pillow 13, which `fastembed` does not allow; `ci-and-validation.md` §5). Annotation: GitHub forces `actions/checkout@v4` and `actions/setup-node@v4` from Node 20 to
Node 24 (deprecation warning). Branch protection is not configured, so the red gate does not block merges.

`braces` options (owner decision; nothing changed here):
1. **Approve the PROPOSED exception** (owner, approver, expiry ≤ 180 days): gate turns green quickly; accepts a
   build-time-only risk with a review date. Smallest change; a governance act, not a code change.
2. **Upgrade to Tailwind 4:** removes the path; configuration and CSS migration plus a visual check of all pages;
   regression risk across every screen. Not justified by the assignment.
3. **Leave it red and documented:** honest, zero risk to code, but graders see a failing workflow on `main`.

## 10. Improvement backlog

| ID | Pri | Item | Why / requirement | Smallest change | Files | Tests | Risk | Before submission? |
|---|---|---|---|---|---|---|---|---|
| P1-1 | P1 | Live policy evaluation on the 11 policy cases (+ 33 routing cases) | minimum-pass "≥5 policy questions correctly"; RAG quality 20% | run `python -m evals.run_evals --live` once with an approved provider/model and key in the process environment; record results in `ai_eval_results.md` §4 | docs only | the existing live suites | none to code; cost is a few cents at small-model prices (verify current pricing); synthetic demo policies only | strongly recommended; needs your approval |
| P1-2 | P1 | Correct the CI status claims (D-1) with the real run table | accuracy of the submission evidence | replace "never ran" with §9's table; keep dated history | listed in D-1 | none | none | **yes** |
| P1-3 | P1 | Decide the advisory gate presentation | graders see a red workflow | choose option 1 or 3 in §9 (option 2 is P3) | `.github/security/advisory-exceptions.json` (option 1) or docs (option 3) | gate unit tests already exist | low | decision needed |
| P2-1 | P2 | Browser test of a grounded policy answer with sources | full-stack integration; RAG citations visible | one scripted rule in `docker-compose.e2e.yml` (`AI_FAKE_LLM_SCRIPT`) quoting a retrieved chunk + one spec in `workflow-copilot.spec.ts` | 2 files | Playwright | low (test-only) | optional |
| P2-2 | P2 | "Recent AI actions" panel for every user on `/copilot` | recommended UI section | read-only list from the existing `GET /api/v1/ai/audit` (own scope) through `lib/copilot-api.ts` | `components/copilot/`, one page | Vitest + one Playwright check | low (frontend only, no backend change) | optional |
| P2-3 | P2 | Action-list mapping and doc fixes (D-2..D-4) | documentation completeness | add §4's table; fix two lines | `ai_permissions_matrix.md`, `ai_eval_results.md` | none | none | recommended |
| P2-4 | P2 | RAG abstention threshold 0.35 → 0.475 | fewer weak contexts reach the model | owner/HR decision on `rag-calibration.md`; then one setting | `core/config.py` default or env | `ai/rag/`, offline evals | medium (1 false abstention in 28 on holdout) | no |
| P3-1 | P3 | Actions on Node 24 majors and SHA pins; branch protection | CI hygiene | release-gate item | `ci.yml`, repo settings | CI run | low | no |
| P3-2 | P3 | Streaming, tracing, chat policy upload, manual walkthrough | bonuses / polish | — | — | — | — | no |

## 11. Recommended sequence

1. P1-2 documentation correction (no code; one commit).
2. P1-3 decision on the advisory gate (option 1 or 3).
3. P1-1 live policy evaluation, only after you approve provider, model, key handling and a cost ceiling.
4. P2-3 documentation additions; optionally P2-1 and P2-2, each as its own reviewed phase.
5. Final verification (local suites, CI run on the pushed commit, protected-file checksums), then the submission
   checklist below.

## 12. Academic submission checklist

- [x] Four core AI capabilities implemented inside CB Nest; required endpoints present.
- [x] No AI write path to business tables; actions via existing APIs with the user's token.
- [x] SQL read-only with AST validation, forbidden columns, role scope, row limit, safe errors.
- [x] Employee cannot approve leave, assign projects or read others' sensitive data (backend + browser tests).
- [x] Audit logging of AI interactions without secrets (message stored as fingerprint by design).
- [x] Usable frontend flow; Playwright suite green locally and in GitHub Actions.
- [x] Required documents present; no secrets in history (gitleaks local and CI).
- [x] Documentation states real CI results (P1-2; corrected 2026-10-20).
- [x] Live policy evaluation recorded (P1-1; 2026-10-20, `ai_eval_results.md` §4.2).
- [ ] Advisory-gate decision recorded (P1-3).

## 13. Production-readiness checklist (separate; not required for submission)

Open: release gates in `production-hardening/release-readiness.md` §6 — G-11 `enforce` (stays `log`), proxy/TLS and
header logging, monitoring and alert owners, backups and a restore drill, data governance and retention (G-01..G-10),
token storage and strict CSP, role changes forcing a new sign-in, persistent audit of business refusals, SHA-pinned
actions and branch protection, the `braces` and Pillow decisions, non-SQLite databases and multiple workers (not verified),
real-model quality evaluations. Verdict: **NOT YET PRODUCTION READY**.

## 14. Verdict

**Ready after specific fixes** for academic submission: P1-2 (correct the CI statements) is required for accurate
evidence; P1-1 (a small approved live policy evaluation) is strongly recommended because it is the only minimum
passing requirement resting mainly on offline evidence (**update:** P1-2 corrected and P1-1 run on 2026-10-20, recorded
in the commit that adds this report); P1-3 needs your decision. Evidence: §2, §6, §9.
