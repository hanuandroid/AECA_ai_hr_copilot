# Production Hardening — Phase 0 Baseline

**Date:** 2026-10-11 · **Scope:** `HRMS-main` (CB Nest + NovaWorks PeopleOps AI HR Copilot)
**Method:** read-only inspection, test runs, dependency audits and runtime checks against the local Docker development stack. No application behaviour was changed in Phase 0.
**Related:** [../ai-components-architecture-and-production-readiness.md](../ai-components-architecture-and-production-readiness.md) (full component assessment) · [../ai-components-inventory.md](../ai-components-inventory.md)

Evidence labels:
- `[run 2026-10-11]` — executed and inspected during this baseline;
- `[test]` — asserted by an existing test that passed in today's run;
- `[code]` — source inspection only;
- `[advisory]` — matched against a published advisory database; **exploitation not attempted**.

---

## 1. Git state (preserved)

| Item | State |
|---|---|
| Branch | `main`, in sync with `origin/main`; HEAD `c685e8b Phase 10 complete - AI HR Copilot` |
| Modified (approved earlier, uncommitted) | `backend/app/services/auth.py` (refresh-token rejection), `backend/tests/characterization/test_auth.py` (+2 regression cases), `frontend/Dockerfile` (`npm ci` with lockfile), `backend/.env.example` (empty numeric settings commented out) |
| Modified (user) | `CLAUDE.md`: line-ending change only (no content difference ignoring CR) |
| Untracked | `backend/.dockerignore`, `docs/ai-components-*.md` (2), `writing-block.md` (user), and this directory |
| Not tracked by design | `backend/.env` (git-ignored, required by Compose), `backend/storage/*` (dev DB, model cache), `frontend/node_modules`, `frontend/.next` |

## 2. Architecture check (expected vs actual)

| Expected | Verified | Evidence |
|---|---|---|
| FastAPI backend | Yes, 0.116.1 | `[run]` container |
| SQLAlchemy + SQLite | Yes, SQLAlchemy 2.0.43, SQLite 3.46.1 (aiosqlite) | `[run]` |
| Alembic migrations | Yes, head `0020_ai_audit_events`; `0017`–`0020` are AI-owned | `[run]` upgrade on the dev DB (2026-10-10) |
| Next.js 15 frontend | Yes, **15.5.2** (React 19.1.1) | `[run]` |
| LangGraph orchestration | Yes, 1.2.12; fixed 8-node dispatcher, no checkpointer | `[code]` `services/ai/orchestration/graph.py` |
| Anthropic provider + abstraction | Yes (SDK 1.9.0); **no API key configured; never called live** | `[code]`, `[run]` (`AI_UNAVAILABLE`) |
| `BAAI/bge-small-en-v1.5` embeddings | Yes, fastembed 0.8.1, 384-d vectors | `[run]` live index |
| Hybrid (lexical + vector) RAG | Yes: 0.5·cosine + 0.5·IDF term overlap, brute force | `[code]` `rag/retriever.py` |
| Read-only SQL agent | Yes: sqlglot AST validation, scope rewrite, `mode=ro` + `query_only` + authorizer | `[code][test]` |
| Action proposals + separate confirmation | Yes: hashed single-use token, binding hash, TTL, atomic claim, permission re-check | `[code][test]` |
| JWT auth + backend authorization | Yes: HS256; role loaded from DB; AI capability matrix | `[code][test]` |
| Append-only AI audit | Yes: UPDATE/DELETE triggers on `ai_audit_events` | `[code][test]` |
| Offline deterministic evaluations | Yes: 6 suites, 139 cases, run as pytest tests | `[run]` (passed inside the backend suite) |
| Docker configuration | Yes: **development only** (`uvicorn --reload`, `next dev`, root user, no healthchecks) | `[run]` stack built and running |
| CI configuration | **None** (no `.github/` or other pipeline) | `[run]` |

**Request lifecycle:**
1. Browser (`lib/copilot-api.ts`, JWT from `localStorage`).
2. `AIAuditMiddleware` (request_id).
3. `get_current_user` (decode JWT, reject `type=refresh`, load Employee).
4. `get_ai_user_context` (reject INACTIVE, frozen context).
5. Owner-scoped conversation.
6. LangGraph `classify`, then Policy RAG / SQL agent / action proposal.
7. Anthropic, used only after authorization.
8. Persisted turn plus audit events.

Confirmed actions go through `POST /chat/actions/confirm`, then an in-process call to the existing business route with the caller's token. Details: assessment §B–§C.

## 3. Environment and versions `[run 2026-10-11]`

