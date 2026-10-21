# Phase 10: Final Report — NovaWorks PeopleOps Copilot on CB Nest

> **Historical record (2026-10-03).** This report closed assignment Phase 10. Its "Ready for final submission" status
> and test counts predate the production-hardening and capstone work; it is kept unchanged as a dated record. Current
> status and evidence: [capstone-submission-report.md](capstone-submission-report.md) and
> [final-security-readiness.md](final-security-readiness.md). Release status: **NOT YET PRODUCTION READY**.

Date: 2026-10-03. Details: [ai_architecture.md](ai_architecture.md), [ai_permissions_matrix.md](ai_permissions_matrix.md),
[ai_eval_results.md](ai_eval_results.md), [ai_threat_model.md](ai_threat_model.md),
[architecture/phase-10-gap-analysis.md](architecture/phase-10-gap-analysis.md).

Handoff status (2026-10-03): **Phase 10 COMPLETE. Ready for final submission.**
- Verified offline: backend 1685 passed, frontend 86 passed, TypeScript 0 errors, `next build` PASS (23 routes),
  evaluations 139/139.
- Pending on the next machine: Docker validation (`docker-compose up`, migrate, seed, `ingest_policies.py`,
  fastembed in the container).
- Pending an API key: live-model evaluation (`python -m evals.run_evals --live`).
- Separate decision: the npm security advisory for the pinned `next@15.5.2` (not upgraded).

## 1. Core requirements

