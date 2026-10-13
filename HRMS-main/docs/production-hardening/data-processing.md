# HR Data Processing and Model-Provider Register

**Status:** provisional · **Owner of open decisions:** business / legal / security (not engineering)
**Date:** 2026-10-13 · **Scope:** every place where data leaves the backend for an AI model provider.
**Evidence:** code paths cited below; payload tests in `backend/tests/ai/orchestration/test_provider_payloads.py`.

> No provider retention, training or residency guarantee is stated here. Those depend on the Anthropic account
> configuration and contract, and remain **open decisions** (§5). Until they are confirmed, the safe defaults in §4
> stay in place.

## 1. Providers

| Provider | Used for | Where it runs | Configured by |
|---|---|---|---|
| Anthropic Messages API | Routing fallback, policy answers, SQL generation, optional SQL result wording, action proposals | External (internet) | `AI_LLM_PROVIDER=anthropic`, `AI_LLM_MODEL`, `AI_LLM_FAST_MODEL`, `ANTHROPIC_API_KEY` |
| fastembed `BAAI/bge-small-en-v1.5` | Policy embeddings | **Local** (in the API container); no data leaves | `AI_EMBEDDING_PROVIDER=fastembed` |
| Hugging Face Hub | **One-time download** of the embedding model files at first ingestion; no HR data is sent | External | `AI_EMBEDDING_CACHE_DIR` (cached afterwards) |

No API key is configured in this repository's environments today: **no live provider call has been made**.

## 2. Call sites (what is sent)

Every call goes through `services/ai/audit/provider.observe()`, which records provider, model, operation, latency
and token counts, **never the content**.

| # | Operation (audit `operation`) | When | Sent to the provider | Personal data in the payload | Never sent |
|---|---|---|---|---|---|
| 1 | `route_classification` (fast model) | Only when deterministic routing rules find no signal | The user's current message. If the conversation has history: up to 6 recent messages / 2000 chars (user text and the stored, redacted assistant replies), delimited as untrusted data | Whatever the user typed. Prior assistant replies (deterministic SQL wording contains counts, not rows) | Identity, role, ids, tokens, DB rows |
| 2 | `policy_answer` | Policy question with retrieved evidence | Fixed system prompt; up to 4 policy excerpts (≤800 chars each) with title/section/filename; the question | Whatever the user typed. Policy text is company content, not employee data | Employee records, file paths, checksums, embeddings |
| 3 | `sql` (generation) | HR data question | Fixed system prompt; the **schema** of allowed tables/columns (names and descriptions only); today's date; scope rules; the question | Whatever the user typed. **No row values** | Sensitive columns (not in the schema shown), any row data |
| 4 | `sql` (result wording) | **Only if `AI_SQL_RESULT_FORMATTING=llm`** | Question + redacted result rows (allowed columns only: e.g. names, emails, leave dates/reasons, ticket titles/descriptions within the caller's scope) | **Yes: in-scope HR data of the caller's permitted records** | Sensitive columns (dropped), PAN/IFSC/long-number/token patterns (redacted) |
| 5 | `action_proposal` | HR action request | Fixed system prompt; the list of actions the caller's role may use (names + descriptions); today's date; the request | Whatever the user typed | Employee records, ids from the DB, tokens |

Verified by tests (fake provider, real request pipeline):
- default mode: SQL rows never reach the provider (#4 not called);
- in every scenario, the seeded employee's salary, bank account, IFSC, PAN, DOB, UAN, password hashes and the bearer token never appear in any payload;
- `llm` mode sends rows (documented opt-in) but still none of the prohibited fields.

## 3. Prohibited data (enforced in code, not by prompts)

| Data | Control |
|---|---|
| Passwords / hashes, JWTs, API keys | Never part of any payload builder; `redact()` patterns for tokens/keys on stored text |
| Bank, IFSC, PAN, DOB, salary, UAN, ESI, photo paths | Not in the SQL schema registry (`sql/schema.py`, import-time check); `redact_rows` drops sensitive-named columns; RAG answer guard blocks sensitive terms |
| Other employees' records | Row scope enforced by SQL rewrite and the read-only executor; policy RAG never reads employee tables |

**Residual:** text the user types is sent as typed (control characters stripped). If a user pastes their own PAN or bank
number into a question, it reaches the provider. Optional pre-provider redaction of user text is listed in §5.

## 4. Safe defaults in force

| Setting | Default | Effect |
|---|---|---|
| `AI_SQL_RESULT_FORMATTING` | `deterministic` | SQL answers are worded by the backend ("Found N matching records."); rows never leave |
| `ALLOW_EXTERNAL_TRACING` | `false` | Production refuses `LANGSMITH_*` / `LANGCHAIN_*` tracing variables (would export prompts) |
| `AI_ENABLED` | `false` in `production.env.example` | No provider calls until AI is deliberately enabled with an approved configuration |
| Audit | content-free | Audit stores ids, codes, latency and token counts; message text only as a keyed fingerprint |
| Conversations | redacted text, owner-only | Stored in the app DB (retention: see deployment runbook, provisional 90 days) |

Failure behaviour: without a key, or when the provider fails, the Copilot returns `AI_UNAVAILABLE` / `AI_PROVIDER_ERROR`.
Deterministic refusals and the RAG "insufficient evidence" path still work.

## 5. Open decisions (required before enabling AI with real HR data)

| # | Decision | Owner | Blocks |
|---|---|---|---|
| 1 | Which data categories may be sent to the provider (user free text, policy text, schema, in-scope HR rows) | Legal / DPO | Enabling `AI_ENABLED` in production |
| 2 | Provider data retention / zero-data-retention settings on the Anthropic account, and whether provider training on inputs is excluded (verify against the actual account and current provider terms) | Account owner + legal | Enabling `AI_ENABLED` in production |
| 3 | Data processing agreement / contractual approval, data residency requirements | Legal | Enabling `AI_ENABLED` in production |
| 4 | Whether `AI_SQL_RESULT_FORMATTING=llm` may ever be enabled (sends in-scope HR rows) | Legal / DPO | Any change from `deterministic` |
| 5 | Whether to redact user-typed identifiers (PAN, account numbers, tokens) before provider calls | Security + product | Optional hardening |
| 6 | Employee notice / transparency text for the Copilot | HR / legal | Production rollout |

Status of all items above: **OPEN** (none confirmed as of 2026-10-13).
