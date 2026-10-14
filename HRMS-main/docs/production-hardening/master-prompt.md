<!--
Production-hardening master prompt: the 12-phase programme definition used to run this work with Claude Code.
Copied verbatim from the workspace file writing-block.md (2026-10-11) so it is available on other systems; the
original file is unchanged. Phase numbering here is the programme plan; the phases actually approved and completed
are recorded in phase-status.md, security-remediation.md, phase-2-report.md and development-handoff.md.
Approval boundaries in those records take precedence over this plan.
-->

# Claude Code Master Prompt: Production-Ready AI HR Copilot

## Project

**Project:** NovaWorks PeopleOps AI HR Copilot  
**Repository:** `HRMS-main`  
**Goal:** Strengthen the existing system into a secure, reliable, observable, evaluated, and deployable production-grade AI application.

Act as a **Principal AI Architect, Application Security Engineer, Senior Backend Engineer, AI Evaluation Engineer, and DevSecOps Engineer**.

Your task is to inspect, fix, validate, and productionize the existing application using evidence from the repository. This is a hardening and engineering project—not a rewrite.

---

## 1. Non-negotiable engineering rules

1. Inspect the existing repository, architecture documents, security reports, implementation plans, and Git state before making changes.
2. Treat the current code as the source of truth. Previous reports may be stale.
3. Preserve the existing architecture unless a specific, demonstrated problem justifies changing it.
4. Do not replace working components simply because another library or architecture is fashionable.
5. Do not remove existing functionality, weaken authorization, skip tests, or introduce mock behavior to make tests pass.
6. Do not change unrelated application functionality.
7. Do not overwrite existing user changes, reset the branch, discard work, or perform destructive Git operations.
8. Do not commit, push, deploy, migrate production data, or modify external infrastructure without explicit authorization.
9. Never print, copy into documentation, or expose actual API keys, passwords, JWT secrets, database credentials, or production personal data.
10. Do not claim a test, browser check, Docker build, live-model evaluation, penetration test, or deployment validation succeeded unless it actually ran and its result was inspected.
11. If a task requires unavailable credentials, services, tools, or permissions, document the blocker and continue with independent work.
12. Prefer small, reviewable changes with focused tests over large refactors.
13. Maintain backward compatibility for existing API contracts unless a security fix requires a change. Document any unavoidable breaking change.
14. Do not introduce Redis, Kafka, a new vector database, a new LLM framework, or a separate AI microservice without measured need and an explicit architecture decision.

### Initial Git safety check

Run and inspect:

- `git status --short --branch`
- `git diff --stat`
- `git diff`
- Relevant untracked files and recent commits

Identify pre-existing user changes and preserve them. Never assume the working tree is clean.

## 2. Existing architecture to preserve and verify

The current application is expected to use:

- FastAPI backend
- SQLAlchemy and SQLite
- Alembic migrations
- Next.js 15 frontend
- LangGraph orchestration
- Anthropic as an LLM provider, with existing provider abstractions
- Local embedding model `BAAI/bge-small-en-v1.5`
- Policy RAG with lexical and vector retrieval
- A read-only SQL Agent
- HR action proposals with separate human confirmation
- JWT-based authentication and backend authorization
- Append-only AI audit events
- Offline deterministic evaluation suites
- Docker-based development or deployment configuration

Verify every item against the actual repository. Do not assume the list is fully accurate.

Preserve the existing security architecture:

- The backend is the authority for authentication, authorization, and business rules.
- The model is never an authority for permissions.
- The SQL Agent cannot modify business data.
- AI-proposed actions cannot execute without independent server-side confirmation.
- Confirmation must remain bound to the authorized user and exact action parameters, expire, and be single-use.
- Conversation text such as “yes” must never directly execute a pending action.
- Retrieved documents and conversation history are untrusted input.
- Sensitive employee data must not be exposed merely because an authenticated user can access the AI interface.

## 3. Phase 0 — Establish a verified baseline

Before implementation:

1. Read the architecture and production-readiness reports, AI component inventory, security findings, README, dependency manifests, Docker files, environment examples, migrations, tests, and CI configuration.
2. Map the actual request lifecycle from the Next.js UI through authentication, backend authorization, LangGraph, retrieval/tools, model provider, persistence, and audit.
3. Identify all existing fixes and uncommitted changes.
4. Run the existing backend and frontend test suites using documented project commands.
5. Record the actual Python, Node.js, Next.js, dependency, database, and Docker versions.
6. Inspect current framework security advisories and determine whether installed versions are affected.
7. Create a findings matrix containing:

