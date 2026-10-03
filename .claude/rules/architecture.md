# Architecture Rules

Do not introduce architectural changes without first explaining **what, why, files affected, risk, and tests required**, and getting approval.

## Existing architecture (source of truth: `HRMS-main/docs/repository-audit.md`)

- `api/v1/endpoints/*.py`: route handlers that currently contain validation, authorization and persistence. There is no service/repository layer for business modules. **Do not refactor them** as part of AI work.
- `services/auth.py`: `get_current_user`, `require_roles`. The only shared service.
- `core/`: config (pydantic-settings), security (JWT/bcrypt), response envelope, structlog.
- `models/`: SQLAlchemy ORM. `db/session.py`: async engine + `get_db`.
- Alembic: hand-written, linear revisions. New revisions continue the chain from the current head. Never edit old revisions.

## Dependency direction

```text
endpoints/chat.py → services/ai/* → (llm | embeddings | vector_store | sql_executor | api_tools | repositories for AI-owned tables)
services/ai/*     → core/*, models/* (read), services/auth (context only)
services/ai/*     ✗→ endpoints/* (never import business endpoint functions)
business endpoints ✗→ services/ai/* (existing app must not depend on AI)
```

## AI service boundaries

- `services/ai/authorization/` is deterministic, with no LLM. Its only I/O is loading the caller's direct reports when building `AIUserContext`. Every other AI module calls `authorize` / `require` / `run_authorized` with a `Capability`. Never check `ctx.role` directly.
- `llm/` is the only place with provider SDK imports. `embeddings.py` is the only place with embedding SDK imports.
- `sql_executor.py` is the only module that executes agent-generated SQL. It uses its own read-only connection.
- `api_tools.py` is the only module that calls business endpoints. It uses HTTP with the user's token.
- AI-owned tables (`policy_chunks`, `ai_audit_logs`, `ai_pending_actions`) are written only by their repository functions.

## API conventions

- All AI routes are under `/api/v1/chat/*`, authenticated with `Depends(get_ai_user_context)`, which wraps `get_current_user` and rejects INACTIVE users.
- They return the standard envelope via `JSONResponse` with a correct HTTP status. Error codes are prefixed `AI_`.
- Request/response bodies use typed Pydantic models in `schemas/chat.py` or `services/ai/schemas.py`.

## Frontend / backend responsibilities

- The backend is authoritative for every permission decision.
- Frontend role awareness (hiding buttons, role cookie) is UX only.
- Follow the existing page shell (`Sidebar` + `Topbar`), the `lib/api.ts` function style and Tailwind classes.