| Requirement | Status | Evidence |
|---|---|---|
| Policy RAG | COMPLETE | `services/ai/rag/`; 142 tests; policy eval 11/11; the five assignment policy prompts answered with sources (formal set) |
| SQL agent | COMPLETE | `services/ai/sql/` (sqlglot validation, scope rewrite, read-only executor); 270 tests; SQL eval 20/20; the five assignment SQL prompts in the formal set; 15 destructive/sensitive SQL attacks blocked (adversarial matrix) |
| HR action agent | COMPLETE | `services/ai/actions/` (registry of existing endpoints, user's token, confirmation); 123 tests; action eval 20/20; the five assignment action prompts proposed with confirmation required |
| RBAC | COMPLETE | `services/ai/authorization/` (capability matrix, DB-loaded role, INACTIVE rejected); 341 tests; adversarial matrix (forged JWT role claim, forged fields, headers, query, ownership) |
| Frontend | COMPLETE | `/copilot`, `/admin/ai-usage`, `/admin/ai-audit`; 86 frontend tests; `tsc` 0 errors; `next build` PASS (23 routes) |
| Audit | COMPLETE | `ai_audit_events` (append-only; user, role, intent, tool, action status, timestamp, request/conversation ids; the message as a keyed fingerprint); 156 tests |

## 2. Bonus requirements

| Bonus | Status | Evidence |
|---|---|---|
| LangGraph | COMPLETE | `orchestration/graph.py`: finite state machine, forward-only transitions, iteration limits; 259 tests; router eval 33/33 |
| HITL | COMPLETE | Single-use, user/target/parameter-bound, expiring confirmations; the confirmation card in the UI; "yes" in chat never executes |
| Streaming | NOT IMPLEMENTED | Deliberately not built: FastAPI 0.116 closes request-scoped dependencies (the DB session) before a streaming body runs. A safe implementation needs a FastAPI upgrade or a session re-plumbing of the chat path (gap analysis §3). |
| Tracing | PARTIAL | Internal tracing via the audit trail (request/conversation ids, latency, provider latency, tokens, errors, permission failures, action lifecycle). No LangSmith/OpenTelemetry export; prompt inputs and model outputs are intentionally not recorded. |
| Evaluation dataset | COMPLETE | Six offline suites, 139 cases, including `evals/assignment_eval.json` with every assignment prompt in the assignment's format; one runner (`python -m evals.run_evals`) with JSON and text reports |
| Prompt injection defence | COMPLETE | Policy documents as delimited data plus the answer guard; router refusal rules; history as data; schema-validated model output; end-to-end tests including a poisoned policy document |
| Role-specific modes | COMPLETE | Employee, Manager and Admin Copilot modes from backend capabilities (UX only) |
| AI usage dashboard | COMPLETE | `/admin/ai-usage`: requests, intents, tools, permission denials, SQL blocks, RAG no-answer rate, confirmation and execution rates, latency. Admin-only by the backend. |

## 3. Security guarantees (verified)

- **Authorization.** The LLM never decides authorization. Identity and role come from the DB-loaded user on every
  request. A validly signed JWT with a forged `ADMIN` claim stays EMPLOYEE. Body, header, query and history claims
  are ignored or rejected (422).
- **No AI writes to business tables.**
  - The SQL agent is SELECT-only on a read-only connection. DELETE, UPDATE, INSERT, DROP, ALTER, TRUNCATE,
    PRAGMA, ATTACH, `SELECT *`, sensitive columns, `sqlite_master`, `load_extension`, multi-statement and comment
    tricks are all rejected, and business data is verified unchanged.
  - Actions run only through existing HRMS endpoints with the user's own token, after explicit confirmation.
- **Sensitive fields.** Passwords, bank details, PAN, DOB, salary and payroll identifiers never leave through AI.
  The UI masks them again as defence in depth.
- **Employee restrictions.** An employee cannot read another employee's data, approve leave or assign projects.
  A manager cannot act outside their team. Inactive users are rejected on every AI endpoint.
- **Confirmations** are single-use. Replay, duplicate (concurrent), expired, cross-user, tampered (parameters,
  target, resource, action) and permission-changed confirmations are all rejected. Natural-language "Yes",
  "Confirm", "Do it", "Proceed" and "go ahead" never execute.
- **Conversations** are owner-only. Foreign, unknown, malformed and archived ids get an identical 404.
- **Prompt injection.** Document, request, history and model-output injection cannot become an authorization
  source.
- **Audit** is append-only (database triggers), contains no prompts, answers, SQL or rows, is admin-only
  organisation-wide, and fails closed only before a confirmed mutation.
- **Production configuration.** Startup refuses the default JWT secret, fake providers and a missing key when
  `ENVIRONMENT=production`. No secrets are committed.

## 4. Test results

```text
Backend:      1685 passed, 0 failed
              (P1 132, P2 341, P3 142, P4 270, P5 123, P6 259, P7 173, P8 156, P10 89)
Frontend:     86 passed, 0 failed (Vitest + Testing Library)
TypeScript:   0 errors
Build:        next build PASS (23 routes)
Evaluation:   139/139 offline deterministic
              (Policy 11, SQL 20, Router 33, Conversation 25, Actions 20, Assignment 30)
Live model:   NOT RUN (no API key)
```

## 5. Known limitations

- **Live model quality is unmeasured.** No API key was available. All evaluation uses scripted model output, so
  real answer, SQL and action-extraction quality, and the live routing classifier, have not been measured
  (`run_evals --live` is ready).
- **Provider availability.** Without `ANTHROPIC_API_KEY` the Copilot answers only what deterministic rules decide
  plus the RAG insufficient-evidence path; everything else returns `AI_UNAVAILABLE`. The fastembed native runtime
  could not load on this Windows host (an environment limitation), so tests use hash embeddings.
- **Unsupported query types.**
  - Router rules are English heuristics.
  - Multi-intent handling is limited to the POLICY → SQL → ACTION order, with one action per message.
  - The SQL agent covers the allowlisted HR tables only (no attendance, announcements or polls; payroll is
    admin-only aggregates).
  - There is no streaming.
- **UX.**
  - History reloads show text only. Citations, tables and pending confirmations are not persisted, by privacy
    design.
  - After three consecutive "yes/confirm" messages the original proposal leaves the follow-up context window. It
    is still never executed.
  - There is no Markdown rendering.
  - The main app sidebar is desktop-only (existing behaviour).
  - Responsive layout has not been verified in a real browser.
- **Deployment assumptions.**
  - Single-instance SQLite; the audit flush happens after the response (non-critical events can be lost on a
    crash).
  - There is no retention job for conversations or audit events.
  - CORS allows only `http://localhost:3000` (it must be changed for real deployments).
  - The Dockerfile runs `uvicorn --reload` (development).
- **Dependency advisory.** npm reports a security advisory for the pinned `next@15.5.2`. Upgrading was out of
  scope and needs a decision.
- **Pre-existing app issues** (not AI): R1 — `GET /employees/{id}` returns sensitive fields to any user. The AI
  never calls it, and it stays documented under decision D4.

## 6. Final architecture

```text
              ┌──────────────────────────── Next.js (client only) ─────────────────────────────┐
              │ /copilot (modes, chat, citations, tables, confirmation cards)                  │
              │ /admin/ai-usage, /admin/ai-audit                                               │
              └───────────────────────────────┬────────────────────────────────────────────────┘
                                              │ Bearer JWT
┌─────────────────────────────────────────────▼──────────────────────────────────────────────────┐
│ FastAPI   AIAuditMiddleware (request_id) ─────────────────────────────────────────► ai_audit_events│
│   get_current_user (DB role) ─► get_ai_user_context (INACTIVE rejected) ─► AI authorization      │
│   conversation ownership ─► bounded untrusted context                                           │
│   LangGraph: classify ─► Policy RAG | SQL agent | Action PROPOSAL | clarification | refusal       │
│       Policy RAG ─► policy_chunks (hybrid retrieval, guarded grounding, sources)                │
│       SQL agent  ─► validate ─► scope ─► read-only SQLite connection                            │
│       Action     ─► ai_pending_actions ─► /chat/actions/confirm ─► existing HRMS APIs ─► DB       │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```
