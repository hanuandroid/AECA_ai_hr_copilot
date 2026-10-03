# Phase 10: Requirement and Bonus Gap Analysis

The requirements come from `assignment_05_ai_hr_copilot.md`; the source of truth is the current repository.
Statuses use only COMPLETE, PARTIAL, GAP and NOT IMPLEMENTED. A bonus is not counted as complete because
something similar exists.

## 1. Core requirements

| Assignment requirement | Current implementation | Status | Evidence | Phase 10 action |
|---|---|---|---|---|
| Policy RAG: ingestion, chunking, embeddings, vector search, grounded answers, sources, insufficient context | `services/ai/rag/` (Phase 3) | COMPLETE | `tests/ai/rag` (142); policy eval 11/11; assignment eval policy 5/5 | None |
| SQL agent: schema-aware generation, validation, read-only execution, sensitive-field blocking, role filtering, safe errors | `services/ai/sql/` (Phase 4) | COMPLETE | `tests/ai/sql` (270); SQL eval 20/20; assignment eval SQL 5/5 | Added the assignment's four missing suggested SQL prompts to the formal set |
| HR action agent: intent extraction, tool schemas, API wrappers, permission checks, error handling, summaries | `services/ai/actions/` (Phase 5) | COMPLETE | `tests/ai/actions` (123); action eval 20/20 | None |
| RBAC (Employee / Manager / Admin) | `services/ai/authorization/` (Phase 2); every service re-authorizes | COMPLETE | `tests/ai/authorization` (341); adversarial matrix | None |
| Chat API: `/chat/policy`, `/chat/sql`, `/chat/actions` (+ optional router) | `endpoints/chat.py`; unified `POST /chat` instead of `/chat/router` | COMPLETE | API tests in every AI suite | None |
| Frontend AI interface | `/copilot`, `components/copilot/*` (Phase 9) | COMPLETE | 86 frontend tests; `next build` PASS | Role modes and usage dashboard (bonuses) |
| AI audit logging: user ID, role, message, intent, tool/API, action status, timestamp | `services/ai/audit/`, `ai_audit_events` (Phase 8) | COMPLETE (role added in Phase 10) | `tests/ai/audit` | **Gap fixed:** the actor role is now recorded (`actor_role`, from the DB user). The message is kept as a keyed fingerprint in audit (privacy) and as redacted text in the owner-only conversation store, linked by `request_id`. |
| Documentation: `ai_architecture.md`, `ai_permissions_matrix.md`, `ai_eval_results.md`, README | `ai_architecture.md`, `ai_permissions_matrix.md` | GAP → COMPLETE | — | **Gap fixed:** created `docs/ai_eval_results.md`; README updated (it still described the AI endpoints as 501 stubs) |
| Minimum passing / automatic failure conditions | — | COMPLETE | See `docs/phase-10-final-report.md` §3 | Verified by the adversarial matrix and the formal set |

**Expected file names.** The assignment's suggested file names differ from the actual modules (the
assignment says "expected files may include"). The mapping is:

| Suggested | Actual |
|---|---|
| `policy_rag.py`, `embeddings.py`, `vector_store.py` | `rag/service.py`, `embeddings.py`, `rag/vector_store.py` |
| `sql_agent.py`, `sql_guardrails.py` | `sql/service.py`, `sql/validator.py` + `sql/scope.py` + `sql/executor.py` |
| `action_agent.py`, `api_tools.py`, `permissions.py` | `actions/service.py`, `actions/registry.py` + `actions/client.py`, `authorization/` |
| `models/ai_audit_log.py`, `services/ai/audit.py`, `<migration>_add_ai_audit_logs.py` | `models/ai_audit_event.py`, `services/ai/audit/`, `0020_ai_audit_events.py` |
| `app/ai-copilot/page.tsx`, `components/ai/*` | `app/copilot/[[...conversation]]/page.tsx`, `components/copilot/*` |

## 2. Bonus requirements

