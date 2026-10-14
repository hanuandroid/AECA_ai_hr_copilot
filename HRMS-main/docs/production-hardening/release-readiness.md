# Release-readiness matrix (Phase 3, workstream 3.9)

**Date:** 2026-10-14 · **Overall status: NOT YET PRODUCTION READY**

Status vocabulary:
- **VERIFIED:** automated tests or runs in this repository pass.
- **LOCAL:** checked manually on local containers only.
- **PROPOSED:** designed or documented, not in force.
- **BLOCKED:** waiting on credentials or a decision.
- **OPEN:** not done.

Owner "TBD" means a named human owner is still required.

## 1. Security and authorization

| Requirement | Control | Evidence | Status | Remaining risk | Owner |
|---|---|---|---|---|---|
| LLM never decides authorization | Deterministic `services/ai/authorization/`; capability matrix | `tests/ai/authorization/`, adversarial matrix, red-team corpus (hostile model) | VERIFIED | — | Eng |
| Business API object-level authorization | Manager scope (B-05, S-01a–e); route matrix | `authorization-matrix.md`; `tests/characterization/` | VERIFIED (60 routes reviewed) | S-02 admin self-actions; S-03 org-wide manager content; S-04 test gaps | TBD (decisions) |
| Org-wide policy publication is ADMIN-only | Migration 0021, publication workflow, retrieval status filter | `test_hr_policy_publication.py`, `test_policy_publication_index.py` | VERIFIED | Manual re-indexing after publish (alert A16) | Eng / HR |
| Sensitive fields never leave via AI | SQL registry, redaction, provider-payload tests | `tests/ai/sql/`, `test_provider_payloads.py`, red-team corpus | VERIFIED (offline) | Live model untested | Eng |
| Prompt and document injection | Delimited context, guard, refusal rules | adversarial matrix, red-team corpus (40 attacks × 2 model modes) | VERIFIED (offline) | **Live model behaviour NOT RUN** | Eng / Security |
| Inactive users denied | `get_current_user` 403 `USER_INACTIVE` | `test_auth.py`, AI API tests | VERIFIED | — | Eng |
| Token handling | `type: access`, HS256 only, required `exp`, PyJWT | `test_auth.py`, `test_framework_security.py` | VERIFIED | B-17 JWT in `localStorage`; no revocation | TBD |
| Host / CORS / Range hardening | TrustedHost, CORS allowlist, range limit | `test_framework_security.py`; container smoke | VERIFIED / LOCAL | Real proxy untested | Ops |
| Abuse and cost controls | Per-user rate limit, token budget, concurrency, login throttle | `tests/ai/test_limits.py` | VERIFIED (single process) | In-memory only; IP+account login key | TBD (scale decision) |
| Secrets | Production checks; `.env` untracked; gitleaks | `test_production_config.py`; gitleaks run | VERIFIED / LOCAL | Secret store not chosen | Ops |

## 2. Dependencies and supply chain

| Requirement | Evidence | Status | Remaining risk | Owner |
|---|---|---|---|---|
| No critical advisories | npm and pip audits (2026-10-14) | VERIFIED | — | Eng |
| No high runtime advisories | npm `--omit=dev`: 0; pip runtime: 0; postcss override pinned by `dependency-pins.test.ts` | VERIFIED | The override is unsupported by Next; remove it on `next@16` | Eng |
| High build-time advisories | `braces` GHSA-vfj7 (no patched release; Tailwind 4 removes it) | **BLOCKED** (exception PROPOSED, no owner) | CI gate fails until approved or migrated | **TBD (owner / approver)** |
| CI pipeline | `.github/workflows/ci.yml` | LOCAL (never run on GitHub) | Actions not pinned to SHAs; no branch protection | TBD |

## 3. AI quality

| Requirement | Evidence | Status | Remaining risk | Owner |
|---|---|---|---|---|
| Deterministic pipeline evals | `python -m evals.run_evals`: 139/139 | VERIFIED (scripted models) | Measures pipelines, not model quality | Eng |
| Live routing, RAG, SQL, actions and injection quality | — | **BLOCKED** (API key, budget, data decisions 1–3) | Unknown real-model behaviour | TBD |
| Retrieval threshold calibrated on real embeddings | `rag-calibration.md` (fastembed, 80 cases) | PROPOSED (0.425–0.475; config unchanged) | Small demo dataset; hard negatives need the answer layer | HR (dataset) / Eng |
| Citation correctness, faithfulness, unsupported claims, abstention | — | **BLOCKED** (live model) | — | TBD |
| Acceptance targets | — | **PENDING** (dataset review) | — | TBD |
| Latency, tokens and cost under real load | Audit metrics exist | **BLOCKED** (live model) | — | TBD |

## 4. Data governance and lifecycle

| Requirement | Status | Remaining decision | Owner |
|---|---|---|---|
| Provider data categories, retention / ZDR / training, DPA and residency | **BLOCKED** (decisions 1–3 in `data-processing.md`) | All open | Legal / DPO / account owner |
| SQL rows never sent to the provider | VERIFIED (`AI_SQL_RESULT_FORMATTING=deterministic`) | Whether `llm` may ever be enabled | Legal / DPO |
| Retention (90 / 30 / ≥365 days) | PROPOSED (tooling VERIFIED; dry run only) | Approve the periods; audit lifecycle | HR / Legal |
| Backups, restore, RPO 24 h / RTO 4 h | LOCAL (restore drill on a dev copy) | Encryption, destination, off-host copies, access control, schedule | Ops / Security |

## 5. Operations

| Requirement | Status | Remaining | Owner |
|---|---|---|---|
| Production containers (non-root, read-only, health, graceful stop) | LOCAL | Deploy to target infrastructure | Ops |
| TLS reverse proxy | OPEN | Choose and configure | Ops |
| Monitoring and alerts | PROPOSED (`monitoring-alerts.md`, 18 alerts) | Tooling, owners, on-call | Ops |
| Runbook | PROPOSED (`deployment-runbook.md`) | Validate in the target environment | Ops |
| Browser-level end-to-end tests | OPEN (component tests only) | Playwright approval (Phase 3 workstream 3.7, not approved) | TBD |
| Scale-out (more than 1 worker or instance) | OPEN (refused at startup) | Shared limiter store; database choice | TBD |

## 6. Release gates (all must be met; none are met yet)

1. Data-governance decisions 1–3 approved, and the provider account configuration verified.
2. Live-model evaluation run within an approved budget, with targets approved and met on the holdout set.
3. CI green on GitHub, with branch protection and actions pinned. No unapproved high or critical advisories.
4. S-02 controls decided and implemented (at minimum, last-admin protection).
5. Retention periods approved. Backup encryption, destination and off-host copies in place; a timed restore drill in the target environment.
6. TLS proxy, monitoring with named owners, and alerts A1–A6, A8 and A10 wired and test-fired.
7. Browser end-to-end checks completed for the critical flows (`deployment-runbook.md` §8).
