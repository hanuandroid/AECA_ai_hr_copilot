# Release-readiness matrix (Phase 3, workstream 3.9)

**Date:** 2026-10-14, re-verified 2026-10-17 (RBAC Step 10) · **Overall status: NOT YET PRODUCTION READY**

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
| Business API object-level authorization | Manager scope (B-05, S-01a–e); RBAC Steps 1–9 | `page-endpoint-inventory.md` (all 79 routes, generated from the route suite); `tests/rbac/`, `tests/characterization/` | VERIFIED (role gate for every route; object scope AUTO for every endpoint group; S-04 closed 2026-10-17) | S-04, S-06 and S-08 closed 2026-10-17 | Eng |
| Admin self-actions and last active admin (S-02) | RBAC Step 4: self role/salary/bank/PAN/password/payslip refused; one conditional UPDATE for the last admin | `test_admin_self_actions.py`, `test_last_active_admin.py` (concurrency rounds) | VERIFIED (SQLite, one process) | Other databases and multiple processes not verified | Eng |
| Content audiences (S-03) and HR policy audiences | RBAC Steps 6–7: ORG/TEAM content, ALL/MANAGERS/ADMINS policies across list, download, retrieval, citations, replay | `test_content_audience.py`, `test_policy_audience.py`, `test_policy_audience_rag.py`, `test_policy_replay.py` | VERIFIED (offline) | Live-model behaviour not run | Eng |
| Copilot never broader than the API | RBAC Step 8 parity table; exceptions E1/E2; employee SQL catalogs own-rows only (S-05) | `test_action_parity.py`, `test_confirmation_parity.py`, `test_read_path_parity.py`, `test_catalog_parity.py` | VERIFIED (offline) | Approved deviation from the assignment's employee project catalogue | Eng / product |
| Frontend page access | RBAC Step 9: one map, `AccessGuard`, filtered sidebar | `frontend/tests/page-access.test.tsx` | VERIFIED (component tests) | No real-browser test (Playwright not approved) | Eng |
| Org-wide policy publication is ADMIN-only | Migration 0021, publication workflow, retrieval status filter | `test_hr_policy_publication.py`, `test_policy_publication_index.py` | VERIFIED | Manual re-indexing after publish (alert A16) | Eng / HR |
| Sensitive fields never leave via AI | SQL registry, redaction, provider-payload tests | `tests/ai/sql/`, `test_provider_payloads.py`, red-team corpus | VERIFIED (offline) | Live model untested | Eng |
| Prompt and document injection | Delimited context, guard, refusal rules | adversarial matrix, red-team corpus (40 attacks × 2 model modes) | VERIFIED (offline) | **Live model behaviour NOT RUN** | Eng / Security |
| Inactive users denied | `get_current_user` 403 `USER_INACTIVE` | `test_auth.py`, AI API tests | VERIFIED | — | Eng |
| Token handling | `type: access`, HS256 only, required `exp`, PyJWT; self-service password change (Step 4) | `test_auth.py`, `test_framework_security.py`, `test_password_change.py` | VERIFIED | **B-17 open**: JWT in `localStorage`; no revocation, so an issued access token stays valid up to 15 minutes after a password change (role and INACTIVE status are re-read from the database on every request) | TBD |
| Host / CORS / Range hardening | TrustedHost, CORS allowlist, range limit | `test_framework_security.py`; container smoke | VERIFIED / LOCAL | Real proxy untested | Ops |
| Abuse and cost controls | Per-user rate limit, token budget, concurrency, login throttle | `tests/ai/test_limits.py` | VERIFIED (single process) | In-memory only; IP+account login key | TBD (scale decision) |
| Secrets | Production checks; `.env` untracked; gitleaks | `test_production_config.py`; gitleaks v8.18.4 on 2026-10-17: full history, 46/46 commits, no leaks (redacted, as in CI) | VERIFIED / LOCAL | Secret store not chosen | Ops |

## 2. Dependencies and supply chain

