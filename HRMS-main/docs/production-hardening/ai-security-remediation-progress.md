# AI Security Remediation — Progress Tracker

**Programme:** autonomous remediation of the AI safety audit and the R1 (PII minimisation) audit, both taken at
`f8c551a` on 2026-10-18. **Started:** 2026-10-18. **Release status: NOT YET PRODUCTION READY** (unchanged by this
programme; see the final assessment at the end).

Rules for this file: record only what was actually executed and observed. Tests run in the `hrms-test-py311` container
(Python 3.11, no API keys: `-e OPENAI_API_KEY= -e ANTHROPIC_API_KEY=`). Synthetic data only. Never push or deploy.
Governance decisions are never assumed: dependent work is marked **BLOCKED** with the decision it needs.

## 0. Baseline (Phase 0, 2026-10-18)

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `f8c551a` (21 commits ahead of `origin/main`, not pushed) |
| Working tree at start | R1 Task 1 changes (uncommitted, verified the same day) + 4 protected untracked `writing-block*.md` files (never touched) |
| Test commands | backend `python -m pytest -q`; offline evals `python -m evals.run_evals`; gate tests `pytest .github/scripts`; frontend `npm test`, `npx tsc --noEmit`, `npm run build` |
| Last observed results | 2026-10-18 (R1 Task 1 tree): backend 2924 passed, offline evals 139/139, gate tests 25 passed. Live-model eval NOT_RUN |
| Sources reconciled | AI safety audit (2026-10-18, chat report), R1 audit (2026-10-18), `data-processing.md`, `release-readiness.md`, `monitoring-alerts.md`, `ai_permissions_matrix.md` |

## 1. Finding → phase map

Status: OPEN · IN PROGRESS · DONE (committed) · BLOCKED (decision named) · NOT PLANNED (reason given).

| ID | Finding (revalidated against `f8c551a`) | Phase | Acceptance criteria | Verification | Status |
|---|---|---|---|---|---|
| AIS-01 | Single-value SQL answer ("The result is …") stored as assistant text and sent to the classifier model as history | 1 | SQL-derived values absent from every later provider payload; immediate answer and owner replay unchanged | two-turn HTTP tests with payload spy; negative control on the old code | DONE (phase 1) |
| AIS-02 | No central provider boundary: user text and history leave unminimised; no operation allowlist | 2 | every model call passes one policy in `audit/provider.observe`; unknown operation fails closed; credentials never sent; identifiers replaced before sending and restored only in the same request's output; counts (never values) audited | per-operation payload tests for `/chat` and the 3 direct endpoints | DONE (phase 2) |
| AIS-03 | Narrow detection (no email, separated phone, Aadhaar); no Unicode normalisation | 3 | NFKC + format-character stripping on every question; extended patterns with a measured synthetic false-positive / false-negative corpus | table-driven corpus tests | DONE (phase 3) |
| AIS-04 | Regex refusal rules run on `/chat` only | — | — | — | NOT PLANNED: stricter direct-endpoint refusals change their error contract; the deterministic controls behind them (SQL validator, scope, registry, confirmation, RAG guard) are the boundary and are tested per endpoint |
| AIS-05 | Anthropic endpoint not pinned (OpenAI is) | 4 | Anthropic client pinned to the public API URL; `ANTHROPIC_BASE_URL` ignored | provider tests | DONE (phase 4) |
| AIS-06 | Anthropic server-side fallback beta on by default | 4 | existing configuration preserved; decision recorded | — | BLOCKED (G-05) |
| AIS-07 | Log-privacy tests inspect only `ai.audit` lines | 5 | full stdout/stderr of whole requests (success and error paths) free of synthetic PII, tokens, prompts | capsys/capfd tests | DONE (phase 5) |
| AIS-08 | Storage privacy not pinned by tests (conversation rows, pending-action summary, terminal records) | 5 | tests pin what is stored; no business payload altered | storage tests | DONE (phase 5) |
| AIS-09 | Leave reason (possibly health data) in action summary and `payload_json` | 5 / 9 | documented and tested as-is; clearing stored payloads needs approval | — | BLOCKED (G-02, G-03) for any change |
| AIS-10 | No request deadline or per-request model-call budget | 6 | bounded end-to-end deadline and call budget enforced before each model call; SDK retries cannot outlive the deadline; failures are safe AI errors | unit + HTTP tests | DONE (phase 6) |
| AIS-11 | Daily cost cap disabled; no pricing configured | 6 | — | — | BLOCKED (G-07: verified pricing and budget) |
| AIS-12 | One-click confirmation for high-impact actions (deactivation, announcements) | 7 | step-up re-authentication for high-impact AI actions; low-risk flow unchanged; replay/expiry/tamper/cross-user/concurrency still fail | backend + frontend tests | DONE (phase 7) |
| AIS-13 | In-memory rate limiter (single worker) | 7 | documented; production already refuses more than one worker (`production_checks.py`) | existing test | DONE (phase 7, documentation) |
| AIS-14 | No prompt version or retrieved-chunk ids in audit | 8 | prompt fingerprint and chunk ids recorded as identifier tokens only | audit tests | DONE (phase 8) |
| AIS-15 | No alerting tooling | 8 | — | — | BLOCKED (G-08: monitoring tooling, owners, channels) |
| AIS-16 | Groundedness heuristics / false refusals not measured | 8 | extra RAG guard tests; false-refusal cases measured without loosening any refusal rule | tests / eval | DONE (phase 8, measured) |
| AIS-17 | Retention purge operator-run; partial-failure behaviour untested | 9 | dry run, idempotency, rollback on failure, EXECUTING/UNKNOWN preserved, audit append-only — tested; scheduling only with deployment support | retention tests | DONE (phase 9) |
| AIS-18 | Retention periods, erasure, audit archival | 9 | — | — | BLOCKED (G-03, G-04) |
| AIS-19 | Live-model evaluation never run | — | — | — | BLOCKED (G-06) |
| AIS-20 | Strict script CSP / JWT in `localStorage` | — | — | — | BLOCKED (CSP decision review of 2026-10-18 awaiting approval; S1) |
| AIS-21 | Request-body size limit not verified at the app layer | — | — | — | NOT PLANNED: deployment-level (TLS reverse proxy); `validate_question` bounds the text that is used |

