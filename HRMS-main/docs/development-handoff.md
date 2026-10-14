# Development handoff: continuing on another system

**Date:** 2026-10-14 · **Release status: NOT YET PRODUCTION READY**

> ⚠ **The local commits are not on GitHub.** A fresh `git clone` of `origin` gives `c685e8b` ("Phase 10 complete") and none of the production-hardening work. Transfer the local commits first (§11).

## 1. Project and architecture

The NovaWorks PeopleOps AI HR Copilot adds AI features to the existing CB Nest HRMS in `HRMS-main/`:
- **Policy RAG**, with citations;
- **read-only SQL agent**;
- **HR action agent**, where the model proposes, the backend authorizes, and the user confirms;
- **LangGraph router**;
- **conversation memory**;
- **append-only AI audit**;
- **deterministic AI authorization (RBAC)**.

Stack:
- **Backend:** FastAPI + async SQLAlchemy + SQLite, with Alembic.
- **Frontend:** Next.js 15 App Router.

Where to read more:
- **Rules:** `CLAUDE.md` and `.claude/rules/` (read these first).
- **Architecture:** `docs/ai_architecture.md`, `docs/architecture/`, `docs/ai-components-inventory.md`, `docs/ai-components-architecture-and-production-readiness.md`.
- **Permissions:** `docs/ai_permissions_matrix.md` (AI); `docs/production-hardening/authorization-matrix.md` (business API, all 60 routes).
- **Phase history:** `docs/phase-status.md`. The hardening programme definition is `docs/production-hardening/master-prompt.md`; approval records override that plan.

## 2. Completed work (local `main`)

| Stage | Record | Key commits |
|---|---|---|
| Phases 0–10 (assignment build) | `docs/phase-status.md`, `docs/phase-10-final-report.md` | `c685e8b` (on origin) |
| Pre-hardening fixes | `docs/production-hardening/baseline.md` | `ebca752`, `06f6fb9`, `cf9233a` |
| Hardening Phase 0: baseline | `baseline.md` | `cf9233a` |
| Hardening Phase 1: critical security remediation | `security-remediation.md` | `ab3d1d0`, `3df00f5`, `ff7a448`, `e493031`, `79c26d4` |
| Hardening Phase 2: deployment, abuse controls, data lifecycle, CI | `phase-2-report.md`, `deployment-runbook.md`, `backup-restore.md`, `data-processing.md` | `2531d72` … `44882b8` (12 commits) |
| Phase 2 closure (approved 2026-10-14) | `authorization-matrix.md`, this file | `6bcbc6d` (pytest 9), `bee1a3f` (postcss override), `16b06eb` (ADMIN-only policy publication), `0246c17` (matrix, admin self-actions) |
| Phase 3, offline workstreams only | `rag-calibration.md`, `monitoring-alerts.md`, `release-readiness.md` | `9f48358` (3.3), `c875a06` (3.6), `c3d6928` (3.8 / 3.9) |

All production-hardening documents are in `docs/production-hardening/`.

## 3. Current boundary and next approved tasks

**Approved and done:** everything in §2.

**Approved scope with nothing left to implement:** Phase 3 offline workstreams 3.3, 3.6, 3.8 and 3.9. If you resume, stay within them:
- **3.3:** re-run `python -m evals.rag_calibration` whenever the policy corpus or dataset changes. Don't change `AI_RAG_MIN_SCORE` without approval.
- **3.6:** extend `evals/datasets/redteam_v1.json` by bumping its version. Keep the invariants deterministic.
- **3.8 / 3.9:** keep `monitoring-alerts.md` and `release-readiness.md` current.

**NOT approved; do not start:**
- live-model evaluation (3.1);
- the reviewed evaluation dataset (3.2);
- live quality metrics (3.4);
- live latency and cost (3.5);
- Playwright browser tests (3.7);
- any later programme phase;
- S-02 / S-03 behaviour changes;
- Tailwind 4 or Next 16;
- any push or deployment.

**Decisions waiting on the project owner** (`release-readiness.md` §6 lists the release gates):
1. **S-02 admin self-actions.** An ADMIN can demote themselves and leave zero active admins. Proposed controls are in `authorization-matrix.md` §2.
2. **S-03 organisation-wide content.** Announcements, polls and the project catalogue need permissions decisions (`authorization-matrix.md` §3).
3. **braces advisory exception.** Name an owner and approver, or plan Tailwind 4. The CI dependency gate fails until then, by design.
4. **Retrieval dataset.** HR review of the dataset, then acceptance targets, before any `AI_RAG_MIN_SCORE` change.
5. **Data governance and lifecycle.** Data-processing decisions 1–3, retention periods, backup encryption, destination and off-host copies.
6. **Live-model work.** An API key and evaluation budget, through a secure process.

