# Production Hardening — Phase 2: Deployment, Abuse Controls, Data Lifecycle and CI

**Dates:** 2026-10-12 → 2026-10-13 · **Previous phase:** [security-remediation.md](security-remediation.md) · **Baseline:** [baseline.md](baseline.md)
**Status:** Phase 2 **complete** locally. Nothing has been pushed or deployed, and no remote CI has run.
**Release status: NOT YET PRODUCTION READY** (§8).

Companion documents:
- [deployment-runbook.md](deployment-runbook.md)
- [backup-restore.md](backup-restore.md)
- [data-processing.md](data-processing.md)

## 1. Findings: outcome

| Finding | Outcome | Status |
|---|---|---|
| B-06 Default JWT secret / unsafe config | Production startup refuses default or short secrets, wildcard hosts and origins, more than one worker, unapproved tracing, and AI enabled without a key. A dedicated fingerprint key is required. | IMPLEMENTED · TESTED · VALIDATED (container refuses) |
| B-07 No production deployment config | Multi-stage Dockerfiles (`production` and default `dev` targets), `docker-compose.prod.yml`. Non-root, read-only root filesystem, `cap_drop ALL`, healthchecks, 1 worker, graceful shutdown, ports on 127.0.0.1. | IMPLEMENTED · VALIDATED (smoke, §5) |
| B-08 Frontend toolchain on Node 20 | Production image on **Node 24.21.0** (pinned). Tests, `tsc` and the build pass inside that image. The dev image stays on Node 20 (unchanged workflow). | IMPLEMENTED · VALIDATED |
| B-09 No rate limits or budgets | Per-user AI request limit, per-user daily token budget, process-wide model-call concurrency, and login throttling (§4). | IMPLEMENTED · TESTED |
| B-10 Action timeout not enforced | `asyncio.wait_for` deadline (`AI_ACTION_EXECUTION_TIMEOUT_SECONDS`, 10 s). On expiry the status is UNKNOWN, with `ACTION_OUTCOME_UNKNOWN` audit and `504 AI_ACTION_OUTCOME_UNKNOWN`. Never retried. | IMPLEMENTED · TESTED |
| B-11 No reconciliation | Read-only stale and unknown actions report (`scripts/stale_actions_report.py`). Retention never deletes EXECUTING or UNKNOWN actions. | IMPLEMENTED · TESTED · drilled |
| B-12 `ACTION_CONFIRMED` before the claim | The claim happens first; then a fail-closed audit write. If the audit write fails, the claim is released (EXECUTING → PENDING), so the token is not burnt. | IMPLEMENTED · TESTED |
| B-13 Fingerprint key derived from JWT secret | `AI_AUDIT_FINGERPRINT_KEY`. Production requires at least 32 characters, different from the JWT secret. | IMPLEMENTED · TESTED |
| B-14 No retention | Dry-run-by-default purge; ACTIVE ADMIN operator; audited; audit events never deleted (§6). | IMPLEMENTED · TESTED · drilled (dry run only) |
| B-15 Provider data governance | `AI_SQL_RESULT_FORMATTING=deterministic` by default: SQL result rows are never sent to the provider. Data register, plus payload tests asserting what each call sends. | IMPLEMENTED · TESTED. **Decisions open** |
| B-16 LangSmith tracing guard | Production refuses `LANGSMITH_*` / `LANGCHAIN_*` tracing variables unless `ALLOW_EXTERNAL_TRACING=true`. | IMPLEMENTED · TESTED |
| B-18 `file_path` exposure + `/chat/sessions` stubs | `GET /hr-policies` no longer returns `file_path`. The stubs are kept (the PRD references them) and tested for 401 and 403 `USER_INACTIVE`. | IMPLEMENTED · TESTED |
| B-20 No CI | `.github/workflows/ci.yml` and an advisory gate (§3). | IMPLEMENTED · **tested locally only** |
| B-23 Starlette advisories | FastAPI 0.133.1 + Starlette 1.3.1 (smallest patched combination). Each advisory has a behavioural test. | IMPLEMENTED · TESTED |
| B-24 python-jose | Replaced by PyJWT 2.15.1. `type: access`, required `exp` and HS256-only behaviour preserved. The suite also passes with jose, ecdsa, rsa and pyasn1 uninstalled. | IMPLEMENTED · TESTED |
| **B-25 (new)** Host-header-driven redirects | `TrustedHostMiddleware` with `ALLOWED_HOSTS`; spoofed Host returns 400. Regression tests added. | IMPLEMENTED · TESTED · VALIDATED |
| **B-26 (new)** Multi-range `Range` DoS still present in Starlette 1.3.1 (about 15 s for 30,000 ranges) | `RangeHeaderLimitMiddleware` ignores Range headers with more than 8 ranges or more than 1 KB (full 200 response). The test requires under 2 s. | IMPLEMENTED · TESTED |
| **S-01** Object-level authorization review | Manager scope added to tickets (404 identical to unknown), document and payslip uploads, job title and project listing. Other modules reviewed and left unchanged (see the commit message of `e18f936`). | IMPLEMENTED · TESTED |

