# AI Architecture — NovaWorks PeopleOps Copilot (CB Nest)

Status: **Phases 1–10 implemented**. These cover the foundation, authorization boundary, Policy RAG, SQL agent,
Action agent, LangGraph router, conversations, audit and observability, the Copilot UI, and production hardening
with the formal evaluation. Decisions D1–D7 are approved (see `ai_implementation_plan.md`). Final status and
evidence: [phase-10-final-report.md](phase-10-final-report.md).

## 1. System view

Final architecture (as implemented):

```text
Next.js /copilot (+ /admin/ai-usage, /admin/ai-audit)   — client only; never an authorization boundary
        │  Authorization: Bearer <JWT>   (X-Request-Id / X-Conversation-Id returned)
        ▼
FastAPI  AIAuditMiddleware (request_id, append-only audit) ── /api/v1/chat* , /api/v1/ai/*
        │
get_current_user (existing JWT; role re-loaded from DB) ─► get_ai_user_context (INACTIVE rejected)
        │
Conversation ownership (Phase 7) ─► bounded, untrusted follow-up context
        │
LangGraph router (Phase 6): classify ─► policy | sql | action-PROPOSAL | clarification | refusal ─► finalize
        │                 (each service re-authorizes via services/ai/authorization — Phase 2)
        ├─ Policy RAG (Phase 3): hybrid retrieval over policy_chunks ─► grounded answer + sources (guarded)
        ├─ SQL agent (Phase 4): validate ─► scope rewrite ─► read-only connection (mode=ro, query_only, authorizer)
        └─ Action agent (Phase 5): propose ─► ai_pending_actions ─► POST /chat/actions/confirm ─► existing HRMS API
        ▼                                                                       (user's own token)
ai_audit_events (Phase 8, append-only) ─► /api/v1/ai/audit, /api/v1/ai/audit/metrics (admin dashboard)
```

Original Phase 0 design (historical):

```text
Next.js  /ai-copilot  ──JWT (Authorization: Bearer)──►  FastAPI /api/v1/chat/{policy|sql|actions|router|confirm}
                                                                │
                                                   get_current_user (existing)  ← DB-loaded role
                                                                │
                                                     AIUserContext (id, role, team_ids, active)
                                                                │
                                           ┌──────── LangGraph orchestrator (Phase 6) ────────┐
                                           │ classify_intent → permission_check → route        │
                                           │   ├─ PolicyRAG  (read-only: policy_chunks)        │
                                           │   ├─ SQLAgent   (read-only SQLite connection)     │
                                           │   └─ ActionAgent (HTTP → existing endpoints)      │
                                           │ → format_response → audit_log                     │
                                           └───────────────────────────────────────────────────┘
                                                                │
              ┌─────────────────────────┬───────────────────────┴───────────────┬──────────────────────┐
              ▼                         ▼                                       ▼                      ▼
     policy_chunks (vectors)    sqlite (mode=ro, query_only,          existing /api/v1 endpoints   ai_audit_logs
                                authorizer: SELECT/READ only)         with the USER's token        ai_pending_actions
```

**Architecture decision D7 (approved):** *AI authorization ≥ existing application authorization.* The AI layer may be stricter than an existing endpoint and must never be more permissive, even where the endpoint itself allows more (R4 team scoping, ticket status by owners, project creation by managers). `services/ai/authorization/` (originally planned as `permissions.py`) is authoritative for AI capabilities. The LLM never determines authorization.

### AI authorization boundary (Phase 2, implemented)

> **AI authorization is stricter than existing HRMS authorization. AI authorization must never be more permissive.**

`backend/app/services/ai/authorization/` is the **only** place AI code decides access. It has no I/O except building the context.

| Module | Responsibility |
|---|---|
| `capabilities.py` | `Capability` enum (23 capabilities), `TargetRule` (NONE/SELF/TEAM/REPORTS/ORG/ORG_EXCEPT_SELF), `ScopeLevel` |
| `policy.py` | `AI_PERMISSION_MATRIX`, an immutable `MappingProxyType` of capability → {role → target rule}. This is the single source of truth. Missing means deny. |
| `context.py` | `AIUserContext` (frozen, slots): `employee_id, role, is_active, manager_id, department_id, direct_report_ids`, plus `team_ids`. It is built by `build_ai_user_context(employee, db)` from the DB row. The FastAPI dependency `get_ai_user_context` reuses `get_current_user` and **rejects INACTIVE users before anything else runs** (`AI_USER_INACTIVE`, 403). |
| `scope.py` | `TargetEmployee(employee_id)` (typed, positive int). `target_allowed()`. `DataScope` + `hr_read_scope()` / `sql_read_scope()` give the row scope the SQL agent will bind (SELF → {me}, TEAM → {me} ∪ direct reports, ORG → unrestricted). |
| `sensitive.py` | `SENSITIVE_COLUMNS` (exact names from the real schema) plus `SENSITIVE_TOKENS` (catches aliases such as `salary`, `dob`, `account_number`, `pf_number`, `password_hash`, camelCase). `is_sensitive_field()`. `is_column_readable(ctx, table, column)`, where the payroll table needs PAYROLL_READ_ORG. |
| `service.py` | `authorize(ctx, capability, resource=None) -> AuthorizationResult{allowed, capability, reason, message}` (never raises). `require(...)` raises. `run_authorized(ctx, capability, operation, resource=...)` authorizes **before** the operation (LLM/retrieval/SQL/tool) is awaited. |

