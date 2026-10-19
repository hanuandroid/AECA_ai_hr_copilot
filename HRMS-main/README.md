<img width="2866" height="1274" alt="image" src="https://github.com/user-attachments/assets/4b4defd5-663f-45d1-b9e3-f9adbcfabbae" />

# CB Nest

**CB Nest** is a full-stack HR Management System built for hands-on learning. It covers real-world business workflows — employee lifecycle, attendance, leave approvals, payroll, ticketing, and more — using a modern stack (FastAPI + Next.js + Docker). Use it to understand how production-grade HRMS platforms work, and extend it with your own AI features.

> 📖 **New here?** Start with the [Learner Guide](docs/Learner_Guide.md) for a complete walkthrough of the project architecture, database, and how to explore the code.

## Overview

This project provides a working HRMS platform with authentication, employee operations, attendance, leave management, communication features, finance data views, and ticketing workflows.

It is designed as a practical base for AI feature integration (RAG, assistants, agent workflows) without rebuilding core HR modules from scratch.


## Tech Stack

- Frontend: Next.js 15, React 19, Tailwind CSS
- Backend: FastAPI, SQLAlchemy (async), Pydantic v2
- Database: SQLite
- Migrations: Alembic
- Orchestration: Docker Compose

## Features

- JWT auth with role-aware access (ADMIN, MANAGER, EMPLOYEE)
- Employee directory with search, filters, and pagination
- Attendance clock in/clock out with status and mode tracking
- Leave balances and leave request workflow
- Announcements and polls
- Team calendar (leaves, WFH, holidays, birthdays)
- My Profile edits, profile photo upload, job history, documents
- Finance views (salary, statutory, payroll history)
- Tickets with assignment, status updates, onboarding tasks
- HR policy upload/download library
- Admin/Manager employee document upload flow (APPOINTMENT, TAX, PAYSLIP, OTHER)
- My Documents with search, view, and download; delete is allowed only for `OTHER` document type
- Password-protected PDF payslips (DOB in `DD-MM-YY`) for generated and uploaded payslips
- Notification bell for announcements, polls, ticket assignment, ticket status, leave decision, and employee-document uploads by others (not self-uploads)
- **NovaWorks PeopleOps Copilot** (AI): policy Q&A with citations, read-only HR data questions, HR actions with
  explicit confirmation, conversations with follow-ups, role-specific modes, an AI audit log and an AI usage dashboard
  (see [AI Copilot](#ai-copilot) below)

## Repository Structure

```text
.
|-- backend/
|   |-- alembic/
|   |   |-- versions/
|   |   `-- env.py
|   |-- app/
|   |   |-- api/v1/endpoints/
|   |   |-- core/
|   |   |-- db/
|   |   |-- models/
|   |   |-- schemas/
|   |   `-- services/
|   |-- scripts/
|   |   `-- seed.py
|   |-- storage/
|   |   |-- hr-policies/
|   |   `-- profile-photos/
|   |-- .env.example
|   |-- alembic.ini
|   |-- Dockerfile
|   `-- requirements.txt
|-- frontend/
|   |-- app/
|   |   |-- announcements/
|   |   |-- attendance/
|   |   |-- dashboard/
|   |   |-- employees/
|   |   |-- finance/
|   |   |-- hr-policies/
|   |   |-- leaves/
|   |   |-- login/
|   |   |-- me/
|   |   |-- organization/
|   |   |-- polls/
|   |   |-- team-calendar/
|   |   `-- tickets/
|   |-- components/
|   |   |-- layout/
|   |   `-- ui/
|   |-- lib/
|   |   `-- api.ts
|   |-- Dockerfile
|   |-- middleware.ts
|   `-- package.json
|-- docs/
|   |-- api/
|   |-- Learner_Guide.md
|   |-- PRD.md
|   `-- db_tables_samples.md
|-- docker-compose.yml
`-- README.md
```

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose v2)
  - On Windows: install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and ensure the **WSL 2 backend** is enabled
- **Git**


## Quick Start

First-time setup:

```bash
git clone <repo-url>
cd HRMS
cp backend/.env.example backend/.env
```


For PowerShell on Windows:

```powershell
Copy-Item backend/.env.example backend/.env
```

Then run from repository root:

```bash
docker-compose build
docker-compose run --rm api alembic -c alembic.ini upgrade head   # the API refuses to start until the DB is at head
docker-compose up -d
docker-compose exec api python scripts/seed.py
```

Open:

- App: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- API redoc: `http://localhost:8000/redoc`

Verify everything is working:

```bash
docker-compose ps
```

## Default Credentials

- Admin: `admin@mock-hrms.dev` / `password123`
- Manager: `manager@mock-hrms.dev` / `password123`
- Employee: `employee@mock-hrms.dev` / `password123`

## Configuration

Backend environment file: `backend/.env`

Use `backend/.env.example` as reference. Current key settings:

- `DATABASE_URL=sqlite+aiosqlite:///./storage/hrms.db`
- `APP_TIMEZONE=Asia/Kolkata`
- JWT settings (`JWT_SECRET_KEY`, expiry values)

### AI configuration (backend/.env)

Provider selection (Anthropic or OpenAI), setup, mocked tests and the approved smoke test: [`docs/ai-provider-setup.md`](docs/ai-provider-setup.md).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes, when `AI_LLM_PROVIDER=anthropic` | — | Anthropic key. Environment only; never committed. |
| `OPENAI_API_KEY` | yes, when `AI_LLM_PROVIDER=openai` | — | OpenAI key. Environment only; never committed; never sent to the browser. |
| `AI_OPENAI_MODEL` / `AI_OPENAI_FAST_MODEL` | `AI_OPENAI_MODEL` yes, when `openai` | none / falls back to `AI_OPENAI_MODEL` | OpenAI model ids (must support Structured Outputs with JSON schema). No default model is assumed |
| `AI_OPENAI_REASONING_EFFORT` | no | unset | Sent only when set (reasoning-capable OpenAI models) |
| `AI_ENABLED` | no | `true` | `false` turns every AI endpoint into `503 AI_UNAVAILABLE` |
| `AI_LLM_PROVIDER` | no | `anthropic` | `anthropic`, `openai` or `fake` (fake is for tests; refused when `ENVIRONMENT=production`). Switching is configuration only; a misconfigured provider fails closed (`AI_UNAVAILABLE`) and never falls back to another provider |
| `AI_LLM_MODEL` / `AI_LLM_FAST_MODEL` | no | `claude-sonnet-5-5` / `claude-haiku-4-5-20251001` | Anthropic answer/generation model, and the routing classifier model |
| `AI_EMBEDDING_PROVIDER` / `AI_EMBEDDING_MODEL` | no | `fastembed` / `BAAI/bge-small-en-v1.5` | Local embeddings for Policy RAG (no external API) |
| `AI_RAG_TOP_K`, `AI_RAG_MIN_SCORE`, `AI_POLICY_CHUNK_SIZE`, `AI_POLICY_CHUNK_OVERLAP` | no | `4`, `0.35`, `800`, `120` | Retrieval and chunking |
| `AI_SQL_MAX_ROWS`, `AI_SQL_MAX_JOINS`, `AI_SQL_TIMEOUT_SECONDS` | no | `100`, `4`, `3` | SQL agent limits |
| `AI_ACTION_CONFIRMATION_TTL_SECONDS` | no | `300` | Lifetime of an action confirmation |
| `AI_CONVERSATION_MAX_MESSAGES`, `AI_CONVERSATION_CONTEXT_MESSAGES`, `AI_CONVERSATION_CONTEXT_CHARS` | no | `200`, `6`, `2000` | Conversation limits and follow-up context |
| `AI_AUDIT_ENABLED` | no | `true` | Persist AI audit events |
| `AI_LLM_[FAST_]INPUT/OUTPUT_COST_PER_MTOK`, `AI_OPENAI_[FAST_]INPUT/OUTPUT_COST_PER_MTOK` | no | unset | Optional per-provider pricing for **estimated** cost in the usage dashboard (rates are never shared between providers) |

With `ENVIRONMENT=production` the API refuses to start with the default `JWT_SECRET_KEY`, fake AI providers, or a
missing key or model for the selected provider (`ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` + `AI_OPENAI_MODEL`).

### Password step-up for high-impact admin operations (G-11)

`STEP_UP_ENFORCEMENT` = `off` | **`log`** (default and approved) | `enforce` (not approved, not enabled). Creating,
deactivating or reactivating an employee, changing an employee's password, role, email, bank or PAN details, and
posting an announcement or poll to the whole organisation need the administrator's current password in the
`X-Step-Up-Password` header; the admin UI asks for it in a dialog. In `log` mode a missing or wrong password is only
logged (`auth.step_up.not_satisfied`), never blocked. The value is read at startup (recreate the API container after
changing it). Operator details, rollback and header-logging requirements:
[`deployment-runbook.md`](docs/production-hardening/deployment-runbook.md) §2.1.

## Common Commands

Start services:

```bash
docker-compose up -d
```

Restart API + Web after code changes:

```bash
docker compose restart api web
```

Stop services:

```bash
docker-compose down
```

Run migrations:

```bash
docker-compose run --rm api alembic -c alembic.ini upgrade head
docker-compose restart api   # the API refuses to start on an older database (start-up revision guard)
```

Reseed data:

```bash
docker-compose exec api python scripts/seed.py
```

Optional one-time migration (legacy payslip files to DOB-password-protected PDFs):

```bash
docker compose exec api python scripts/migrate_payslips_to_encrypted_pdf.py
```

Check containers:

```bash
docker-compose ps
```

## Reset Database

On macOS/Linux:

```bash
docker-compose down
rm -f backend/storage/hrms.db
docker-compose build
docker-compose run --rm api alembic -c alembic.ini upgrade head   # the API refuses to start until the DB is at head
docker-compose up -d
docker-compose exec api python scripts/seed.py
```

On PowerShell:

```powershell
docker-compose down
Remove-Item backend\storage\hrms.db -Force -ErrorAction SilentlyContinue
docker-compose build
docker-compose run --rm api alembic -c alembic.ini upgrade head   # the API refuses to start until the DB is at head
docker-compose up -d
docker-compose exec api python scripts/seed.py
```

## API Notes

- API base path: `/api/v1`
- Health checks:
  - `/health`
  - `/api/v1/health`
- Standard response envelope:
  - Success: `{ "success": true, "data": ..., "error": null }`
  - Error: `{ "success": false, "data": null, "error": { "code": "...", "message": "..." } }`
- Document uploads:
  - Employee self-upload: `POST /api/v1/employees/me/documents`
  - Employee self-delete: `DELETE /api/v1/employees/me/documents/{document_id}` (`OTHER` type only)
  - Admin/Manager upload for any employee: `POST /api/v1/employees/{employee_id}/documents`
  - Admin/Manager payslip upload: `POST /api/v1/employees/{employee_id}/documents/payslip`

## AI Copilot

Status (2026-10-19): assignment Phases 0–10 complete; production hardening, RBAC remediation, the AI security
remediation and G-11 password step-up done locally; the capstone completion programme is in progress
([`capstone-completion-progress.md`](docs/production-hardening/capstone-completion-progress.md) is the current
source of truth for status and evidence).
- **Production readiness: NOT YET PRODUCTION READY** (release gates: `docs/production-hardening/release-readiness.md` §6).
- **Latest fresh results:** backend 3154 passed, offline evaluations 139/139 (scripted model output), CI gate tests 25 passed (container run 2026-10-19 at `4e37fb8`); frontend 238 passed and TypeScript 0 errors (host run 2026-10-19 at `5d4a20b`).
- **Not run yet:** live-model evaluation (needs approval for external calls and spending), browser walkthrough of the
  demo, CI on GitHub. The local commits are not on GitHub until a push is approved.

The NovaWorks PeopleOps Copilot adds AI to CB Nest without bypassing it:

```text
Next.js /copilot ─► FastAPI /api/v1/chat ─► AI authorization (DB role, INACTIVE rejected)
   ─► conversation ownership ─► LangGraph router ─► Policy RAG | read-only SQL agent | action PROPOSAL
   ─► existing HRMS APIs (only after explicit confirmation) ─► append-only AI audit
```

- **UI:** `/copilot` (chat, conversations, citations, data tables, confirmation cards). Admins also get
  `/admin/ai-usage` (operational metrics) and `/admin/ai-audit` (audit log).
- **Endpoints:**
  - `POST /api/v1/chat` (unified).
  - `POST /api/v1/chat/policy`, `/chat/sql`, `/chat/actions`, `/chat/actions/confirm`.
  - `GET /api/v1/chat/permissions`, `GET|DELETE /api/v1/chat/conversations[/{id}]`.
  - `GET /api/v1/ai/audit[/metrics]`.
- **Guarantees:**
  - The LLM never decides authorization.
  - AI never writes business tables; actions go through the existing APIs with the user's own token, only after
    explicit confirmation.
  - The SQL agent is read-only and scoped.
  - Passwords, bank, PAN, DOB, salary and payroll identifiers never leave via AI.
- **Where each requirement lives** (full deliverable map: [`ai_architecture.md`](docs/ai_architecture.md) §2.1):

  | Requirement | Package / service (`backend/app/`) | Endpoint | Tests (`backend/tests/` unless noted) |
  |---|---|---|---|
  | Policy RAG | `services/ai/rag/` (`PolicyRagService`), `services/ai/embeddings.py` | `POST /api/v1/chat/policy` | `ai/rag/`, `evals/policy_eval.json` |
  | Read-only Text-to-SQL | `services/ai/sql/` (parser, validator, scope, read-only executor) | `POST /api/v1/chat/sql` | `ai/sql/`, `ai/hardening/`, `evals/sql_eval.json` |
  | HR actions via existing APIs | `services/ai/actions/` (registry, confirmation, client with the user's token) | `POST /api/v1/chat/actions`, `/chat/actions/confirm` | `ai/actions/`, `evals/action_eval.json` |
  | Router (LangGraph) and conversations | `services/ai/orchestration/`, `services/ai/conversation/` | `POST /api/v1/chat`, `/chat/conversations` | `ai/orchestration/`, `ai/conversation/` |
  | RBAC and backend authorization | `services/ai/authorization/`; business routes in `api/v1/endpoints/` | `GET /api/v1/chat/permissions` | `ai/authorization/`, `rbac/`, `characterization/` |
  | Audit logging | `services/ai/audit/`, table `ai_audit_events` (no message text; see `ai_architecture.md` §3.5) | `GET /api/v1/ai/audit[/metrics]` | `ai/audit/` |
  | Provider boundary and limits | `services/ai/provider_policy.py`, `services/ai/limits.py`, `services/ai/llm/` | all model-calling routes | `ai/test_provider_policy.py`, `ai/test_limits.py`, `ai/test_openai_provider.py` |
  | Frontend | `frontend/app/copilot/`, `frontend/components/copilot/`, `frontend/lib/copilot-*.ts` | — | `frontend/tests/copilot-*.test.tsx` |
- **Policy index:** after migrate and seed, run
  `docker-compose exec api python scripts/ingest_policies.py`.
- **Tests and evaluation** (offline, no API key):
  - `cd backend && .venv/Scripts/python -m pytest -q`
  - `cd frontend && npm test && npm run build`
  - `cd backend && .venv/Scripts/python -m evals.run_evals`. `--live` runs the live-model suites (router classification and Policy RAG answer quality) with the provider selected by the environment: `AI_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, or `AI_LLM_PROVIDER=openai` + `OPENAI_API_KEY` + `AI_OPENAI_MODEL`; otherwise NOT_RUN. External calls that cost money: run only with approval and synthetic data.

Docs:
- [`ai_architecture.md`](docs/ai_architecture.md)
- [`ai_permissions_matrix.md`](docs/ai_permissions_matrix.md)
- [`ai_eval_results.md`](docs/ai_eval_results.md)
- [`ai_threat_model.md`](docs/ai_threat_model.md)
- [`architecture/`](docs/architecture/)
- [`phase-10-final-report.md`](docs/phase-10-final-report.md)

## Troubleshooting

- If frontend shows stale build/runtime issues:
  - `docker-compose restart web`
- If API changes are not reflected:
  - `docker-compose restart api`
- If migration or seed fails:
  - reset DB using the commands above, then migrate and seed again

## Documentation

- Learner guide: [`Learner_Guide.md`](docs/Learner_Guide.md) — full project walkthrough, architecture, database schema, learning path
- Product requirements: [`PRD.md`](docs/PRD.md)
- Database schema reference: [`db_tables_samples.md`](docs/db_tables_samples.md)
- AI architecture and contracts: [`ai_architecture.md`](docs/ai_architecture.md), [`architecture/`](docs/architecture/)
- AI permissions: [`ai_permissions_matrix.md`](docs/ai_permissions_matrix.md); AI evaluation results: [`ai_eval_results.md`](docs/ai_eval_results.md)
- Final AI report: [`phase-10-final-report.md`](docs/phase-10-final-report.md)
- Demo walkthrough (setup to teardown, synthetic data): [`demo-script.md`](docs/demo-script.md)


Copyright (c) Codebasics. All rights reserved.


