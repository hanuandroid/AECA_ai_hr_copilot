# Repository Audit — CB Nest (Phase 0)

Audit date: 2026-09-30
Scope: `HRMS-main/` (backend, frontend, docs, infra). Read-only inspection; no application code changed.

---

## 1. Current Architecture

| Layer | Technology | Evidence |
|---|---|---|
| Frontend | Next.js 15.5 (App Router), React 19, Tailwind 3, lucide-react, shadcn-style UI primitives | `frontend/package.json`, `frontend/components/ui/*` |
| Backend | FastAPI 0.116, Pydantic v2, SQLAlchemy 2.0 async | `backend/requirements.txt`, `backend/app/main.py` |
| Database | SQLite via `aiosqlite` (`sqlite+aiosqlite:///./storage/hrms.db`) | `backend/app/core/config.py` |
| Migrations | Alembic, 16 hand-written revisions (`0001` → `0016_employee_documents_uploaded_by`) | `backend/alembic/versions/` |
| Logging | `structlog` JSON renderer, configured in app lifespan | `backend/app/core/logging.py` |
| Orchestration | Docker Compose: `api` (uvicorn --reload, port 8000), `web` (next dev, port 3000), code volume-mounted | `docker-compose.yml` |
| CI/CD | **None** | no workflow files |
| Git | Workspace is **not** a git repository | environment |

### Backend layout

```text
backend/app/
  main.py                 FastAPI app, CORS (localhost:3000), /health
  api/v1/router.py        mounts all routers under /api/v1
  api/v1/endpoints/*.py   route handlers — contain business logic + queries directly
  core/config.py          pydantic-settings Settings (env-driven)
  core/security.py        bcrypt hashing, JWT encode/decode (python-jose, HS256)
  core/response.py        success_response / error_response envelope helpers
  core/logging.py         structlog config
  db/session.py           async engine, SessionLocal, get_db dependency
  models/*.py             SQLAlchemy ORM models (20 tables)
  schemas/*.py            Pydantic request models (few response models)
  services/auth.py        get_current_user, require_roles  ← only service module
scripts/seed.py           destructive reseed: 1,003 employees, skills, projects, policies, payroll
```

**Important architectural fact:** there is **no service or repository layer** for business logic. Validation, authorization and persistence all live inside endpoint functions (e.g. `leaves.py::submit_leave_request`, `approve_leave_request`). This means:

- The only way for an AI agent to reuse existing business rules **without duplicating them** is to call the **HTTP endpoints** (in-process via ASGI or over the network). Importing endpoint functions directly would bypass FastAPI dependency resolution and is not recommended.
- This directly supports the assignment rule *Agent → Backend API → existing logic → DB*.

### Response envelope

All endpoints use `{"success": bool, "data": ..., "error": {"code","message"} | null}`. Errors are raised as `HTTPException(detail=error_response(...))`, so on the wire errors look like `{"detail": {"success": false, ...}}`. Some policy endpoints raise plain-string `detail`. AI endpoints must return the top-level envelope (use `JSONResponse`), matching `chat.py` stubs.

---

## 2. Authentication