Design properties:
- **Narrow inputs:** only an `AIUserContext`, a `Capability` enum member, and an optional `TargetEmployee`. Strings (such as capability names produced by an LLM), dicts, or `authorized: true` payloads are rejected with `INVALID_REQUEST`. No parameter exists through which the prompt, LLM output, tool arguments, retrieved documents or frontend claims could assert a role or a decision.
- **Team scope** comes from `employees.manager_id` (direct reports) loaded server-side, never from `role = 'MANAGER'` or LLM-generated conditions.
- **Generic denials:** "no capability", "out of scope" and "target does not exist" all return the same message, *"You are not authorized to access this resource."* Scope checks never query the target, so there is no existence oracle.
- **Defence in depth for R9:** the dependency rejects INACTIVE users, and `authorize()` independently denies any context with `is_active=False`.
- `services/ai/guards.py` (Phase 1) now imports its sensitive-field definition from `authorization/sensitive.py`, so there is one definition.
- `GET /api/v1/chat/permissions` returns the caller's capabilities and data scope (for role-aware UI in Phase 9). An `AIError` exception handler in `main.py` renders AI errors in the standard envelope. It is registered for `AIError` only, and existing endpoint error shapes are unchanged (tested).

**Critical invariant:** no AI module holds a read-write connection to business tables. The only AI writes are to AI-owned tables (`policy_chunks`, `ai_audit_logs`, `ai_pending_actions`), done by dedicated repository functions. All business mutations are HTTP calls to existing endpoints using the requesting user's bearer token.

## 2. Backend module layout

Phase 1 implemented (✅) and planned modules:

```text
backend/app/services/ai/
  __init__.py          ✅
  errors.py            ✅ AIError base + AIUnavailable/AIProvider/AIInvalidOutput/AIPermissionDenied (safe messages, HTTP status)
  providers.py         ✅ build_llm_provider / build_embedding_provider: config → provider; fail closed (AI_UNAVAILABLE)
  guards.py            ✅ SENSITIVE_FIELDS, forbidden backend routes (R1 et al.), strip_sensitive_fields
  redaction.py         ✅ JWT / bearer / API key / PAN / IFSC / long-number redaction + truncation
  llm/base.py          ✅ LLMProvider protocol: generate(), generate_structured(); LLMMessage, LLMResponse
  llm/fake.py          ✅ FakeLLMProvider: deterministic rules, schema-validated structured output, call log
  embeddings.py        ✅ EmbeddingProvider (embed, embed_query, model_id); FakeEmbeddingProvider (hash);
                          FastEmbedEmbeddingProvider (Phase 3, lazy); cosine_similarity
  (AI settings live in core/config.py::Settings: ai_* fields, anthropic_api_key: SecretStr)
  authorization/       ✅ Phase 2: capabilities, policy (matrix), context, scope, sensitive, service (see above)
  llm/anthropic_provider.py ✅ Phase 3: AnthropicLLMProvider (messages.parse structured output, server fallbacks)
  llm/openai_provider.py   ✅ 2026-10-16: OpenAILLMProvider (Responses API, strict JSON-schema structured output, store=false); see ai-provider-setup.md
  rag/                 ✅ Phase 3 (see §3.1): models, loader, chunker, vector_store, ingest, retriever, prompts,
                          guard, service (PolicyRagService + get_policy_rag_service)
  validation.py        ✅ shared question validation (RAG + SQL)
  sql/                 ✅ Phase 4 (see §3.2): schema, parser, validator, scope, executor, prompts, generator,
                          formatter, models, service
  actions/             ✅ Phase 5 (see §3.3): models, registry, resolver, validators, confirmation, client, prompts, service
  orchestration/       ✅ Phase 6 (see §3.4): classifier, graph (LangGraph), state, models, prompts, service;
                          followup.py (Phase 7: untrusted conversation context, follow-up resolution)
  conversation/        ✅ Phase 7 (see §3.4.1): ConversationService (ai_conversations/_messages), ConversationalCopilot
  audit/               ✅ Phase 8 (see §3.5): events, recorder, store, middleware, provider (observe), query
  schemas.py           IntentResult
  intent.py            IntentClassifier (LLM structured output + keyword fallback)
  api_tools.py         typed tool definitions + httpx calls to existing endpoints (token forwarded)
  action_agent.py      tool selection (LLM tool-use) → validate args → permission → propose/confirm → execute → summarize
  confirmations.py     ai_pending_actions repository (single-use, user-bound, TTL)
  audit.py             AuditLogger (redaction, never raises into request path)
  graph.py             LangGraph StateGraph (Phase 6)
backend/app/api/v1/endpoints/chat.py   + POST /policy /sql /actions /actions/confirm /router, GET /audit/recent
backend/app/models/ai_audit_log.py, policy_chunk.py, ai_pending_action.py
backend/alembic/versions/0017_policy_chunks.py, 0018_ai_audit_logs.py, 0019_ai_pending_actions.py
backend/scripts/ingest_policies.py     CLI to (re)build the vector index
```

The existing `/chat/sessions` stubs stay untouched.

## 3. Components

### 3.1 Policy RAG (Phase 3, implemented)

