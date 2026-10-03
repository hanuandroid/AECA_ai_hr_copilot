# Phase 8: AI Audit Logging, Observability and Evaluation

> Audit observes the system; it never controls it. Recording never authorizes, blocks or changes a request,
> with one deliberate fail-closed exception: a confirmed HR action is not executed unless its
> `ACTION_CONFIRMED` event has been durably written.

Code:

| Path | Contents |
|---|---|
| `backend/app/services/ai/audit/events.py` | Closed event types, statuses and reason codes; typed, allowlisted `AuditMetadata` |
| `backend/app/services/ai/audit/recorder.py` | Request scope (contextvar), `record`, `record_critical`, identity binding, text fingerprint, timers |
| `backend/app/services/ai/audit/store.py` | The only writer: INSERT only, own session, fallback to the structured log |
| `backend/app/services/ai/audit/middleware.py` | `AIAuditMiddleware`: one `request_id` per AI HTTP request, `X-Request-Id`, request-level event, flush |
| `backend/app/services/ai/audit/provider.py` | `observe(llm, operation)`: provider/model/latency/tokens/estimated cost through the existing abstraction |
| `backend/app/services/ai/audit/query.py` | Read-only, paginated queries and metrics |
| `backend/app/api/v1/endpoints/ai_audit.py` | `GET /api/v1/ai/audit` and `GET /api/v1/ai/audit/metrics` (GET only) |
| `backend/app/models/ai_audit_event.py` | The model; migration `0020_ai_audit_events` |
| `backend/evals/reporting.py`, `run_evals.py`, `live_router_eval.py` | Common evaluation reporting, CLI runner, opt-in live evaluation |

Tests are in `backend/tests/ai/audit/`.

## 1. Audit architecture

```text
HTTP request to /api/v1/chat* or /api/v1/ai*
  └─ AIAuditMiddleware ── new AuditScope(request_id) ─ contextvar ─────────────────────┐
       ├─ get_ai_user_context ── bind_user(DB employee id) [+ AUTHORIZATION_DENIED if inactive]
       ├─ ConversationalCopilot ── bind_conversation(id) only after the owner check
       ├─ router / RAG / SQL / actions / authorization / provider wrapper ── record(...)  (in memory)
       ├─ ActionAgentService._execute ── record_critical(ACTION_CONFIRMED)  (written NOW; fail-closed)
       └─ response sent with X-Request-Id
  finally: record(AI_HTTP_REQUEST) → store.flush(scope)  (fail-open: on error, events go to the structured log)
```

- **One correlation id.** `request_id` is created once per HTTP request. The router, the conversation layer and
  every event share it, and it is returned in `X-Request-Id` and in `/chat` bodies.
- **Passive recording.** `record()` is synchronous and in memory, and never raises. Invalid metadata is replaced
  by `error_code=AUDIT_METADATA_REJECTED`.
- **Isolated writes.** Events are written in their own DB session, so a business rollback cannot erase them and
  audit I/O never holds business locks.
- **Coverage.** Only AI paths are audited; the existing HRMS endpoints are untouched.
- **Structured logs.** Every event is also emitted as a structured JSON log line (`ai.audit.event`) through the
  existing structlog setup. No new logging dependency was added.

## 2. Event types (closed enum `AuditEventType`)

| Group | Events |
|---|---|
| Request | `AI_HTTP_REQUEST` (endpoint template, method, HTTP status, latency) |
| Chat | `CHAT_REQUEST`, `CHAT_CLASSIFIED`, `CHAT_ROUTED`, `CHAT_RESPONSE` |
| Policy | `POLICY_REQUEST`, `POLICY_RESPONSE` |
| SQL | `SQL_REQUEST`, `SQL_RESPONSE`, `SQL_SECURITY_REJECTION` |
| Actions | `ACTION_PROPOSED`, `ACTION_CONFIRMATION_REQUESTED`, `ACTION_CONFIRMED`, `ACTION_EXECUTED`, `ACTION_REJECTED`, `ACTION_EXPIRED` |
| Authorization | `AUTHORIZATION_ALLOWED`, `AUTHORIZATION_DENIED` |
| Outcomes | `CLARIFICATION_REQUESTED`, `REFUSAL`, `AI_ERROR` |
| Provider | `AI_PROVIDER_REQUEST`, `AI_PROVIDER_RESPONSE`, `AI_PROVIDER_ERROR` |

Statuses: `STARTED`, `SUCCESS`, `FAILED`, `DENIED`, `REFUSED`, `CLARIFICATION_REQUIRED`,
`CONFIRMATION_REQUIRED`, `PARTIAL`.

