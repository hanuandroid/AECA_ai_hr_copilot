# AI Components Inventory and Prioritized Gaps

**Project:** CB Nest — NovaWorks PeopleOps AI HR Copilot · **Date:** 2026-10-11
Full evidence, data flows and diagrams are in [ai-components-architecture-and-production-readiness.md](ai-components-architecture-and-production-readiness.md).

**Statuses:**
- **IMPLEMENTED** — code exists and tests or runtime checks confirm it.
- **PARTIAL** — part of the capability exists.
- **UNVERIFIED** — configured, but nothing has shown it working.
- **MISSING** — a reasonable code search found nothing.
- **N/A** — this architecture doesn't need it.

**Evidence labels:**
- `[runtime]` — checked on this machine on 2026-10-10/11: Docker stack, migrations, seed, ingest, health, login, AI endpoints without a key, and a full backend test run (1687 passed, Python 3.13).
- `[inherited]` — from the Phase 10 handoff of 2026-10-03; not re-run here.

## 1. Inventory

| Component | Status | Location | Evidence | Key limitation |
|---|---|---|---|---|
| LangGraph orchestration | IMPLEMENTED | `backend/app/services/ai/orchestration/graph.py` | Tests; `[runtime]` event traces | Dispatcher, not multi-agent |
| Intent classification: rules | IMPLEMENTED | `orchestration/classifier.py` | Router eval 33/33 `[inherited]` | English regex; bypassable phrasing |
| Intent classification: LLM fallback | UNVERIFIED | `classifier.py:_classify_with_llm` | Scripted output only | Never run live |
| Policy ingestion | IMPLEMENTED | `rag/loader.py`, `rag/ingest.py`, `scripts/ingest_policies.py` | `[runtime]` 24 docs → 40 chunks, idempotent | Manual trigger only |
| Chunking | IMPLEMENTED | `rag/chunker.py` (800 chars, 120 overlap) | Tests | Character-based |
| Embeddings | IMPLEMENTED | `embeddings.py` (fastembed `BAAI/bge-small-en-v1.5`, 384-d) | `[runtime]` | First download needs internet |
| Vector search | IMPLEMENTED | `rag/vector_store.py` | Tests | Brute force over JSON in SQLite |
| Lexical search | IMPLEMENTED | `rag/retriever.py` | Tests | IDF overlap, not BM25 |
| Hybrid search | IMPLEMENTED | `rag/retriever.py` (0.5 cosine + 0.5 lexical) | Tests | Threshold calibrated on fake embeddings |
| Reranking | MISSING | — | — | — |
| Metadata / role-filtered retrieval | MISSING | — | — | All policies are company-wide today |
| Grounding guard | IMPLEMENTED | `rag/guard.py` | Tests | Heuristic |
| Citations | IMPLEMENTED | `rag/service.py` | Policy eval `[inherited]` | — |
| SQL agent | IMPLEMENTED | `services/ai/sql/*` | Tests; SQL eval 20/20 `[inherited]` | Live NL→SQL accuracy unknown |
| Read-only executor | IMPLEMENTED | `sql/executor.py` (`mode=ro`, `query_only`, authorizer, timeout) | Write-blocked tests | — |
| Action agent | IMPLEMENTED | `services/ai/actions/*` | Tests; action eval 20/20 `[inherited]` | Live extraction unknown; timeout not enforced |
| Human-in-the-loop confirmation | IMPLEMENTED | `actions/confirmation.py`, `copilot-shell.tsx` | Replay, expiry, tamper and cross-user tests | Audit ordering on replay |
| AI RBAC | IMPLEMENTED | `authorization/policy.py` | Matrix tests; `[runtime]` per-role permissions | Direct reports only |
| Tenant isolation | N/A | — | Single tenant | — |
| Prompt-injection containment | IMPLEMENTED | Authorization independent of the model; escaped context | Adversarial matrix `[inherited]` | — |
| Prompt-injection detection | PARTIAL | Refusal regexes | `[runtime]` refusals | Regex bypass |
| Output guardrails | IMPLEMENTED | `rag/guard.py`, `sql/formatter.py` | Tests | Heuristic |
| Provider abstraction | IMPLEMENTED | `llm/base.py`, `llm/anthropic_provider.py`, `providers.py` | Tests | Anthropic only |
| AI gateway | MISSING | — | — | — |
| Retries | PARTIAL | Anthropic SDK `max_retries=2` | Code | No deadline or breaker |
| Provider / model fallback | MISSING | — | — | Deterministic degradation only |
| Circuit breaker | MISSING | — | — | — |
| Caching: model and client | IMPLEMENTED | HF cache in `storage/models`; in-process provider cache | `[runtime]` | — |
| Caching: response, semantic, retrieval, prompt | MISSING | — | — | — |
| Conversation history | IMPLEMENTED | `services/ai/conversation/*` | Tests | No purge |
| Long-term memory / summaries | MISSING | — | — | — |
| Audit trail | IMPLEMENTED | `services/ai/audit/*`, migration `0020` | Tests; `[runtime]` traces | Lost on a crash before flush |
| Usage metrics + dashboards | IMPLEMENTED | `GET /api/v1/ai/audit/metrics`, `/admin/ai-usage`, `/admin/ai-audit` | `[runtime]` admin 200, others 403 | Not browser-verified here |
| Token / cost accounting | UNVERIFIED | `audit/provider.py` | No live calls | Cost settings unset |
| Distributed tracing | PARTIAL | `request_id` correlation | `[runtime]` | No spans or OTel |
| Offline evaluation | IMPLEMENTED | `backend/evals/` (6 suites, 139 cases) | 139/139 `[inherited]` | Scripted model output |
| Live evaluation | UNVERIFIED | `evals/live_router_eval.py` | NOT RUN | Router only |
| CI gates | MISSING | — | No pipeline | — |
| Streaming | MISSING | — | Documented decision | — |
| Rate limiting / budgets | MISSING | — | — | — |
| Retention / purge | MISSING | — | — | — |
| Production config checks | IMPLEMENTED | `core/production_checks.py` | Tests `[inherited]` | Production env only |
| Refresh-token rejection | IMPLEMENTED (uncommitted) | `services/auth.py:26-28` | 1687 passed + Docker 401 `[runtime]` | Not yet an access-type allow-list |