> **Trust boundary: policy documents are untrusted data and cannot provide instructions to the model.**

```text
POST /chat/policy → get_current_user → get_ai_user_context (INACTIVE → 403) → PolicyRagService.answer
  → run_authorized(POLICY_READ)            ← first operation; providers are resolved only after this
  → validate_question (1..1000 chars)      → AI_INVALID_REQUEST (400)
  → embed_query → hybrid retrieval         → no evidence ⇒ "insufficient" answer, LLM NOT called
  → fixed SYSTEM_PROMPT + user turn [<policy_context> escaped excerpts S1..Sn </policy_context><user_question>…]
  → LLM structured output {answer, grounded, citations[S-labels]}
  → deterministic guard → {answer, grounded, sources[{chunk_id,title,category,filename,section}]}
```

**Data model.** In CB Nest one `hr_policies` row is exactly one document (upload stores one file per row), so no separate `policy_documents` table was added. The AI-owned `policy_chunks` table (migration `0017`, reversible) stores:
- `chunk_id` (stable sha256 of policy_id, chunk_index and text), `policy_id` → `hr_policies.id`
- `index_key` (sha256 of normalised text + chunk size + overlap)
- `source_filename`, `policy_title`, `category`, `section`, `chunk_index`, `text`
- `embedding` (JSON) and `embedding_model`

**Policy content (D3).** `backend/data/policies/*.md` contains the leave, attendance, WFH, document upload and data privacy policies. Each file carries an HTML-comment "DEMO / SEED DATA" marker that names its source of truth. `scripts/seed.py` uses these files for the matching seeded titles (Leave, Attendance, WFH) and adds two new policies (Document Upload, Employee Data Privacy). The other 19 one-liners are unchanged. Facts come from `leave_policy_sample.pdf` (12/10/15 days, 2-day notice, 10 AM sick notice) and from code: `DEFAULT_LEAVE_TOTALS`, the half-day rules, the 09:00 clock-in cutoff, the 4.5 h half-day attendance rule, and the upload limits and types.

**Ingestion** (`rag/ingest.py`, run with `scripts/ingest_policies.py`):
1. The loader reads each row's `content` column, or its file path-guarded under `POLICY_UPLOAD_DIR` (.md/.txt/.pdf via pypdf).
2. It normalises the text (BOM/CRLF/HTML comments stripped).
3. The chunker splits it.
4. Chunks are embedded in a batch.
5. The store replaces that policy's chunks.

A document is skipped when its `index_key` and embedding model are unchanged. Chunks of policies that became unreadable or were removed are deleted. The second run is a no-op (tested, and verified end-to-end on a freshly seeded DB: 24 documents, 40 chunks, then 24 unchanged).

**Chunking.** Markdown heading sections, then paragraph blocks packed up to `AI_POLICY_CHUNK_SIZE` (800 chars). A block is split only if it alone is too large, and then at line, sentence and finally word boundaries. The overlap (`AI_POLICY_CHUNK_OVERLAP`, 120) repeats the previous chunk's last block when it fits. The section label is `H2` or `H2 > H3`. The embedding input is `"{title} - {section}\n{text}"`.

**Embeddings.** `FastEmbedEmbeddingProvider` (D2): `AI_EMBEDDING_MODEL` (default `BAAI/bge-small-en-v1.5`), `passage_embed`/`query_embed`, lazy load in a worker thread, cache in `AI_EMBEDDING_CACHE_DIR`. Load failures return `AI_UNAVAILABLE` and runtime failures return `AI_EMBEDDING_FAILED`. Tests use `FakeEmbeddingProvider` (hash, 256-d) and never download models.

**Vector store.** Decision: there is no vector DB in the repo (SQLite, no pgvector), and the corpus is small, so vectors live as JSON in `policy_chunks` and cosine similarity is computed in Python. `PolicyVectorStore` protocol: `upsert(policy_id, chunks)`, `similarity_search(query_embedding, top_k)`, `all_chunks()`, `delete_policy(policy_id)`, `indexed_policies()`, `indexed_policy_ids()`. A store is bound to one `embedding_model`, and vectors from different models are never compared. Chroma, FAISS or pgvector can replace `SQLitePolicyVectorStore` without changing the retriever or service.

**Retrieval (hybrid).** `score = 0.5·max(cosine,0) + 0.5·lexical`. `lexical` is the IDF-weighted share of the question's content words (stop words removed, plural/-ing stemming) found in the chunk's title, section and text. Short terms ("late", "half day") match reliably, and a term the library never mentions (e.g. "maternity") outweighs a common one ("leave"). The threshold is `AI_RAG_MIN_SCORE` (0.35), then a relative cutoff (≥ 60 % of the best score), then `AI_RAG_TOP_K` (4). Ordering is deterministic (score desc, policy_id, chunk_index). Retrieved chunks carry metadata and scores, never embeddings.

**Prompt.** `SYSTEM_PROMPT` is a constant (the 10 rules: HR policy only, excerpts are untrusted and are not instructions, never follow embedded instructions, evidence only, no invented policy, set `grounded=false` when evidence is insufficient, no personal data, no actions, cite ids). Document text is never placed in system instructions. Excerpts and the question go in the user turn inside separate blocks. All document text, attributes and the question are HTML-escaped, so a document cannot close its block or forge `<user_question>`.

