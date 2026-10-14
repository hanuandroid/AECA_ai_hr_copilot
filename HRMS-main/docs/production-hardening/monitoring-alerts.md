# Monitoring and alert requirements (Phase 3, workstream 3.8)

**Status: SPECIFICATION ONLY.** Nothing here is wired to alerting yet.
- **Open decisions:** the monitoring and alerting tooling, alert owners and on-call rotation, notification channels, and log retention for the monitoring store.
- **Thresholds:** proposed starting points, to be tuned after a pilot. They are not measured service levels.
- **Signals:** every signal below already exists in the code today: structured log events, the admin metrics API, Docker health and the operator scripts. No new instrumentation is assumed.

## 1. Signal sources

| Source | Where | Notes |
|---|---|---|
| Container health | Docker `HEALTHCHECK` on `/api/v1/health` (API); web container state | Unhealthy after 3 failed checks, 30 s apart |
| Structured logs (structlog, JSON) | API stdout | Event names below. Redacted by design: no prompts, tokens or HR values |
| AI audit metrics | `GET /api/v1/ai/audit/metrics` (ADMIN) | Requests by status and role, refusals by reason, provider calls, errors and latency, tokens, estimated cost, RAG no-answer rate, SQL security rejections, action lifecycle counts |
| Operator scripts | `stale_actions_report.py` (exit 3 means review needed), `backup.py` / `restore.py --verify-only` (non-zero means failure), `retention_purge.py` dry run | Run on a schedule; exit codes are the signal |
| Proxy | Reverse proxy access logs | TLS, 4xx/5xx and latency at the edge (proxy not yet chosen) |

## 2. Alerts

Severity: **P1** = page now (availability, integrity or security impact); **P2** = same business day; **P3** = weekly review.

| # | Alert | Signal | Proposed condition | Sev | Runbook |
|---|---|---|---|---|---|
| A1 | API down | Docker health / proxy 5xx on `/api/v1/health` | Unhealthy at least 2 min | P1 | deployment-runbook §3–4 |
| A2 | Web down | Web container not running, or proxy 5xx on `/login` | At least 2 min | P1 | deployment-runbook §3 |
| A3 | Unsafe configuration at start | Container restart loop with `Unsafe production configuration` | Any | P1 | deployment-runbook §2 |
| A4 | Audit store failing | `ai.audit.write_failed`, `ai.audit.critical_write_failed`, `ai.audit.fallback` | Any `critical_write_failed`; 5 or more `write_failed` in 10 min | P1 | Confirmed actions fail closed. Check disk, DB locks and the audit triggers |
| A5 | Unknown action outcome | `ai.action.deadline_exceeded`; stale-actions report exit 3 | Any | P1 | backup-restore §2: verify in the HR system and resolve manually; never retry |
| A6 | Backup missing or failed | `backup.py` exit ≠ 0; newest `hrms-backup-*` older than 26 h; `--verify-only` failure | Any | P1 | backup-restore §3–4 |
| A7 | Security rejections spike | `sql_security_rejections`, `authorization_denied`, `refusals_by_reason` (PROMPT_INJECTION, SENSITIVE_DATA_REQUEST) per user | 20 or more per user per hour, or 3× the 7-day baseline | P2 | Review the user's audit trail (`GET /ai/audit?employee_id=`); consider disabling the account |
| A8 | Router integrity | `ai.router.trusted_state_mismatch`, `ai.router.illegal_state_write`, `ai.audit.identity_conflict` | Any | P1 | Indicates a code defect or tampering. Disable AI (`AI_ENABLED=false`) and investigate |
| A9 | Provider failing | `ai.llm.api_error`, `ai.llm.timeout`, `ai.llm.connection_error`, `provider_errors` | 10% or more of provider calls over 15 min | P2 | Check provider status. The Copilot degrades to `AI_PROVIDER_ERROR` / `AI_UNAVAILABLE` |
| A10 | Provider authentication | `ai.llm.auth_failed`, `ai.provider.missing_key` | Any | P1 | Rotate or restore the key through the secret store; never paste it into tickets |
| A11 | Model capacity | `ai.limits.llm_busy` (503 `AI_BUSY`) | 5% or more of AI requests over 15 min | P2 | Single worker by design. Review `AI_MAX_CONCURRENT_LLM_CALLS`; scaling needs a shared limiter (open decision) |
| A12 | Abuse or limits | `ai.limits.rate_limited`, `ai.limits.budget_exceeded`, login 429 `TOO_MANY_ATTEMPTS` | One user over budget on 3 days in a row; login 429s from at least 5 IPs on one account in 10 min | P2 | Possible credential stuffing or a misbehaving client |
| A13 | Cost | `estimated_cost_usd` (requires configured prices) | Daily total 2× the 7-day average, or above the approved budget | P2 | Budget owner (open decision) |
| A14 | Latency | `latency_ms`, `provider_latency_ms` p95 | p95 AI request over 20 s for 30 min; non-AI p95 over 1 s (proxy) | P3 | Capacity review |
| A15 | RAG quality drift | `rag_no_answer_rate`; `ai.rag.retrieval_failed`, `ai.embedding.failed` | No-answer rate ±50% against baseline over a week; any `embedding.failed` | P3 / P2 | Re-run calibration (rag-calibration.md); check `ingest_policies.py` |
| A16 | Policy index stale | `hr_policy.status_changed` (PUBLISHED or ARCHIVED) with no ingestion run afterwards | Over 1 h | P3 | Run `scripts/ingest_policies.py`. Retrieval already filters non-published policies |
| A17 | Disk | Volume `api_storage` usage | 80% or more | P2 | Check backups, uploads and the model cache |
| A18 | TLS certificate | Proxy certificate expiry | Under 21 days | P2 | Proxy operator |

## 3. Dashboards (proposed)

1. **Service:** health, request rate, 4xx/5xx, p50/p95 latency.
2. **Copilot:** requests by route and status, refusals by reason, clarification rate, provider calls, errors and latency, tokens and estimated cost per day, RAG no-answer rate. This is what the existing `/admin/ai-usage` page shows.
3. **Security:** authorization denials, SQL security rejections, injection refusals, rate-limit and budget denials, and inactive-user denials, per user and per hour.
4. **Operations:** backup age and size, last verify result, stale-actions count, retention dry-run counts, disk.

## 4. Requirements for the chosen tooling

- Ingest JSON logs without parsing message text. Alert on `event` names and fields only.
- **Never index or forward** request bodies, prompts or Authorization headers. Logs are already redacted; keep it that way at the collector (no body capture at the proxy).
- Restrict log access to the operations and security roles. Retention follows the approved audit and retention policy (open decision).
- Alerts must link to the runbook sections above, and each must have a named owner.

## 5. Not yet verified

- None of the alerts has fired in a real environment. Thresholds are untested.
- Proxy signals depend on the proxy chosen.
- Cost alerts need configured prices (`AI_LLM_*_COST_PER_MTOK`) and an approved budget.