| Finding | Severity | Evidence | Exploitability | Proposed fix | Regression test |
|---|---|---|---|---|---|

Classify findings as Critical, High, Medium, Low, or Informational. Separate verified vulnerabilities from suspected issues.

Write the baseline report to:

`docs/production-hardening/baseline.md`

Do not modify application behavior during the initial audit. After establishing the baseline, proceed to remediation unless blocked by a destructive, ambiguous, or external change requiring approval.

## 4. Phase 1 — Fix critical authorization and application security gaps

Prioritize real vulnerabilities before AI enhancements.

### 4.1 JWT and token-type validation

Inspect access-token and refresh-token handling across every protected route.

- Ensure refresh tokens cannot authenticate as access tokens.
- Require explicit, valid token-type claims and reject unknown token types.
- Validate signature, algorithm, expiry, issuer and audience wherever applicable to the existing token design.
- Verify revocation or session invalidation behavior if supported.
- Preserve the current authentication architecture where possible.
- Add negative tests for refresh-token reuse, malformed claims, expired tokens, wrong signatures, and token confusion.
- Verify behavior through actual protected API routes.

Do not invent a new token format without checking existing clients and compatibility requirements.

### 4.2 Inactive users and authorization enforcement

Inspect authentication dependencies and all sensitive business endpoints.

- Reject inactive or disabled users on protected requests.
- Enforce object-level authorization for employee, leave, project, payroll, and administrative operations.
- Fix the employee-detail endpoint if it exposes bank information, PAN, salary, or other sensitive fields to unauthorized users.
- Apply field-level access control where appropriate.
- Enforce manager-to-employee and manager-to-action relationships server-side.
- Prevent managers from approving arbitrary employees' leave requests or assigning projects outside their permitted scope.
- Check ownership and permissions on both read and write operations.
- Return safe error responses without leaking sensitive records or internal implementation details.

Do not rely on frontend hiding, route-name restrictions, model prompts, or AI-specific authorization as a substitute for business API authorization.

### 4.3 SQL Agent hardening

Preserve the read-only SQL design and verify that it cannot be bypassed.

- Allow only explicitly supported read-only SQL statements.
- Reject multiple statements, DDL, DML, transaction manipulation, dangerous pragmas, and unsupported SQL constructs.
- Enforce table, column, join, row-scope, and query-complexity restrictions.
- Apply authorization-derived employee or organizational scope on the server.
- Revalidate generated SQL immediately before execution.
- Enforce database-level read-only protections wherever feasible.
- Verify that SQLite authorizer protections cannot be bypassed by alternate execution paths.
- Apply statement deadlines, row limits, and response-size limits.
- Prevent sensitive columns from reaching the model unless the caller is authorized to access them and the fields are necessary.
- Add adversarial tests for prompt injection, schema discovery, unauthorized employee IDs, nested queries, union queries, and attempted mutations.

Never allow the model to execute arbitrary SQL or grant itself additional permissions.

### 4.4 Action confirmation and transaction safety

Inspect the complete action lifecycle, including pending proposals, confirmation tokens, persistence, execution, and audit.

- Bind confirmation to the authenticated user, exact action type, target, and canonical parameters.
- Enforce expiry and single-use consumption atomically.
- Recheck permissions, target ownership, and business rules immediately before execution.
- Ensure concurrent confirmation attempts cannot execute an action twice.
- Fix audit ordering so a confirmation is not recorded as successfully claimed before the atomic claim succeeds.
- Distinguish proposal, confirmation attempt, successful claim, execution start, success, failure, timeout, and unknown outcome.
- Introduce idempotency protection where supported by the underlying business operation.
- Define safe reconciliation for operations that may have succeeded despite a timeout.
- Never blindly retry a potentially successful, non-idempotent HR action.
- Enforce a real end-to-end execution deadline; verify that HTTP client timeouts actually apply to the selected transport.
- Ensure the UI never treats a proposed action as completed before the backend confirms its result.

Add tests for expiry, tampering, replay, parallel requests, permission changes, timeouts, process interruption, and ambiguous outcomes.

### 4.5 Audit integrity

- Keep security-critical audit events append-only.
- Use a separate, securely managed key for audit fingerprints rather than deriving it directly from the JWT secret.
- Protect audit data against unauthorized reads and modification.
- Define reliable persistence behavior for critical events.
- Avoid logging prompts, resumes, policy content, raw SQL results, tokens, or sensitive employee fields unnecessarily.
- Make audit event states unambiguous and correlate them with request and action IDs.
- Document the limits of tamper evidence; do not claim a hash chain or fingerprint guarantees integrity if the key or storage can be compromised.