**Remaining (not fixed in Phase 2):**
- B-17: JWT in `localStorage`.
- B-21: Tailwind 3 build chain.
- Nested `postcss` (needs `next@16`).
- `pytest` advisory (dev-only).
- RAG threshold calibrated on fake embeddings, and no live-model evaluation (B-20 part).
- No token revocation.
- No wall-clock limit on PDF parsing in the operator scripts.

## 2. Dependency versions and advisories (checked 2026-10-13)

| Component | Version |
|---|---|
| Python (CI / images) | 3.11 |
| FastAPI | 0.133.1 |
| Starlette | 1.3.1 |
| PyJWT | 2.15.1 (python-jose removed) |
| pypdf | 6.19.0 |
| python-multipart | 0.0.31 |
| Node (production web, CI) | 24.21.0 |
| Node (dev image) | 20 (unchanged) |
| Next.js | 15.5.27 |

| Audit | Runtime | Build/test-time |
|---|---|---|
| pip-audit 2.9.0, `requirements.txt` | **0 findings** | — |
| pip-audit, `requirements-dev.txt` | — | `pytest 8.4.2`: PYSEC-2026-1845 / CVE-2025-71176 (local `/tmp` DoS / privilege issue; fix 9.0.3, major). pip-audit gives no severity, so the gate treats it as **blocking**. |
| npm audit `--omit=dev` | `postcss` (nested in `next`): **high** GHSA-6g55-p6wh-862q, GHSA-r28c-9q8g-f849; moderate GHSA-qx2v-qp2m-jg93, GHSA-fxqj-rqcc-2cmp. Fix requires `next@16`. | — |
| npm audit (full) | — | **high** `braces` GHSA-vfj7-8cjw-p6xm (via tailwindcss@3; fix needs tailwindcss@4); moderate `postcss-selector-parser` GHSA-rj75-hqrm-r3gf |
| Critical | **0** | **0** |

The **draft** exceptions for the three blocking items are in `.github/security/advisory-exceptions.json`, with status `PROPOSED` and no owner or approver. **The gate rejects them, so the dependency-audit job fails** until a named owner approves each one, or the dependencies are upgraded. This is intentional: tooling never approves its own exceptions.

## 3. CI (`.github/workflows/ci.yml`): local verification only

Jobs:
- **backend** (Python 3.11): security regressions first (auth, authorization, SQL guardrails, actions, audit, hardening, limits, framework security), then the remaining suite, the offline evaluations and the gate's unit tests.
- **frontend** (Node 24.21.0): `npm ci`, `npm test`, `tsc --noEmit`, `npm run build`.
- **dependency-audit**: pip-audit and npm audit, then the advisory gate. The gate fails on any critical, and on high or unknown severity without an approved, unexpired, non-wildcard exception. Moderate findings are reported. Each finding is labelled RUNTIME or BUILD.
- **docker**: builds both production targets, checks that the API image refuses an unsafe configuration, and checks both images run as non-root.
- **secret-scan**: gitleaks 8.18.4 over the full history, with redacted output.

