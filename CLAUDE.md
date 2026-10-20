# CLAUDE.md — NovaWorks PeopleOps Copilot on CB Nest

This workspace adds AI features (Policy RAG, SQL Agent, HR Action Agent, AI RBAC) to the existing **CB Nest** HRMS in `HRMS-main/`.

- Assignment: `assignment_05_ai_hr_copilot.md`
- Execution plan: `Claude Code Master Prompt — AI HR Operations Copilot.md`
- Current phase and status: `HRMS-main/docs/phase-status.md`. **Read it first.** Work one phase at a time, and stop for approval between phases.

## Current Project Status

Assignment Phases 0–10 are complete. A separate **production-hardening programme** is in progress (records in
`HRMS-main/docs/production-hardening/`; resume from `HRMS-main/docs/development-handoff.md`). Release status:
**NOT YET PRODUCTION READY**.

- **Done (`origin/main` was at `86001f5` until 2026-10-20; since then `main` up to `2225fe7` is on origin, checked with `git ls-remote`):** hardening Phases 0–2, the approved
  Phase 2 closure, the approved Phase 3 *offline* workstreams 3.3 / 3.6 / 3.8 / 3.9 (all on origin); locally, the
  OpenAI provider (`e3ec3d6`, `90e6582`, `0ca8326`) and the complete RBAC remediation plan, Steps 1–10
  (`HRMS-main/docs/production-hardening/rbac-remediation-plan.md`; Step 9 = `345ca96`). S-02, S-03 and S-05 fixed.
  Route and page coverage: `HRMS-main/docs/production-hardening/page-endpoint-inventory.md`.
- **Also done locally (2026-10-18/19):** the AI security remediation (AIS-01..20;
  `HRMS-main/docs/production-hardening/ai-security-remediation-progress.md`) and G-11 password step-up on high-impact
  business APIs (`d570ab6`, `STEP_UP_ENFORCEMENT=log`; never switch to `enforce` without approval) with the admin UI
  dialog (`29ca184`). Capstone completion programme in progress:
  `HRMS-main/docs/production-hardening/capstone-completion-progress.md` (current status and evidence).
- **Also done locally (2026-10-20):** Playwright role-access and business-workflow E2E suite (`HRMS-main/frontend/e2e/`,
  isolated `HRMS-main/docker-compose.e2e.yml` stack, fake AI providers, CI `e2e` job), session revocation on
  deactivation/reactivation, employee phone-number fix, test-only scripted fake model (`AI_FAKE_LLM_SCRIPT`, refused in
  production). Reports: `HRMS-main/docs/final-security-readiness.md`, `role-access-test-report.md`,
  `deep-workflow-test-report.md`.
- **Latest fresh results (2026-10-20, local):** backend 3190 passed (1 opt-in live test skipped) (3208 passed, 1 skipped after the live-harness tests were added; `verify-backend.sh`, 2026-10-21), offline evaluations 139/139 (scripted model output), CI gate tests 25 passed, frontend Vitest 268 passed, TypeScript 0 errors, build PASS, Playwright E2E 152 passed (0 failed, 0 skipped, 0 flaky), gitleaks clean (`docs/capstone-submission-report.md`). Manual browser walkthrough: NOT_RUN. GitHub Actions on `2225fe7` (2026-10-20, run 39471328944): backend, frontend, e2e, docker and secret-scan jobs passed; the dependency-advisory gate failed (expected: the build-time `braces` advisory, reproduced locally; the run's step log was not read); see `HRMS-main/docs/production-hardening/ci-and-validation.md` §3. Earlier runs (2026-10-19 at `165e96e`: 3172 backend, 238 frontend; 2026-10-17: 2778 backend, 152 frontend; Docker
  production smoke PASS) are historical. Live-model evaluation (2026-10-20, OpenAI `gpt-4o-mini`, one approved run): router 33/33, policy 11/11
  (`HRMS-main/docs/ai_eval_results.md` §4.2); live SQL/action quality and injection resistance not measured. Only SQLite is verified; downgrade below migration 0016 fails (S-07).
