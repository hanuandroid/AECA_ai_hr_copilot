# Phase Status

Current status (2026-10-20): see the end of this file and
[`production-hardening/capstone-completion-progress.md`](production-hardening/capstone-completion-progress.md).
Release status: **NOT YET PRODUCTION READY**. Latest fresh results (2026-10-20, local): backend 3190 passed (1 opt-in live test skipped) (3208 passed, 1 skipped after the live-harness tests were added; `verify-backend.sh`, 2026-10-21), offline evaluations 139/139 (scripted model output), CI gate tests 25 passed, frontend Vitest 277 passed at `6338adc` (268 on 2026-10-20 before the sidebar tests), TypeScript 0 errors, build PASS, Playwright E2E 152 passed (0 failed, 0 skipped, 0 flaky; isolated stack, fake AI providers), gitleaks clean (`docs/capstone-submission-report.md`). The manual browser
walkthrough has **not** been run. Live-model evaluation (2026-10-20, OpenAI `gpt-4o-mini`, one run): router 33/33, policy 11/11; live SQL/action quality not measured. GitHub Actions through `859531f` (2026-10-21): every job passed except the dependency-advisory gate (`braces`, build-time; Pillow 12.3.0, runtime; no fix or exception approved). (2026-10-19 at `165e96e`: 3172 backend, 238 frontend.) (The 2026-10-03 "ready for final submission" note, with 1685 backend and 86
frontend tests, is historical.)

---

Phase: 0 — Repository Audit & Engineering System
Status: COMPLETE (awaiting approval to start Phase 1)

Objectives:
- Audit the existing CB Nest repository without modifying application code.
- Create the Claude Code engineering system (CLAUDE.md, rules, skills, agents).
- Produce the design docs: implementation plan, architecture, permissions matrix, eval plan, threat model.