Reason codes (closed): `PROMPT_INJECTION`, `SENSITIVE_DATA_REQUEST`, `DESTRUCTIVE_REQUEST`, `DIRECT_SYSTEM_ACCESS`,
`UNSUPPORTED_REQUEST`, `AMBIGUOUS_REQUEST`, `MULTIPLE_ACTIONS`, `TOO_MANY_REQUESTS`, `CONFIRMATION_IN_CHAT`,
`INACTIVE_USER`, `CAPABILITY_NOT_GRANTED`, `OUT_OF_SCOPE`, `INVALID_REQUEST`.

Event types come only from backend code. There is no API that accepts an event type.

## 3. Event lifecycle (examples)

Policy turn, one `request_id`:

```text
CHAT_REQUEST → CHAT_CLASSIFIED → CHAT_ROUTED → POLICY_REQUEST → AUTHORIZATION_ALLOWED(POLICY_READ)
  → AI_PROVIDER_REQUEST → AI_PROVIDER_RESPONSE → POLICY_RESPONSE → CHAT_RESPONSE → AI_HTTP_REQUEST
```

A refused prompt injection produces `CHAT_REQUEST → CHAT_CLASSIFIED → REFUSAL(PROMPT_INJECTION) → CHAT_RESPONSE(REFUSED) → AI_HTTP_REQUEST`.

Failures add `AI_ERROR` (safe code and HTTP status). Examples: classification failure
(`CHAT_CLASSIFIED FAILED AI_CLASSIFICATION_FAILED`), provider failure (`AI_PROVIDER_ERROR`), service crash
(`AI_ROUTING_FAILED`).

## 4. Correlation IDs

| Field | Source | Notes |
|---|---|---|
| `event_id` | UUID4 per event | Unique, stable |
| `request_id` | Middleware, once per HTTP request | Connects all events of one request; `X-Request-Id`; equals the `/chat` body `request_id`. Client-sent `X-Request-Id` is ignored. |
| `sequence` | Per request | Event order within the request |
| `employee_id` | `get_ai_user_context` (DB-loaded employee) | Never from body, query, header, history or model; cannot be rebound within a request |
| `conversation_id` | Conversation service, after the owner check | A foreign or unknown id is never associated (null on not-found) |

A conversation can be reconstructed without content: filter by `conversation_id` and read the `CHAT_RESPONSE`
events (`request_id`, `route`, `status`, `follow_up`). For example: C1 → R1 POLICY SUCCESS, R2 SQL SUCCESS.

## 5. Privacy and redaction policy

| Data | Stored? |
|---|---|
| User prompts / assistant answers | **No.** Only length plus a keyed 64-bit HMAC fingerprint (`message_hash`, `response_hash`) for correlation. The key is derived from the server secret, so the fingerprint cannot be dictionary-checked without it. |
| SQL text, parameters, result rows | **No.** Only `query_type`, validated `tables`, `scope`, `row_count`, `truncated`, `security_result`/rejection code. |
| Retrieved policy text | **No.** Only `grounded` and the number of sources. |
| Confirmation tokens, JWTs, API keys, headers | **No.** There are no fields for them, and values that look like them are rejected. |
| Employee PII (PAN, bank, salary, DOB, UAN/ESI) | **No** |
| Internal ids (`employee_id`, target employee id, leave/ticket id, confirmation record id) | Yes. These are internal identifiers needed to answer "who did what to which record". |

This is enforced by design, not by convention:

- `AuditMetadata` has a fixed field set (`extra="forbid"`).
- Every text value must be a short identifier-like token (`^[A-Za-z0-9_.:+\-/{}]{0,120}$`) and must not match the
  secret/PAN/IFSC/long-number patterns.
- Lists are capped at 20 items.

Free text such as "Ignore all…", "salary 72000", "password=…" or a JWT cannot be stored, by construction. Tests
send JWT, API key, PAN, bank account, IFSC, UAN, ESI, password and salary values through `/chat` and assert that
they appear in neither persisted audit rows nor `ai.audit` log lines.

There are no `AI_AUDIT_STORE_PROMPTS` / `AI_AUDIT_STORE_RESPONSES` switches: raw prompt and response storage is
simply not implemented. Conversation text exists only in the owner-only Phase 7 store, redacted.

## 6. SQL observability

- **Success.** `SQL_REQUEST` then `SQL_RESPONSE {query_type: SELECT, tables, scope, row_count, truncated,
  security_result: ALLOWED}` with latency.
- **Validator or scope rejection.** `SQL_SECURITY_REJECTION` (DENIED) with the safe code (`SQL_MUTATION_REJECTED`,
  `SQL_COLUMN_NOT_ALLOWED`, `SQL_SCOPE_VIOLATION`, …) and the validated tables when known.