| Component | Version |
|---|---|
| Host | Windows 11 Pro 10.0.26200; Docker 29.6.1; Compose v5.2.0 |
| API image | `python:3.11-slim` → Python 3.11.17 |
| Web image | `node:20-alpine` → Node 20.20.2, npm 10.8.2 (**Node 20 is end-of-life since 2026-04-30**) |
| Host toolchain | Node 24.13.0 / npm 11.8.0; Python 3.13 and 3.14 (documented 3.12 **not installed**) |
| Key backend deps | fastapi 0.116.1 (starlette 0.47.3), sqlalchemy 2.0.43, pydantic 2.11.7, anthropic 1.9.0, langgraph 1.2.12, sqlglot 30.20.0, fastembed 0.8.1, pypdf 5.1.0, python-jose 3.5.0 |
| Key frontend deps | next 15.5.2, react 19.1.1, tailwindcss 3.x, vitest 5.0.3, jsdom 30 |

## 4. Test results `[run 2026-10-11]`

| Suite | Command | Runtime | Result |
|---|---|---|---|
| Backend (all, incl. 139 eval cases) | `docker compose run --rm --no-deps -T api sh -c "pip install -r requirements-dev.txt && python -m pytest -q"` | Python 3.11.17 (deployment runtime; first time this runtime has been tested) | **1685 passed, 2 failed** (193 s) |
| Frontend unit / integration | `npm ci && npm test` (`frontend/`) | Node 24.13.0 (host) | **86 passed** (5 files) |
| Frontend unit / integration | `npx vitest run` in the `web` container | Node 20.20.2 (image) | **FAILED to start: 5 load errors.** `jsdom@30` → `undici` needs newer Node (`new CacheStorage` crash) |
| Type check | `npx tsc --noEmit` (after a clean build) | Node 24 | **0 errors.** Errors seen before the build came only from dev-server-generated `.next/types/validator.ts` |
| Production build | `npm run build` (web container stopped during the build, then restarted) | Node 24 | **PASS**: 22 app routes + middleware |
| Live-model evaluation | `python -m evals.run_evals --live` | — | **NOT RUN**: no `ANTHROPIC_API_KEY` |

**The 2 backend failures** were investigated. Both are test-design defects that only appear once the README setup has been followed:

- `tests/test_infrastructure.py::test_real_dev_database_untouched` asserts that `storage/hrms.db` **does not exist**. It exists because the documented Docker setup created and seeded it (2026-10-10). The test's intent is "the test run doesn't modify the dev DB"; isolation itself is covered by `test_database_is_isolated` (passed).
- `tests/ai/hardening/test_production_config.py::test_no_real_secrets_are_committed` asserts that `backend/.env` **does not exist on disk**. That file is required by `docker-compose.yml` and is git-ignored; "committed" should be checked against git, not the filesystem.

Both passed on 2026-10-10 (Python 3.13), before the Docker setup created those files. They aren't weakened or changed here (project rule: test changes need approval). See finding B-19.

## 5. Dependency advisories `[advisory, run 2026-10-11]`

### npm (`npm audit`, lockfile): 10 packages — 1 critical, 7 high, 2 moderate

| Package | Installed | Severity | Notable advisories | Fixed in |
|---|---|---|---|---|
| `next` (direct) | 15.5.2 | **Critical** | GHSA-9qr9-h5gf-34mp (RCE in the React Flight protocol, CVE-2025-66478, `<15.5.7`); GHSA-p293-qw3h-jr36 (unauthenticated RCE on **Windows-hosted** servers, `<15.5.24`); GHSA-2xp9-vwfh-vxw4 (unauthenticated RCE in the Image Optimization API with AVIF, `<15.5.24`); plus ~25 high/moderate (middleware/proxy bypass, SSRF, DoS, cache poisoning, XSS) | **15.5.27** (same 15.5 line, non-major) |
| `postcss`, `sharp` (via next) | — | High | Arbitrary file read via sourceMappingURL; libvips / libheif / librsvg CVEs | Via `next@15.5.27` |
| `tailwindcss` 3 chain (`braces`, `micromatch`, `chokidar`, `postcss-selector-parser`, `fast-glob`, `postcss-nested`) | — | High / moderate | ReDoS / stack-exhaustion DoS in build tooling | Tailwind 4 (**major**) |

Applicability notes:
- The app uses the App Router.
- `next/image` is imported in 2 files, and `/_next/image` responds on the running dev server.
- There are no explicit `"use server"` actions.
- The Windows-host RCE applies to anyone running `npm run dev` / `npm start` on a Windows host (as on this machine), not to the Linux container.
- Exploitation was **not** attempted.

