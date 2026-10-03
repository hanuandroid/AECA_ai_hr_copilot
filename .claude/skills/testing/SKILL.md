---
name: testing
description: Backend test harness for CB Nest AI work — pytest + pytest-asyncio + httpx ASGI client, temp SQLite with Alembic migrations, role fixtures (admin, manager, team employee, non-team employee, inactive), fake LLM/embeddings, characterization tests for existing endpoints. Use whenever writing or running tests.
---

# Testing

## Purpose
Fast, isolated, deterministic tests that protect existing behaviour and prove every AI security rule.

## When to use
Every phase ("Test" step), and whenever you add a service, endpoint, permission or guardrail.

## Workflow
1. **Layout** (`HRMS-main/backend/tests/`):
   ```text
   conftest.py              temp DB, app override, clients per role
   characterization/        existing endpoints the AI wraps (leaves, tickets, announcements, employee projects, auth)
   ai/unit/                 permissions, guardrails, chunker, redaction, confirmations
   ai/api/                  /chat/* endpoints per role
   security/                negative tests mapped to threat ids (test names include T#)
   ```
2. **DB fixture**: create a temp file DB, set `DATABASE_URL` **before** importing `app`, run `alembic upgrade head` programmatically, and insert a minimal deterministic dataset. Don't use the 1,000-employee seed. The SQL executor test needs a file DB (not `:memory:`) so the read-only connection can open it.
3. **Users**: `admin`, `manager`, `employee` (manager_id = manager), `outsider` (an EMPLOYEE with a different/no manager), `inactive`. Get tokens via `create_access_token` or `POST /auth/login`.
4. **Client**: `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
5. **Providers**: override the DI dependencies with `FakeLLM` (scripted responses keyed by input) and `HashEmbedding`. Assert call counts where behaviour depends on them (e.g. no LLM call on insufficient context).
6. **Characterization first**: before wrapping an endpoint, pin its current behaviour (status codes, envelope, role denials).
7. Run `cd HRMS-main/backend && pytest -q` (in Docker: `docker-compose exec api pytest -q`).

## Constraints
- No network, no real API keys, no `storage/hrms.db`.
- Parametrize permission tests from a single matrix table so docs and tests can't drift.
- Never delete, skip or loosen a failing test without a documented root cause.

## Examples
```python
@pytest.mark.parametrize("role,tool,allowed", MATRIX_TOOL_CASES)
def test_tool_visibility(role, tool, allowed, ctx_for):
    assert (tool in permissions.tools_for(ctx_for(role))) is allowed
```

## Validation checklist
- [ ] Full suite passes locally
- [ ] New code has tests. Each security rule has a negative test.
- [ ] Characterization tests still green (existing behaviour preserved)
- [ ] Test counts recorded in `phase-status.md`

## Common failure modes
- Importing `app.main` before overriding `DATABASE_URL` (the engine binds at import).
- Async fixtures without `pytest-asyncio` mode configured (`asyncio_mode = auto` in `pytest.ini`).
- `database is locked` when the AI request and an in-process tool call both write. Commit or close the outer session first.
- `settings.policy_upload_dir` pointing to `/app/storage`. Override it to a tmp dir.