### Phase 1 exit criteria

Do not proceed to broad architectural enhancements until the critical and high-severity findings are fixed or explicitly documented as blocked.

Run focused security tests, then the full existing test suites. Record remaining vulnerabilities and exact evidence.

## 5. Phase 2 — Harden AI-specific security

### 5.1 Prompt injection and untrusted content

Review system prompts, retrieved policy chunks, employee records, conversation history, tool descriptions, and model outputs.

- Clearly separate trusted instructions from untrusted data.
- Treat retrieved documents and user content as data, not executable instructions.
- Prevent documents from authorizing tools or changing access permissions.
- Keep tool permissions and argument validation outside the model.
- Validate all model-produced structured outputs against strict schemas.
- Reject invalid action types, unauthorized targets, unexpected fields, oversized payloads, and malformed outputs.
- Keep SQL validation and action authorization independent of model-generated reasoning.
- Do not rely on regex-based refusal detection as a security boundary.
- Add adversarial tests covering indirect prompt injection, tool manipulation, data exfiltration attempts, malicious policy documents, and cross-user context leakage.

### 5.2 LLM data privacy and provider governance

Trace every data field sent to external model and embedding providers.

Document:

- What information is transmitted
- Which provider receives it
- Why the information is required
- Whether it contains personal or sensitive employee data
- Applicable provider retention and training settings
- Configurable data minimization and redaction
- Failure behavior when the provider is unavailable

Minimize the data sent to external models. Never send bank details, PAN, passwords, authentication tokens, or unrelated personal fields.

Do not claim that provider data is never retained or used for training without verifying the applicable account configuration and provider terms. Record unresolved governance decisions rather than inventing answers.

### 5.3 Cross-user isolation

- Verify that conversations, messages, retrieval results, pending actions, and audit views are scoped to the authorized user and permitted organizational context.
- Prevent guessed IDs, altered URLs, or crafted requests from accessing another user's records.
- Verify authorization independently on every backend endpoint.
- Ensure cached results, if any, cannot cross authorization boundaries.
- Do not introduce multi-tenancy unless the existing product requirements require it.

### 5.4 Secrets and configuration

- Keep credentials out of source code, frontend bundles, logs, Docker images, and committed environment files.
- Validate required environment variables during startup.
- Fail safely when security-critical configuration is missing.
- Provide a sanitized `.env.example`.
- Check for accidentally enabled tracing or telemetry exporters that could send prompts and outputs to external services.
- Ensure debug mode and development-only endpoints are disabled in production.

## 6. Phase 3 — Make AI orchestration reliable

Inspect the current LangGraph flow and provider abstractions before changing them.

### LLM gateway and provider behavior

Introduce or strengthen a centralized AI execution boundary only where it provides concrete value. It should consistently handle:

- Provider and model selection
- Request deadlines
- Retry policy
- Error normalization
- Token and usage accounting
- Cost estimation where pricing information is configured
- Concurrency limits
- Structured output validation
- Cancellation
- Provider-specific telemetry

Requirements:

- Retry only appropriate transient failures.
- Use bounded retries, backoff, and jitter.
- Do not retry authorization failures or malformed requests.
- Do not silently fall back to a less capable or less secure model.
- Make provider fallback explicit, observable, and tested.
- Apply a total request deadline across retrieval, model calls, and tools.
- Avoid retrying side-effecting operations unless idempotency is guaranteed.
- Distinguish provider failure from an actual negative business result.
- Preserve the existing provider interface unless a demonstrated limitation requires change.

### LangGraph workflow

- Define typed state and explicit node responsibilities.
- Validate transitions and tool outputs.
- Prevent unbounded agent loops.
- Enforce maximum tool calls, recursion, wall-clock time, and model-token budgets.
- Keep authorization outside the model-controlled graph state.
- Avoid storing secrets or unnecessary PII in checkpoints.
- Define cancellation, error, and partial-failure behavior.
- Ensure user-visible responses accurately reflect incomplete or failed operations.

Do not add additional agents merely to make the system appear more sophisticated.

### Timeouts and concurrency

- Add bounded concurrency for model calls and expensive tools.
- Avoid exhausting database connection pools or application workers.
- Use request cancellation where supported.
- Define a maximum response size and request size.
- Ensure background tasks do not silently disappear when the request finishes or the process exits.