## 4. Known blockers

| Area | Blocker |
|---|---|
| Security | S-02, S-03 open; B-17 (JWT in `localStorage`, no revocation) |
| Dependencies | `braces` GHSA-vfj7 (build-time; no patched release) has a PROPOSED exception, so the CI gate fails. Next's postcss is fixed by an override (remove it on `next@16`) |
| AI evaluation | No live-model results (no key). Retrieval calibration is a proposal on a small demo dataset |
| Operations | CI never ran on GitHub. No TLS proxy, alerting or backup schedule. Single worker / single instance only |

## 5. Required versions

| Component | Version | Where declared |
|---|---|---|
| Python | **3.11** (all verification ran on 3.11.17) | `backend/Dockerfile` (`python:3.11-slim`), `.github/workflows/ci.yml`, `.python-version` |
| Node.js | **24.21.0** for production and CI; the dev Compose image still uses `node:20-alpine` (unchanged dev workflow) | `frontend/Dockerfile`, `ci.yml`, `frontend/.nvmrc` |
| Docker | Docker Desktop / Engine with Compose v2 | — |

## 6. Install and test

```bash
# Backend: tests build a throwaway SQLite DB through Alembic and never touch storage/hrms.db; no .env needed.
cd HRMS-main/backend
python3.11 -m venv .venv && . .venv/bin/activate        # Windows: py -3.11 -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                                      # full suite (some tests need a git checkout)
python -m evals.run_evals                                # offline evals; live part reports NOT_RUN without a key
python -m pytest -q ../../.github/scripts                # advisory-gate tests (run from backend/)

# Same, without a local Python, in a throwaway container (this is how results were verified):
docker run --rm -v "$PWD/../..:/repo" -w /repo/HRMS-main/backend python:3.11 sh -c \
  "git config --global --add safe.directory '*' && pip install -q -r requirements-dev.txt && python -m pytest -q"

# Frontend
cd HRMS-main/frontend
npm ci
npm test            # Vitest
npx tsc --noEmit
npm run build
```

The virtual-environment route on a host Python 3.11 is not verified on the current machine, which has no Python 3.11. The container route is verified.

## 7. Database, seed and policy index (development stack)

```bash
cd HRMS-main
cp backend/.env.example backend/.env       # then fill values locally; never commit .env
docker compose up -d --build
docker compose exec api alembic -c alembic.ini upgrade head     # head: 0021_hr_policy_publication
docker compose exec api python scripts/seed.py                  # demo data only
docker compose exec api python scripts/ingest_policies.py       # needs AI_ENABLED=true; downloads the embedding model once
```

- Existing databases need `alembic upgrade head` for migration **0021**. Existing policies become PUBLISHED version 1.
- Run `ingest_policies.py` again after an ADMIN publishes a policy.
- The README uses `docker-compose` (v1 syntax); either form works.

## 8. Environment variables (names only; see `backend/.env.example`, `backend/production.env.example`, `frontend/.env.example`)

| Group | Variables |
|---|---|
| Core and auth | `ENVIRONMENT`, `APP_NAME`, `APP_TIMEZONE`, `DATABASE_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` |
| HTTP | `ALLOWED_HOSTS`, `CORS_ALLOW_ORIGINS`, `FORWARDED_ALLOW_IPS` |
| Storage | `EMPLOYEE_DOCUMENT_UPLOAD_DIR`, `POLICY_UPLOAD_DIR`, `PROFILE_PHOTO_UPLOAD_DIR` |
| AI provider | `AI_ENABLED`, `AI_LLM_PROVIDER`, `AI_LLM_MODEL`, `AI_LLM_FAST_MODEL`, `ANTHROPIC_API_KEY`, `AI_MAX_TOKENS`, `AI_TEMPERATURE`, `AI_LLM_EFFORT`, `AI_LLM_SERVER_FALLBACKS`, `AI_REQUEST_TIMEOUT_SECONDS`, `AI_*_COST_PER_MTOK` |
| AI behaviour | `AI_EMBEDDING_PROVIDER`, `AI_EMBEDDING_MODEL`, `AI_EMBEDDING_CACHE_DIR`, `AI_RAG_TOP_K`, `AI_RAG_MIN_SCORE`, `AI_POLICY_CHUNK_SIZE`, `AI_POLICY_CHUNK_OVERLAP`, `AI_SQL_MAX_ROWS`, `AI_SQL_MAX_JOINS`, `AI_SQL_TIMEOUT_SECONDS`, `AI_SQL_RESULT_FORMATTING` (keep `deterministic`), `AI_ACTION_CONFIRMATION_TTL_SECONDS`, `AI_ACTION_EXECUTION_TIMEOUT_SECONDS`, `AI_CONVERSATION_*` |
| Limits | `AI_RATE_LIMIT_PER_MINUTE`, `AI_DAILY_TOKEN_BUDGET`, `AI_DAILY_COST_CAP_USD`, `AI_MAX_CONCURRENT_LLM_CALLS`, `AI_LLM_QUEUE_TIMEOUT_SECONDS`, `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` |
| Audit and retention | `AI_AUDIT_ENABLED`, `AI_AUDIT_FINGERPRINT_KEY`, `AI_CONVERSATION_RETENTION_DAYS`, `AI_PENDING_ACTION_RETENTION_DAYS`, `AI_AUDIT_RETENTION_DAYS`, `ALLOW_EXTERNAL_TRACING` |
| Frontend | `NEXT_PUBLIC_API_BASE_URL` (build time) |