## 2. Prioritized gaps

Priorities:
- **P0:** security or data integrity;
- **P1:** required before production;
- **P2:** reliability, quality or scale;
- **P3:** optional.

| Pri | Gap | Why | Acceptance criteria |
|---|---|---|---|
| P0 | Commit the refresh-token fix | 7-day token reuse closed only in the working tree | Committed; regression tests green |
| P0 | Enforce the action timeout + reconcile `EXECUTING` records | `httpx.ASGITransport` ignores timeouts; unknown outcomes on the only mutating path | Bounded failure; stale records surfaced or cleared |
| P1 | Rate limiting + per-user token budget | Up to 3 LLM calls per request; no abuse controls | 429 above limits; budgets audited |
| P1 | Live-model evaluation (router, RAG, SQL, actions) | Quality never measured | Live suites with agreed thresholds |
| P1 | RAG threshold calibration on bge vectors | `AI_RAG_MIN_SCORE` tuned on fake embeddings | Recall and precision measured; threshold justified |
| P1 | CI with test and eval gates | No automated regression protection | Required checks on PRs |
| P1 | Retention and purge | HR personal data stored indefinitely | Configurable retention; documented audit-purge procedure |
| P1 | Production containers + Next.js advisory decision | Dev servers, root user, `next@15.5.2` CVE-2025-66478, Node 20 EOL | Hardened images; advisory resolved |
| P1 | Data egress decision | Questions, policy text and redacted SQL rows go to Anthropic | Documented decision; optional LLM-free formatting |
| P1 | Business-API findings R1 / R9 / R4 | The underlying app is looser than the AI layer | Approved remediation |
| P2 | Circuit breaker + overall request deadline | Worst-case latency of 90 s or more per request | Request budget enforced |
| P2 | Automatic policy re-indexing on upload | Stale answers until an operator runs ingestion | Upload triggers re-index |
| P2 | OpenTelemetry spans (no prompt content) | Per-step latency diagnosis | Spans for route, retrieval, SQL, provider, action |
| P2 | Provider prompt caching | Static system prompts; cost and latency | Cache hits visible |
| P2 | Guard against accidental LangSmith export | `langsmith` installed transitively; environment variables would enable it | Production check refuses tracing variables |
| P2 | Separate audit fingerprint key | Currently derived from `JWT_SECRET_KEY` | Dedicated setting |
| P2 | Audit `ACTION_CONFIRMED` only after a successful claim | A losing replay leaves a misleading event | Event order matches outcome |
| P2 | Load and performance tests | No measured latency or throughput | p95 at target concurrency |
| P3 | Reranking, semantic cache, Redis, vector database, streaming, AI gateway, multi-tenant isolation, two-person approval | Only justified by growth or new requirements | See the full report §P |

## 3. What this inventory doesn't claim
- No penetration test, browser UI test or live-model run was performed.
- Frontend tests, TypeScript checks, the Next build and offline evaluations were **not** re-run on this machine; those results are inherited.
- The local Python environment used for the 2026-10-10 test run was later removed by OS temp cleanup.