## 7. Phase 4 — Improve Policy RAG quality

Keep the current hybrid retrieval pipeline initially. Improve it using measured evidence.

### Retrieval correctness

- Verify document ingestion, extraction, chunking, metadata, embedding generation, persistence, and indexing.
- Preserve source document identity and useful section metadata.
- Define deterministic document update and deletion behavior.
- Avoid duplicate chunks during repeated ingestion.
- Track document version, ingestion status, and embedding model version where appropriate.
- Verify that retrieval filters are applied before content reaches the LLM.
- Return citations linked to actual retrieved sources.
- Never fabricate citations or claim that unsupported content came from a policy.
- Abstain or request clarification when evidence is insufficient.
- Distinguish no relevant policy found from retrieval or provider failure.

### Evaluation-driven tuning

The current retrieval threshold was calibrated with synthetic or fake embeddings, so treat it as unverified for real semantic quality.

Create a representative evaluation dataset containing:

- Common HR policy questions
- Questions requiring multiple policy sections
- Paraphrased and ambiguous questions
- Unanswerable questions
- Outdated or conflicting policies
- Prompt-injection documents
- Questions involving sensitive employee data
- Questions that should not be answered from available documents

Measure:

- Recall@k and precision@k where labels exist
- MRR or nDCG where appropriate
- Citation correctness
- Answer faithfulness to retrieved evidence
- Unsupported-claim rate
- Correct abstention rate
- Retrieval latency

Use real configured embeddings for quality evaluation. Do not tune thresholds solely to pass synthetic tests.

### Reranking and indexing

Evaluate reranking, approximate nearest-neighbor indexing, and other retrieval changes only after establishing a baseline. Keep the simpler implementation if it meets the measured latency and quality requirements.

Do not introduce a new vector database without a demonstrated scale or performance requirement.

## 8. Phase 5 — Build a credible AI evaluation system

Keep deterministic tests and add real model-quality evaluation as a separate layer.

### Evaluation categories

1. Routing and intent classification
2. Policy retrieval and grounded answers
3. SQL generation and query safety
4. Action extraction and proposal correctness
5. Prompt-injection resistance
6. Authorization and sensitive-data leakage
7. Structured-output validity
8. Provider failures and timeout behavior
9. Latency, token usage, and cost
10. Regression behavior across model or prompt changes

For each evaluation, define:

- Dataset and version
- Expected behavior
- Scoring method
- Pass threshold
- Known limitations
- Model/provider/version
- Execution date and result

Requirements:

- Maintain deterministic security and regression tests in CI.
- Add live-model evaluations only when credentials are available and external calls are explicitly configured.
- Keep API keys out of tests and reports.
- Use synthetic or appropriately de-identified evaluation data.
- Separate hard security gates from statistical quality metrics.
- Do not make flaky live-model tests mandatory on every normal unit-test run.
- Add a controlled evaluation command for release qualification.
- Establish regression thresholds based on measured baselines, not invented targets.

Create a documented release evaluation report showing which tests actually ran and which remain unverified.

## 9. Phase 6 — Observability, audit, and operations

Implement useful, privacy-aware operational observability.

### Request tracing

- Correlate each request across API, graph execution, retrieval, provider calls, tool calls, and action execution.
- Use request, conversation, and action identifiers without exposing credentials.
- Add OpenTelemetry-compatible tracing where practical.
- Keep prompt and response content out of telemetry by default.
- Verify that optional exporters cannot transmit sensitive data unexpectedly.
- Record provider latency, retrieval latency, token usage, errors, timeouts, and action outcomes.

### Metrics and alerts

Provide operational metrics for:

- Request count and error rate
- Model provider failures
- p50/p95/p99 latency
- Token consumption and estimated cost
- Retrieval no-hit and low-confidence rates
- Invalid structured outputs
- SQL safety rejections
- Action confirmation, execution, failure, and unknown-outcome rates
- Rate-limit rejections
- Database pool exhaustion

Document thresholds and alert actions based on the actual deployment. Do not create fake metrics or hard-code unsupported cost figures.

### Audit and retention

- Make critical action and security audit writes reliable.
- Add a documented retention and purge strategy for conversations, audit records, temporary uploads, and pending actions.
- Define legal, security, and operational retention requirements before deleting audit data.
- Ensure purge operations are authorized, logged, and tested.
- Avoid exposing audit data through unrestricted admin APIs.