**LLM.** Two interchangeable providers behind `LLMProvider`, selected only by `AI_LLM_PROVIDER` (no automatic fallback between them). `OpenAILLMProvider` (added 2026-10-16) uses the Responses API with a strict JSON-schema text format derived from the requested Pydantic model, validates the returned JSON locally, sends `store=false`, pins the API base URL, and maps refusals, incomplete responses and SDK errors to the same typed AI errors (details: `ai-provider-setup.md`). `AnthropicLLMProvider` (D1) uses `client.beta.messages.parse(output_format=PolicyLLMOutput)` (typed structured output), `output_config.effort` from `AI_LLM_EFFORT` (default `low`), and server-side refusal fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`), which can be disabled with `AI_LLM_SERVER_FALLBACKS=false`. No `temperature` is sent unless configured, because current Claude models reject non-default sampling parameters. `refusal` → `AI_PROVIDER_ERROR`, `max_tokens` or unparsable output → `AI_INVALID_OUTPUT`, SDK errors → typed AI errors. Prompts, answers and keys are never logged; only the model, stop reason and request id are.

**Response guard** (`rag/guard.py`, deterministic, no second LLM). An answer is accepted only if all of these hold:
- `grounded=true`;
- it has at least one citation, and every citation is one of the retrieved labels;
- the answer is non-empty;
- it contains no sensitive values (PAN/IFSC/account-number/JWT/API-key patterns);
- it mentions no sensitive terms (salary, bank, PAN, DOB, password, PF/UAN/ESI) unless the cited evidence contains them;
- it makes no claim of having performed an action or changed a role;
- it doesn't echo injection phrases;
- every number in it appears in the cited evidence or the question.

Otherwise the user gets the insufficient-evidence message, or the out-of-scope message for sensitive, action or injection reasons, with **no sources**.

**Citations.** Sources are built only from the guard's validated set of *retrieved* chunks (`chunk_id, title, category, filename, section`). The LLM cannot add a source.

**Errors.** `AI_INVALID_REQUEST` 400, `AI_USER_INACTIVE`/`AI_PERMISSION_DENIED` 403, `AI_RETRIEVAL_FAILED` 500, `AI_EMBEDDING_FAILED`/`AI_PROVIDER_ERROR`/`AI_INVALID_OUTPUT` 502, `AI_UNAVAILABLE` 503. None of them include DB text, paths, keys, provider internals or stack traces (tested).

**Evaluation.** `backend/evals/policy_eval.json` (11 cases: the 5 required questions plus document upload, approval process, attendance rules, and 3 must-refuse cases). `evals/policy_rag_eval.py` measures retrieval hit, citation validity, groundedness and refusal. Offline it uses `ExtractiveFakeLLM`, which can only quote retrieved sentences, so it measures the pipeline, not model quality. Current result: 1.0 on all four metrics.

### 3.2 SQL Agent (Phase 4, implemented)

> **The LLM is never an authorization authority. SQL validation is enforced independently of the LLM prompt.** The LLM is an untrusted SQL generator, and the backend is the security authority.

```text
POST /chat/sql → get_current_user → get_ai_user_context (INACTIVE → 403)
  → SQLAgentService.answer → run_authorized(SQL_READ_SELF)     ← before any LLM call
  → validate_question → scope = sql_read_scope(ctx)              (SELF / TEAM / ORG from the matrix)
  → SQLGenerator: LLM structured output {sql, explanation}       (extra fields → AI_INVALID_OUTPUT)
  → parser.parse_select       one statement, root SELECT/set-op, no mutation/admin node anywhere
  → validator (pass 1)        CTE names, complexity, functions, :me only, no stars, tables, columns
                              (sqlglot qualify vs real schema), approved joins, [non-ORG] literal ids,
                              [non-ORG] scope intent
  → scope.enforce_scope       wrap every table in a backend scope subquery, inject my_team, LIMIT
  → scope.verify_final_sql    re-parse the regenerated SQL and prove wrappers/CTE/LIMIT (pass 2)
  → executor                  mode=ro + query_only + per-query authorizer + timeout + row cap
  → formatter                 redact rows → LLM answer → deterministic guard (fallback summary)
