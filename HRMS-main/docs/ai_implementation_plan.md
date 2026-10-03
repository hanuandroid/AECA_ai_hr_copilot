# AI Implementation Plan

Companion docs: `repository-audit.md`, `ai_architecture.md`, `ai_permissions_matrix.md`, `ai_eval_plan.md`, `ai_threat_model.md`, `phase-status.md`.

Each phase follows these steps: inspect → plan → implement → test → security review → diff review → docs → `phase-status.md` update. **Each phase needs explicit approval before it starts.**

## Decisions (approved 2026-09-30)

| # | Decision | Outcome |
|---|---|---|
| D1 | LLM provider | **Approved: Anthropic behind the `LLMProvider` interface.** Model ids come from env (`AI_LLM_MODEL`, `AI_LLM_FAST_MODEL`), and the key only from env (`ANTHROPIC_API_KEY`, `SecretStr`). Business logic depends only on the interface. The concrete provider is wired in Phase 3; until then the factory fails closed (`AI_UNAVAILABLE`). |
| D2 | Embedding provider | **Approved: local `fastembed`** behind `EmbeddingProvider`, with the model set by `AI_EMBEDDING_MODEL` and no API key. Tests use `FakeEmbeddingProvider` (hash-based) and never download models. The concrete provider and dependency arrive in Phase 3. |
| D3 | Policy corpus enrichment | **Approved.** Add fuller, clearly-marked **demo** policy markdown seed files. The source-of-truth facts are `leave_policy_sample.pdf` (CL 12, SL 10, EL 15, 2-day notice, sick leave informed before 10 AM), `policy_sample.pdf`, `privacy_policy_sample.txt`, and code (`DEFAULT_LEAVE_TOTALS`, half-day FIRST_HALF/SECOND_HALF same-day rule, the attendance cutoff). Nothing may conflict with code. Scheduled with Phase 3 (RAG). |
| D4 | Audit R1 (`GET /employees/{id}` leaks bank/PAN/salary) | **Approved: no endpoint change now.** It is documented by `tests/characterization/test_employees.py::test_r1_*`, and the AI is blocked from the route by `services/ai/guards.py` (+ tests). AI output is scrubbed with `strip_sensitive_fields`. **TODO(R1):** remediate as a separate approved change (see `ai_threat_model.md`). |
| D5 | `.gitignore` `*.md` rule | **Approved, deferred.** Don't `git init` automatically. When the repo is initialised, keep the broad `*.md` rule (upstream intent: keep scratch markdown out of the repo) and add explicit allow-list entries: `!README.md`, `!docs/**/*.md`, `!CLAUDE.md`, `!.claude/**/*.md`. |
| D6 | Confirmations | **Approved: every AI action that changes persistent business state requires explicit confirmation.** The confirmation is server-validated, bound to the user, tool and exact validated args, single-use and expiring, and it cannot be created or modified by the LLM. Built in Phase 5. |
| D7 | AI authorization strictness | **Approved architecture decision:** *AI authorization ≥ existing application authorization.* The AI may be stricter and must never be more permissive, even where an existing endpoint allows more (e.g. R4: managers can approve any leave). `services/ai/permissions.py` is authoritative for AI capabilities, and the LLM never decides authorization. |

## Phases