## 10. Phase 7 — Rate limits, cost control, and abuse prevention

Add server-side controls appropriate to the deployment:

- Authentication and endpoint-level rate limits
- Separate limits for expensive AI operations
- Per-user and, if applicable, per-organization quotas
- Maximum prompt and document sizes
- Maximum retrieval context and output tokens
- Maximum graph iterations and tool calls
- Bounded concurrent provider requests
- Configurable daily or monthly budget thresholds
- Safe behavior when limits are exceeded

Ensure limits cannot be bypassed by changing the frontend, creating multiple conversations, or invoking endpoints directly.

If shared rate-limit state is needed across multiple application instances, document the requirement and choose the smallest suitable solution. Do not add Redis to a single-instance deployment without evidence that it is necessary.

## 11. Phase 8 — Production frontend and API security

Inspect the Next.js authentication and deployment model.

- Review localStorage usage for JWTs and the exposure to XSS.
- Evaluate a secure HttpOnly, Secure, SameSite cookie-based session where compatible with the existing authentication design.
- If cookie authentication is adopted, implement appropriate CSRF protections and test them.
- Do not perform a token-storage migration without accounting for API clients, refresh flows, and backward compatibility.
- Validate and encode untrusted content before rendering.
- Check for unsafe HTML rendering, open redirects, and insecure URL handling.
- Verify authorization failures and expired-session behavior in the UI.
- Prevent sensitive employee information from leaking through client-side state, browser storage, logs, or error messages.
- Add browser-level tests for critical flows if browser automation is available.
- If browser automation is unavailable, report that limitation explicitly.

Review CORS, security headers, CSP compatibility, cookies, TLS assumptions, request sizes, upload validation, and production error handling.

## 12. Phase 9 — Dependencies, containers, and deployment

### Dependency security

- Check current security advisories for installed Python, Node.js, Next.js, React Server Components, and relevant transitive dependencies.
- Upgrade to supported, patched releases compatible with the project.
- Treat known vulnerable versions as findings requiring remediation or a documented, time-bounded exception.
- Prefer targeted dependency updates over unnecessary major-version migrations.
- Pin or lock dependencies consistently and verify reproducible installation.
- Run dependency and image vulnerability checks where available.

The previously identified Next.js version and Node.js 20 runtime require fresh verification against current advisories and support status before release. Do not assume an old version is safe because the build passes.

### Docker and production configuration

- Separate development and production startup behavior.
- Ensure production does not use `next dev` or `uvicorn --reload`.
- Use non-root users where practical.
- Avoid copying secrets into images.
- Add suitable health and readiness checks.
- Use graceful shutdown and bounded request handling.
- Verify frontend/API networking and environment configuration.
- Validate persistent storage and database backup/restore procedures.
- Document migration order and rollback strategy.
- Confirm reverse-proxy headers, TLS, body-size limits, and timeout settings.
- Verify the production build and container images, not just local development.

Do not run production migrations or deploy containers without explicit approval.

### SQLite deployment decision

Evaluate whether SQLite is appropriate for the expected concurrency, write volume, backup requirements, and number of application instances.

Do not migrate automatically. Document the evidence and recommendation. If PostgreSQL is justified, propose a separate migration plan with schema compatibility, Alembic migration testing, backup/restore validation, and rollback.

## 13. Phase 10 — CI, release gates, and disaster recovery

Add or strengthen CI using the repository's existing tooling.

Required checks should include, where applicable:

- Formatting and linting
- Type checking
- Backend unit and integration tests
- Frontend unit tests
- Production frontend build
- Migration checks
- SQL Agent security tests
- Action/HITL security tests
- Authorization regression tests
- Secret scanning
- Dependency vulnerability checks
- Docker build validation
- Deterministic offline AI evaluations

Create explicit release gates:

**Block release when:**

- Critical or High exploitable vulnerabilities remain unresolved.
- Authorization or tenant/user isolation tests fail.
- AI tools can bypass server-side authorization.
- SQL mutations are possible through the AI path.
- Human confirmation can be replayed or bypassed.
- Sensitive data is exposed to unauthorized users.
- Required migrations or production builds fail.
- Secrets are exposed.
- Backup and recovery requirements for the deployment are unmet.

Define documented exceptions with owner, rationale, compensating controls, and expiry. Do not silently downgrade severity to pass CI.

Create and test backup and restore procedures for the database, policy documents, and required persistent configuration. Do not claim recovery capability until restoration has been tested.

## 14. Phase 11 — Performance and resilience