Permissions are `contents: read`. **Not done:** pushed, run on GitHub, branch protection enabled, actions pinned to commit SHAs.

| Local check (2026-10-13) | Result |
|---|---|
| actionlint 1.7.7 | 0 issues |
| Gate unit tests (`.github/scripts`) | 25 passed |
| Gate against the real audit output | **FAIL** (3 blocking items, draft exceptions not approved), as expected |
| Frontend job in `node:24.21.0-alpine`, clean copy | npm ci OK · 86 tests passed · tsc OK · build OK |
| Backend job in clean `python:3.11` (3.11.17), no `.env` | security regressions 1221 passed · remaining 625 passed · offline evals 139/139 (live NOT_RUN) · gate tests 25 passed |
| Docker job commands | Both images build; unsafe config gives exit 3 with `RuntimeError: Unsafe production configuration: JWT_SECRET_KEY …` (setting names only); api uid 10001, web uid 1000 |
| gitleaks, full history (20 commits) | 1 finding: a synthetic JWT-shaped redaction fixture in `test_conversation_service.py`. Allowlisted **by exact fingerprint** in `.gitleaksignore`, with the reason. After that: no leaks. |

## 4. Rate-limit and budget assumptions (provisional, configurable)

| Control | Default | Key | Response | Where |
|---|---|---|---|---|
| AI requests | 20 per minute, sliding window | DB-loaded employee id (never a client-supplied value). Shared across `POST /chat`, `/chat/policy`, `/chat/sql`, `/chat/actions`, `/chat/actions/confirm`, so new conversations and alternate endpoints share it. | 429 `AI_RATE_LIMITED` | Dependency before any service or LLM call |
| Daily tokens | 200,000 per user per UTC day | Sum of `total_tokens` from that user's provider audit events | 429 `AI_BUDGET_EXCEEDED` | Same dependency |
| Daily cost cap | Disabled (`AI_DAILY_COST_CAP_USD` unset) | — | — | Needs a pricing and budget policy |
| Concurrent model calls | 4 per process; 15 s queue wait | Process-wide | 503 `AI_BUSY` | Around every observed LLM call |
| Login | 10 per minute | Client IP + normalized (trimmed, lower-cased) email | 429 `TOO_MANY_ATTEMPTS` | Before the DB lookup and bcrypt |

Over-limit requests never reach the LLM; tests count model-call slot acquisitions. They are audited as `AI_ERROR`, plus `AI_HTTP_REQUEST` with status 429 or 503.

Assumptions and limits:
- Counters are **in memory**: they reset on restart and are only correct with **one worker on one instance**. Production startup refuses more than one worker. Scaling out needs a shared store (open decision).
- The client IP is only correct if `FORWARDED_ALLOW_IPS` is set to the proxy address. Otherwise every user shares the proxy's IP.
- The login key is IP + account. A distributed attempt on one account from many IPs, or one IP trying many accounts, is limited only per pair. Account lockout and per-IP-global limits are not implemented.
- The token budget counts provider usage that has been recorded. A call in flight when the budget is crossed completes.

## 5. Container smoke evidence (local, 2026-10-12/13)

Setup: isolated Compose project `hrms-prod`, a throwaway volume, random secrets, AI disabled.

- API: healthy; uid 10001; read-only root filesystem; spoofed Host → 400; graceful stop in 1.5 s.
- Web: `next start` on Node 24.21.0; graceful stop in 0.8 s.
- Login from the configured web origin → 200 with CORS headers. A foreign origin gets no allow-origin header.
- `POST /chat` → `AI_UNAVAILABLE` (AI disabled).
- An unsafe configuration refuses to start.
- `ingest_policies.py` with AI disabled refuses, but prints a raw traceback (cosmetic; open).

## 6. Retention, backup and restore