### Python (`pip-audit -r requirements.txt --format json`, Python 3.11 resolver, throwaway container)

94 packages audited: **5 vulnerable, 112 advisory entries** (some duplicated across databases).

| Package | Installed | Advisories | Reachability in this app | Fixed in |
|---|---|---|---|---|
| `pypdf` | 5.1.0 | 85: crafted-PDF DoS (infinite loops, RAM exhaustion, long runtimes) | **Reachable.** `employees.py:145` parses **uploaded payslip PDFs**, and any manager can upload (R4). `rag/loader.py` parses policy PDFs at ingestion. One process means a crafted PDF can stall the API | 6.19.0 (**major** 5 → 6) |
| `python-multipart` | 0.0.20 | 12: multipart / urlencoded parsing DoS; path traversal only with non-default `UPLOAD_DIR` options (not used) | **Likely reachable** on the upload endpoints (`employees.py`, `hr_policies.py`). Whether parsing precedes authentication was not demonstrated | 0.0.31 |
| `starlette` (via FastAPI 0.116.1) | 0.47.3 | 12: Range-header quadratic DoS (`FileResponse`), Host / path validation in URL reconstruction, `request.form()` limit bypass, Windows `StaticFiles` path issue (not used) | Range DoS reachable on authenticated download endpoints (`FileResponse` in `employees.py`) | 0.49.1 – 1.3.1; **needs a FastAPI upgrade** (0.116.x pins Starlette <0.48) |
| `python-jose` | 3.5.0 | 1: CVE-2026-85394, asymmetric-key acceptance in HMAC initialization | **Likely not reachable**: static HS256 secret, `algorithms=["HS256"]` pinned (`core/security.py:33`) | **No fix**; migrate to PyJWT |
| `ecdsa` (via python-jose) | 0.19.2 | 1: CVE-2024-23342 (Minerva timing on P-256 signing) | Not reachable: the app never signs with ECDSA | No fix |

## 6. Findings matrix

Severity: Critical / High / Medium / Low / Info. **Verified** means demonstrated by a run, a passing test or definitive code evidence. **Suspected** means plausible but not demonstrated.

### 6.1 Verified