- **Parse or execution failures** produce `SQL_RESPONSE FAILED`.
- The rejected SQL text is never recorded. Tests assert that neither "DELETE", "WHERE" nor column names leak.
  There is no SQL-debug logging switch.

## 7. Action lifecycle

| Step | Events |
|---|---|
| Propose (`/chat`, `/chat/actions`) | `ACTION_PROPOSED` (action, target and resource ids, confirmation record id) and `ACTION_CONFIRMATION_REQUESTED` (CONFIRMATION_REQUIRED, state PENDING). A clarification gives `CLARIFICATION_REQUESTED` (route ACTION). |
| Confirm, before execution | `ACTION_CONFIRMED`, **written immediately**. If it cannot be written: `503 AI_AUDIT_UNAVAILABLE`, nothing is claimed or executed, and the token stays valid for a retry. |
| HRMS API succeeded | `ACTION_EXECUTED` (state COMPLETED). This is the only way it is ever recorded. |
| HRMS API rejected (stale state, overlap…) | `ACTION_REJECTED FAILED` with the backend code |
| Invalid, replayed, foreign token; permission or target denial | `ACTION_REJECTED DENIED` (`AI_CONFIRMATION_INVALID`, `AI_PERMISSION_DENIED`, `AI_TARGET_NOT_FOUND`) |
| Expired token | `ACTION_EXPIRED` |

Tests check that a proposal never yields `ACTION_CONFIRMED`/`ACTION_EXECUTED`, that a confirmation yields exactly
`ACTION_CONFIRMED → ACTION_EXECUTED`, and cover business rejection, replay, expiry, and the audit-down
fail-closed path.

## 8. Authorization events

- `authorize()` records `AUTHORIZATION_ALLOWED {capability, scope (target rule), target_checked}` or
  `AUTHORIZATION_DENIED {capability, reason_code}`. Inactive users are recorded by `get_ai_user_context`
  (`INACTIVE_USER`).
- `record()` is fire-and-forget: its return value is never read on a decision path (static test). No raw
  authorization context is stored.
- An out-of-scope target is visible as `ACTION_REJECTED DENIED AI_TARGET_NOT_FOUND` for route ACTION and action
  LEAVE_APPROVE, without names or other employee data.

## 9. Evaluation framework

- `evals/reporting.py` adapts the existing evaluators (unchanged) to a common `SuiteResult` with these metrics:

  | Suite | Metrics |
  |---|---|
  | policy | retrieval, citation, grounding, refusal |
  | sql | allowed/refused classification, security rejection, scope, forbidden columns, no mutation |
  | router | route, status, clarification, refusal, never executes, business data unchanged |
  | conversation | follow-up route, context resolution, security preservation |
  | actions | proposal, confirmation requirement, unauthorized rejection, no mutation before confirmation |

- `python -m evals.run_evals` runs the five eval tests in a **pytest subprocess**. The test harness builds a
  throwaway SQLite DB (Alembic plus test seed) in a temp directory, and the runner imports nothing from `app`, so
  it can never touch `storage/hrms.db`. It writes `evals/results/<suite>.json`, `summary.json` and `summary.txt`
  (git-ignored) and prints the summary.
- `--live` adds the live-model router classification (`evals/live_router_eval.py`) **only if `ANTHROPIC_API_KEY`
  is set**. Otherwise the report says `LIVE_MODEL_EVAL = NOT_RUN`. Live results are never faked or mixed with
  offline results.
- The actions suite executes confirmed cases through the real HRMS API, but only on the throwaway evaluation
  database. The proposal steps assert that business data is unchanged.

Current offline result: 109/109 cases. Policy 11, SQL 20, Router 33, Conversation 25, Actions 20; every metric is
100%; `LIVE_MODEL_EVAL = NOT_RUN` (no key in this environment).

## 10. Metrics (`GET /api/v1/ai/audit/metrics`, admin only)

Optional `created_from` / `created_to` window. Audit-API reads are excluded from request counts.

| Metric | Source |
|---|---|
| `requests_total/success/failed/denied` | `AI_HTTP_REQUEST` status (401/403 → denied) |
| `requests_refused`, `requests_clarification`, `refusals_by_reason` | `REFUSAL`, `CLARIFICATION_REQUESTED` |
| `authorization_denied`, `sql_security_rejections` | Matching events |
| `action_proposals/confirmations/executions/rejections/expirations` | Lifecycle events |
| `provider_calls`, `provider_errors`, `ai_errors` | Provider and error events |
| `latency_ms`, `provider_latency_ms` | `{count, avg, p95, max}` over the latest ≤ 10 000 events |
| `tokens`, `estimated_cost_usd` | Only from reported usage and configured pricing; `null` otherwise |