Production startup refuses unsafe values (`app/core/production_checks.py`).

## 9. Docker builds

```bash
cd HRMS-main
docker compose build                                               # development images (dev targets)
docker compose -f docker-compose.prod.yml build                   # production targets (see deployment-runbook.md)
docker build --target production -t hrms-api:ci backend
docker build --target production -t hrms-web:ci --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 frontend
```

Production configuration, smoke checks, backups and the verification checklist are in `docs/production-hardening/deployment-runbook.md`.

## 10. Latest verified results (2026-10-14)

| Check | Environment | Result |
|---|---|---|
| Backend tests | Clean `python:3.11` container (3.11.17), using the exact §6 container command, no `.env` | **1966 passed** |
| Offline evals | same | 139/139; LIVE_MODEL_EVAL = NOT_RUN |
| Advisory-gate tests (run from `backend/`) | same | 25 passed |
| Frontend tests / tsc / build | Host Node 24.13 | 90 passed / 0 errors / PASS |
| Docker production builds and smoke | `docker build --target production` | Both images build. Unsafe config refused (exit 3). API uid 10001, web uid 1000. API with throwaway secrets: migrated to `0021` (head), health 200, spoofed Host 400 |
| Retrieval calibration | fastembed 0.8.1 offline | proposal only (`rag-calibration.md`) |
| Dependency gate | npm + pip audit | **FAIL by design** (`braces`, PROPOSED exception) |

## 11. Git state and transferring the work

The state when this file was written: branch `main` tracking `origin/main` (remote head `c685e8b`). After the handoff commits, `main` is **30 local commits ahead** and none are pushed (`git status -sb` shows the current count).

**Option A (needs approval to push):** `git push origin main`, then clone or pull on the other computer.

**Option B (no push): git bundle.**

```bash
# On this computer, from the repository root:
git bundle create ../novaworks-main.bundle origin/main..main
git bundle verify ../novaworks-main.bundle
# On the other computer (in an existing clone of origin):
git fetch /path/to/novaworks-main.bundle main:refs/remotes/bundle/main
git log --oneline origin/main..bundle/main      # review
git merge --ff-only bundle/main
```

Bundle limitations:
- It carries commits only. It does **not** carry ignored or untracked files: `.env`, `storage/` (local DB, uploads, model cache), `writing-block*.md`, the assignment brief and the phase prompt files.
- The other clone must already contain `origin/main` (`c685e8b`). A bundle doesn't change GitHub.

**Files to copy manually if wanted (never commit them):**
- `backend/.env`: recreate from the example instead of copying secrets where possible.
- `assignment_05_ai_hr_copilot.md` and `Claude Code Master Prompt — AI HR Operations Copilot.md`: `CLAUDE.md` refers to them; they're git-ignored by design.
- `writing-block.md`: its content is preserved as `docs/production-hardening/master-prompt.md`.

## 12. Resuming with Claude Code

1. Read `CLAUDE.md`, `.claude/rules/`, `docs/phase-status.md` (end of file) and this handoff.
2. Re-run §6 to confirm a green baseline on the new machine before changing anything.
3. Work only inside the approved boundary (§3). Stop for approval between phases. Never push, deploy, approve advisory exceptions or enable live-model calls without explicit approval.