| ID | Finding | Severity | Evidence | Exploitability | Proposed fix | Regression test |
|---|---|---|---|---|---|---|
| B-01 | `GET /api/v1/employees/{id}` returns another employee's DOB, salary, bank account, IFSC, PAN, UAN and ESI to **any** authenticated user (pre-existing R1) | **Critical** | `[run]` As EMPLOYEE id 3, `GET /employees/2` → 200 with all of those fields populated (names checked only, values not printed); `[test]` characterization test pins it | Trivial: any logged-in employee, sequential ids → whole-organisation exposure of regulated identifiers | Field-level filtering: sensitive financial fields only for **self** and **ADMIN**; others get directory fields. Inspect frontend callers first | Per-role tests (self / peer / manager / admin) on the endpoint; flip the R1 characterization test through an approved remediation |
| B-02 | `next@15.5.2` is in range for 3 critical unauthenticated RCE advisories and ~25 others | **Critical** | `[advisory]` npm audit 2026-10-11 | Advisories describe unauthenticated remote exploitation; not tested here | Upgrade to `next@15.5.27` (same line); re-run tests, `tsc`, build | `npm audit --audit-level=critical` in CI; build + frontend tests |
| B-03 | INACTIVE (deactivated) employees can **log in** and use the whole business API (pre-existing R9; rated Low in the original audit) | **High** | `[test]` `test_inactive_user_not_rejected_by_existing_auth` passed today | A departed employee keeps full access, including B-01 | Reject INACTIVE in `/auth/login`, `/auth/token` and `get_current_user` | Flip the characterization test; add per-endpoint inactive tests |
| B-04 | Token typing is a deny-list: refresh tokens are now rejected (uncommitted fix), but access tokens carry no `type`, and there is no `iss`/`aud` or revocation | **Medium** (the High refresh-confusion part is fixed in the working tree) | `[run]` refresh token → 401 in tests and in Docker; `[code]` `core/security.py` | Low after the fix | Add `type:"access"` and require it; reject unknown types; keep HS256; document no revocation | Token-confusion tests: missing / unknown / refresh type, wrong algorithm |
| B-05 | Managers can approve or reject **any** employee's leave and assign **any** employee to projects through the business API (pre-existing R4) | **Medium** | `[test]` characterization tests (outside-team approval / assignment) passed today | Any manager | Enforce `employee.manager_id == current_user.id` (admin unrestricted) in the endpoints | Flip the characterization tests; add manager-outside-team negatives |
| B-06 | Dockerised stack runs with `JWT_SECRET_KEY=change_me`; `production_checks` enforces only when `ENVIRONMENT=production` | **Medium** (dev) / **Critical if deployed as-is** | `[code]` `.env.example`; `[run]` stack running | Token forgery by anyone who knows the default | Fail startup on a default secret unless `ENVIRONMENT=dev` **and** explicitly allowed; document | Startup-config tests |
| B-07 | Production deployment config absent: `uvicorn --reload`, `next dev`, root containers, no healthchecks, Node 20 (EOL) | **High** (release blocker) | `[code]` Dockerfiles / Compose; `[run]` | Operational and security exposure | Separate production Dockerfiles / Compose: non-root, `next start`, no reload, healthchecks, Node 22/24 LTS | Docker build + smoke test in CI |
| B-08 | Frontend test toolchain cannot run on the image's Node 20 | **Medium** | `[run]` 5 load errors in the container | — | Move the image to a supported Node LTS (with B-07) | Run `npm test` in the image in CI |
| B-09 | No rate limiting, quotas or cost budgets on AI or auth endpoints | **High** (production) | `[code]` search | Login brute force; LLM cost exhaustion (up to 3 model calls per request) | Per-user / per-IP limits; per-user daily token budget; separate AI limits | 429 tests; budget-exceeded tests |
| B-10 | Action execution timeout not enforced: `httpx.ASGITransport` ignores timeouts | **Medium** | `[code]` installed httpx `_transports/asgi.py` has no timeout handling; `actions/client.py:49-117` | A hung handler hangs the confirm request; record stuck in `EXECUTING` | `asyncio.wait_for` deadline; distinct `TIMEOUT` / `UNKNOWN` outcome; no retry | Test with a deliberately hanging route |
| B-11 | No reconciliation of `EXECUTING` / unknown action outcomes | **Medium** | `[code]` `actions/confirmation.py` | State ambiguity after crash or timeout | Stale-`EXECUTING` detection + admin-visible status; idempotency where supported | Interruption simulation tests |
| B-12 | `ACTION_CONFIRMED` audited **before** the atomic claim | **Low** | `[code]` `actions/service.py` `_execute` | Misleading audit on a lost replay race | Separate `CONFIRMATION_ATTEMPTED` / `CLAIMED` events; keep fail-closed before execution | Concurrent-confirm test asserting event sequence |
| B-13 | Audit fingerprint HMAC key derived from `JWT_SECRET_KEY` | **Low** | `[code]` `audit/recorder.py:174-177` | Key coupling; rotation breaks correlation | Dedicated `AI_AUDIT_FINGERPRINT_KEY`, required in production | Config tests |
| B-14 | No retention or purge for conversations, pending actions, audit events | **Medium** | `[code]` search | Indefinite storage of HR personal data | Retention policy + authorized, logged purge (audit purge via a documented procedure) | Purge tests |
| B-15 | User questions, policy text and redacted SQL rows (names, emails, leave reasons) are sent to Anthropic; no documented data-processing decision | **Medium** (governance) | `[code]` `sql/formatter.py`, `rag/service.py` | — | Data-flow register; option to disable LLM formatting; column minimisation | Tests that forbidden fields never reach provider payloads |
| B-16 | `langsmith` is installed transitively; `LANGSMITH_*` / `LANGCHAIN_*` environment variables would export traces externally; no guard | **Low** | `[run]` package present, variables unset | Accidental data export | Production check refusing tracing variables unless explicitly allowed | Config test |
| B-17 | JWT stored in `localStorage` (app-wide pattern) | **Medium** | `[code]` `frontend/app/login/page.tsx:32` | Any XSS → token theft (AI output is rendered as text; tested) | Evaluate HttpOnly cookie session + CSRF (app-wide; Phase 8) | Browser tests (blocked: no browser automation available) |
| B-18 | `GET /hr-policies` exposes server `file_path` (pre-existing R11); legacy `/chat/sessions*` 501 stubs skip the inactive check | **Low** | `[code]` | Information disclosure (paths) | Remove the field from the response; remove or align the stubs | API tests |
| B-19 | Two tests assert file absence instead of their intent, so they fail on any machine that followed the README | **Low** | `[run]` §4 | — | Rewrite: dev DB hash/mtime unchanged across the session; `.env` not tracked by git | The rewritten tests themselves |
| B-20 | No CI, no release gates; live-model quality never measured; RAG threshold calibrated on fake embeddings; Python version drift (3.11 image / 3.12 docs / 3.13 local) | **High** (release blocker) | `[run]` no pipeline; phase-status; `[run]` versions | — | CI on Python 3.11 + Node LTS; offline eval gate; live eval when a key exists | CI itself |
| B-21 | Tailwind 3 build-tool advisories (ReDoS / DoS) | **Low** (build-time only) | `[advisory]` | Needs a malicious build input | Plan the Tailwind 4 migration separately (major) | Build in CI |
| B-22 | `pypdf` 5.1.0 crafted-PDF DoS advisories, reachable through payslip upload | **High** (availability) | `[advisory]` + `[code]` `employees.py:145` | Any manager (R4) uploads a crafted PDF; the single-process API stalls | Upgrade to `pypdf` 6.x (major; test payslip encryption + RAG loading); add a PDF size limit and a parse timeout | Payslip upload/encrypt tests; RAG PDF loader tests; oversized-PDF rejection test |
| B-23 | `python-multipart` 0.0.20 and `starlette` 0.47.3 DoS / parsing advisories | **Medium** | `[advisory]` + `[code]` upload and `FileResponse` endpoints | Authenticated (multipart possibly earlier; not demonstrated) | `python-multipart` 0.0.31 (patch); Starlette via a FastAPI upgrade (also unblocks streaming); request body size limits at the proxy | Full backend suite; upload limit tests |
| B-24 | `python-jose` 3.5.0 has an unfixed advisory and is effectively unmaintained | **Low** (not reachable with pinned HS256) | `[advisory]` + `[code]` | — | Migrate to PyJWT together with B-04 token typing | Token tests (existing + B-04) |