```

**Package** `backend/app/services/ai/sql/`:

| Module | Contents |
|---|---|
| `schema.py` | registry (`TABLES`, `DENIED_TABLES`, owner columns, approved joins, `my_team`, `:me`) |
| `parser.py` | sqlglot SQLite parse and AST deny-list |
| `validator.py` | pass-1 rules |
| `scope.py` | rewrite, LIMIT, pass-2 verification |
| `executor.py` | read-only execution |
| `prompts.py` | generation and formatting prompts |
| `generator.py` | SQL generation call |
| `formatter.py` | redaction and answer formatting |
| `models.py` | output schemas, `SQLAnswer`, `SQLRunRecord` |
| `service.py` | `SQLAgentService`, `get_sql_agent_service` |

**Parser choice.** sqlglot 30.20.0 (pure Python, no dependencies) with the `sqlite` dialect, which matches the repository's database. The only SQL ever executed is regenerated from the validated AST with `comments=False`. The model's raw text is never executed, so anything hidden in comments cannot run.

**Deny-list (AST node types).**
- Mutations (`SQL_MUTATION_REJECTED`): Insert, Update, Delete, Merge, Drop, Create, Alter, AlterColumn, TruncateTable, and `REPLACE INTO` (parsed as a Command).
- Everything else (`SQL_NOT_ALLOWED`): Command (VACUUM, CALL, …), Pragma, Attach, Detach, Grant, Revoke, Transaction, Commit, Rollback, Set, Use, Copy, LoadData, Analyze, Describe, Show, Into, Returning, Lock, Execute, Call.
- Also refused: any statement root other than SELECT/UNION/INTERSECT/EXCEPT (for example `EXEC x` parses as an alias), multiple statements, and recursive CTEs.
- Functions are allowlisted: count, sum, avg, min, max, total, abs, round, length, lower, upper, trim, substr, coalesce, ifnull, nullif, iif, CASE, CAST, date, time, datetime, julianday, strftime, instr, replace, group_concat, plus LIKE/GLOB operators. `load_extension`, `readfile`, `randomblob`, `sqlite_version`, table-valued pragmas and so on are refused.

**Complexity limits.** At most `AI_SQL_MAX_JOINS` (4) joins, 8 SELECTs and a subquery depth of 3. No cartesian, comma, CROSS, USING or NATURAL joins, and no recursive CTEs. Execution has an `AI_SQL_TIMEOUT_SECONDS` (3) progress-handler timeout.

**Row limit.** The outermost `LIMIT` becomes `min(literal, AI_SQL_MAX_ROWS=100)`; a missing or non-literal limit becomes 100. The executor also fetches at most 100 rows and reports `truncated`.

**Read-only execution.** A separate `sqlite3` connection is opened as `file:<db>?mode=ro` with `PRAGMA query_only=ON`, so the app's read/write engine is never used. A per-query authorizer allows `SQLITE_SELECT`, `SQLITE_READ` of the allowlisted columns of *this query's* tables, table-level reads (empty column) only for those tables and CTEs, and allowlisted functions. It denies everything else. Proven by tests that execute writes and forbidden reads directly (bypassing the validator), with the DB hash unchanged.

**Result handling.** Columns with sensitive names are dropped and string values pass through `redact()` **before** the formatter model sees them. The formatter answer must not contain sensitive values or action/role claims, and every number in it must appear in the rows or the question; otherwise a deterministic summary is returned. Empty results never call the formatter. `sql` (the validated SQL with `:me`, not the rewritten one) is returned only to roles with `SQL_VIEW_RAW` (MANAGER, ADMIN).

**Observability-ready.** `SQLRunRecord` holds request_id, employee_id, role, capability, question length, SQL hash, scope, tables, outcome/rejection detail, duration, row count and LLM calls. It is logged as `ai.sql.run`, with no row values, question text or SQL text.

### 3.3 HR Action Agent (Phase 5, implemented)

> **The LLM proposes actions; the backend authorizes and executes them. All data-changing AI actions require explicit confirmation. The Action Agent can call only registered HRMS APIs.**

```text
POST /chat/actions {question}
  get_current_user → get_ai_user_context (INACTIVE → 403)
  ActionAgentService.propose
    active + ≥1 action capability (before any LLM call)
    LLM → ActionProposal (structured; action enum + name/id HINTS; extra fields → AI_INVALID_OUTPUT)
    ACTION_REGISTRY[action] → fixed capability, method, route, params schema
    run_authorized(ctx, spec.capability)                      capability check
      TargetResolver (reads only, scope-filtered BEFORE matching; ambiguity → clarification)
      require(ctx, capability, TargetEmployee(owner))          target-level check (defence in depth)
      typed params validation (shape only; business rules stay in the API)
      ConfirmationService.create → ai_pending_actions (token hash, binding hash, TTL)
  → {status: clarification_required | confirmation_required, summary, confirmation_id, expires_at}

POST /chat/actions/confirm {confirmation_id}            (extra fields → 422)
  ActionAgentService.confirm
    load_valid: same user, PENDING, not expired, binding hash intact (else AI_CONFIRMATION_INVALID)
    run_authorized(ctx, capability, TargetEmployee) again (permissions may have changed)
      claim: UPDATE … SET status='EXECUTING' WHERE id=? AND status='PENDING' (single use) + commit
      HRMSActionClient → existing endpoint via in-process ASGI, Authorization: Bearer <caller's own token>
      existing get_current_user / role checks / validation / DB logic   ← authoritative
  → {status: completed | failed (business error code + message)}