- `POST /api/v1/auth/login` (JSON) and `POST /api/v1/auth/token` (OAuth2 form) issue JWTs.
- Access token payload: `{"sub": "<employee_id>", "role": "<ROLE>", "exp"}`; HS256; 15-minute expiry (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- A refresh token is issued, but **there is no refresh endpoint**.
- `services/auth.py::get_current_user` decodes the bearer token, **re-loads the Employee from the DB** and returns the ORM object. Role is taken from the DB row, not the token claim. That is correct and the AI layer must reuse it.
- `get_current_user` does **not** reject `INACTIVE` employees. A deactivated user with a live token keeps access until expiry.
- Frontend stores the access token in `localStorage["hrms_access_token"]`. It sets `hrms_auth=1` and `hrms_role=<role>` cookies for **UX only**. `middleware.ts` only checks that `hrms_auth` exists.
- `JWT_SECRET_KEY` defaults to `"change_me"` in both `config.py` and `.env.example`.

## 3. Authorization

- Mechanism: inline role checks (`if current_user.role not in {Role.ADMIN, Role.MANAGER}`) or the `require_roles(...)` dependency.
- Roles: `ADMIN`, `MANAGER`, `EMPLOYEE` (`models/enums.py::Role`).
- **No team/ownership scoping for managers anywhere.** Managers can list **all** pending leaves and approve/reject **any** leave, assign any ticket, and assign any employee to any project.
- Ownership scoping exists only for employees on tickets (`employee_id` or `assignee_id`) and for "me" endpoints.
- Team relationship available in data: `employees.manager_id` (self-FK). Also `department_id`.

### Endpoint authorization inventory (relevant to AI)

| Method + Path | Who | Notes |
|---|---|---|
| GET `/leaves/balances/me` | any | also self-heals/creates balance rows (writes!) |
| GET `/leaves/requests/me` | any | own only |
| POST `/leaves/requests` | any | validates dates, half-day, overlap; ADMIN self-leave auto-approved |
| GET `/leaves/requests/pending` | MANAGER, ADMIN | **all** pending, not team-scoped |
| POST `/leaves/requests/{id}/approve` | MANAGER, ADMIN | checks balance; no team check |
| POST `/leaves/requests/{id}/reject` | MANAGER, ADMIN | no team check |
| GET `/tickets` | any | employee: own/assigned; mgr/admin: all (`mine=true` filter) |
| POST `/tickets` | any | category IT/HR/ONBOARDING, priority LOW/MEDIUM/HIGH |
| POST `/tickets/{id}/assign` | MANAGER, ADMIN | sets IN_PROGRESS if OPEN |
| POST `/tickets/{id}/status` | MANAGER, ADMIN, owner, assignee | |
| GET/POST `/announcements` | read: any; create: MANAGER, ADMIN | |
| GET `/employees` | any | directory: id, name, email, dept, role, status, joining_date |
| GET `/employees/{id}` | **any** | ⚠ returns salary, bank, PAN, DOB, PF/ESI. See Risks R1 |
| GET `/employees/{id}/projects` | MANAGER, ADMIN | |
| POST `/employees/{id}/projects` | MANAGER, ADMIN | `{project_id, role_on_project}`; upserts |
| GET/POST `/employees/projects/catalog` | MANAGER, ADMIN | create project |
| DELETE `/employees/{id}` | ADMIN | deactivate (soft) |
| PATCH `/employees/{id}/reactivate` | ADMIN | |
| GET `/finance/payroll/me`, `/statutory/me`, `/profile/me` | any | own only; includes bank/PAN |
| GET `/hr-policies` | any | returns `file_path` (internal server path) |
| POST `/hr-policies/upload` | MANAGER, ADMIN | .txt/.md/.pdf ≤ 2 MB, stored under `POLICY_UPLOAD_DIR` |
| GET `/hr-policies/{id}/download` | any | path-traversal guarded |
| GET `/calendar/team` | any | mgr/admin: everyone; employee: own department |

**Endpoints referenced by the assignment that do NOT exist:**
- `PATCH /api/v1/leaves/requests/{id}`: no leave edit/cancel API. The real routes are `POST .../approve` and `POST .../reject`.
- `PATCH /api/v1/tickets/{id}`: the real routes are `POST /tickets/{id}/assign` and `POST /tickets/{id}/status`.
- There is no API to read **another employee's** leave balance or leave history.

## 4. Users / Roles

Seed (`scripts/seed.py`):

| User | Email | Role | Dept |
|---|---|---|---|
| Admin User (id 1) | admin@mock-hrms.dev | ADMIN | HR |
| Manager User (id 2) | manager@mock-hrms.dev | MANAGER | Finance |
| Employee User (id 3) | employee@mock-hrms.dev | EMPLOYEE | Engineering, `manager_id=2` |
| 1,000 Faker employees | random | EMPLOYEE | cycle of Engineering/Finance/Marketing/Freelancer, **all `manager_id=2`** |

The password is `password123` for everyone. Because every seeded employee reports to Manager User, "manager's team" means all 1,001 non-admin employees in demo data. Team-scoping logic must still be implemented correctly. Tests must create their own non-team employees to prove it.

## 5. HR Entities (tables)

`employees, departments, job_history, employee_documents, attendance_logs, leave_balances, leave_requests, holidays, announcements, polls, poll_responses, tickets, onboarding_tasks, payroll_records, hr_policies, skills, employee_skills, projects, employee_projects`

Sensitive columns found:

| Table | Column(s) | Sensitivity |
|---|---|---|
| employees | `hashed_password` | secret |
| employees | `bank_name`, `bank_account_number`, `bank_account_name`, `bank_branch`, `bank_ifsc` | financial PII |
| employees | `pan_number`, `pan_name`, `pan_dob`, `date_of_birth` | government ID / PII |
| employees | `current_salary_usd` | compensation |
| employees | `profile_photo_path`, `profile_photo_mime` | internal paths |
| employees | `phone`, `address`, `blood_type` | personal PII (not in assignment list; see Permissions Matrix) |
| payroll_records | `pan`, `pf_uan`, `esi_no`, `gross`, `net`, `deductions` | ⚠ **`pan` duplicated here**, missing from the assignment's forbidden list |
| employee_documents | `file_path` | internal paths |
| hr_policies | `file_path`, `checksum`, `uploaded_by` | internal metadata |

## 6. Existing APIs

28 routes under `/api/v1` across 13 routers (see §3 table for AI-relevant ones). Health: `/health` and `/api/v1/health` (DB `SELECT 1`).

## 7. AI Integration (existing)

- `api/v1/endpoints/chat.py`: `POST /chat/sessions` and `POST /chat/sessions/{id}/messages` are authenticated **501 stubs** returning `CHAT_NOT_IMPLEMENTED`.
- `schemas/chat.py`: `ChatSessionCreate`, `ChatMessageCreate`.
- `docs/api/chat_*.json`: JSON-schema contracts for the stubs.
- `hr_policies.embedding` is a `JSON` column that is always `NULL`. It holds one vector per policy, so it is **not chunk-level**.
- **No** LLM SDK, embedding library, vector store, LangChain/LangGraph or prompt code exists.
- **No** AI env vars in `Settings` or `.env.example`.

## 8. Policy Storage

- `hr_policies` rows point to files via `file_path`. `content` is `NULL` for seeded and uploaded rows.
- Seed writes 22 files `seed_policy_NN.md` into `POLICY_UPLOAD_DIR` (`/app/storage/hr-policies`). **Each is a single sentence.** For example, the Leave Policy is "Employees can avail casual, sick, and earned leave as per allocated balances."
- The local `backend/storage/hr-policies/` has only 2 uploaded files: a `.txt` privacy policy and one PDF. The seeded `.md` files and `hrms.db` are **not present locally**, so the DB has not been seeded in this checkout.
- Richer sample content is in the repo root: `leave_policy_sample.pdf` (CL 12, SL 10, EL 15, 2-day notice, sick-leave informed before 10 AM), `policy_sample.pdf`, `privacy_policy_sample.txt`.
- Business facts that exist **only in code**: default leave totals in `leaves.py::DEFAULT_LEAVE_TOTALS`, half-day rules (FIRST_HALF/SECOND_HALF, same-day only), and the 9:30 AM clock-in cutoff in the attendance endpoint.
- **Consequence:** with the current corpus, the RAG assistant **cannot correctly answer** 3 of the 5 mandatory questions: sick-leave count, half-day leave, and what happens if late. It would correctly refuse. A policy content enrichment step is required (see Plan, Phase 3).
- `pypdf` is already a dependency, so PDF text extraction is available.

## 9. Frontend

- The App Router has one `page.tsx` per feature: `dashboard, employees(+new, payslips, projects), me(+documents), finance, attendance, leaves, announcements, hr-policies, polls, team-calendar, tickets, organization, login`.
- Page shell: `<main className="flex min-h-screen"><Sidebar/><section><Topbar name title/>…`.
- Nav: `components/layout/sidebar.tsx` has a static `navItems` array.
- The API client in `lib/api.ts` has one `fetchX(token, ...)` per endpoint and returns `ApiResult<ApiEnvelope<T> | {detail}>`. The token is read from `localStorage` in each page.
- Route protection in `middleware.ts` uses an explicit route list plus a `matcher`, so **new routes must be added to both**.
- The `EmployeeDetails` type already includes bank/PAN fields. The frontend currently **displays** another employee's sensitive data from `GET /employees/{id}`.
- There is no frontend test tooling (no jest/vitest/playwright). Only `next lint` is available, and no ESLint config was found.

## 10. Testing

- **There are zero automated tests** in backend or frontend: no `tests/`, no pytest, no conftest, no CI.
- The "existing tests must keep passing" requirement is vacuous today. To protect existing behaviour, we must **first add characterization tests** for the endpoints AI tools will wrap.

## 11. Environment & Deployment

- `backend/.env.example` covers app, DB, JWT and upload dirs. Real `.env` files are absent and git-ignored.
- Docker: `python:3.11-slim`, `uvicorn --reload`. The frontend runs `next dev` in the container. There is no production build config.
- The policy upload dir defaults to an absolute container path (`/app/storage/...`), so running outside Docker requires env overrides.

---

## 12. Risks

| ID | Severity | Risk | Impact on AI work |
|---|---|---|---|
| R1 | **High** (pre-existing) | `GET /employees/{id}` returns salary/bank/PAN/DOB/PF/ESI to **any** authenticated user | AI must **never** wrap this endpoint. The fix is out of AI scope and needs owner approval (it changes existing behaviour; the frontend relies on it). |
| R2 | High | No tests exist | Regressions are undetectable. Add characterization tests before touching anything. |
| R3 | High | No service layer | The AI cannot reuse rules except via HTTP. In-process ASGI calls with SQLite risk `database is locked` if the outer request holds a write transaction. |
| R4 | Medium | Managers are not team-scoped in existing APIs | The AI permission layer must be **stricter** than the app (team only) to satisfy the matrix. It must never be looser. |
| R5 | Medium | Policy corpus is too thin for mandatory eval questions | Add enriched policy docs (seed/upload), not code changes to leave logic. |
| R6 | Medium | `.gitignore` contains `*.md` (only `PRD.md` whitelisted) | All new docs, including README updates, would be excluded from git once the repo is initialised. Needs a whitelist change. |
| R7 | Medium | `JWT_SECRET_KEY=change_me` default | Tokens are forgeable if deployed as-is. Document it; don't silently change it. |
| R8 | Medium | `payroll_records.pan`, `pf_uan`, `esi_no` are not in the assignment's forbidden list | Add them to the SQL forbidden set. |
| R9 | Low | Inactive users keep access until token expiry | The AI context should reject `INACTIVE` users (a stricter check that is safe to add in the AI layer only). |
| R10 | Low | `GET /leaves/balances/me` writes (self-heal/create) | It is a "read" tool with side effects. Acceptable because it is idempotent, but note it in the tool registry. |
| R11 | Low | `GET /hr-policies` exposes `file_path` | RAG sources must return title/category/original_filename only. |
| R12 | Low | 15-min access token, no refresh endpoint | Chat UI must handle 401 by redirecting to login. |
| R13 | Low | `alembic/env.py` model imports omit skill/project/holiday modules | Autogenerate would be wrong. Keep migrations hand-written, as the repo already does. |
| R14 | Info | No LLM provider/API key configured | The provider choice is an open decision (see implementation plan). |

## 13. Recommended Integration Points

| Need | Integration point | Rationale |
|---|---|---|
| Auth for AI endpoints | `services/auth.get_current_user` + `oauth2_scheme` (raw token for tool forwarding) | Reuse; no second auth system |
| AI endpoints | Extend `api/v1/endpoints/chat.py` (keep the `/sessions` stubs untouched) | Already mounted at `/api/v1/chat` |
| AI services | New package `backend/app/services/ai/` | Matches assignment's expected paths |
| AI config | Extend `core/config.py::Settings` + `.env.example` | Existing env pattern |
| Response envelope | `core/response.py` helpers | Existing convention |
| Logging | `structlog` with a redaction processor for AI logs | Existing logger |
| Policy chunks + vectors | New table `policy_chunks` (migration `0017`). Keep `hr_policies.embedding` for an optional doc-level vector. | The existing field cannot hold chunk vectors |
| Audit | New table `ai_audit_logs` (migration `0018`) + `models/ai_audit_log.py` | Assignment deliverable |
| Action tools | HTTP calls to existing endpoints with the **user's** bearer token (`httpx.AsyncClient`, ASGI transport in-process, base URL configurable) | Preserves all existing validation/authorization |
| SQL agent DB access | Separate **read-only** SQLite connection (`mode=ro` URI + `PRAGMA query_only` + `sqlite3` authorizer) | Defence in depth independent of the parser |
| Frontend | New `app/ai-copilot/page.tsx`, `components/ai/*`, new functions in `lib/api.ts`, sidebar entry, middleware route | Existing patterns |
| Tests | New `backend/tests/` with pytest + pytest-asyncio + httpx, temp SQLite DB per test session | None exist |

---

## 14. Phase 1 addendum (2026-09-30): runtime inspection & characterization findings

### Runtime
- The Docker image uses Python **3.11**. Locally, Python 3.12 and 3.14 are installed and Docker is **not** available. Pinned deps (pydantic 2.11, SQLAlchemy 2.0.43) install and run on 3.12, so tests run in `backend/.venv` (3.12). `.venv/` is already git-ignored.
- `alembic/env.py` reads `DATABASE_URL` from the environment and `Settings` does too, so the DB location is configurable without code changes. The async engine binds at import of `app.db.session`, so tests set the env **before** importing `app`.
- The full migration chain `0001 → 0016` runs cleanly on a fresh SQLite file.
- App startup (`lifespan`) only configures logging. There is no DB init at startup and no seed at startup.
- `scripts/seed.py` is destructive (`DELETE FROM` every table) and uses the app `SessionLocal`. Tests do **not** use it; they use `tests/support/data.py`.

### Characterization findings (all pinned by tests in `backend/tests/characterization/`)

| Finding | Behaviour observed | Test | AI implication |
|---|---|---|---|
| Role source | Role is loaded from the DB. A validly-signed token claiming ADMIN for an EMPLOYEE gets 403. | `test_auth.py::test_role_is_loaded_from_database_not_token_claim` | Good. The AI context reuses it. |
| **R9 confirmed** | INACTIVE employees can log in and call APIs (e.g. create leave). | `test_inactive_user_not_rejected_by_existing_auth`, `test_inactive_user_can_still_create_leave` | The AI context must reject INACTIVE (Phase 2). |
| **R4 confirmed** | MANAGER sees all pending leaves, approves a non-team leave, and assigns a non-team employee to a project. | `test_manager_sees_all_pending_leaves_not_only_team`, `test_manager_can_approve_leave_outside_team`, `test_manager_can_assign_employee_outside_team` | D7: the AI adds team pre-checks. |
| **R1 confirmed** | `GET /employees/{id}` returns salary, bank account, IFSC, PAN, DOB and PF UAN of any employee to any user. `hashed_password` is not returned. | `test_employees.py::test_r1_*` | Route blocked for AI (`guards.py`). TODO(R1). |
| Ticket status | EMPLOYEE owner/assignee may update ticket status. An unrelated EMPLOYEE gets 403. | `test_ticket_owner_employee_can_update_status` | The AI matrix is stricter (EMPLOYEE: no). |
| Project catalog | MANAGER may create projects. | `test_manager_and_admin_create_project` | The AI matrix is stricter (ADMIN only). |
| Leave creation | `employee_id` in the payload is ignored, and the token owner is always used. ADMIN leave is auto-approved and reduces the balance immediately. | `test_leave_is_always_created_for_the_token_owner`, `test_admin_leave_is_auto_approved_and_balance_reduced` | Safe to wrap. |
| Deactivation | ADMIN only. Soft (status=INACTIVE). Self-deactivation is blocked. The directory hides INACTIVE users. | `test_admin_deactivates_employee_softly`, `test_deactivation_guards` | Safe to wrap (ADMIN, confirm). |
| Login email validation | `LoginRequest.email` is `EmailStr`. Special-use TLDs such as `.local` are rejected with 422 before credentials are checked. | (fixture data uses `@hrms-test.dev`) | None. Note for test data. |
| **Attendance cutoff (found in Phase 3)** | `attendance.py::CLOCK_IN_CUTOFF` is **09:00**, and clock-in after it is LATE. Worked time under 4.5 h at clock-out is recorded as `HALF_DAY` attendance. The legacy one-line seed policy says "before **9:30 AM**", which conflicts with the code. | (policy content) | The D3 attendance policy follows the code (9:00 AM). The legacy text is no longer seeded for "Attendance Policy". |
| Error shapes | Business errors come back as `{"detail": {"success": false, "error": {...}}}`, and Pydantic validation errors as 422 `{"detail": [...]}`. | `tests/support/http.py::error_code` | The action agent must parse both shapes. |