Run realistic, bounded tests using representative synthetic data.

Measure:

- API latency and throughput
- Concurrent chat sessions
- Retrieval latency
- Provider latency and timeout behavior
- Database pool and lock contention
- Memory and CPU use
- Request and output size limits
- Recovery from provider outage
- Recovery from database or process restart
- Duplicate-action prevention under concurrent requests

Test overload behavior. The application must reject, queue, or degrade safely rather than consume unlimited resources.

Do not invent a production capacity figure. Derive capacity guidance from actual test results and clearly document the test environment.

## 15. Phase 12 — Release-readiness verification

Produce a final evidence-based assessment covering:

1. Architecture and major data flows
2. Resolved findings and changed files
3. Remaining vulnerabilities and accepted risks
4. Test commands and actual results
5. Dependency and container scan results
6. Live-model evaluation results or blockers
7. Retrieval quality results
8. Latency and load-test results
9. Audit and privacy controls
10. Deployment and rollback instructions
11. Backup and restore evidence
12. Operational dashboards and alerts
13. Known limitations
14. Recommended next steps

Classify every major capability as:

- IMPLEMENTED AND VERIFIED
- IMPLEMENTED BUT NOT FULLY VERIFIED
- PARTIALLY IMPLEMENTED
- NOT IMPLEMENTED
- BLOCKED

Do not label the application production-ready if a critical control is missing or a required verification was not performed.

---

## 16. Required implementation workflow

Use this workflow for each phase:

1. Inspect the relevant implementation and tests.
2. Write a short plan and list the files expected to change.
3. Add or update regression tests for the finding.
4. Implement the smallest correct fix.
5. Run focused tests.
6. Run related integration/security tests.
7. Run the full relevant test suites.
8. Inspect the final diff for security regressions and unrelated changes.
9. Update the relevant documentation.
10. Record actual commands, pass/fail counts, and unresolved issues.

If a test fails, investigate the cause. Do not delete, weaken, or skip the test merely to obtain a passing result.

When a phase is complete, report:

- Findings fixed
- Files changed
- Tests executed and exact outcomes
- Security impact
- Compatibility risks
- Remaining issues
- Whether the phase's exit criteria were met

Proceed to the next phase only when the preceding phase meets its exit criteria. If blocked, preserve working changes, document the blocker, and continue with independent non-risky tasks where possible.

## 17. Required deliverables

Create or update these documents without duplicating existing documentation unnecessarily:

- `docs/production-hardening/baseline.md`
- `docs/production-hardening/security-remediation.md`
- `docs/production-hardening/ai-quality-evaluation.md`
- `docs/production-hardening/observability-and-operations.md`
- `docs/production-hardening/deployment-runbook.md`
- `docs/production-hardening/release-readiness.md`

Include a prioritized backlog with severity, impact, dependencies, acceptance criteria, and verification evidence.

Keep reports concise enough for engineering use. Redact secrets and personal data.

## 18. Definition of done

The system may be recommended for production only when:

- Authentication and authorization are verified across sensitive APIs.
- Employee data is protected by object-level and field-level access controls.
- SQL Agent restrictions hold under adversarial testing.
- HR actions require secure, atomic, independently authorized confirmation.
- Timeouts and uncertain action outcomes are handled safely.
- AI prompts, retrieved documents, and model outputs cannot grant permissions.
- Provider data handling is documented and approved.
- Rate limits and resource budgets are enforced.
- Retrieval quality is evaluated with real embeddings and representative data.
- Live-model behavior is evaluated where the required credentials are available.
- Audit, retention, telemetry, and alerts have been validated.
- Dependencies and production containers are supported and appropriately patched.
- CI, migrations, backups, restoration, and rollback procedures are tested.
- Remaining risks are explicit and accepted by the responsible owner.

A passing unit-test suite alone is not sufficient.

## Start now

Begin with Phase 0. Inspect the current Git state and repository, then execute the phases in order. Prioritize security vulnerabilities over AI feature expansion. Preserve the current architecture and all unrelated user work.

At the end of each phase, provide an evidence-based report. At the end of the overall run, state one of the following:

- **PRODUCTION CANDIDATE — REQUIRED CHECKS VERIFIED**
- **NOT YET PRODUCTION READY — BLOCKING ITEMS REMAIN**
- **VERIFICATION INCOMPLETE — REQUIRED ENVIRONMENT OR EVIDENCE UNAVAILABLE**

Never claim production readiness based only on source-code inspection or passing unit tests.