## 2. Governance gates (decisions the organisation must make; never assumed)

| Gate | Decision | Owner | Blocks |
|---|---|---|---|
| G-01 | Data categories each provider may receive (register #1, #7) and the DPA / retention / ZDR / training / residency terms (#2, #3, #8, #9) | Legal / DPO / account owner | Enabling AI with real HR data |
| G-02 | Whether health-related leave reasons may be sent to an external model at all (R1 N2) | Legal / DPO + HR | Any change to action free text handling |
| G-03 | Retention periods (90 / 30 / ≥365 days) and whether stored action free text may be cleared after completion | HR / Legal | AIS-09, AIS-18 |
| G-04 | Erasure on request / on employee exit; audit archival procedure | Legal / DPO | AIS-18 |
| G-05 | Anthropic server-side fallback beta acceptable under the provider terms | Account owner + legal | AIS-06 |
| G-06 | Live-model evaluation: approved key, budget, synthetic-only inputs | Product + finance | AIS-19 |
| G-07 | Verified model pricing and per-user cost budget | Finance | AIS-11 |
| G-08 | Monitoring/alerting tooling, owners, channels (`monitoring-alerts.md`) | Ops | AIS-15 |
| G-10 | Refusal-rule tuning: 6 of 18 benign sensitive-keyword policy questions are refused by the deterministic rules (AIS-16). Loosening a refusal rule weakens a control | Security + product | false-refusal reduction |
| G-11 | Step-up on the high-impact business endpoints (review M1). **Implementation approved and done 2026-10-18 in `log` mode; admin-UI password prompt done 2026-10-19 (§6)**; switching `STEP_UP_ENFORCEMENT` to `enforce` still needs explicit approval and an enforcement-readiness review | Security + product | stolen-token protection for high-impact actions |
| G-09 | Conversation storage minimisation of contact data (would change what users see when replaying their own history) | Product + DPO | further storage redaction |

## 3. Phase log

(Each phase appends: expected behaviour, files changed, commands executed, observed results, commit, remaining risks.)

### Phase 1 — AIS-01 SQL-derived values in classifier history · DONE

- **Expected behaviour:** the user still receives the full SQL answer and can replay it; no later provider payload
  contains a value from an earlier SQL result.
- **Change:** `conversation/service.py` — `build_context` uses `_context_text()`: the assistant reply of any turn whose
  stored route includes `SQL` becomes `SQL_REPLY_IN_CONTEXT` (decided by the backend-recorded route, not by pattern
  matching; also covers rows stored before the fix; stored rows unchanged). Limitation: a combined turn
  (e.g. `POLICY+SQL`) is replaced as a whole.
- **Tests:** `tests/ai/orchestration/test_provider_payloads.py` — `test_sql_values_never_reach_the_classifier_through_history`
  (synthetic email, leave reason, ticket text; actual outbound payloads of both turns), `test_direct_sql_endpoint_response_is_unchanged`;
  `tests/ai/conversation/test_conversation_service.py` — `test_sql_turn_replies_never_enter_the_context` (8 routes),
  `test_sql_placeholder_keeps_follow_up_state`. Doc: `data-processing.md` call site 1 corrected.
- **Executed 2026-10-18:** focused 61 passed; the new two-turn tests on the original `service.py` (read-only mount,
  copy in `/tmp`) failed 3/3 with the values present in the classifier payload; full backend **2924 passed**; offline
  evals **139/139**; gate tests **25 passed**; `git diff --check` clean.
- **Remaining:** the stored conversation still contains the value (owner-only replay; G-09).

### Phase 2 — AIS-02 Central provider boundary · DONE

- **Expected behaviour:** every model call passes one policy before the provider is called; an unregistered operation
  never reaches a provider; credentials are never sent; recognisable identifiers leave only as per-call placeholders;
  business values the user typed still reach the business API unchanged; the audit holds counts, not values.
- **Change:** new `services/ai/provider_policy.py` (`PROVIDER_OPERATIONS`, `prepare`, `restore_structured`,
  `restore_text`); `audit/provider.ObservedLLMProvider` prepares every call (`_prepare`) before the concurrency slot and
  restores placeholders in that call's output; `errors.AIProviderPolicyError` (`AI_PROVIDER_POLICY_BLOCKED`, 500);
  `audit/events.AuditMetadata.redacted_values` / `redacted_categories`; `redaction.py` patterns named for reuse (no
  behaviour change). Phase 2 categories: CREDENTIAL (irreversible), PAN, IFSC, NUMBER (9–18 digits).
- **Tests:** `tests/ai/test_provider_policy.py` (9: allowlist, stable placeholders, unchanged ordinary text,
  neutralised placeholder-looking input, fail-closed preparation, re-validated restoration, provider never sees values);
  `tests/ai/orchestration/test_provider_payloads.py` — payload spy moved BEHIND the boundary; `/chat/policy`,
  `/chat/sql`, `/chat` router payloads free of synthetic PAN/account/JWT; action arguments restored into the pending
  payload while the provider saw placeholders; audit counts without values; unregistered operation never called.
- **Existing tests adjusted (not weakened):** `tests/ai/audit/test_audit_recorder.py` (2) and `tests/ai/test_limits.py`
  (2) wrapped fakes with arbitrary operation names (`"test_operation"`, `"test"`); those now fail closed by design, so
  they use the registered `"policy_answer"`. Their assertions are unchanged. (With the old names, `test_limits` waited
  forever on a provider that the boundary never called: the cause of one hung run, killed after 49 minutes.)
- **Executed 2026-10-18:** focused suites (policy 9, orchestration 273, audit 166, providers 15, OpenAI 42, hardening
  187) — all passed after the fixes above; full backend **2938 passed**; offline evals **139/139**; gate tests **25**.
- **Remaining:** names and free text are not recognisable (G-01/G-02); the model can mangle a placeholder (then the
  placeholder text, not the value, reaches the business API).

### Phase 3 — AIS-03 Normalisation and identifier detection · DONE

- **Expected behaviour:** every question is NFKC-normalised with invisible format characters removed before any rule,
  service or model sees it; the provider boundary also recognises emails, phone numbers and spaced Aadhaar numbers;
  the current message and the history are minimised identically; dates, amounts, ranges and short ids are untouched.
- **Change:** `validation.normalize_text` (NFKC + Unicode category Cf removed) used by `validate_question` (all entry
  points) and by `provider_policy.prepare` (all provider-bound text, including stored history); `redaction.py`:
  `EMAIL_RE`, `AADHAAR_SPACED_RE`, `PHONE_CANDIDATE_RE` + `is_phone_number` (with "+": 8–15 digits; otherwise a
  10-digit Indian mobile, optional 0/91 prefix; one consistent separator per number). These are provider-boundary
  only: `redact()` (stored history, logs, SQL results shown to the user) is unchanged — that is decision G-09.
- **Measured (synthetic corpus, `tests/ai/test_pii_detection_corpus.py`):** 22 positives (email, PAN, IFSC, Aadhaar
  spaced/unspaced, account, 9 phone formats, full-width and Devanagari digits, zero-width obfuscation, mixed message,
  credential) — **0 false negatives**; 9 negatives (dates, times, amounts, ranges, ticket/employee/order ids,
  invoice/version strings, short digit groups) — **0 false positives**; 4 pinned known misses (person names, spelled
  digits, free-text health reason, irregular grouping). One defect found and fixed while building the corpus: a date
  followed by a phone number fused into one 18-digit candidate and the phone was missed.
- **Tests:** corpus (43 incl. normalisation of full-width, zero-width, bidi override, ligature; invisible-only question
  rejected; non-Latin text unchanged); `test_contact_details_in_current_and_earlier_turns_never_reach_the_classifier`.
- **Executed 2026-10-18:** focused 550 + corpus 43 passed; full backend **2982 passed**; offline evals **139/139**;
  gate tests **25**.
- **Remaining:** names and free text (G-01/G-02); other national formats beyond the corpus are untested.

### Phase 4 — AIS-05 Provider endpoint pinning · DONE (AIS-06 BLOCKED on G-05)

- **Expected behaviour:** neither real provider can be redirected by an environment variable; a misconfigured provider
  never falls back to the other real provider; the server-side fallback setting is unchanged until G-05 is decided.
- **Revalidation:** with the installed SDK (anthropic 1.9.0) an unpinned `AsyncAnthropic` client followed
  `ANTHROPIC_BASE_URL=https://attacker.example` (observed in the test container, synthetic key, no request made).
- **Change:** `llm/anthropic_provider.py` — `ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"` passed as
  `base_url`. Register (`data-processing.md` §4) updated.
- **Tests (`tests/ai/rag/test_providers_rag.py`):** main and fast clients stay on the pinned URL with a hostile
  `ANTHROPIC_BASE_URL`; fallback beta only as configured (main on by default, fast never, off when disabled); a
  misconfigured OpenAI or Anthropic selection fails closed instead of using the other provider's key.
- **Executed 2026-10-18:** provider suites 110 passed; full backend **2985 passed**; offline evals **139/139**; gate
  tests **25**.

### Phase 5 — AIS-07 / AIS-08 Output and storage privacy tests · DONE

- **Expected behaviour:** no request writes user canaries, bearer tokens, confirmation tokens, HR row values, model SQL or
  system-prompt text to stdout/stderr on any path; AI tables keep no credentials or confirmation tokens; business
  values the user typed reach the business record unchanged.
- **Tests (`tests/ai/hardening/test_output_privacy.py`, synthetic canaries: email, phone, PAN, JWT):**
  - fd-level capture (`capfd`: every logger and print, stdout + stderr) across a policy turn, an SQL turn (the user
    receives the row value), an action proposal and its confirmation through the real ticket endpoint, an SQL rejected
    by the validator, malformed model output, and a malformed policy answer — none of the forbidden strings appear;
  - conversation rows: credentials and PAN never stored; **email and phone the user typed are stored** in their own
    owner-only history (pinned explicitly; changing it is decision G-09);
  - pending action after confirmation: `COMPLETED`, `used_at` set, only the token hash stored, no credential; the
    ticket description keeps the user's own contact values (restored from placeholders).
- **Code changes:** none — the existing logging was already content-free on every exercised path. (While writing the
  test, the generic needle `"SELECT "` matched the audit label `query_type=SELECT`; the check now uses the actual model
  SQL texts.) The HMAC fingerprint is already pinned by `tests/ai/audit/test_audit_recorder.py::test_text_fingerprint_is_keyed_and_content_free`.
- **Executed 2026-10-18:** new tests 3 passed; full backend **2988 passed**; offline evals **139/139**; gate tests **25**.
- **Remaining:** AIS-09 (leave reasons and other free text in action records) unchanged — G-02 / G-03.

### Phase 6 — AIS-10 Request deadline and model-call budget · DONE (AIS-11 BLOCKED on G-07)

- **Expected behaviour:** one AI HTTP request makes at most `AI_MAX_MODEL_CALLS_PER_REQUEST` model calls (default 8 =
  the most a legitimate `/chat` turn needs: 4 classifier clauses + policy + SQL generation + SQL wording + action) and
  finishes its model work within `AI_REQUEST_DEADLINE_SECONDS` (default 120 s; provisional, operations to tune; 0
  disables). Queueing, the provider call and the SDK's own retries all count against the deadline. Failures are AI
  errors (`AI_REQUEST_LIMIT_EXCEEDED` 422, `AI_REQUEST_TIMEOUT` 504); nothing is executed — confirmation makes no model
  call, and a proposal that times out creates no pending action.
- **Change:** `limits.py` — `RequestBudget`, `charge_model_call()` (before every call), `within_deadline()` (cancels
  the provider call at the deadline), deadline-aware `llm_slots.slot()` (AI_BUSY unchanged when the queue timeout is
  shorter), `AIRequestBudgetMiddleware` (pure ASGI, AI paths only, reset after the request); `audit/provider.observe`
  charges every call; `errors.py` two classes (and the 422 constant now uses Starlette's current name); `config.py`
  two settings; `main.py` registers the middleware. Enforcement lives in `limits.py`, not in the audit scope ("audit
  observes, never authorizes").
- **Not done:** a circuit breaker — not justified while one provider is configured and every failure already fails
  fast and closed; per-user cost caps stay disabled until verified pricing exists (G-07).
- **Tests (`tests/ai/orchestration/test_request_budget.py`, 8):** no budget outside requests; call budget enforced
  before the call; passed deadline refuses the next call; a slow provider call is cancelled at the deadline; queueing
  never outlives the deadline while AI_BUSY still wins when shorter; the default covers the longest legitimate turn;
  HTTP: budget exceeded → 422, no business change, budget not leaked; slow model → 504 within 2 s, provider call
  cancelled, no pending action.
- **Executed 2026-10-18:** focused 84 passed; full backend **2996 passed**; offline evals **139/139**; gate tests **25**.

### Phase 7 — AIS-12 Step-up for high-impact actions · DONE; AIS-13 rate limiter · DOCUMENTED

- **Expected behaviour:** `EMPLOYEE_DEACTIVATE` and `ANNOUNCEMENT_CREATE` (org-wide or team reach; account lock-out)
  need the confirming user's own password in addition to the single-use confirmation, so a stolen session token alone
  cannot confirm them **through the Copilot**. **Correction (independent review M1, Phase 10):** this does not stop a
  token thief, who can call `DELETE /api/v1/employees/{id}` or `POST /api/v1/announcements` directly without a
  password; step-up on the business endpoints is a business-endpoint change (rule 7) — gate G-11. Order on confirm: record checks (owner, unused, unexpired, binding) →
  capability/target re-authorization → password (throttled per user before hashing) → atomic claim → audit → execute.
  A refused password (403 `AI_STEP_UP_FAILED`, never 401 — the session is valid) or throttle (429
  `AI_STEP_UP_THROTTLED`) leaves the confirmation PENDING and executes nothing. All other actions are unchanged.
- **Change (backend):** `actions/registry.ActionSpec.step_up` (set for the two actions); `ActionResponse.step_up_required`;
  `schemas/chat.ActionConfirmRequest.password` (unconstrained so a validation error can never echo it);
  `actions/service._verify_step_up` (login limiter key `ai-step-up|<employee>`, bcrypt check against the DB hash);
  `errors.AIStepUpFailedError`, `AIStepUpThrottledError`; audited as `ACTION_REJECTED` (DENIED) with the code only.
- **Change (frontend):** password field only on step-up cards (`components/copilot/message-parts.tsx`), cleared after
  every attempt; `lib/copilot-api.confirmAction` sends `password` only when given; the shell keeps the card open after
  a step-up refusal (user-initiated new attempt; nothing automatic); `lib/copilot-errors.ts` messages for the step-up
  codes and for Phase 2/6 codes (`AI_PROVIDER_POLICY_BLOCKED`, `AI_REQUEST_LIMIT_EXCEEDED`, `AI_REQUEST_TIMEOUT`).
- **Existing tests adjusted (intent unchanged: the authorized user confirms):** `tests/ai/actions/conftest.py`
  (`propose_and_confirm` passes the synthetic test password for step-up actions only), `test_action_eval.py`,
  `test_action_parity.py`, `test_announcement_audience_ai.py`, `test_confirmation_parity.py` (one call each). A static
  guard (`test_no_ai_module_references_forbidden_routes`) caught an auth route path in a new comment; the comment was
  reworded, the guard untouched.
- **New tests:** `tests/ai/actions/test_step_up.py` (11: only the two actions; ordinary actions unchanged; missing /
  empty / wrong / over-long / non-string password refused with nothing executed and the record still PENDING; correct
  password executes once, replay still refused, audit has the code but no password, no password in stdout/stderr;
  authorization re-checked before the password; throttling before hashing; announcements too);
  `frontend/tests/copilot-shell.test.tsx` (3: password required and sent only with that confirmation and cleared;
  refusal keeps the card open for a new attempt; ordinary cards show no password field).
- **AIS-13:** the in-memory limiters stay single-process by design; `production_checks.py` already refuses more than
  one worker. A shared store (e.g. Redis) would be new infrastructure — not introduced (approval gate 4).
- **Executed 2026-10-18:** backend focused 1400 + 42 passed; full backend **3007 passed**; offline evals **139/139**;
  gate tests **25**; frontend **185 passed**, `tsc` 0 errors, `next build` compiled.
- **Remaining:** no second-person approval for high-impact actions (a business policy decision, not assumed).

### Phase 8 — AIS-14 traceability, AIS-16 measured quality · DONE (AIS-15 alerting BLOCKED on G-08)

- **Expected behaviour:** every provider event names which fixed system prompt was used (12-hex SHA-256 prefix,
  `prompt_version`), and every policy answer records the cited chunk ids (`source_ids`, opaque 32-hex ids) — enough
  to trace a request across classification, retrieval, model call and response without storing any text.
- **Change:** `audit/events.AuditMetadata.prompt_version`, `.source_ids` (validated as identifier tokens);
  `audit/provider.prompt_version()` and `_meta(schema, system)`; `rag/service` records `source_ids` on
  `POLICY_RESPONSE`.
- **Measured (scripted models; NOT real-model quality):** refusal rules on an 18-question benign HR set — 12 pass,
  **6 false refusals** (bank-detail update, PAN onboarding documents, password reset, password-sharing policy,
  privacy-policy coverage of salary data, DOB visibility). Pinned, not changed: loosening is gate G-10. The offline
  eval suite was not extended with these, because its cases must state the correct expected outcome and today's
  outcome for six of them is wrong; the measurement lives in pytest instead.
- **RAG guard:** existing tests already cover not-grounded, missing/fabricated citations, empty answers, sensitive
  values and terms, action/role claims, injection echoes and unsupported numbers. Added: a citation list with one
  unknown label is rejected; **pinned limitation** — an invented rule without numbers that cites a real excerpt passes
  (no entailment check; only an evaluation can measure it, G-06).
- **Alerting (AIS-15):** `monitoring-alerts.md` lists the signals (provider failures, AI_BUSY, budget hits,
  ACTION_OUTCOME_UNKNOWN, audit write failures); no tooling exists in the repository or deployment — BLOCKED (G-08).
- **Tests:** `tests/ai/hardening/test_observability_quality.py` (23).
- **Executed 2026-10-18:** focused 372 + 23 passed; full backend **3030 passed**; offline evals **139/139**; gates **25**.

### Phase 9 — AIS-17 Retention safety and observable status · DONE (AIS-18 BLOCKED on G-03 / G-04)

- **Expected behaviour:** retention stays a dry run by default and an ADMIN-operated apply; a second apply removes
  nothing; a failure part-way leaves every AI table as it was; EXECUTING/UNKNOWN actions and audit events are never
  deleted; a scheduler can detect that a purge is due without deleting anything.
- **Reconciliation finding:** `monitoring-alerts.md` said the retention dry run's exit code is the scheduling signal,
  but `retention_purge.py` always exited 0. Fixed with an opt-in `--check` (dry run; exit 3 when conversations,
  messages or pending actions are past retention; cannot be combined with `--apply`), same convention as
  `stale_actions_report.py`. Docs: `monitoring-alerts.md`, `deployment-runbook.md`.
- **Change:** `services/ai/retention.check_exit_code`, `CHECK_DUE_EXIT_CODE`; `scripts/retention_purge.py --check`.
  No retention period, deletion rule or audit lifecycle changed.
- **Tests (`tests/ai/test_retention.py`, +4):** idempotent apply; a simulated failure on the third delete rolls back
  the first two; `check_exit_code` 3 → 0 after an apply; `--check` deletes nothing and `--check --apply` is refused
  (exit 2). Append-only audit triggers are already pinned (`tests/ai/audit/test_audit_api.py`).
- **Not done (gated):** scheduling itself (no scheduler exists in the deployment; ops decision G-08); approval of the
  periods (G-03); erasure on request or on exit and audit archival (G-04); clearing free text in action records (G-02).
- **Executed 2026-10-18:** retention 15 passed; full backend **3034 passed**; offline evals **139/139**; gates **25**.

### Phase 10 — Full regression and independent review · DONE

- **Cumulative scope `f8c551a..HEAD`:** AI services, the AI chat endpoint, `config.py`, `main.py` (one middleware, one
  AI-path validation handler), `schemas/chat.py`, tests, docs, frontend Copilot components. **No** migrations,
  business endpoints, `services/auth.py`, models or dependency files changed. No test skipped, xfailed or deselected.
- **gitleaks (full history):** 3 findings — the synthetic JWT-shaped canaries added by AIS-02/AIS-07 tests
  (decode to `{"alg":"HS256"}` / `{"sub":"test"|"canary"}` / fake signatures). History is not rewritten; the three
  exact fingerprints were added to `.gitleaksignore` with the reason, following the existing reviewed precedent (plus two
  for the synthetic probe password of the L1 test, found by the rescan after the Phase 10 commit) —
  **reviewer confirmation requested**. Rescan: no leaks.
- **Independent review** (read-only security-review agent over the cumulative diff): no CRITICAL/HIGH. Actions:
  - **M1** step-up protects the Copilot path only (a token thief can call the business endpoints directly) —
    CONFIRMED; tracker and `ai_permissions_matrix.md` corrected; business-endpoint step-up is gate **G-11**.
  - **L1** FastAPI's default 422 echoed request inputs, including a step-up password — reproduced (2/2 bodies);
    fixed: `main._validation_error_handler` drops `input`/`ctx` on AI paths only (business paths keep the default,
    pinned by a test).
  - **L2** step-up shared the sign-in limiter's key space — fixed: dedicated `limits.step_up_limiter` (reset by the
    test harness like the others).
  - **L3** the request budget is unbounded outside AI paths — test added: every route depending on
    `get_ai_user_context` / `enforce_ai_limits` is inside the middleware prefixes.
  - **I1** the classifier turned limit/timeout/policy errors into `AI_CLASSIFICATION_FAILED` — fixed: re-raised with
    their own codes (still fail closed).
  - **I2** `observe()` trusted an `observed` attribute — fixed: `isinstance(ObservedLLMProvider)`.
  - **I3** NFKC + Cf removal also strips ZWJ/ZWNJ in some Indic/Persian spellings (stricter only) — accepted, noted.
  - **I4** audit shows how many identifiers a turn contained (counts, ADMIN-only) — accepted, noted.
- **Executed 2026-10-18:** review-fix suites 302 passed; full backend **3042 passed**; offline evals **139/139**; gates
  **25**; frontend **185 passed**, `tsc` 0 errors, `next build` compiled (no frontend change since).

## 4. Final assessment (Phase 11, 2026-10-18)

**Verdict: NOT YET PRODUCTION READY.** The implementable findings are fixed and tested; the remaining blockers are
organisational decisions and live verification, which this programme must not assume.

| Status | Findings |
|---|---|
| DONE (code + tests) | AIS-01, 02, 03, 05, 07, 08, 10, 12, 14, 16 (measured), 17; review fixes L1, L2, L3, I1, I2 |
| DONE (documentation) | AIS-13 (single worker enforced by `production_checks.py`) |
| BLOCKED (decision) | AIS-06 (G-05), 09 (G-02/G-03), 11 (G-07), 15 (G-08), 18 (G-03/G-04), 19 (G-06), 20 (CSP decision review / S1); G-09, G-10, G-11 |
| NOT PLANNED (reason in §1) | AIS-04, AIS-21 |

**Last executed results (HEAD after phase 10, Python 3.11 container, no API keys):** backend 3042 passed, 0 skipped;
offline evals 139/139 (scripted providers); gate tests 25; frontend 185 passed, `tsc` 0 errors, `next build` compiled;
gitleaks no leaks (five exact synthetic-fixture fingerprints allowlisted, reviewer confirmation requested).
Live-model evaluation: **NOT_RUN**. Only SQLite verified. Nothing pushed or deployed.

**Data-flow summary after the programme:** every model call passes `audit/provider.observe` → `provider_policy.prepare`
(operation allowlist; NFKC; credentials removed; email/phone/PAN/IFSC/Aadhaar/long numbers → per-call placeholders,
restored only in that call's output) → per-request budget and deadline → concurrency slot → pinned provider endpoint.
SQL rows stay in the backend by default and SQL replies never re-enter classifier history. Names and free text
(leave reasons, ticket text, policy wording) still reach the provider as typed (G-01/G-02).

**Release blockers (explicit):** G-01 provider data categories / DPA / retention / ZDR / residency (both providers);
G-06 live-model evaluation (quality, false refusals, groundedness on real models); G-03/G-04 retention periods,
erasure and audit lifecycle; G-08 monitoring and alerting tooling; AIS-20 strict script CSP and token storage;
G-11 business-endpoint step-up if stolen-token protection for high-impact actions is required; plus the pre-existing
gates in `release-readiness.md` §6 (PostgreSQL not verified, `braces` advisory exception PROPOSED, restore drill).

**Commits (local only, on `main`):** `3db7479` phase 0 · `4f43f1c` AIS-01 · `cde6ec6` AIS-02 · `be543d4` AIS-03 ·
`c6500de` AIS-05 · `be41b8c` AIS-07/08 · `eefc1d8` AIS-10 · `3550197` AIS-12 · `f3cdb59` AIS-14/16 · `18233fc` AIS-17 ·
`fd18e8c` review fixes · `a608039` gitleaks allowlist · (this assessment). No migrations.

## 5. G-11 — Step-up on high-impact business APIs (approved 2026-10-18) · IMPLEMENTED, `log` mode

- **Threat:** a stolen ADMIN access token (15-minute life, revocable by global sign-out) could, without any password:
  create an account with any role and a chosen password (standing access), reset another user's password, email or
  role (takeover), change bank/PAN details, deactivate or reactivate accounts, and publish ORG-wide announcements/polls.
- **Change:** `app/services/step_up.py` — `check_step_up` (shared with the Copilot: missing → no hashing and no attempt
  counted; otherwise `core/rate_limit.step_up_limiter` per user BEFORE bcrypt; >128 chars refused) and
  `enforce_step_up` (`STEP_UP_ENFORCEMENT`: `off` / **`log` (default)** / `enforce`; logs
  `auth.step_up.not_satisfied` with operation, employee id and outcome only). Header `X-Step-Up-Password` (CORS already
  allows all headers). Called after each endpoint's existing authorization and validation and before any mutation in:
  `DELETE /employees/{id}`, `PATCH /employees/{id}/reactivate`, `POST /employees`, `PUT /employees/{id}` (only when the
  password, role, email, bank or PAN fields actually change — unchanged resubmissions need nothing),
  `POST /announcements` and `POST /polls` (ORG audience only). Codes in `enforce`: 403 `STEP_UP_REQUIRED`, 403
  `STEP_UP_FAILED`, 429 `STEP_UP_THROTTLED`. Copilot: `actions/service` uses the shared check (key `ai-step-up|id`;
  business key `api-step-up|id`) and forwards the verified password to `HRMSActionClient.execute(step_up_password=…)`,
  which sends the header for that one in-process call and drops it. No migration, no new dependency.
- **Tests:** `tests/rbac/test_step_up_api.py` (109): every protected operation × missing / empty / wrong / over-long
  password refused in `enforce` with an unchanged business snapshot and no echo; correct password succeeds; throttling
  (429) before hashing with nothing changed; unauthorized callers get byte-identical answers to `log` mode (authorization
  first); 404/400 validation precedence kept; non-sensitive updates, unchanged resubmissions, TEAM announcements/polls
  stay passwordless; `log` mode never blocks and logs the outcome; `off` skips; no password in stdout/stderr or any
  response; the route-permission matrix rows of all six routes hold under `enforce` with the password.
  `tests/ai/actions/test_step_up.py` (+3): Copilot deactivation and ORG announcement complete under `enforce`; no
  password in the pending action, audit rows or output; negative control — if forwarding were removed the business API
  alone refuses (reported to the Copilot as the existing generic `FORBIDDEN`) and nothing changes.
- **Rollout risks / prerequisites before `enforce` (approval required):**
  1. ~~the admin UI does not send `X-Step-Up-Password` yet~~ — done in G-11 phase 2 (§6); `log` mode still shows how
     often each operation would be refused (`auth.step_up.not_satisfied`);
  2. any other API client (scripts, integrations) must add the header;
  3. reverse proxies must not log request headers (the password travels in one);
  4. `current_salary_usd` is not in the step-up field set (the approved scope named password, role, email, bank, PAN);
  5. all six endpoints' refusals in `log` mode are only logs, not audit events (business endpoints have no audit trail).
- **Executed 2026-10-18:** `tests/rbac/test_step_up_api.py` 109 passed; actions + rbac suites 1018 passed (one expectation
  corrected: the Copilot reports business 403s as the existing generic `FORBIDDEN`); full backend **3154 passed**, 0
  skipped; offline evals **139/139**; gate tests **25**; frontend **185 passed**, `tsc` 0 errors (no frontend change;
  `next build` not re-run); gitleaks on the new files: only the already-allowlisted lines 129/131 of
  `test_step_up.py`. `STEP_UP_ENFORCEMENT` stays `log`.

## 6. G-11 phase 2 — Admin UI password step-up (2026-10-19) · IMPLEMENTED (frontend only; backend unchanged, `log` mode)

- **Change:** `lib/api.ts` — `STEP_UP_HEADER` and an optional trailing `stepUpPassword` argument on `createEmployee`,
  `updateEmployee`, `deactivateEmployee`, `reactivateEmployee`, `createAnnouncement` and `createPoll`; it is sent only
  as the `X-Step-Up-Password` header (never URL, query or body). `lib/step-up.ts` — `employeeUpdateNeedsStepUp`
  (mirrors the backend's `_update_needs_step_up`: new password, different role, email (case-insensitive), changed
  bank/PAN after the backend's normalisation), `audienceNeedsStepUp` (ORG), and the mapping of `STEP_UP_REQUIRED` /
  `STEP_UP_FAILED` / `STEP_UP_THROTTLED` (either envelope) to clear "Nothing was changed" messages.
  `components/security/step-up-dialog.tsx` — reusable `useStepUp()` + accessible modal dialog (`role="dialog"`,
  labelled, `type=password`, `autocomplete=current-password`, focus on open, Escape/Cancel). Pages: announcements and
  polls (ORG only; TEAM posts unchanged), employees directory (deactivate / reactivate), employee form (create always;
  edit only when a protected value really changes; if the backend still answers `STEP_UP_REQUIRED` to an unprompted
  edit, the dialog opens and nothing is resent until the administrator confirms).
- **Password handling:** typed into the dialog's own state only; cleared on submit (whatever the outcome), cancel,
  Escape and close (unmount); passed straight into the one request; never in localStorage/sessionStorage/cookies,
  global state, logs or analytics. A refused or throttled password keeps the dialog open for a deliberate new attempt.
  A network failure is **never retried automatically**: the page says the change may or may not have been applied.
- **Tests:** `tests/step-up-admin.test.tsx` (29) — prompt for every applicable operation; TEAM announcement/poll and
  non-sensitive / unchanged / case-only-email edits without a prompt; header sent only after confirmation; password
  absent from URLs, bodies, storage, cookies, console and the DOM; cancel clears the field and sends nothing; wrong
  password and throttling messages with retry; `STEP_UP_REQUIRED` message; network failure not resent; create keeps the
  existing form validation; backend-requested step-up on an unprompted edit opens the dialog; unit table for
  `employeeUpdateNeedsStepUp`. `tests/content-audience.test.tsx`: the ADMIN ORG post now confirms with a synthetic
  password (intent unchanged).
- **Executed 2026-10-19:** frontend **214 passed** (16 files); `tsc` 0 errors; `next build` PASS; backend
  `tests/rbac/test_step_up_api.py` + `tests/ai/actions/test_step_up.py` **128 passed** (contract unchanged, keys
  blanked in the container); gitleaks history PASS and `--no-git` on the changed files PASS (no new allowlist entries).
- **Still open (not decided here):** switching `STEP_UP_ENFORCEMENT` to `enforce` (approval + enforcement-readiness
  review); salary (`current_salary_usd`) step-up remains an **open scope decision** (not implemented); other API
  clients must add the header; reverse proxies/APM must not log request headers; the frontend check is UX only —
  the backend remains the enforcement point. Release status unchanged: **NOT YET PRODUCTION READY**.