```

**Package** `backend/app/services/ai/actions/`:

| Module | Contents |
|---|---|
| `models.py` | `ActionName`, `ActionProposal` (untrusted), typed params mirroring the API schemas, `PreparedAction`, `ActionResponse` |
| `registry.py` | `ACTION_REGISTRY`: 10 frozen specs, validated at import (method ∈ {POST, DELETE}, `/api/v1/` path, not on the deny-list, confirmation required, name == capability) |
| `resolver.py` | `TargetResolver`: employee, assignee, pending leave, ticket and project lookup restricted to the capability's target rule |
| `validators.py` | `prepare_action`: resolution + target `require()` + params validation + human summary |
| `confirmation.py` | `ConfirmationService`: the only writer in the package, and only to `ai_pending_actions` |
| `client.py` | `HRMSActionClient`: one typed method per action, one registry-bound `_send` |
| `prompts.py` | `ACTION_SYSTEM_PROMPT`; the user turn lists only the role-allowed actions |
| `service.py` | `ActionAgentService`, `get_action_agent_service` |

**Backend integration and identity propagation.** The client calls the running app in-process (`httpx.ASGITransport(request.app)`), using the *caller's own bearer token* (taken from the request by the existing `oauth2_scheme`).
- Every mutation passes through the real route: its own `get_current_user`, role checks, validation and DB code.
- There is no service account, no admin token and no duplicated business logic.
- Tests prove the identity end to end: `approver_id` and `author_id` are the caller.
- The outer request commits (releasing SQLite locks) before the inner call.

**No direct writes.** The agent has no write path to business tables:
- The resolver only SELECTs.
- The confirmation repository writes only `ai_pending_actions`.
- Static tests scan the package for write calls, commits and HTTP imports outside `client.py`.
- Behavioural tests compare business-table hashes before and after every denied, clarified or failed action.

**Retries / idempotency.** Mutations are never retried. A timeout or 5xx returns `AI_BACKEND_UNAVAILABLE` and marks the confirmation FAILED. Stale state, such as a leave approved meanwhile, is rejected by the existing API (`LEAVE_NOT_PENDING`), and the token is single-use.

**Audit-ready.** `ActionAuditRecord` (user, role, stage, action, target, resource, confirmation record id, outcome, error code, timestamp) is logged as `ai.action`. `ai_pending_actions` keeps the status, result code and used_at of every confirmation. Neither holds tokens, request bodies or personal data values. Phase 8 will persist these records in `ai_audit_logs`.

**Not supported (no backing API).** Editing or cancelling a leave request. A rejection reason (the existing reject endpoint takes no body). Reactivation (not requested by the assignment).

#### Phase 0 design notes (historical)
- Only role-filtered tool schemas are sent to the LLM (tool use / function calling).
- LLM output is validated against Pydantic arg models. Invalid output leads to a clarification question; the agent never guesses IDs.
- Entity resolution (e.g. "Employee User", "HR Policy Copilot") uses read tools such as `find_employee` or a scoped SQL lookup, never free text straight into an ID.
- **Propose:** the permission check runs, then an `ai_pending_actions` row is stored (user_id, tool, validated args, expires_at, status) and an `ActionProposal` with a `confirmation_id` and human-readable summary is returned.
- **Confirm:** `POST /chat/actions/confirm {confirmation_id, decision}`. The server re-checks the user, TTL, status and permissions, then makes the httpx call to the existing endpoint with the user's token. The timeout defaults to 10 s. Only idempotent GETs are retried, and mutations are never auto-retried. The call's result is summarized for the user.
- Backend error envelopes (403/404/409/400) are mapped to user-friendly messages. A 403 from the existing API is treated as authoritative.
- **Transport:** an `httpx.AsyncClient` with `ASGITransport(app)` in-process by default, or a configurable `AI_BACKEND_BASE_URL` for out-of-process. The outer AI request must not hold an open write transaction during the call, because of the SQLite lock (Audit R3).

### 3.4 Router / Orchestration (Phase 6, implemented)

Full design: [architecture/copilot-router.md](architecture/copilot-router.md).

- `POST /api/v1/chat` → `CopilotService` → LangGraph `StateGraph(CopilotState)`. The graph runs
  `classify → policy | sql | action | clarification | refusal | error → finalize`, with conditional edges only
  from `ALLOWED_TRANSITIONS`. Edges only go forward and no node runs twice.
- Limits: `MAX_GRAPH_ITERATIONS=5`, `recursion_limit=10`. There is no checkpointer, so state is request-scoped.
- Classification order: refusal rules, then clause split, then deterministic signals, then the structured
  fast-model fallback (`RouterClassification`, `extra="forbid"`), but only for clauses without a signal. Failure
  is closed: `AI_UNAVAILABLE` or `AI_CLASSIFICATION_FAILED`, never a guessed route.
- Nodes call only `PolicyRagService.answer`, `SQLAgentService.answer` and `ActionAgentService.propose`, with the
  trusted `ctx` from `config["configurable"]["deps"]`. Routing is not authorization: each service re-authorizes.
- Multi-intent runs POLICY → SQL → ACTION with at most one action. Ambiguity or two mutations produce a
  clarification.
- The Phase 0 design (a separate permission check after classification) was not needed. The capability checks
  stay inside each service, so the router cannot drift from them.

### 3.4.1 Conversations (Phase 7, implemented)

Full design: [architecture/phase-7-conversation-memory.md](architecture/phase-7-conversation-memory.md).

- Owner-only conversations (`ai_conversations`, `ai_conversation_messages`, migration 0019). Every lookup is scoped
  by the caller; unknown/malformed/archived/foreign ids are an identical 404.
- Per turn: owner check → bounded untrusted context (last 6 messages, ≤ 2000 chars) → USER message stored → router
  (`answer_turn` with context) → ASSISTANT message stored (or a safe error row). Sequence is allocated atomically.
- Follow-ups: deterministic detection; route = the message's own rule signal, else the previous route, else the LLM
  with delimited history. Services receive only user text (earlier question + current message); assistant text
  is never forwarded. History is context, never authorization and never truth.

### 3.5 Audit and observability (Phase 8, implemented)

Full design: [architecture/phase-8-audit-observability.md](architecture/phase-8-audit-observability.md). It
supersedes the Phase 0 notes below.

- `AIAuditMiddleware` gives every `/api/v1/chat*` and `/api/v1/ai*` request one `request_id` (`X-Request-Id`). AI
  code records typed, allowlisted events in memory, and they are flushed to the append-only `ai_audit_events`
  table after the response (fail-open to the structured log). `ACTION_CONFIRMED` is written before execution
  (fail-closed).
- No prompts, answers, SQL, rows, tokens or PII are stored: lengths, keyed fingerprints, codes, routes and
  counts only.
- Provider calls go through `observe()` (provider, model, latency, tokens, estimated cost when priced).
- Read API: `GET /api/v1/ai/audit` and `/api/v1/ai/audit/metrics`, using AUDIT_READ_ORG / AUDIT_READ_OWN.
- Evaluation: `python -m evals.run_evals` (offline, throwaway DB; `--live` needs a key).

#### Phase 0 design notes (historical)
- `ai_audit_logs` stores `id, user_id, role, message (redacted, truncated to 2000), intent, agent, tool_name, action_status, records_accessed (JSON ids/counts), latency_ms, error_code, created_at`.
- Writes use their own session and commit, after the response is built. A failure is logged and never breaks the user response.
- Redaction removes JWT-like strings, PAN patterns, 9–18 digit account numbers and IFSC codes before persistence and before structlog.

### 3.6 Phase 10: hardening, modes and dashboard (implemented)

- **Role-specific Copilot modes:** Employee, Manager and Admin. The title, focus areas and example prompts come from
  the backend's `GET /chat/permissions` capabilities (`lib/copilot-display.ts::copilotMode`). This is UX only.
- **AI usage dashboard:** `/admin/ai-usage`. The metrics endpoint adds intents, tools, actions by type, requests by
  role, RAG no-answer rate, action confirmation rate and action execution success. All of it is derived from the
  existing audit events (no new store), and the endpoint is admin-only (AUDIT_READ_ORG). Audit events now record the
  actor's role (`actor_role`) as historical metadata.
- **Production checks:** `app/core/production_checks.py`. With `ENVIRONMENT=production`, startup refuses the default
  or weak `JWT_SECRET_KEY`, fake AI providers, and a missing key or model for the selected provider.
- **Formal evaluation set:** `evals/assignment_eval.json` (the assignment's prompts plus injection cases), run by
  `python -m evals.run_evals`.
- **Streaming:** not implemented. FastAPI 0.116 closes `yield` dependencies (the request DB session) before a
  streaming body runs. See [architecture/phase-10-gap-analysis.md](architecture/phase-10-gap-analysis.md) §3.

**Models and providers.**
- **LLM:** Anthropic Claude through `AnthropicLLMProvider`, or OpenAI through `OpenAILLMProvider` (`AI_LLM_PROVIDER=openai`).
  - `AI_LLM_MODEL` (default `claude-sonnet-5-5`) generates answers, SQL and action proposals.
  - `AI_LLM_FAST_MODEL` (default `claude-haiku-4-5-20251001`) classifies routes.
- **Embeddings:** local `fastembed` with `BAAI/bge-small-en-v1.5`; there is no embeddings API.
- **Test and eval implementations:** `FakeLLMProvider`, `FakeEmbeddingProvider`, `ExtractiveFakeLLM` and scripted
  multi-schema fakes. They are refused in production.

## 4. API contracts (all return the standard envelope)

| Endpoint | Request | `data` on success |
|---|---|---|
| POST `/api/v1/chat/policy` ✅ | `{question}` (alias `{message}`) | `{answer, grounded: bool, sources:[{chunk_id,title,category,filename,section}]}` |
| GET `/api/v1/chat/permissions` ✅ | — | `{employee_id, role, capabilities[], data_scope}` |
| POST `/api/v1/chat/sql` ✅ | `{question}` (alias `{message}`) | `{answer, scope, columns, rows, row_count, truncated, sql?}` (`sql` only with SQL_VIEW_RAW) |
| POST `/api/v1/chat/actions` ✅ | `{question}` (alias `{message}`) | `{status: "clarification_required"\|"confirmation_required", message, action?, summary?, confirmation_id?, expires_at?}` |
| POST `/api/v1/chat/actions/confirm` ✅ | `{confirmation_id}` only (extra fields → 422) | `{status: "completed"\|"failed", message, action, summary, result?, error_code?}` |
| POST `/api/v1/chat` ✅ | `{message, conversation_id?}` (alias `{question}`; extra fields → 422) | `{conversation_id, follow_up, request_id, status: answered\|clarification_required\|confirmation_required\|refused\|partial, code?, intents[], routes[], classification_source, message, parts:[{route, status, message, data?, error_code?}]}`. Single-route service errors keep the service's code/status (+ `X-Conversation-Id` header). |
| GET `/api/v1/chat/conversations` ✅ | `?limit` (≤ 50) | own active conversations `[{conversation_id, title, message_count, created_at, updated_at}]` |
| GET `/api/v1/chat/conversations/{id}` ✅ | `?limit` (≤ 200) | own conversation + most recent messages `[{sequence, role, content, route?, status?, error_code?, created_at}]` |
| DELETE `/api/v1/chat/conversations/{id}` ✅ | — | archive own conversation `{conversation_id, status: ARCHIVED}` |
| GET `/api/v1/ai/audit` ✅ | `?limit (≤100)&offset&event_type&status&route&request_id&conversation_id&employee_id (ADMIN)&created_from&created_to` | `{items:[{event_id, request_id, sequence, conversation_id, employee_id, event_type, status, route, duration_ms, metadata, created_at}], meta:{total, limit, offset}}`. ADMIN: all; others: own only. |
| GET `/api/v1/ai/audit/metrics` ✅ | `?created_from&created_to` | ADMIN only: request/refusal/clarification/denial/action/provider counts, latency `{count, avg, p95, max}`, tokens, estimated cost (null unless priced) |

Errors: `{"success": false, "data": null, "error": {"code": "AI_…", "message": "…"}}` with an appropriate HTTP status (401/403/422/429/502/503). There are no stack traces, SQL errors, prompts or tokens.

Browser clients: CORS exposes `X-Request-Id` and `X-Conversation-Id` (Phase 9) so the frontend can show support
references and recover the conversation of a failed first turn.

## 5. Configuration (env vars; implemented ones marked ✅)

```text
AI_ENABLED=true                                 ✅
AI_LLM_PROVIDER=anthropic                       ✅ anthropic | fake
AI_LLM_MODEL=claude-sonnet-5-5                  ✅ (D1)
AI_LLM_FAST_MODEL=claude-haiku-4-5-20251001     ✅ router classification (Phase 6; no effort/fallback beta)
ANTHROPIC_API_KEY=                              ✅ SecretStr, env only, never committed
AI_TEMPERATURE=                                 ✅ unset by default (Claude 5.x rejects non-default sampling)
AI_MAX_TOKENS=4096                              ✅
AI_LLM_EFFORT=low                               ✅ output_config.effort
AI_LLM_SERVER_FALLBACKS=true                    ✅ server-side refusal fallback ("default")
AI_REQUEST_TIMEOUT_SECONDS=30                   ✅
AI_EMBEDDING_PROVIDER=fastembed                 ✅ fastembed | fake
AI_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5       ✅
AI_EMBEDDING_CACHE_DIR=/app/storage/models      ✅
AI_RAG_TOP_K=4                                  ✅
AI_RAG_MIN_SCORE=0.35                           ✅
AI_POLICY_CHUNK_SIZE=800                        ✅
AI_POLICY_CHUNK_OVERLAP=120                     ✅
AI_SQL_MAX_ROWS=100                             ✅ Phase 4
AI_SQL_MAX_JOINS=4                              ✅ Phase 4
AI_SQL_TIMEOUT_SECONDS=3                        ✅ Phase 4
AI_ACTION_CONFIRMATION_TTL_SECONDS=300          ✅ Phase 5 (in-process ASGI is the only transport; no base-URL setting was needed)
AI_CONVERSATION_MAX_MESSAGES=200                ✅ Phase 7 per-conversation message limit
AI_AUDIT_ENABLED=true                           ✅ Phase 8 persist audit events (false: structured log only, no fail-closed)
AI_LLM_INPUT_COST_PER_MTOK= / _OUTPUT_          ✅ Phase 8 optional USD/MTok for AI_LLM_MODEL (estimated cost; unset → null)
AI_LLM_FAST_INPUT_COST_PER_MTOK= / _OUTPUT_     ✅ Phase 8 optional USD/MTok for AI_LLM_FAST_MODEL
AI_CONVERSATION_CONTEXT_MESSAGES=6              ✅ Phase 7 messages read for follow-up context
AI_CONVERSATION_CONTEXT_CHARS=2000              ✅ Phase 7 classifier history budget
AI_CONVERSATION_STORED_MESSAGE_CHARS=4000       ✅ Phase 7 stored message cap (after redaction)
```

The Phase 0 names `POLICY_TOP_K`/`POLICY_MIN_SCORE` from the Phase 3 prompt map to the Phase 1 names `AI_RAG_TOP_K`/`AI_RAG_MIN_SCORE`, keeping the established `AI_` prefix convention.

If `AI_ENABLED=false` or the provider key is missing, AI endpoints return `503 AI_UNAVAILABLE` and the rest of the app is unaffected.

## 6. Dependencies

| Package | Purpose | Status |
|---|---|---|
| `anthropic==1.9.0` | LLM provider (requires Python ≥ 3.10) | ✅ Phase 3 |
| `openai==3.23.0` | Alternative LLM provider (Responses API); shares `httpx2` with `anthropic` | ✅ 2026-10-16 |
| `fastembed==0.8.1` + `onnxruntime==1.22.1` | local embeddings (pinned ORT supports Python 3.11/3.12) | ✅ Phase 3 |
| `pytest`, `pytest-asyncio`, `httpx` | tests (requirements-dev.txt) | ✅ Phase 1 |
| `sqlglot==30.20.0` | SQL parsing/qualification/rewriting (Python ≥ 3.9, no deps) | ✅ Phase 4 |
| `httpx==0.28.1` | in-process calls to the existing API (moved from dev-only to runtime requirements) | ✅ Phase 5 |
| `langgraph==1.2.12` | orchestration state machine (Python ≥ 3.10; brings langchain-core transitively; only `RunnableConfig` is imported) | ✅ Phase 6 |

Cosine similarity is computed in pure Python (corpus is small), so numpy is not a direct dependency. It arrives transitively with fastembed.