All durations come from `time.perf_counter()` (monotonic, high resolution). Latency is measured for:
- the HTTP request;
- classification;
- each service call (policy, SQL, action);
- each provider call.

Prometheus, Grafana and OpenTelemetry were not introduced.

**Provider data.** Provider, model, operation, schema, latency and success or failure come from the
`observe()` wrapper around the existing `LLMProvider`. Token usage is reported by `AnthropicLLMProvider` through
a per-task context variable (`report_usage`), and by `generate()` responses. The fake providers report no usage
for structured calls, so tokens are `null` offline.

**Cost.** Cost is estimated only from configured pricing: `AI_LLM_INPUT/OUTPUT_COST_PER_MTOK` and
`AI_LLM_FAST_INPUT/OUTPUT_COST_PER_MTOK` (USD per million tokens). These are unset by default, so cost is `null`.
It is labelled an estimate, not billing.

## 11. Failure behaviour

| Failure | Behaviour |
|---|---|
| Audit DB write fails at flush | The request outcome is unchanged (allowed stays allowed, refused stays refused, denied stays denied). Every event, including denial evidence, is written to the structured log as `ai.audit.fallback`, plus `ai.audit.write_failed`. |
| Audit DB write fails for `ACTION_CONFIRMED` | **Fail-closed**: `503 AI_AUDIT_UNAVAILABLE`. The mutation does not run; the confirmation stays PENDING. |
| Invalid metadata | Replaced by `AUDIT_METADATA_REJECTED`; the request continues |
| `AI_AUDIT_ENABLED=false` | Events are still emitted to the structured log but not persisted, and the fail-closed check is skipped (an explicit operator choice) |

**No recursion.** The audit package imports no provider, router, service or HTTP client (static test), so audit
code can never create an AI request.

## 12. Retention

There is no assignment-mandated retention period, so none is invented. Audit events are kept indefinitely; there
is no deletion job and no retention setting (it would be unimplemented configuration).
- **Purging.** The table is append-only at the database level: triggers reject UPDATE and DELETE. A future
  retention purge must therefore be an explicit, documented administrative migration or procedure that handles
  the triggers. It can never happen silently from the application.
- **Volume.** Volume is roughly 10 events per AI request. Indexes are `request_id`, `conversation_id`,
  `created_at`, `(employee_id, created_at)` and `(event_type, created_at)`, chosen for the API's filters and
  metrics windows.

## 13. Access control

| Who | `GET /api/v1/ai/audit` | `GET /api/v1/ai/audit/metrics` |
|---|---|---|
| ADMIN (`AUDIT_READ_ORG`) | All events; may filter by `employee_id` | Yes |
| MANAGER, EMPLOYEE (`AUDIT_READ_OWN`) | Only their own events; any other `employee_id` → 403 | 403 |
| Inactive / unauthenticated | 403 `AI_USER_INACTIVE` / 401 | Same |

- The role comes from the DB via `get_ai_user_context` and the Phase 2 matrix; headers and query parameters
  cannot widen access.
- Filters are typed and closed: `event_type`/`status` enums, `route`/`request_id`/`conversation_id` patterns,
  `employee_id`, `created_from`/`created_to`. There are no expressions.
- Pagination is `limit` ≤ 100 and `offset` (repository convention) with `meta.total`. Reading the audit log
  appends events, so stable paging pins `created_to`.
- There are no POST/PUT/PATCH/DELETE routes (405), and the triggers make the table append-only. Clients cannot
  create, edit, re-type, re-attribute, re-date or delete events.
- The audit API exposes metadata only, never conversation content.
- Path note: the audit API lives under `/api/v1/ai/*`, as the Phase 8 prompt suggests, rather than
  `/api/v1/chat/*`. It uses the same AI dependency and envelope.

## 14. Known limitations

- **Approximate percentiles.** Percentiles are computed in-app over the latest ≤ 10 000 request events per
  window, which suits this deployment size; there is no time-series store.
- **No offline token or cost data.** Token usage and cost exist only for real Anthropic calls; offline runs show
  `null`, by design.
- **Live evaluation not run.** The live-model evaluation hasn't been run (no API key). The offline suites
  validate pipelines and security behaviour with scripted model output, not model quality.
- **Retention.** No retention purge exists (see §12).
- **Failed sign-ins not audited.** Unauthenticated requests (401) produce only the request-level event, with no
  user; failed sign-ins are outside the AI audit scope.
- **Fail-open window.** If the process crashes between the response and the flush, that request's non-critical
  events are lost. Critical action events are written before execution.