| Bonus | Current implementation | Status | Evidence | Phase 10 action |
|---|---|---|---|---|
| 1. LangGraph orchestration | `orchestration/graph.py` (Phase 6): classify → policy, sql, action, clarification, refusal or error → finalize | COMPLETE | `tests/ai/orchestration` (259); router eval 33/33 | None. The suggested "Load User Context / Permission Check / Audit Log" steps exist as the auth dependency, per-service authorization and the audit middleware, not as graph nodes (documented). |
| 2. Human-in-the-loop confirmation | Phase 5 single-use, bound, expiring confirmation; Phase 9 confirmation card | COMPLETE | Actions tests; frontend confirmation tests; adversarial matrix | None |
| 3. Streaming chat responses | None | NOT IMPLEMENTED | Probe: FastAPI 0.116 closes `yield` dependencies (the request DB session) before a `StreamingResponse` body runs | **Decided not to implement** (see §3) |
| 4. LangSmith / OpenTelemetry tracing | Phase 8 internal tracing: request and conversation ids, latency, provider latency, tokens, errors, permission failures, tool and action lifecycle | PARTIAL | `tests/ai/audit` | No new stack (Phase 10 rule). Not LangSmith/OTel, and prompt inputs and model outputs are intentionally not captured (privacy). |
| 5. Formal evaluation dataset | Per-capability sets (policy, sql, router, conversation, actions) plus the runner | PARTIAL → COMPLETE | `evals/*` | **Added** `evals/assignment_eval.json` + `.py`: every assignment-suggested prompt plus Step 3/4 cases (30 cases) in the assignment format (input, role, expected route and behaviour, security expectation), wired into `run_evals` |
| 6. Prompt-injection defence | Document-as-data RAG prompt and guard (Phase 3), router refusal rules (Phase 6), history as data (Phase 7), schema-validated model output (Phases 4–6) | COMPLETE | `tests/ai/rag/test_injection_and_sensitive.py` and others | **Added** end-to-end tests: a poisoned policy document, echoed injection, history injection, SQL/action/model-output injection (adversarial matrix) |
| 7. Role-specific Copilot modes | Phase 9: example prompts by profile role | PARTIAL → COMPLETE | — | **Added** Employee, Manager and Admin Copilot modes (title, focus areas, prompts) driven by the backend's `GET /chat/permissions` capabilities. Prompts appear only for granted capabilities; UX only. |
| 8. AI usage dashboard | Phase 8 metrics tiles on `/admin/ai-audit` | PARTIAL → COMPLETE | — | **Added** `/admin/ai-usage`. The metrics endpoint gains intents, tools, actions by type, requests by role, RAG no-answer rate and action confirmation/execution rates, derived from existing audit events (no new store); admin-only by the backend. |

## 3. Decisions

- **Streaming: not implemented (future enhancement).** In FastAPI 0.116 the exit code of `yield` dependencies
  runs before a `StreamingResponse` body is iterated. That was verified with a probe: "close" was logged before
  the first chunk. The Copilot's conversation, RAG and action services share the request's
  `Depends(get_db)` session, so a streamed turn would run on a closed session. Doing this safely needs either a
  FastAPI upgrade (newer releases change this behaviour; a dependency upgrade is a Phase 10 stop condition) or
  re-plumbing every service to own its session inside the stream. That is a substantial architectural change to
  the chat path, so per the Phase 10 rules it is documented rather than built. A safe design for later:
  - status events (`classify` → `policy`/`sql`/`action proposal`) via a progress callback in `GraphDeps`;
  - a final event carrying the same response as `/chat`;
  - an action event that only ever says "confirmation required".
- **Tracing: no OpenTelemetry/LangSmith added.** Phase 8 already covers every tracked dimension except prompt
  inputs and model outputs, which are deliberately excluded for privacy. Adding an external tracing stack would
  duplicate the audit pipeline and create a new place where sensitive prompts could leak.

## 4. Other Phase 10 findings fixed

- **Router accuracy (found by the formal set):**
  - "How many sick leaves do I get?" was routed to SQL. Entitlement wording is now a policy question.
  - "Who is assigned to HR Policy Copilot?" was routed to POLICY because the project name contains "Policy".
    Record lookups starting with who/which are now checked before policy wording.

  Both are narrow deterministic rules, and the Phase 6/7 suites stay green.
- **Production configuration:** `ENVIRONMENT=production` now refuses to start with the default
  `JWT_SECRET_KEY`, a secret shorter than 32 characters, fake AI providers, or a missing Anthropic key
  (`app/core/production_checks.py`). Development and tests are unchanged.