Implemented:
- docs/repository-audit.md
- docs/ai_implementation_plan.md, ai_architecture.md, ai_permissions_matrix.md, ai_eval_plan.md, ai_threat_model.md
- CLAUDE.md, .claude/rules/*, .claude/skills/*, .claude/agents/* (at workspace root)

Tests:
- None. The repository has zero existing tests (audit §10). Phase 1 adds the test harness and characterization tests.

Known Issues:
- R1: GET /employees/{id} returns bank/PAN/salary to any authenticated user (pre-existing; decision D4).
- R5: Policy corpus too thin for mandatory RAG questions (decision D3).
- R6: .gitignore ignores *.md, so new docs would not be committed (decision D5).
- Workspace is not a git repository, so there is no diff/commit workflow yet.
- Open decisions D1–D6 (see ai_implementation_plan.md).

Security Review:
- No code changed. The threat model is documented in ai_threat_model.md.

Files Changed:
- New docs and Claude config only. No application files modified.

Next Phase:
- Phase 1: AI foundation + test harness + characterization tests. Blocked on D1/D2 (provider choices).

---

Phase: 1 — Foundation, Test Harness and Characterization Tests
Status: COMPLETE (awaiting approval to start Phase 2)

Objectives:
- Operational pytest harness on an isolated temp SQLite DB built by the real Alembic migrations.
- Real-auth fixtures for admin, manager, team employee, non-team employee, inactive user.
- Characterization tests for every existing endpoint the AI will wrap, including authorization quirks and R1.
- Provider-neutral LLM/embedding interfaces with deterministic fakes, and env-backed AI configuration.

Implemented:
- Test infrastructure: backend/pytest.ini, backend/requirements-dev.txt (pytest 8.4.2, pytest-asyncio 1.2.0, httpx 0.28.1),
  tests/conftest.py, tests/support/{data,http}.py.
  Flow: temp dir → env (DATABASE_URL, upload dirs, JWT secret, fake AI providers) set before app import →
  alembic upgrade head → deterministic seed → template snapshot → template restored before every test →
  engine disposed after every test → temp dir deleted at session end.
- AI config: core/config.py gains ai_* settings plus anthropic_api_key (SecretStr, default None); .env.example has placeholders only.
- AI foundation (services/ai/): errors.py, providers.py (fail-closed factories), llm/base.py (LLMProvider:
  generate, generate_structured), llm/fake.py (FakeLLMProvider), embeddings.py (EmbeddingProvider: embed;
  FakeEmbeddingProvider; cosine_similarity), guards.py (sensitive field set, forbidden-route deny-list incl. R1,
  strip_sensitive_fields), redaction.py.
- Decisions D1–D7 recorded in ai_implementation_plan.md; D7 added to ai_architecture.md.

Tests: 132 passed, 0 failed (≈6 s, offline, no API keys, no model downloads)
- tests/test_infrastructure.py (4): database isolated, migrations run (head 0016), seed isolated between tests, dev DB untouched
- tests/characterization/test_auth.py (12): employee/manager/admin authentication, inactive user NOT rejected (R9),
  bad password/unknown user/missing/garbage/wrong-signature/expired token, role from DB not token, deleted subject
- tests/characterization/test_leaves.py (27): create/validation/overlap/half-day/admin auto-approve, pending list,
  approve/reject by role, outside-team approval (R4), non-pending, missing, insufficient balance, own reads
- tests/characterization/test_tickets.py (16): create by role, enum validation, list scoping, assign by role, status by
  owner/outsider/manager, invalid status, not found
- tests/characterization/test_announcements.py (5): create by role, validation
- tests/characterization/test_employees.py (22): project assignment (role, outside team R4, upsert, not found),
  project catalog (role, duplicate, invalid status), deactivation (role, guards), directory fields, R1 exposure
- tests/ai/test_providers.py (16): fake LLM, fake embeddings, config defaults/env, factories fail closed, no vendor SDK loaded
- tests/ai/test_guards.py (30): forbidden routes (R1 etc.), allowed routes, sensitive-field stripping,
  forbidden-list coverage, static scans (no forbidden routes / no endpoint imports in AI code), redaction
Run: cd HRMS-main/backend && .venv/Scripts/python -m pytest -q    (Docker: docker-compose exec api pytest -q
after installing requirements-dev.txt)

Known Issues:
- `test_inactive_user_rejected` (requested name) cannot be satisfied by the existing auth without modifying
  services/auth.py (forbidden in Phase 1). Current behaviour is characterized as
  `test_inactive_user_not_rejected_by_existing_auth`. The rejection test lands with AIUserContext in Phase 2.
- Anthropic and fastembed concrete providers are not wired yet (Phase 3). The factories return AI_UNAVAILABLE for them.
- Tests ran on local Python 3.12; the Docker image is 3.11 (not verified here, Docker unavailable locally).
- D3 policy corpus enrichment is scheduled with Phase 3.
- Workspace is still not a git repository (D5: do not auto-init).

Security Review:
- No business endpoint, services/auth.py, core/security.py or migration was modified.
- R1 documented by test and by threat model TODO(R1). The AI deny-list and a static scan prevent AI use of the route.
- R4/R9 characterized. The AI layer will be stricter (D7).
- No secrets added. anthropic_api_key is a SecretStr (not shown in repr). .env.example has empty key placeholders.
- Tests never touch storage/hrms.db (asserted) and make no network calls.

Files Changed:
- Modified (additive): backend/app/core/config.py, backend/.env.example
- New: backend/pytest.ini, backend/requirements-dev.txt, backend/app/services/ai/{__init__,errors,providers,guards,
  redaction,embeddings}.py, backend/app/services/ai/llm/{__init__,base,fake}.py, backend/tests/** (11 files)
- Docs: phase-status.md, ai_implementation_plan.md, repository-audit.md (§14), ai_architecture.md, ai_threat_model.md
- Local only (git-ignored): backend/.venv

Next Phase:
- Phase 2: AIUserContext (reject INACTIVE, team_ids = direct reports), deterministic permissions.py from the
  matrix, `get_ai_context` dependency, table-driven permission tests.

---

Phase: 2 — AI Authorization and Permission Boundary
Status: COMPLETE (awaiting approval to start Phase 3)

Objectives:
- One authoritative, deterministic AI authorization layer used by all future AI capabilities.
- AI authorization is stricter than existing HRMS authorization. AI authorization must never be more permissive (D7).

Repository vs prompt discrepancies (documented, resolved using the repo):
- No separate User model: Employee is the user (employee_id is the user id). No organisation concept (single tenant),
  so AIUserContext has no organization_id.
- Active flag is Employee.status (ACTIVE/INACTIVE), not a boolean.
- There is no "pf_number" column. The repo has payroll_records.pf_uan/esi_no/pan/deductions and employees.phone/address/
  blood_type/profile_photo_*. All are in the sensitive policy, and "pf_number" is caught by token matching.
- Draft matrix deviations (stricter, per assignment matrix + D7): EMPLOYEE TICKET_STATUS_UPDATE → denied;
  MANAGER PROJECT_CREATE → denied. Added capabilities: DIRECTORY_READ, SQL_VIEW_RAW, PAYROLL_READ_ORG,
  AUDIT_READ_OWN/ORG. Manager ticket scope → TEAM (Phase 0 design said ALL).
- Suggested file names models.py/permissions.py → capabilities.py/policy.py (clearer split). Same single package.

Implemented:
- backend/app/services/ai/authorization/: capabilities.py (23 capabilities, target rules, scope levels),
  policy.py (immutable AI_PERMISSION_MATRIX), context.py (frozen AIUserContext from DB, get_ai_user_context
  dependency rejecting INACTIVE), scope.py (TargetEmployee, target rules, DataScope, hr/sql read scope),
  sensitive.py (schema-exact + token-based sensitive-field policy, table-aware column check),
  service.py (authorize / require / run_authorized, generic denials, structured deny reasons).
- errors.py: AIInactiveUserError (AI_USER_INACTIVE, 403), ai_error_response.
- guards.py: now uses the central sensitive-field policy (one definition; also catches aliases).
- chat.py: GET /api/v1/chat/permissions (caller's capabilities + data scope; read-only, no LLM).
- main.py: exception handler for AIError only (existing error shapes unchanged; tested).

Final matrix: see ai_permissions_matrix.md §0 (mirrors policy.py; test_matrix.py holds an independent copy).

Tests: 473 total, 473 passed, 0 failed, 0 skipped (≈10 s, offline)
- Phase 1 suites: 132, all still passing (no assertions changed).
- Phase 2 (tests/ai/authorization/): 341
  - test_matrix.py (139): every role × capability, inactive denied for every cell, immutability, least privilege,
    documented stricter-than-API cases
  - test_context_and_scope.py (38): context from DB, immutability, no untrusted inputs, team scoping (manager own team /
    outside team / self-approval, employee → other, admin org / self), existence-oracle parity, data scopes
  - test_sensitive.py (125): exhaustive schema classification (employees, payroll_records), metadata columns,
    prompt-required names + aliases, no role reads sensitive columns, payroll admin-only
  - test_bypass.py (27): prompt manipulation, LLM `authorized: true`, non-context/str capability/dict resource rejected,
    inactive & unauthorized → fake LLM call count 0, generic non-leaking denials
  - test_permissions_api.py (12): per-role capabilities over HTTP, test_inactive_user_rejected, unauthenticated,
    frontend claims (cookie/headers/query) ignored, forged JWT role ignored, no sensitive values, existing error shape unchanged
- Mutation check: widening LEAVE_APPROVE failed 13 tests; removing the inactive check in the dependency failed
  the API test (authorize() still denies independently).

Known Issues:
- Existing HRMS still has R1/R4/R9 by design of this phase (not fixed in business code; the AI layer does not inherit them).
- The AIError handler is registered with a sync lambda; fine for Starlette, but may be turned into an async function
  when chat endpoints grow (Phase 7).
- Direct reports only (single level). Skip-level managers get no access to indirect reports. This is a deliberate, stricter choice.
- Python: verified on 3.12 locally. The Docker image is 3.11. The new code uses only ≤3.10 syntax (dataclass slots,
  X | None); datetime.UTC (3.11+) was already used by core/security.py. Docker/3.11 remain UNVERIFIED
  (Docker and Python 3.11 are not installed here).

Security Review:
- No change to services/auth.py, core/security.py, business route handlers, migrations or frontend.
- Authorization is independent of LLM output, prompts, tool args, documents and frontend claims (tests above).
- No secrets, no production AI calls, no model downloads.

Files Changed:
- New: backend/app/services/ai/authorization/{__init__,capabilities,policy,context,scope,sensitive,service}.py,
  backend/tests/ai/authorization/{__init__,conftest,test_matrix,test_context_and_scope,test_sensitive,test_bypass,
  test_permissions_api}.py
- Modified (additive): backend/app/services/ai/errors.py, backend/app/services/ai/guards.py,
  backend/app/api/v1/endpoints/chat.py, backend/app/main.py
- Docs: phase-status.md, ai_implementation_plan.md, ai_architecture.md, ai_threat_model.md,
  ai_permissions_matrix.md, CLAUDE.md, .claude/rules/*, .claude/agents/*, .claude/skills/agent-tool-calling
- Encoding hygiene: removed UTF-8 BOMs that PowerShell had added to 2 Phase 1 test files (no content change).

Next Phase:
- Phase 3: Policy RAG (Anthropic provider + fastembed wiring, D3 demo policy corpus, policy_chunks, /chat/policy),
  every call guarded by run_authorized(ctx, POLICY_READ, ...).

---

Phase: 3 — Policy RAG
Status: COMPLETE (awaiting approval to start Phase 4)

Objectives:
- Grounded, cited answers to HR policy questions through POST /api/v1/chat/policy, behind the Phase 2
  authorization boundary, with policy documents treated as untrusted data.

Repository vs prompt discrepancies (resolved using the repo):
- No `backend/seed.py`: the seed is `backend/scripts/seed.py`.
- No separate document table is needed: one hr_policies row == one document, so policy_chunks references hr_policies.
- Config names follow the Phase 1 `AI_` convention: POLICY_TOP_K/POLICY_MIN_SCORE → AI_RAG_TOP_K/AI_RAG_MIN_SCORE;
  POLICY_CHUNK_SIZE/OVERLAP → AI_POLICY_CHUNK_SIZE/AI_POLICY_CHUNK_OVERLAP.
- The seeded Attendance one-liner says 9:30 AM, but code enforces 09:00 → the demo policy follows code (repository-audit §14).
- Policy titles come from hr_policies (e.g. "WFH Policy"), not the markdown H1.
- The request accepts `question` (Phase 3 spec) and `message` (assignment example).
- Sources use the assignment's `filename` key plus `chunk_id`, `section`, `title`, `category`.

Implemented:
- D3 content: backend/data/policies/{leave,attendance,wfh,document_upload,data_privacy}_policy.md (demo-marked).
  scripts/seed.py uses them and adds 2 policies (24 total), and clears policy_chunks first.
- Migration 0017_policy_chunks (reversible; upgrade/downgrade/upgrade tested) + models/policy_chunk.py.
- services/ai/rag/: models, loader (content/file, path-guarded, md/txt/pdf), chunker (heading → paragraph → size,
  sentence fallback, overlap, stable chunk ids, index_key), vector_store (PolicyVectorStore protocol +
  SQLitePolicyVectorStore), ingest (idempotent, stale removal), retriever (hybrid cosine + IDF lexical, threshold,
  relative cutoff, top-k, deterministic), prompts (fixed system prompt, escaped delimited context), guard
  (deterministic), service (PolicyRagService, run_authorized first, lazy providers).
- Providers: AnthropicLLMProvider (beta.messages.parse structured output, effort, server-side refusal fallback,
  no temperature by default, error mapping, no prompt/key logging); FastEmbedEmbeddingProvider (lazy, threaded);
  EmbeddingProvider gains embed_query + model_id; provider caches.
- POST /api/v1/chat/policy; scripts/ingest_policies.py; evals/policy_eval.json + evals/policy_rag_eval.py.
- Config: AI_MAX_TOKENS 4096, AI_LLM_EFFORT, AI_LLM_SERVER_FALLBACKS, AI_EMBEDDING_CACHE_DIR, AI_POLICY_CHUNK_SIZE/
  OVERLAP. AI_TEMPERATURE is now optional/unset (was 0.0; Claude 5.x rejects non-default temperature).
- Deps: anthropic==1.9.0, fastembed==0.8.1, onnxruntime==1.22.1.

Tests: 614 total, 614 passed, 0 failed, 0 skipped (~32 s, offline)
- Phase 1: 131 (was 132; one parametrized case moved, see Known Issues). Phase 2: 341. Phase 3: 142.
- Phase 3 files: chunking+loading 13, ingest+store 9 (incl. migration downgrade/upgrade), retrieval 18,
  prompt+guard 27, service 27, injection+sensitive 14, API 14, providers 17, eval 3.
- Mutation checks: bypassing run_authorized → 2 failures; disabling the number guard → 3; unescaped document text → 1.
- End-to-end smoke (temp DB): alembic head 0017 → scripts/seed.py (24 policies) → ingest (24 docs, 40 chunks)
  → ingest again (24 unchanged, 0 written).
- Eval (ExtractiveFakeLLM, 11 cases): retrieval_hit 1.0, citation_validity 1.0, groundedness 1.0, refusal 1.0.

Changed Phase 1 tests (behaviour intentionally changed; no assertion weakened):
- test_infrastructure: expected Alembic head 0016 → 0017 and policy_chunks added to the expected tables.
- test_providers: the `fastembed → AI_UNAVAILABLE` case is replaced by test_fastembed_provider_is_lazy (the provider is
  now wired). test_no_vendor_sdk_loaded_by_ai_foundation now runs in a subprocess
  (test_no_vendor_sdk_loaded_by_app_import) because Phase 3 tests legitimately import anthropic in-process, and it now
  also checks that fastembed/onnxruntime are not loaded at app import.
- conftest: the per-test reset also restores policy files (RAG tests modify/delete them).

Known Issues / Limitations:
- **Real providers not exercised locally.** No Anthropic key is configured (by design, tests are offline). fastembed's
  onnxruntime cannot load on this Windows machine: the VC++ runtime is 14.29 and newer ORT needs the 2015-2022
  redistributable. This is an environment issue, not a code issue; Linux/Docker is unaffected. The provider fails closed
  (AI_UNAVAILABLE). To use it on Windows, install the latest "Microsoft Visual C++ Redistributable (x64)".
- AI_RAG_MIN_SCORE=0.35 was calibrated against the fake embeddings only. It must be re-checked with fastembed vectors
  (run evals/policy_rag_eval.py with real providers in Docker, Phase 10). Example: "maternity leave" scores 0.37 with
  fake embeddings, so that case is refused by grounded=false/guard rather than by retrieval.
- New or changed policy uploads are not indexed automatically: run scripts/ingest_policies.py (the upload endpoint
  was not modified, by design).
- Guard heuristics are English, regex-based and conservative. They may reject some legitimate answers (e.g. numbers
  written as words are not checked; paraphrased numbers such as "ten" pass unchecked).
- No conversation memory (single-turn), no streaming (later phases).
- Docker / Python 3.11 still UNVERIFIED (not installed here). New deps declare Python ≥3.10, and the pinned ORT 1.22.1
  has cp311 wheels.

Security Review:
- Flow verified: User → JWT auth → AIUserContext (INACTIVE rejected) → POLICY_READ → RAG. Never User → LLM → RAG.
- Policy document ≠ system instruction (constant system prompt; escaped, delimited context).
- LLM output ≠ authorization decision (context immutable; authorize() has no LLM input).
- Policy RAG ≠ employee data access (index built only from hr_policies; guard blocks sensitive values).
- No change to services/auth.py, core/security.py, business route handlers, migrations 0001-0016 or the frontend.
- No secrets committed (.env.example placeholders only). No production AI calls in tests.

Files Changed:
- New: backend/app/services/ai/rag/{__init__,models,loader,chunker,vector_store,ingest,retriever,prompts,guard,service}.py,
  backend/app/services/ai/llm/anthropic_provider.py, backend/app/models/policy_chunk.py,
  backend/alembic/versions/0017_policy_chunks.py, backend/data/policies/*.md (5), backend/scripts/ingest_policies.py,
  backend/evals/{__init__.py,policy_eval.json,policy_rag_eval.py}, backend/tests/ai/rag/* (10 files)
- Modified: backend/app/core/config.py, backend/.env.example, backend/requirements.txt,
  backend/app/services/ai/{embeddings,providers,errors,redaction}.py, backend/app/models/__init__.py,
  backend/app/schemas/chat.py, backend/app/api/v1/endpoints/chat.py, backend/scripts/seed.py,
  backend/tests/{conftest.py,support/data.py,test_infrastructure.py,ai/test_providers.py}
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_implementation_plan.md, repository-audit.md, CLAUDE.md

Next Phase:
- Phase 4: SQL Agent (sqlglot guardrails, scope rewrite from sql_read_scope, read-only connection, /chat/sql).

---

Phase: 4 — Secure SQL Agent (Text-to-SQL)
Status: COMPLETE (awaiting approval to start Phase 5)

Objectives:
- Read-only natural-language HR data questions through POST /api/v1/chat/sql, with role/team scope enforced by the
  backend. The LLM is never an authorization authority, and SQL validation is enforced independently of the LLM prompt.

Repository inspection (actual schema, see ai_permissions_matrix.md §2):
- Queryable: employees (SCOPED; 9 allowed / 16 forbidden columns), job_history, leave_balances, leave_requests,
  tickets (owners employee_id + assignee_id), employee_projects, employee_skills (SCOPED), departments, skills,
  projects (CATALOG), payroll_records (PAYROLL: ADMIN only; id/employee_id/month/gross/net; pan/pf_uan/esi_no/
  deductions forbidden).
- Denied: attendance_logs, announcements, polls, poll_responses, holidays, onboarding_tasks, hr_policies,
  policy_chunks, employee_documents (+ sqlite_*, alembic_version).
- Team = employees.manager_id (direct reports). Status fields: employees.status, leave_requests.status,
  tickets.status, projects.status. Date fields: joining_date, start/end_date, created_at, month.

Prompt vs repository discrepancies / decisions:
- The prompt's "leaves" and "project assignments" tables are leave_requests/leave_balances and employee_projects.
- There is no `salary` column; the salary field is employees.current_salary_usd (forbidden). A payroll `salary` query
  fails as an unknown column → SQL_COLUMN_NOT_ALLOWED.
- EMPLOYEE `employees` rows are SELF (Phase 0 design said ALL directory rows). This is stricter, per the Phase 4 spec's
  "organization bypass → REJECT". employee_skills is also scoped now.
- SELECT * is always refused (explicit columns required), even on catalog tables. COUNT(*) is allowed.
- Out-of-scope intent is DENIED (SQL_SCOPE_VIOLATION) instead of silently narrowed. Narrowing still happens as defence
  in depth, through the scope rewrite.
- SQL_LIMIT_EXCEEDED is not raised: over-limit requests are clamped to AI_SQL_MAX_ROWS (documented behaviour).
- LLM output with extra fields (authorized/role/employee_id/scope) is rejected as AI_INVALID_OUTPUT (stronger
  than "ignore": nothing executes).

Implemented:
- backend/app/services/ai/sql/{schema,parser,validator,scope,executor,prompts,generator,formatter,models,service}.py
- services/ai/validation.py (question validation shared by RAG + SQL); errors.py (+SQL_* errors, internal `detail`);
  rag/guard.py (+claims_action_or_role shared helper)
- POST /api/v1/chat/sql (schemas/chat.py SQLQuestionRequest; accepts question|message)
- Config: AI_SQL_MAX_JOINS=4, AI_SQL_TIMEOUT_SECONDS=3 (AI_SQL_MAX_ROWS=100 existed). Dep: sqlglot==30.20.0.
- Eval: evals/sql_eval.json (20 cases) + evals/sql_agent_eval.py (parses/select-only, tables, scope, forbidden
  columns, result, refusal code, no mutation).
- No migration needed (no new persistence). Migrations 0001-0017 untouched.

Tests: 884 total, 884 passed, 0 failed, 0 skipped (~21 s offline; one earlier run took 56 s while mutation runs shared the CPU)
- Phase 1: 131, Phase 2: 341, Phase 3: 142 (all unchanged), Phase 4: 270
- Phase 4 files: schema_registry, parser_and_validator, scope_and_executor, sql_service, sql_api, sql_eval
- Security coverage (all tested): DELETE, UPDATE, INSERT, REPLACE, DROP, TRUNCATE, ALTER, CREATE, MERGE, DML-in-CTE,
  multiple statements, comment smuggling, PRAGMA/ATTACH/VACUUM/GRANT/EXEC/CALL/BEGIN, SELECT * / t.*, sensitive
  columns anywhere in the query, payroll table by role, unknown/denied tables, schema-qualified tables, table-valued
  functions, dangerous functions, bind parameters, unapproved/cartesian/USING joins, complexity limits, manager scope
  bypass, employee scope bypass, literal ids, OR bypass, forged role/employee id/authorized fields, prompt injection,
  DB-content injection into the formatter, sensitive-value redaction, LIMIT clamp, final-SQL tampering, timeout,
  read-only executor (writes blocked even without validation), DB immutability (hash before/after), HTTP envelopes.
- Required mutation checks (all caught): SELECT-only validator (16 failures) / root check (2); sensitive-column
  validator (19); manager TEAM rewrite (2) / TEAM intent (13); employee SELF rewrite (5); inactive-user
  authorization (70); row limit (44); authorization-before-LLM (1); read-only execution (13).
- SQL eval (fake generator): 20/20 cases pass; every metric = 1.0.

Known Issues:
- Implementation limitations:
  - The scope-intent check recognises specific predicate shapes (owner = :me, employees.manager_id = :me,
    IN (SELECT <owner key> ...), JOIN my_team, equality-join propagation in AND-conjuncts). Legitimate but differently
    phrased queries are refused rather than risk ambiguity (e.g. "who is my manager" via a self-join).
  - Team = direct reports only; skip-level managers see only direct reports.
  - The function allowlist is conservative; some harmless SQLite functions are refused.
  - The formatter guard is regex/number based; paraphrased numbers in words are not verified.
  - No query-plan cost estimation beyond joins/nesting/select limits + timeout.
- Environment limitations: Python 3.12 venv (Docker/3.11 unverified); fastembed native runtime unavailable on this
  Windows host (Phase 3).
- Production-validation limitations: no real Claude SQL generation has been evaluated (no API key; fake generator
  only). Real-model accuracy is measured in Phase 10. The prompt is guidance only; security does not depend on it.

Security Review:
- Verified flow: User → JWT → AIUserContext (INACTIVE rejected) → run_authorized(SQL_READ_SELF) → LLM → parse →
  validate → scope rewrite + LIMIT → re-validate → read-only execute → redact → format. SQL is never executed before
  validation, and the model's raw text is never executed.
- No business-table writes are possible (parser + read-only connection + authorizer; DB hash tests).
- No change to services/auth.py, core/security.py, business route handlers, migrations, frontend.
- No secrets committed. No production AI calls in tests.

Files Changed:
- New: backend/app/services/ai/sql/* (11 files), backend/app/services/ai/validation.py, backend/evals/sql_eval.json,
  backend/evals/sql_agent_eval.py, backend/tests/ai/sql/* (8 files)
- Modified: backend/app/services/ai/errors.py, backend/app/services/ai/rag/{service,guard}.py (shared validation /
  helper; no behaviour change), backend/app/api/v1/endpoints/chat.py, backend/app/schemas/chat.py,
  backend/app/core/config.py, backend/.env.example, backend/requirements.txt
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_permissions_matrix.md, ai_implementation_plan.md,
  CLAUDE.md

Next Phase:
- Phase 5: Action Agent (backend API tool calling with D6 confirmations and D7 team pre-checks).

---

Phase: 5 — Action Agent (Backend API Tool Calling)
Status: COMPLETE (awaiting approval to start Phase 6)

Objectives:
- Perform the assignment's HR actions through chat by calling the EXISTING HRMS APIs as the authenticated user.
  The LLM proposes actions; the backend authorizes and executes them. All data-changing AI actions require
  explicit confirmation. The Action Agent can call only registered HRMS APIs.

Repository inspection (actual routes, confirmed against app.routes in tests):
- POST /api/v1/leaves/requests; POST /api/v1/leaves/requests/{request_id}/approve | /reject (no body);
  POST /api/v1/tickets; POST /api/v1/tickets/{ticket_id}/assign {assignee_id}; POST /api/v1/tickets/{ticket_id}/status
  {status}; POST /api/v1/announcements {title, body}; POST /api/v1/employees/{employee_id}/projects {project_id,
  role_on_project}; POST /api/v1/employees/projects/catalog {name, description, status}; DELETE /api/v1/employees/{id}.
- Prompt vs repo: there is no rejection-reason field (the reject endpoint takes no body), so no reason is sent;
  announcements have no audience field (always company-wide); no leave edit/cancel API exists, so it is not supported.

Implemented:
- backend/app/services/ai/actions/{models,registry,resolver,validators,confirmation,client,prompts,service}.py.
  There is no actions/permissions.py: availability and target rules come only from the Phase 2 matrix (no duplication).
- Migration 0018_ai_pending_actions (AI-owned confirmations: token hash, binding hash, status, TTL) + model.
  sql/schema.py denies the new table to the SQL agent.
- POST /api/v1/chat/actions and POST /api/v1/chat/actions/confirm (schemas/chat.py: ActionRequest,
  ActionConfirmRequest with extra="forbid").
- errors.py: AI_TARGET_NOT_FOUND (404), AI_CONFIRMATION_INVALID (400), AI_BACKEND_UNAVAILABLE (502).
- Config: AI_ACTION_CONFIRMATION_TTL_SECONDS=300. httpx==0.28.1 added to runtime requirements.
- Integration: in-process ASGI calls to the running app with the caller's own bearer token (no service identity).
- Eval: evals/action_eval.json (20 cases) + evals/action_agent_eval.py.

Tests: 1008 total, 1008 passed, 0 failed, 0 skipped (~65 s offline; per-test DB + policy-file restore ~0.1 s on this
Windows host)
- Phase 1: 132 (+1 method-aware registry deny-list test in test_guards.py), Phase 2: 341, Phase 3: 142,
  Phase 4: 270, Phase 5: 123
- Phase 5 files: test_action_flow.py (service level through the real API), test_action_registry.py,
  test_action_api.py (HTTP), test_action_eval.py
- Action eval (scripted proposals, real business API): 20/20 pass; every metric = 1.0
- Deliberate regressions (all caught): see ai_threat_model.md "Phase 5 coverage"

Changed earlier tests (behaviour intentionally changed; no assertion weakened):
- test_infrastructure: head 0017 → 0018 and ai_pending_actions added to the expected tables.
- test_guards::test_no_ai_module_references_forbidden_routes: actions/registry.py is excluded from the text heuristic,
  because it legitimately declares DELETE /employees/{employee_id}. It is replaced by the stricter, method-aware
  test_action_registry_routes_pass_the_deny_list (GET/PUT /employees/{id} remain forbidden).
- tests/ai/sql/conftest.py: db_snapshot moved to tests/support/db.py (shared; same behaviour).
- evals/action_eval.json act-002/act-005 use explicit ids: after earlier cases created a second pending leave and a
  second "VPN" ticket, the name/title forms were (correctly) ambiguous and returned a clarification.

Known Issues:
- Implementation limitations:
  - Name resolution is case-insensitive exact-then-substring on full names, so there are no nicknames or fuzzy matching.
    Ambiguity always asks.
  - Ticket assignees are limited to the caller's HR read scope (manager: team), which is stricter than the existing API.
  - No cancel endpoint: unconfirmed proposals simply expire (TTL 300 s).
  - Leave reject reason and announcement audience are not supported by the existing APIs.
  - Ticket priority defaults to MEDIUM when the user gives none (documented default; category is required).
  - The confirmation summary echoes user-provided text (titles/bodies). The frontend must render it as text.
- Environment limitations: Docker/Python 3.11 unverified (3.12 venv); fastembed native runtime unavailable on this
  Windows host (Phase 3).
- Production-validation limitations: the deterministic Action Agent tests validate the security and execution
  pipeline, not production LLM intent accuracy. No real Claude action extraction has been tested (no API key).
  Claude structured output for the nested optional ActionProposal schema is untested against the live API.

Security Review:
- Verified invariant: LLM → proposes → backend validates / authorizes / resolves / requires confirmation →
  registered HRMS API (existing auth + validation + DB logic) → DB. The LLM is never the final authority.
- No direct business-table writes (static + behavioural tests). No arbitrary URLs or methods. No service identity.
- Regression: existing HRMS endpoints unchanged (82 characterization tests green); no migration 0001-0017 edited;
  services/auth.py, core/security.py and frontend unchanged. No secrets committed.

Files Changed:
- New: backend/app/services/ai/actions/* (9 files), backend/app/models/ai_pending_action.py,
  backend/alembic/versions/0018_ai_pending_actions.py, backend/evals/action_eval.json, backend/evals/action_agent_eval.py,
  backend/tests/ai/actions/* (6 files), backend/tests/support/db.py
- Modified: backend/app/api/v1/endpoints/chat.py, backend/app/schemas/chat.py, backend/app/services/ai/errors.py,
  backend/app/services/ai/sql/schema.py (deny new table), backend/app/models/__init__.py, backend/app/core/config.py,
  backend/.env.example, backend/requirements.txt, backend/tests/test_infrastructure.py, backend/tests/ai/test_guards.py,
  backend/tests/ai/sql/conftest.py
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_permissions_matrix.md, ai_implementation_plan.md,
  CLAUDE.md

Next Phase:
- Phase 6: LangGraph router / orchestration over the policy, SQL and action services.

Phase: 6 — LangGraph Orchestration
Status: COMPLETE (awaiting approval to start Phase 7)

Objectives:
- Add LangGraph as a controlled dispatcher/state machine over the Policy RAG, SQL and Action services, not an
  autonomous agent and not an authorization layer. Unified endpoint POST /api/v1/chat. Action requests are only
  proposed; execution stays on POST /chat/actions/confirm (Phase 5).

Implemented:
- backend/app/services/ai/orchestration/{classifier,graph,models,prompts,service,state}.py. Design:
  docs/architecture/copilot-router.md. Report: docs/architecture/phase-6-completion-report.md.
- Classifier: refusal rules (injection, direct access, destructive, sensitive data), clause split, deterministic
  signals, structured fast-model fallback (RouterClassification, extra="forbid", confidence < 0.6 → ambiguous),
  conservative plan (POLICY → SQL → ACTION, ≤ 1 action, ambiguity → clarification).
- Graph: 8 fixed nodes, ALLOWED_TRANSITIONS (forward only), no revisits, per-node state-write allowlists,
  trusted-state check in finalize, MAX_GRAPH_ITERATIONS=5, recursion_limit=10, no checkpointer.
- POST /api/v1/chat (CopilotRequest {message|question}, extra="forbid"); errors AI_CLASSIFICATION_FAILED (502),
  AI_ROUTING_FAILED (500), AI_MAX_ITERATIONS (422); response codes AI_CLARIFICATION_REQUIRED,
  AI_UNSUPPORTED_REQUEST, AI_CONFIRMATION_REQUIRED.
- providers.get_fast_llm_provider (AI_LLM_FAST_MODEL, no effort/fallback beta). langgraph==1.2.12 added.
- Eval: evals/router_eval.json (33 cases) + evals/router_eval.py.

Tests: 1265 total, 1265 passed, 0 failed, 0 skipped (~84 s offline, no API key)
- Phase 1: 132, Phase 2: 341, Phase 3: 142, Phase 4: 270, Phase 5: 123, Phase 6: 257
- Phase 6 files: test_router_classifier.py (100), test_router_graph.py (64), test_router_api.py (40),
  test_router_integration.py (22, real services), test_router_static.py (22), test_router_provider.py (7),
  test_router_eval.py (2)
- Router eval: 33/33 pass; route/status/source accuracy 1.0; never_executes = business_data_unchanged = 1.0
- Deliberate regressions: 16, all caught (see ai_threat_model.md "Phase 6 coverage")
- No earlier test changed.

Known Issues:
- Implementation limitations: English keyword rules; a trailing clause without a signal merges into the previous
  clause; "and" between two signalled clauses splits the request; no conversation memory (Phase 7); a
  clarification is a full response, not a resumable interruption. The refusal rules are deliberately broad
  (e.g. any "<name>'s salary").
- Production-validation limitations: the live fast-model classifier is untested (no API key). The eval
  validates the routing/safety pipeline with scripted model output.
- Environment limitations unchanged (fastembed native runtime, Docker).

Security Review:
- Routing is never authorization: every service re-authorizes the trusted ctx from deps (tests: employee
  escalation, manager outside team, SQL mutation/sensitive columns/out-of-scope rows through /chat).
- No DB/HTTP/executor/confirmation/auth/shell/eval access in the package (AST scan). ActionProposer exposes
  propose only.
- Identity only from get_ai_user_context; spoofed body fields → 422; headers/query ignored; inactive rejected.
- Existing endpoints unchanged (82 characterization tests green); no migration; services/auth.py,
  core/security.py and frontend unchanged. No secrets committed.

Files Changed:
- New: backend/app/services/ai/orchestration/* (7 files), backend/evals/router_eval.json,
  backend/evals/router_eval.py, backend/tests/ai/orchestration/* (8 files + __init__),
  docs/architecture/{README,copilot-router,phase-6-completion-report}.md
- Modified: backend/app/api/v1/endpoints/chat.py, backend/app/schemas/chat.py, backend/app/services/ai/errors.py,
  backend/app/services/ai/providers.py, backend/requirements.txt
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_implementation_plan.md, CLAUDE.md

Next Phase:
- Phase 7: Chat API (sessions/history around POST /api/v1/chat).

Phase: 7 — Conversation Memory, Sessions & Trusted Follow-Up Context
Status: COMPLETE (awaiting approval to start Phase 8)

Objectives:
- Multi-turn conversations on POST /api/v1/chat: owner-only persistent history and bounded, untrusted follow-up
  context. Conversation history is context, never authorization and never truth.

Implemented:
- Migration 0019_ai_conversations (ai_conversations, ai_conversation_messages; FK, CASCADE, CHECKs,
  UNIQUE(conversation_id, sequence), owner indexes; reversible) + models/ai_conversation.py.
- services/ai/conversation/{service,chat,models}.py: ConversationService (owner-scoped lookups, atomic sequence
  allocation, limits, redacted storage, bounded context), ConversationalCopilot (persistence policy).
- services/ai/orchestration/followup.py (DB-free): ConversationContext, follow-up detection, contextual question,
  "confirm in chat" detection, delimited history. Classifier plan(message, context=None); graph state gains trusted
  conversation_id (verified in finalize) and untrusted conversation_context; CopilotService.answer_turn.
- POST /api/v1/chat {message, conversation_id?} → + conversation_id, follow_up; errors carry X-Conversation-Id.
  GET /chat/conversations, GET /chat/conversations/{id}, DELETE /chat/conversations/{id} (archive).
- errors: AI_CONVERSATION_NOT_FOUND (404, identical for unknown/malformed/archived/foreign), AI_CONVERSATION_LIMIT_REACHED (409).
- Config: AI_CONVERSATION_MAX_MESSAGES=200, AI_CONVERSATION_CONTEXT_MESSAGES=6, AI_CONVERSATION_CONTEXT_CHARS=2000,
  AI_CONVERSATION_STORED_MESSAGE_CHARS=4000. SQL agent denies both new tables.
- Eval: evals/conversation_eval.json (25 multi-turn cases, 47 live turns + 5 seeded history turns) + evals/conversation_eval.py.

Tests: 1440 total, 1440 passed, 0 failed, 0 skipped (offline, no API key)
- Phase 1: 132, Phase 2: 341, Phase 3: 142, Phase 4: 270, Phase 5: 123, Phase 6: 259, Phase 7: 173
- Phase 7 files: test_conversation_service.py (43), test_followup.py (43), test_conversation_api.py (29),
  test_conversation_flow.py (22, real services), test_conversation_static.py (20), test_conversation_migration.py (14),
  test_conversation_eval.py (2)
- Conversation eval: 25/25 cases pass (all turns: route, status, follow-up, source, scope); never_executes =
  business_data_unchanged = 1.0
- Deliberate regressions: 18 tried; all caught after one redundant check was removed and one unit test added
  (see ai_threat_model.md "Phase 7 coverage")

Changed earlier tests (behaviour intentionally changed; no assertion weakened):
- test_infrastructure: head 0018 → 0019 and the two conversation tables added to the expected tables.
- tests/support/db.py: AI_OWNED_TABLES += ai_conversations, ai_conversation_messages (POST /chat now persists the
  conversation; business snapshots still compare every business table).
- tests/ai/orchestration/test_router_static.py: expected package files include followup.py (+2 auto-parametrized
  tests, Phase 6 count 257 → 259); the "no migration for orchestration" test now allows exactly the Phase 7
  conversation migration; the endpoint-dependency test walks nested dependencies (copilot service is reached via
  get_conversational_copilot).

Known Issues:
- Implementation limitations: English follow-up heuristics (reference words / ≤ 6 words without a topic /
  clarification answer); single-anchor resolution; "yes" after a clarification inherits the previous route
  (safe: actions are only proposed); confirmation tokens are user-bound, not conversation-bound (Phase 5
  contract unchanged); archived conversations are retained (no retention job); no summarisation.
- Production-validation limitations: the LLM classifier with history is untested against the live API (no key).
- Environment limitations unchanged (fastembed native runtime, Docker).

Security Review:
- Current identity always wins: ctx rebuilt per request; conversation_id trusted only after the owner check;
  finalize re-verifies it; history is untrusted input that no node can write.
- Only user text is ever forwarded to services; assistant text (incl. "SYSTEM OVERRIDE") only reaches the
  classifier, delimited and escaped. Refusal rules run on the current message.
- Cross-user access, tampered ids, role/user spoofing via history, sensitive-data escalation and confirmation in
  chat are tested negative. No DB writes outside the two AI-owned tables (static test); no secrets committed.
- Existing endpoints unchanged (82 characterization tests green); migrations 0001-0018 untouched;
  services/auth.py, core/security.py and frontend unchanged.

Files Changed:
- New: backend/app/models/ai_conversation.py, backend/alembic/versions/0019_ai_conversations.py,
  backend/app/services/ai/conversation/{__init__,service,chat,models}.py, backend/app/services/ai/orchestration/followup.py,
  backend/evals/conversation_eval.{json,py}, backend/tests/ai/conversation/* (7 test files + conftest + __init__),
  docs/architecture/phase-7-conversation-memory.md
- Modified: backend/app/services/ai/orchestration/{classifier,graph,state,models,prompts,service}.py,
  backend/app/api/v1/endpoints/chat.py, backend/app/schemas/chat.py, backend/app/services/ai/errors.py,
  backend/app/services/ai/sql/schema.py, backend/app/models/__init__.py, backend/app/core/config.py, backend/.env.example,
  backend/tests/test_infrastructure.py, backend/tests/support/db.py, backend/tests/ai/orchestration/test_router_static.py
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_implementation_plan.md,
  architecture/README.md, CLAUDE.md

Next Phase:
- Phase 8: AI Audit Logging.

Phase: 8 — AI Audit Logging, Observability & Evaluation
Status: COMPLETE (awaiting approval to start Phase 9)

Objectives:
- Make every AI interaction traceable, security-auditable, measurable and observable without creating a new
  sensitive-data path; make the evaluation datasets executable with a common report. Audit observes, never controls.

Implemented:
- Migration 0020_ai_audit_events (append-only: UPDATE/DELETE triggers; indexes request_id, conversation_id,
  created_at, (employee_id, created_at), (event_type, created_at)) + models/ai_audit_event.py.
- services/ai/audit/: events (closed enums, typed allowlisted AuditMetadata), recorder (request scope, record,
  record_critical, identity binding, keyed text fingerprint), store (INSERT-only, own session, log fallback),
  middleware (one request_id per AI request, X-Request-Id, AI_HTTP_REQUEST, flush), provider (observe(): provider,
  model, latency, tokens, estimated cost), query (paginated queries + metrics).
- Instrumented: authorization (ALLOWED/DENIED, inactive), router (CLASSIFIED/ROUTED/REFUSAL/CLARIFICATION),
  conversation (CHAT_REQUEST/RESPONSE, conversation bound after owner check), Policy RAG, SQL agent (incl.
  SQL_SECURITY_REJECTION, never SQL text), action lifecycle (PROPOSED → CONFIRMATION_REQUESTED → CONFIRMED (written
  before execution, fail-closed) → EXECUTED only on HRMS success / REJECTED / EXPIRED), provider calls, AI errors.
- GET /api/v1/ai/audit (ADMIN: all; others: own only; typed filters; limit ≤ 100) and GET /api/v1/ai/audit/metrics
  (ADMIN). No write routes.
- Anthropic usage reported through the abstraction (llm/base.report_usage). Config: AI_AUDIT_ENABLED=true,
  AI_LLM_[FAST_]INPUT/OUTPUT_COST_PER_MTOK (unset → cost null).
- Evaluation: evals/reporting.py (common SuiteResult + adapters; the five eval tests publish results when asked),
  evals/run_evals.py (pytest subprocess on a throwaway DB → evals/results/*.json + summary; git-ignored),
  evals/live_router_eval.py (opt-in, key required; otherwise LIVE_MODEL_EVAL = NOT_RUN).

Tests: 1595 total, 1595 passed, 0 failed, 0 skipped (offline, no API key)
- Phase 1: 132, Phase 2: 341, Phase 3: 142, Phase 4: 270, Phase 5: 123, Phase 6: 259, Phase 7: 173, Phase 8: 155
- Phase 8 files: test_audit_recorder.py (55), test_audit_api.py (30), test_audit_events.py (29),
  test_audit_privacy_static.py (28), test_eval_reporting.py (13)
- Offline evaluation (python -m evals.run_evals): 109/109 (policy 11, sql 20, router 33, conversation 25, actions 20);
  every metric 100%; LIVE_MODEL_EVAL = NOT_RUN
- Deliberate regressions: 16, all caught (see ai_threat_model.md "Phase 8 coverage")

Changed earlier tests (behaviour intentionally changed; no assertion weakened):
- test_infrastructure: head 0019 → 0020 and ai_audit_events added to the expected tables.
- tests/support/db.py: AI_OWNED_TABLES += ai_audit_events (every AI request appends audit events).
- tests/ai/sql/test_sql_api.py::test_rejections_use_envelope_and_leak_nothing: the full-DB snapshot excludes only the
  append-only ai_audit_events table (the request legitimately appends audit rows; every other table must be unchanged).
- tests/ai/orchestration/test_router_static.py: the migration list allows 0020 (owned by services/ai/audit).
- The five eval tests call publish(...) (a no-op unless evals/run_evals.py sets AI_EVAL_RESULTS_DIR).

Known Issues:
- Implementation limitations: in-app percentiles over the latest ≤ 10 000 events; tokens/cost only for real
  Anthropic calls (null offline); no retention purge (append-only triggers; explicit admin procedure required);
  failed sign-ins (401) carry no user; events of a request are lost if the process dies between response and flush
  (critical action events are written before execution).
- Production-validation limitations: live-model evaluation NOT RUN (no API key).
- Environment limitations unchanged (fastembed native runtime, Docker).

Security Review:
- Audit is passive: record() never raises or feeds a decision (static test); audit store failure leaves outcomes
  unchanged and logs a fallback; only confirmed action execution is fail-closed on audit failure.
- No prompts, answers, SQL, rows, tokens, JWTs, keys or PII in audit rows or audit logs (typed allowlisted metadata;
  end-to-end redaction tests for JWT, API key, PAN, bank, IFSC, UAN, ESI, password, salary).
- Identity from the DB-loaded user; conversation bound only after the owner check; client X-Request-Id ignored.
- Audit API: Phase 2 capabilities (AUDIT_READ_ORG / AUDIT_READ_OWN), GET only, append-only table.
- Existing endpoints unchanged (82 characterization tests green); migrations 0001-0019 untouched; services/auth.py,
  core/security.py and frontend unchanged. No secrets committed.

Files Changed:
- New: backend/app/services/ai/audit/{__init__,events,recorder,store,middleware,provider,query}.py,
  backend/app/models/ai_audit_event.py, backend/alembic/versions/0020_ai_audit_events.py,
  backend/app/api/v1/endpoints/ai_audit.py, backend/evals/{reporting,run_evals,live_router_eval}.py,
  backend/tests/ai/audit/* (5 test files + conftest + __init__), docs/architecture/phase-8-audit-observability.md
- Modified: app/main.py (middleware + AI error handler), app/api/v1/router.py, app/api/v1/endpoints/chat.py,
  app/core/config.py, .env.example, app/models/__init__.py, app/services/ai/errors.py, app/services/ai/llm/{base,anthropic_provider}.py,
  app/services/ai/authorization/{service,context}.py, app/services/ai/rag/service.py, app/services/ai/sql/{service,schema}.py,
  app/services/ai/actions/service.py, app/services/ai/orchestration/{graph,classifier,service}.py,
  app/services/ai/conversation/chat.py, the five eval tests, tests/test_infrastructure.py, tests/support/db.py,
  tests/ai/sql/test_sql_api.py, tests/ai/orchestration/test_router_static.py, HRMS-main/.gitignore (evals/results/)
- Docs: phase-status.md, ai_architecture.md, ai_threat_model.md, ai_implementation_plan.md, architecture/README.md, CLAUDE.md

Next Phase:
- Phase 9: Frontend AI Experience.

Phase: 9 — Full-Stack HR Copilot UX
Status: COMPLETE (awaiting approval to start Phase 10)

Objectives:
- A usable HR Copilot in the existing Next.js frontend on top of the Phase 6-8 backend: conversations, follow-ups,
  policy citations, data answers, action proposals with explicit confirmation, clarifications, safe refusals and
  errors, refresh persistence, and an admin AI audit view. The frontend is a client, never an authorization boundary.

Implemented:
- /copilot and /copilot/<conversationId> (app/copilot/[[...conversation]]/page.tsx), components/copilot/
  (copilot-shell, message-parts, audit-view), /admin/ai-audit, lib/copilot-api.ts (only AI client; typed contracts),
  lib/copilot-errors.ts (real backend codes), lib/copilot-display.ts (masking, sensitive-column filter, UI hints).
- Sidebar "AI Copilot" item; middleware protects /copilot and /admin (UX redirect only). lib/api.ts exports API_BASE.
- Backend (minimal, security-neutral): CORS expose_headers X-Request-Id / X-Conversation-Id; audit middleware skips
  CORS preflight. No business, agent or authorization change.
- Test tooling (devDependencies only): vitest 5, jsdom, @testing-library/react|dom|user-event|jest-dom; npm test.
  No E2E framework exists or was added: the eight E2E scenarios run as full-flow integration tests against a
  stateful fake backend.

Tests:
- Backend: 1596 passed, 0 failed (Phase 1: 132, 2: 341, 3: 142, 4: 270, 5: 123, 6: 259, 7: 173, 8: 156 incl. +1 CORS/preflight test)
- Frontend: 75 passed, 0 failed (copilot-api 22, copilot-shell 39, copilot-scenarios 9, audit-view 5)
- Deterministic evaluations: 109/109 (unchanged)
- Frontend deliberate regressions: 13 tried, all caught (one first survived: the confirm ref guard; a same-batch double-click test was added)
- Type check: all Phase 9 files and tests clean.

Changed earlier tests: none (one backend test added for the CORS/preflight change).

Known Issues:
- Phase 9 frontend production build verified successfully after a minimal pre-existing polls page TypeScript
  narrowing fix (approved): app/polls/page.tsx reads the narrowed `items` before the state-update callback. The
  build then exposed a second pre-existing blocker, fixed with approval: the four employees pages
  (employees, employees/new, employees/payslips, employees/projects) render their unchanged content beneath a
  `<Suspense fallback={null}>` boundary, as Next.js requires for useSearchParams() during prerendering (still
  statically rendered). Result: tsc 0 errors, 75 frontend tests, `next build` PASS (22 routes), backend 1596 passed.
- npm reports a security advisory for next@15.5.2 (pre-existing pin); upgrade needs a decision.
- History reloads show text only (no citations/rows/confirmation tokens are persisted by design); a pending
  proposal must be re-asked after refresh. No backend endpoint verifies an unknown confirmation outcome.
- No Markdown rendering (plain text). Main app sidebar remains desktop-only (existing); the Copilot list is a drawer
  on small screens. Responsive behaviour was not verified in a real browser.

Security Review:
- Request bodies: message (+ backend-issued conversation_id) only; confirmation body: confirmation_id only; no
  identity/role/permission fields (tests). Role is a UI hint for suggestions and the audit link only.
- AI content rendered as text only (no dangerouslySetInnerHTML; XSS tests across all fields); sensitive-looking
  values masked and sensitive columns dropped as defence in depth.
- 401 → existing sign-in flow; 403/scope → safe copy, no fallback request; foreign conversation → generic
  "Conversation unavailable." and the id is dropped; confirmations never retried, one request per token.
- Backend Phase 1-8 behaviour unchanged; no secrets committed.

Files Changed:
- New: frontend/app/copilot/[[...conversation]]/page.tsx, frontend/app/admin/ai-audit/page.tsx,
  frontend/components/copilot/{copilot-shell,message-parts,audit-view}.tsx, frontend/lib/{copilot-api,copilot-errors,copilot-display}.ts,
  frontend/vitest.config.mts, frontend/tests/* (setup, helpers, 4 test files), frontend/package-lock.json,
  docs/architecture/phase-9-copilot-frontend.md
- Modified: frontend/package.json (test scripts + devDependencies), frontend/lib/api.ts (export API_BASE),
  frontend/components/layout/sidebar.tsx, frontend/middleware.ts, backend/app/main.py (CORS expose_headers),
  backend/app/services/ai/audit/middleware.py (skip OPTIONS), backend/tests/ai/audit/test_audit_events.py (+1 test)
- Docs: phase-status.md, architecture/README.md, ai_architecture.md, ai_threat_model.md, ai_implementation_plan.md, CLAUDE.md

Next Phase:
- Phase 10: Evaluation Framework.

Phase: 10 — Production Hardening, Bonus Completion & Final Evaluation
Status: COMPLETE (awaiting approval; Phase 11 not started)

Objectives:
- Audit core and bonus requirements against the repository, fill genuine gaps with safe high-value work, harden
  production configuration, add adversarial evidence, finalize documentation, and run the full regression.

Implemented:
- Gap analysis: docs/architecture/phase-10-gap-analysis.md. Final report: docs/phase-10-final-report.md.
  New required doc: docs/ai_eval_results.md. README updated (AI section, env vars, commands).
- Formal assignment eval set: evals/assignment_eval.json (30 cases: all assignment prompts + injection + follow-ups)
  + evals/assignment_eval.py + reporting adapter; registered in evals/run_evals.py (now 6 suites, 139 cases).
- Router fixes found by the formal set: entitlement wording ("How many sick leaves do I get?") -> POLICY; record
  lookups starting with who/which ("Who is assigned to HR Policy Copilot?") -> SQL before policy wording.
- Audit: actor_role recorded on request events (from the DB user). Metrics: intents, tools, actions by type,
  requests by role, RAG no-answer rate, action confirmation rate, execution success (derived from existing events).
- Frontend: role-specific Copilot modes from GET /chat/permissions (UX only); /admin/ai-usage dashboard
  (UsageDashboard); links between the admin pages.
- Production checks: app/core/production_checks.py, enforced at startup only when ENVIRONMENT=production.
- Adversarial matrix: tests/ai/hardening/test_adversarial_matrix.py (identity, authorization, SQL, actions,
  prompt injection end to end).
- Streaming: NOT implemented (FastAPI 0.116 closes request-scoped dependencies before a streaming body runs;
  documented). Tracing: Phase 8 internal tracing kept; no OTel/LangSmith added (PARTIAL).

Tests: backend 1685 passed, 0 failed (P1 132, P2 341, P3 142, P4 270, P5 123, P6 259, P7 173, P8 156, P10 89);
  frontend 86 passed; tsc 0 errors; next build PASS (23 routes); evals 139/139; live model NOT RUN.

Changed earlier tests (behaviour intentionally changed; no assertion weakened):
- tests/ai/audit/test_audit_events.py::test_policy_request_is_fully_correlated: the request event's exact metadata
  now also contains actor_role ("EMPLOYEE") - stricter.
- tests/ai/audit/test_eval_reporting.py::test_summary_and_render...: the missing-suites list includes the new
  "assignment" suite.
- frontend/tests/audit-view.test.tsx: the AuditMetrics fixture includes the new metric fields (type extended).

Known Issues: see docs/phase-10-final-report.md §5 (live model not evaluated; no streaming; tracing partial;
  next@15.5.2 advisory; single-instance SQLite assumptions; CORS/Dockerfile are development settings).

Security Review:
- No security boundary weakened; every Phase 1-9 suite green; adversarial matrix (68) passes; no secrets committed.

Files Changed:
- New: backend/app/core/production_checks.py, backend/evals/assignment_eval.{json,py},
  backend/tests/ai/hardening/* (conftest, 4 test files), frontend/components/copilot/usage-dashboard.tsx,
  frontend/app/admin/ai-usage/page.tsx, frontend/tests/phase10-modes-usage.test.tsx,
  docs/ai_eval_results.md, docs/phase-10-final-report.md, docs/architecture/phase-10-gap-analysis.md
- Modified: backend/app/main.py, backend/app/services/ai/audit/{events,recorder,middleware,query}.py,
  backend/app/services/ai/authorization/context.py, backend/app/services/ai/orchestration/classifier.py,
  backend/evals/{reporting,run_evals}.py, the two backend tests above, frontend/lib/{copilot-api,copilot-display}.ts,
  frontend/components/copilot/copilot-shell.tsx, frontend/app/copilot/[[...conversation]]/page.tsx,
  frontend/app/admin/ai-audit/page.tsx, frontend/tests/audit-view.test.tsx, README.md, docs (ai_architecture,
  ai_permissions_matrix, ai_threat_model, ai_implementation_plan, phase-status, architecture/README), CLAUDE.md
- Dependencies: none added, removed or upgraded.

Phase: 11 — Security Review
Status: NOT_STARTED

Phase: 12 — Observability
Status: NOT_STARTED

Phase: 13 — Final Testing
Status: NOT_STARTED

Phase: 14 — Final Documentation
Status: NOT_STARTED

---

Production hardening programme (separate track; see docs/production-hardening/)
- Phase 0 — Verified baseline: COMPLETE (baseline.md, 2026-10-11).
- Phase 1 — Critical security remediation: COMPLETE (2026-10-12; security-remediation.md). Full backend 1744 passed,
  frontend 86 passed, tsc 0 errors, next build PASS.
  Approved remediations of R1 (B-01), R4 (B-05) and R9 (B-03) change characterization tests that pinned the old
  insecure behaviour; each changed test documents the old behaviour and the new invariant. Inactive users are now
  rejected by get_current_user (403 USER_INACTIVE), so AI API tests expect USER_INACTIVE instead of AI_USER_INACTIVE.
  Access tokens require type "access" (one-time re-login). Two environment-sensitive tests (dev DB / .env existence)
  now assert their intent (dev DB unchanged by the run; .env not tracked, git-ignored, docker-ignored).
  Follow-ups (approved 2026-10-12): pending-leave queue scoped to decidable requests; project removal scoped like
  assignment; out-of-scope leave decisions return 404 identical to unknown ids. Inactive AI denials keep their audit
  identity via a server-side request.state marker (audit test unchanged). No test deleted; no assertion weakened.
- Phase 2 — Deployment, abuse controls, data lifecycle, CI: COMPLETE locally (2026-10-13; phase-2-report.md).
  Full backend 1846 passed (Python 3.11), frontend 86 passed (also in node:24.21.0), tsc 0 errors, build PASS.
  Production Dockerfile targets + docker-compose.prod.yml (1 worker, non-root, read-only), trusted hosts (B-25),
  range limit (B-26), FastAPI 0.133.1/Starlette 1.3.1, PyJWT 2.15.1, AI rate limits/budgets/concurrency + login
  throttling, deterministic SQL wording by default, retention (dry run by default; audit events never deleted),
  backup/restore tooling with a restore drill, CI workflow + advisory gate (local only; not pushed, never run on
  GitHub; dependency-audit job fails until the PROPOSED advisory exceptions are approved or deps upgraded).
  Test change: four SQL formatter-guard tests now request formatting="llm" explicitly because the default became
  "deterministic"; assertions unchanged. No test deleted; no assertion weakened.
  Release status: NOT YET PRODUCTION READY. Phase 3 not started (separate approval required).
- Phase 2 closure (approved 2026-10-14): pytest 9.0.3 + pytest-asyncio 1.3.0 (PYSEC-2026-1845 fixed); narrow npm
  override of Next's nested postcss to 8.5.28 (runtime npm advisories 0; CSS output byte-identical; pinned by
  frontend/tests/dependency-pins.test.ts); braces (build-time, no patched release) stays a PROPOSED exception, so the
  CI advisory gate still fails until a human owner approves it. ADMIN-only publication of organisation-wide HR
  policies (migration 0021: status/version + append-only hr_policy_events; drafts never listed to others, never
  indexed or retrieved). Route-by-route authorization matrix (authorization-matrix.md); admin self-actions pinned by
  tests/characterization/test_admin_self_actions.py (S-02 open: an ADMIN can demote themselves leaving zero admins).
  Test changes: pinned migration head 0020 -> 0021 in three tests (new migration, documented in each test).
- Phase 3 (offline workstreams only, approved 2026-10-14): 3.3 real-embedding retrieval calibration (rag-calibration.md;
  proposal only, AI_RAG_MIN_SCORE unchanged), 3.6 offline red-team corpus (40 attacks x benign/hostile model),
  3.8 monitoring-alerts.md, 3.9 release-readiness.md. Live-model evaluation, live calibration and workstreams 3.1,
  3.2, 3.4, 3.5 and 3.7 are NOT started (blocked on approvals / API key).
  Release status: NOT YET PRODUCTION READY.
- Later work, recorded in its own trackers (all local until a push is approved):
  - OpenAI provider (2026-10-16) and RBAC remediation Steps 1–10 (2026-10-17): `production-hardening/rbac-remediation-plan.md`,
    `development-handoff.md` §2.
  - AI security remediation AIS-01..AIS-20 (2026-10-18, phases 0–11) and G-11 password step-up on high-impact business
    APIs (`d570ab6`, `log` mode) with the admin UI dialog (`29ca184`): `production-hardening/ai-security-remediation-progress.md`.
  - Capstone completion programme (2026-10-19): `production-hardening/capstone-completion-progress.md`.
  Release status: NOT YET PRODUCTION READY.