### 6.2 Suspected (not yet demonstrated)

| ID | Suspicion | Why suspected | How to verify |
|---|---|---|---|
| S-01 | Other business endpoints (finance, payslips, documents, attendance, org) may have object-level gaps beyond R1 / R4 | The original audit focused on AI-wrapped endpoints; there's no service layer | Phase 1 endpoint-by-endpoint authorization review with per-role tests |
| S-02 | The `next` RCE advisories are exploitable on this deployment | Version in range; App Router and `next/image` in use | Not attempting exploitation; remediate by upgrading |
| S-03 | SQLite write contention under concurrent chat + action traffic | Single file DB; in-process ASGI calls (original audit R3) | Phase 11 load test |
| S-04 | `python-multipart` DoS is reachable **before** authentication on upload endpoints | Depends on whether FastAPI parses the form body before the auth dependency rejects the request | Targeted test with an unauthenticated oversized / malformed multipart request (Phase 1, non-destructive, local only) |

## 7. Exit status of Phase 0

Phase 0 deliverables are complete:
- git state recorded;
- architecture verified;
- versions recorded;
- backend and frontend suites run (2 environment-triggered test defects explained);
- advisories checked;
- findings matrix produced.

**Phase 1 is not started.** Project rules (`CLAUDE.md`) require approval between phases. Phase 1 also needs explicit approval because it changes existing business endpoints and auth (B-01, B-03, B-05, B-04), changes characterization tests that pin R1 / R4 / R9, and upgrades a dependency (B-02).

### Decisions requested before Phase 1

| # | Decision | Recommendation |
|---|---|---|
| 1 | B-01: who may see sensitive employee fields via `GET /employees/{id}`? | Self and ADMIN only; managers and peers get directory fields. Phase 1 starts by listing the frontend pages that depend on these fields |
| 2 | B-03: reject INACTIVE users at login **and** on every request? | Yes (403 `USER_INACTIVE`, generic message) |
| 3 | B-05: scope manager leave approval and project assignment to direct reports? | Yes (matches the AI matrix; admin unrestricted) |
| 4 | B-04: require `type:"access"` immediately (users re-login once; tokens last 15 min)? | Yes, no transition window |
| 5 | Dependency upgrades (each needs approval under `CLAUDE.md`): `next` 15.5.2 → 15.5.27 (B-02); `python-multipart` → 0.0.31 (B-23); `pypdf` 5 → 6 (B-22, major); FastAPI upgrade for Starlette (B-23); `python-jose` → PyJWT (B-24) | Phase 1: `next`, `python-multipart`, `pypdf` (security-critical, small surface). Phase 9: FastAPI/Starlette, PyJWT, Tailwind 4, Node LTS image |
| 6 | Change characterization tests R1 / R4 / R9 and the two fragile tests (B-19) as part of the approved remediation? | Yes, with the reason recorded in `phase-status.md` |
| 7 | Commit the already-approved working-tree changes before Phase 1? | Recommended, so Phase 1 diffs stay reviewable |