| Requirement | Evidence | Status | Remaining risk | Owner |
|---|---|---|---|---|
| No critical advisories | npm and pip audits re-run 2026-10-17 (pip-audit 2.9.0 runtime and dev; npm audit all and `--omit=dev`) | VERIFIED | — | Eng |
| No high runtime advisories | 2026-10-17: npm `--omit=dev`: 0; pip runtime and dev: none flagged by the gate; postcss override pinned by `dependency-pins.test.ts` | VERIFIED | The override is unsupported by Next; remove it on `next@16`. New moderate build-time `postcss-selector-parser` GHSA-rj75-hqrm-r3gf (report only) | Eng |
| High build-time advisories | `braces` GHSA-vfj7 (no patched release; Tailwind 4 removes it). Advisory gate re-run 2026-10-17: **FAIL** (exception PROPOSED, missing owner and approver) | **BLOCKED** (exception PROPOSED, no owner) | CI gate fails until approved or migrated | **TBD (owner / approver)** |
| CI pipeline | `.github/workflows/ci.yml` | LOCAL (never run on GitHub) | Actions not pinned to SHAs; no branch protection | TBD |

## 3. AI quality

| Requirement | Evidence | Status | Remaining risk | Owner |
|---|---|---|---|---|
| Deterministic pipeline evals | `python -m evals.run_evals`: 139/139 (2026-10-17; `asg-sql-01` changed by the approved step 8 deviation) | VERIFIED (scripted models) | Measures pipelines, not model quality | Eng |
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
| Production containers (non-root, read-only, health, graceful stop) | LOCAL, re-run 2026-10-17 on fresh images: unsafe config refused (exit 3), API uid 10001, migrated a fresh volume to `0023`, read-only container health 200, spoofed Host 400 (web image: see §5a) | Deploy to target infrastructure | Ops |
| Database and rollback | OPEN: SQLite only, single process. **S-07 (found 2026-10-17):** `alembic downgrade` below `0016` fails on SQLite (`0016` downgrade drops a constraint outside batch mode); downgrades `0023`→`0016` work. Protected migration, not changed | Approve a fix or document restore-from-backup as the only rollback below 0016; choose the production database | Ops / Eng |
| TLS reverse proxy | OPEN | Choose and configure | Ops |
| Monitoring and alerts | PROPOSED (`monitoring-alerts.md`, 18 alerts) | Tooling, owners, on-call | Ops |
| Runbook | PROPOSED (`deployment-runbook.md`) | Validate in the target environment | Ops |
| Browser-level end-to-end tests | OPEN (component tests only) | Playwright approval (Phase 3 workstream 3.7, not approved) | TBD |
| Scale-out (more than 1 worker or instance) | OPEN (refused at startup) | Shared limiter store; database choice | TBD |

### 5a. Production container smoke, 2026-10-17 (local, fresh `--target production` images)

| Check | Result |
|---|---|
| API refuses `JWT_SECRET_KEY=change_me` | PASS (exit 3, `RuntimeError: Unsafe production configuration`) |
| API / web run as non-root | PASS (uid 10001 / 1000) |
| Migrate a fresh storage volume | PASS (`0023_policy_audience (head)`) |
| API read-only container, health with an allowed Host | PASS (200) |
| Spoofed Host header | PASS (400) |
| Web read-only container serves `/login` | PASS (200) |
| Web `/organization` without a session | PASS (307 → `/login`, step 9 middleware) |

Throwaway secrets only; a separate container, volume and port; removed afterwards. Not a deployment and not the target
infrastructure (no TLS proxy, monitoring or real data).

## 6. Release gates (all must be met; one of seven is met)

1. Data-governance decisions 1–3 approved, and the provider account configuration verified.
2. Live-model evaluation run within an approved budget, with targets approved and met on the holdout set.
3. CI green on GitHub, with branch protection and actions pinned. No unapproved high or critical advisories.
4. ~~S-02 controls decided and implemented (at minimum, last-admin protection).~~ **Met** (RBAC Step 4, `1d46662`; verified 2026-10-17 on SQLite).
5. Retention periods approved. Backup encryption, destination and off-host copies in place; a timed restore drill in the target environment.
6. TLS proxy, monitoring with named owners, and alerts A1–A6, A8 and A10 wired and test-fired.
7. Browser end-to-end checks completed for the critical flows (`deployment-runbook.md` §8).