See [backup-restore.md](backup-restore.md).

Summary:
- Retention: conversations 90 days, terminal or expired pending actions 30 days, audit events at least 365 days and **never deleted**. Backups daily with 30-day retention; RPO 24 h, RTO 4 h. All of these are **provisional and not approved**.
- Restore drill on a copy of the dev DB: 26 files and 25 tables restored with 0 row-count mismatches; tampered and non-empty-destination cases refused; the real dev DB was unchanged.
- **No retention `--apply` was run on any real database.** Open decisions: encryption, destination, off-host copies, access control, scheduling, final RPO/RTO and the reconciliation owner.

## 7. Tests and commands

| Run | Command | Result |
|---|---|---|
| Full backend, Python 3.11.17 (`hrms-main-api` image, repo mounted) | `scratchpad/pytest311.sh` → `python -m pytest -q` | **1846 passed**, 0 failed (160 s) |
| Backend CI emulation, clean `python:3.11`, no `.env` | ci.yml backend steps | 1221 + 625 = **1846 passed**; evals **139/139** |
| Frontend | `npm test` / `npx tsc --noEmit` / `npm run build` (host Node 24, and `node:24.21.0-alpine`) | 86 passed / 0 errors / PASS |
| New Phase 2 test files | `tests/ai/test_limits.py`, `tests/ai/test_retention.py`, `tests/test_backup_restore.py`, `tests/test_framework_security.py`, `tests/ai/orchestration/test_provider_payloads.py`, `tests/characterization/test_hr_policies.py`, `.github/scripts/test_advisory_gate.py` | all pass |

No test was deleted and no assertion weakened. The four SQL formatter-guard tests now set `formatting="llm"` explicitly, because the default changed to deterministic. Their assertions are unchanged.

## 8. Why still NOT production ready

1. **Unapproved decisions:**
   - data processing with the model provider (retention, training, residency, contract);
   - retention periods;
   - backup encryption and destination;
   - the advisory exceptions.
2. **CI never ran on GitHub.** Branch protection is not configured, and actions are not pinned to SHAs.
3. **Open dependency risk:** high advisories in nested `postcss` (`next@16`) and the Tailwind 3 chain.
4. **No live-model evaluation:** there is no API key. RAG thresholds are calibrated on fake embeddings.
5. **Single-instance design:** SQLite plus in-memory limits. No HA.
6. **Missing operations:** no TLS or reverse proxy config, no alerting, no scheduled backups, no off-host copies.
7. **B-17:** the JWT is in `localStorage`, and there is no token revocation.
8. **Not verified:**
   - browser UI flows;
   - behaviour behind a real reverse proxy;
   - restore on another host;
   - backup under write load;
   - multi-day budget rollover in real time;
   - a real model's behaviour under rate limits.

## 9. Commits (local `main`, not pushed)

| Commit | Content |
|---|---|
| `2531d72` | B-10 deadline, B-12 confirm-after-claim, B-13 fingerprint key |
| `e18f936` | S-01 manager object scope (tickets, documents, payslips, job title, projects) |
| `cf69dbb` | B-18 policy `file_path`, `/chat/sessions` stub tests |
| `eb47a66` | B-23 FastAPI 0.133.1 / Starlette 1.3.1, B-26 range limit |
| `6e11d25` | B-24 PyJWT 2.15.1 |
| `993e5d6` | B-07 / B-25 / B-16 / B-06 production API container, trusted hosts, CORS, production checks |
| `edcf475` | B-07 / B-08 production web container on Node 24 |
| `993f037` | B-09 rate limits, budgets, concurrency, login throttling |
| `a45f545` | B-15 deterministic SQL wording, data-processing register |
| `feb544a` | Test harness token cache refresh (wall-clock robustness) |
| `ebfdf4b` | B-14 retention, B-11 stale report, backup and restore tooling, drill doc |
| _(this commit)_ | B-20 CI workflow, advisory gate, gitleaks allowlist, runbook, this report, label fixes |