| Phase | Goal | Key files (new unless noted) | Tests |
|---|---|---|---|
| **0** | Audit + engineering system | docs/*, CLAUDE.md, .claude/** | — |
| **1** ✅ | AI foundation + test harness | **Done**: `requirements-dev.txt`, `pytest.ini`, `core/config.py` (AI settings), `.env.example`, `services/ai/{errors,providers,guards,redaction,embeddings}.py`, `services/ai/llm/{base,fake}.py`, `tests/` (harness + characterization + AI unit). The Anthropic and fastembed concrete providers move to Phase 3 (no production AI calls in Phase 1). | **132 tests passing** (see `phase-status.md`) |
| **2** ✅ | AI authorization boundary | **Done**: `services/ai/authorization/{capabilities,policy,context,scope,sensitive,service}.py`, `errors.py` (+`AIInactiveUserError`, `ai_error_response`), `guards.py` (uses the central sensitive policy), `chat.py` (+`GET /chat/permissions`), `main.py` (+`AIError` handler only) | **341 new tests (473 total)**, including `test_inactive_user_rejected` |
| **3** ✅ | Policy RAG | **Done.** See `phase-status.md`. The table was built as planned, but the modules live in `services/ai/rag/`, and D3 content is in `backend/data/policies/`. Originally planned: `services/ai/llm/anthropic_provider.py` + `anthropic` dep, `FastEmbedEmbeddingProvider` + `fastembed`/`numpy` deps, `models/policy_chunk.py`, migration `0017`, `services/ai/{vector_store,policy_ingest,policy_rag}.py`, `scripts/ingest_policies.py`, `scripts/policies/*.md` + seed hook (per D3), `POST /chat/policy` | chunker, retrieval threshold, insufficient-context (no LLM call), source correctness, **document prompt injection**, API tests for 3 roles |
| **4** ✅ | SQL Agent | **Done**: `services/ai/sql/` (schema, parser, validator, scope, executor, prompts, generator, formatter, models, service), `POST /chat/sql`, `evals/sql_eval.json` + `evals/sql_agent_eval.py`, 270 tests. See `phase-status.md`. Original plan: | `services/ai/{schema_service,sql_guardrails,sql_executor,sql_agent}.py`, `POST /chat/sql` | guardrail matrix (each blocked keyword, multi-statement, comments, CTE-with-DML, `sqlite_master`, forbidden col via alias, `*`, subquery, UNION, `load_extension`), scope rewrite per role, row limit, error sanitization, executor authorizer denies write even if guardrail bypassed |
| **5** ✅ | HR Action Agent | **Done**: `services/ai/actions/` (models, registry, resolver, validators, confirmation, client, prompts, service), migration `0018_ai_pending_actions`, `POST /chat/actions` + `/chat/actions/confirm`, `evals/action_eval.json` + `evals/action_agent_eval.py`, 123 tests. See `phase-status.md`. Original plan: | `models/ai_pending_action.py`, migration `0019`, `services/ai/{api_tools,action_agent,confirmations}.py`, `POST /chat/actions`, `/chat/actions/confirm` | tool filtering per role, arg validation, team pre-check, confirm single-use/expiry/wrong user, backend 4xx mapping, timeout, **no DB session with write access in agent** (static test) |
| **6** ✅ | LangGraph orchestration | **Done**: `services/ai/orchestration/` (classifier, graph, state, models, prompts, service), `POST /api/v1/chat` (instead of `/chat/router`), `langgraph==1.2.12`, `evals/router_eval.json` + `evals/router_eval.py`, 257 tests, no migration. See `docs/architecture/`. Original plan: `services/ai/{intent,graph}.py`, `POST /chat/router` | routing table, permission-denied path, failure path, state minimality |
| **7** ✅ | Conversation memory & sessions | **Done**: migration `0019_ai_conversations`, `services/ai/conversation/` (service, chat, models), `orchestration/followup.py`, `POST /chat` with `conversation_id`, `GET/DELETE /chat/conversations[/{id}]`, `evals/conversation_eval.json` + `.py`, 173 tests. See `docs/architecture/phase-7-conversation-memory.md`. (Originally "Chat API hardening"; envelope consistency, safe errors and request limits are covered by Phases 6-7.) | ownership/isolation, tampering, history injection, bounded context, ordering/concurrency, follow-up scenarios |
| **8** ✅ | Audit, observability & evaluation | **Done**: migration `0020_ai_audit_events` (append-only), `services/ai/audit/` (events, recorder, store, middleware, provider, query), `GET /api/v1/ai/audit[/metrics]`, `evals/{reporting,run_evals,live_router_eval}.py`, 155 tests. See `docs/architecture/phase-8-audit-observability.md`. Original plan: `models/ai_audit_log.py`, `services/ai/audit.py`, `GET /chat/audit/recent` | event lifecycle, correlation, redaction, access, append-only, failure semantics, evaluation reports |
| **9** ✅ | Frontend (Copilot UX) — **Done**: `/copilot[/<id>]`, `components/copilot/*`, `lib/copilot-{api,errors,display}.ts`, `/admin/ai-audit`, Vitest + Testing Library (75 tests). See `docs/architecture/phase-9-copilot-frontend.md`. Original plan: | `app/ai-copilot/page.tsx`, `components/ai/{chat-panel,source-list,sql-result-table,action-result-card,confirm-dialog,recent-actions}.tsx`, `lib/api.ts` (mod: append), `sidebar.tsx` (mod: one nav item), `middleware.ts` (mod: route) | `next build` + typecheck; manual script for 3 roles (no FE test runner exists — propose vitest only if approved) |
| **10** ✅ (hardening + final evaluation; see `docs/phase-10-final-report.md`) | Evaluation | `evals/{policy,sql,action,security}_eval.json`, `evals/run_evals.py`, `docs/ai_eval_results.md` | runs against fake provider (CI) and real provider (manual) |
| **11** | Security review | `docs/security-test-report.md` | adversarial suite |
| **12** | Observability | structlog spans / optional OpenTelemetry or LangSmith (env-gated) | redaction of traces |
| **13** | Final testing | `docs/test-report.md` | full run |
| **14** | Docs | README (mod), all docs | — |

Migration numbering follows the phase order: `0017_policy_chunks`, `0018_ai_audit_logs`, `0019_ai_pending_actions`. If audit is built earlier, the numbers are renumbered, but the chain always continues from `0016_employee_documents_uploaded_by`.

## Files to change (existing)

| File | Change | Risk |
|---|---|---|
| `backend/requirements.txt` | add AI deps | low |
| `backend/app/core/config.py` | add AI settings with safe defaults | low |
| `backend/.env.example` | add AI vars (empty keys) | low |
| `backend/app/api/v1/endpoints/chat.py` | add new routes; **keep `/sessions` stubs** | low |
| `backend/app/main.py` | *(Phase 2)* one line: `add_exception_handler(AIError, ...)`, so AI errors raised in dependencies render the standard envelope. Existing endpoints are unaffected (tested). | low |
| `backend/app/schemas/chat.py` | add AI request/response models | low |
| `backend/app/models/__init__.py` | import new models | low |
| `backend/scripts/seed.py` | *(D3, done in Phase 3)* uses `data/policies/*.md` for Leave/Attendance/WFH and adds Document Upload + Employee Data Privacy policies; clears `policy_chunks` before `hr_policies` | medium: destructive script already |
| `frontend/lib/api.ts` | append AI client functions and types | low |
| `frontend/components/layout/sidebar.tsx` | add "AI Copilot" nav item | low |
| `frontend/middleware.ts` | protect `/ai-copilot` | low |
| `README.md` | AI section (Phase 14) | low |
| `.gitignore` | *(per D5)* whitelist docs | low |

## Files NOT to change

- `backend/app/services/auth.py`, `backend/app/core/security.py`: authentication stays as is. The AI layer adds stricter checks on top.
- All business endpoints (`leaves.py`, `tickets.py`, `announcements.py`, `employees.py`, `finance.py`, `hr_policies.py`, …). The AI consumes them, and the AI work does not modify them (D4 excepted, only if approved).
- Existing models and migrations `0001`–`0016`.
- Existing frontend pages and components apart from sidebar/middleware/api.ts.
- `docker-compose.yml`, except adding env passthrough if needed.