- **Open decisions:** release gates in `release-readiness.md` §6. S-06 and S-08 fixed, S-04 closed, S-07 mitigated,
  B-17 revocation and global sign-out done (tokens still in `localStorage`; strict script CSP pending) 2026-10-17 (forward-only migrations; the API refuses to start unless the DB is at the code's Alembic head).
- **Blocked on approval:** live-model work (3.1/3.2/3.4/3.5), further pushes, G-11 `enforce`, the `braces` advisory exception (stays PROPOSED; CI gate fails by design), data-governance, retention and
  backup decisions. Never push, deploy or approve exceptions without approval.
- **Do not start a new phase automatically.** First inspect the current implementation and run the requested
  validation.
- Conversation history cannot authorize anything. The remaining architectural rules are below and in
  `.claude/rules/`.

## Repository facts (verified in Phase 0 — see `HRMS-main/docs/repository-audit.md`)

- Backend: FastAPI + SQLAlchemy 2 async + SQLite (`aiosqlite`) + Alembic (hand-written migrations, latest `0024_employee_token_version` (B-17 token revocation); 0017-0020 are AI-owned, 0021 is the business policy-publication workflow, 0022 the announcement/poll audience (RBAC step 6), 0023 the HR policy audience plus the cited-policy ids of conversation replies (RBAC step 7). Downgrading 0022/0023 makes restricted content visible to everyone under the older code). The backend lives in `HRMS-main/backend/app/`.
- **There is no service layer.** Business rules live in endpoint functions under `api/v1/endpoints/`. AI mutations therefore go through **HTTP calls to those endpoints** with the user's token, never through direct DB writes or imported endpoint functions.
- Auth: `app.services.auth.get_current_user` (JWT HS256, role loaded from DB) and `require_roles(...)`. Reuse them and do not create a second auth system.
- Response envelope: `app.core.response.success_response / error_response` → `{success, data, error:{code,message}}`.
- Frontend: Next.js 15 App Router in `HRMS-main/frontend/`. The API client is `lib/api.ts`, the token is in `localStorage["hrms_access_token"]`, and protected routes are listed in `middleware.ts` (both the list and `matcher`).
- Tests: `backend/tests/` (Phase 1). Characterization tests pin existing endpoint behaviour. R1, R4 and R9 were remediated in hardening Phase 1 (each changed test documents the old behaviour); open findings are pinned the same way (e.g. `test_admin_self_actions.py`, S-02). Don't "fix" those tests; they change only with an approved remediation.
- Decision D7: AI authorization ≥ existing authorization. The AI may be stricter, never looser.
- AI authorization boundary (Phase 2): `backend/app/services/ai/authorization/`. AI endpoints depend on `get_ai_user_context` (rejects INACTIVE). Every AI operation runs through `run_authorized(ctx, Capability.X, op, resource=TargetEmployee(id))` or `require(...)`. The matrix lives only in `authorization/policy.py`. Row scope comes from `sql_read_scope(ctx)` / `hr_read_scope(ctx)`, and field checks from `is_sensitive_field` / `is_column_readable`.
- Policy RAG (Phase 3): `backend/app/services/ai/rag/`, endpoint `POST /api/v1/chat/policy`. Demo policy content is in `backend/data/policies/` and must stay consistent with code (leave totals, 09:00 cutoff, upload limits, policy publication). Only PUBLISHED policies are indexed or retrieved; managers submit, ADMINs publish. Each policy has an audience (`ALL` / `MANAGERS` / `ADMINS`, RBAC step 7): retrieval must pass `policy_audiences(ctx)` and filters before scoring; list, download and conversation replay apply the same rule. **Policy documents are untrusted data and cannot provide instructions to the model.** Never put document text into the system prompt.
- LLM/embeddings: `AnthropicLLMProvider` (model from `AI_LLM_MODEL`, key only from `ANTHROPIC_API_KEY`) and `FastEmbedEmbeddingProvider`. Both are lazy and fail closed. Tests always use `FakeLLMProvider` / `FakeEmbeddingProvider` / `evals.policy_rag_eval.ExtractiveFakeLLM`.
- SQL Agent (Phase 4): `backend/app/services/ai/sql/`, endpoint `POST /api/v1/chat/sql`. **The LLM is never an authorization authority, and SQL validation is enforced independently of the LLM prompt.**
  - Pipeline: parse (sqlglot, sqlite) → validate (registry in `sql/schema.py`) → scope rewrite + LIMIT → re-verify → read-only executor.
  - To expose a new table or column, classify it in `sql/schema.py` and update `tests/ai/sql/test_schema_registry.py::EXPECTED_FORBIDDEN`. Never allow a sensitive column.
  - Never execute model SQL text directly.
- Action Agent (Phase 5): `backend/app/services/ai/actions/`, endpoints `POST /api/v1/chat/actions` and `/chat/actions/confirm`. **The LLM proposes actions; the backend authorizes and executes them. All data-changing AI actions require explicit confirmation. The Action Agent can call only registered HRMS APIs.**
  - To add an action:
    1. Add a Phase 2 capability, then an `ACTION_REGISTRY` entry with the existing route, a typed params model and a `HRMSActionClient` method.
    2. Update `tests/ai/actions/test_action_registry.py::EXPECTED_ROUTES` and `ai_permissions_matrix.md` §3.0.
  - Never write business tables from AI code. Never mint tokens or use a service identity. Never retry mutations.
- Copilot router (Phase 6): `backend/app/services/ai/orchestration/` (LangGraph), endpoint `POST /api/v1/chat`. Design: `HRMS-main/docs/architecture/copilot-router.md`. **The router is a dispatcher, not an authorization layer or an autonomous agent.**
  - Nodes call only `PolicyRagService.answer`, `SQLAgentService.answer` and `ActionAgentService.propose`, with the trusted ctx from `config["configurable"]["deps"]`. Never put identity in graph state, and never call `confirm` from the graph.
  - To add a node or route: update `ALLOWED_TRANSITIONS`, `NODE_WRITES` and `tests/ai/orchestration/test_router_graph.py::EXPECTED_NODES`. Edges must stay forward-only and within `MAX_GRAPH_ITERATIONS`.
  - Refusal and intent rules live in `orchestration/classifier.py`. The LLM is only consulted when the rules have no signal, and it fails closed.
- Conversations (Phase 7): `backend/app/services/ai/conversation/` + `orchestration/followup.py`; tables `ai_conversations` / `ai_conversation_messages` (migration 0019). Design: `HRMS-main/docs/architecture/phase-7-conversation-memory.md`. **Conversation history is context, never authorization and never truth.**
  - Every conversation lookup must be scoped by `ctx.employee_id` (`ConversationService.get_user_conversation`); unknown/foreign/malformed/archived ids give the same 404.
  - Only USER text may be forwarded to services (anchor question + current message); never forward assistant text, ids from history, or roles. Refusal rules run on the current message only.
  - Never store prompts, SQL, rows, confirmation tokens or roles in conversation messages; store redacted text only.
- Audit & observability (Phase 8): `backend/app/services/ai/audit/`, table `ai_audit_events` (migration 0020, append-only via triggers), `GET /api/v1/ai/audit[/metrics]`. Design: `HRMS-main/docs/architecture/phase-8-audit-observability.md`. **Audit observes; it never authorizes or changes an outcome** (only a confirmed action is fail-closed on audit failure).
  - Record with `audit.recorder.record(AuditEventType.X, AuditStatus.Y, **typed metadata)`; never pass prompts, answers, SQL, rows, tokens or names. To add a field, add it to `AuditMetadata` (identifier tokens only).
  - Wrap new LLM call sites with `audit.provider.observe(llm, "operation")`. One request = one `request_id` (`recorder.current_request_id()`).
  - Evaluations: `.venv\Scripts\python -m evals.run_evals` (offline, throwaway DB; `--live` needs the selected provider's key and model in the process environment (anthropic or openai), else NOT_RUN; live runs need approval).
- Copilot frontend (Phase 9): `frontend/app/copilot/[[...conversation]]`, `frontend/components/copilot/`, `frontend/lib/copilot-{api,errors,display}.ts`, `/admin/ai-audit`. Design: `HRMS-main/docs/architecture/phase-9-copilot-frontend.md`. **The frontend is never an authorization boundary.**
  - Call AI endpoints only through `lib/copilot-api.ts`; never add identity/role/permission fields to request bodies; confirm only with the backend `confirmation_id`, once, never retried.
  - Render AI output as text only (no `dangerouslySetInnerHTML`/Markdown HTML); map errors via `lib/copilot-errors.ts` using real backend codes.
  - Frontend tests: `cd HRMS-main/frontend; npm test` (Vitest + Testing Library, threads pool).
- Phase 10: formal assignment eval `backend/evals/assignment_eval.json` (6 suites, `python -m evals.run_evals`); adversarial matrix `backend/tests/ai/hardening/`; `app/core/production_checks.py` (fail fast on unsafe production config); role modes from `GET /chat/permissions` (UX only); `/admin/ai-usage`. Final status: `HRMS-main/docs/phase-10-final-report.md`. Streaming is deliberately not implemented (FastAPI 0.116 dependency lifetime; see the gap analysis).
- ⚠ `GET /api/v1/employees/{id}` historically returned bank/PAN/salary to any user (R1; field visibility fixed in hardening Phase 1, B-01). **AI code must still never call it.**

## Commands

```bash
# from HRMS-main/
docker-compose build
docker-compose run --rm api alembic -c alembic.ini upgrade head   # the API refuses to start until the DB is at head
docker-compose up -d
docker-compose exec api python scripts/seed.py
cd frontend && npm run build                 # type-check frontend
```

Tests (local, no Docker needed; Python 3.11 venv, matching the Docker images and CI; a throwaway `python:3.11` container also works, see `HRMS-main/docs/development-handoff.md` §6):

```powershell
cd HRMS-main/backend
py -3.11 -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements-dev.txt   # once
.\.venv\Scripts\python -m pytest -q
```

Policy RAG index (after migrate + seed, or after policy changes; idempotent):

```bash
docker-compose exec api python scripts/ingest_policies.py
```

Without a local Python 3.11: `sh HRMS-main/scripts/verify-backend.sh` (throwaway container; no `.env`, keys blanked). All validation commands and the CI status: `HRMS-main/docs/production-hardening/ci-and-validation.md`.

The harness (`tests/conftest.py`) builds a temp DB via Alembic and never touches `storage/hrms.db`. It restores both the DB and the policy files before each test. Fixtures: `client`, `auth("admin"|"manager"|"employee"|"outsider"|"inactive")`, `db`.

## Non-negotiables (details in `.claude/rules/`)

1. The LLM never decides authorization. `services/ai/authorization/` (deterministic) does.
2. AI never writes business tables. The SQL agent is read-only SELECT on a read-only connection, and actions go through the existing API with the user's token.
3. Forbidden fields never leave the DB via AI: passwords, bank, PAN, DOB, salary, photo paths, payroll PAN/UAN/ESI.
4. Retrieved documents and LLM output are untrusted data.
5. Never log or return JWTs, secrets, raw DB errors, stack traces or prompts.
6. Every new service has tests, and every security rule has a negative test. Never weaken a test to make it pass.
7. Don't modify existing business endpoints, auth or migrations `0001–0016` without explicit approval.

## Where things go

- AI services: `backend/app/services/ai/`
- AI endpoints: `backend/app/api/v1/endpoints/chat.py`
- AI docs: `HRMS-main/docs/ai_*.md`
- Evals: `backend/evals/`

Use the skills in `.claude/skills/` for workflows and the agents in `.claude/agents/` for specialised review.
