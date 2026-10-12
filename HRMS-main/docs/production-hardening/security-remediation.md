# Production Hardening — Phase 1: Critical Security Remediation

**Dates:** 2026-10-11 (implementation) → 2026-10-12 (closure decisions, verification) · **Baseline:** [baseline.md](baseline.md)
**Status:** Phase 1 **complete**. All approved items implemented and tested; full suites green.
**Release status: NOT YET PRODUCTION READY — BLOCKING ITEMS REMAIN** (see §7).

Status vocabulary:
- **IMPLEMENTED** — code changed;
- **TESTED** — automated tests pass;
- **VALIDATED** — observed on the running Docker dev stack;
- **DEFERRED** — moved to a later phase by decision.

## 1. Approved decisions and outcome

| Finding | Approved decision | Status |
|---|---|---|
| B-01 `GET /employees/{id}` exposes sensitive fields (Critical) | Financial / government-ID fields → self + ADMIN. Phone / address / blood type → self + ADMIN + direct manager. Directory fields unchanged | IMPLEMENTED · TESTED · **VALIDATED** (all 4 viewer tiers) |
| B-02 `next@15.5.2` critical RCE advisories (Critical) | Smallest patched target after a fresh check | IMPLEMENTED · TESTED · **VALIDATED** (web container runs 15.5.27; criticals 1 → 0) |
| B-03 INACTIVE users can log in (High) | 401 `INVALID_CREDENTIALS` at login and token issuance; 403 `USER_INACTIVE` on every authenticated request, AI included | IMPLEMENTED · TESTED (no inactive user in the dev seed) |
| B-03 follow-up: audit identity (approved option a, 2026-10-12) | Server-set marker on `request.state` after token validation + DB lookup; the AI audit middleware attributes the denial | IMPLEMENTED · TESTED |
| B-04 Token type not explicit (Medium) | Require `type: "access"`; reject missing, refresh and unknown types | IMPLEMENTED · TESTED · **VALIDATED** |
| B-05 Manager scope (Medium) | Leave approve/reject: managers → direct reports only, never self; ADMIN → anyone but self. Project assign: managers → self + direct reports; ADMIN → anyone | IMPLEMENTED · TESTED |
| B-05 follow-ups (approved 2026-10-12) | Pending-leave queue scoped to what the caller may decide; project **removal** uses the same scope as assignment | IMPLEMENTED · TESTED · partially VALIDATED (queue responds; the dev seed's manager manages everyone, so out-of-scope can't be shown without writing data) |
| B-22 pypdf DoS (High → **re-rated Medium**, §4) | `pypdf` → patched 6.x, size limits, safe failure | IMPLEMENTED · TESTED |
| B-23 (part) `python-multipart` | Smallest patched version | IMPLEMENTED · TESTED · VALIDATED (API container 0.0.31) |
| B-19 Two environment-sensitive tests | Rewrite to their intent | IMPLEMENTED · TESTED |

## 2. Changes

| Area | Files | Change |
|---|---|---|
| Token typing (B-04) | `backend/app/core/security.py` | `create_access_token` adds `type: "access"`; `ACCESS_TOKEN_TYPE` / `REFRESH_TOKEN_TYPE` |
| Auth dependency (B-03, B-04) | `backend/app/services/auth.py` | Only `type == "access"` accepted (else 401 `INVALID_TOKEN`). Non-numeric `sub` → 401 (was a 500). INACTIVE → 403 `USER_INACTIVE`, after first setting the typed marker `InactiveUserRejection(employee_id, role)` on `request.state` |
| Audit identity (B-03 follow-up) | `backend/app/services/ai/audit/middleware.py` | `_record_inactive_rejection()`: for AI routes, if (and only if) the server-side `scope["state"]` holds an `InactiveUserRejection` (exact type), bind that identity and record `AUTHORIZATION_DENIED(INACTIVE_USER)` + `AI_ERROR(USER_INACTIVE)` before `AI_HTTP_REQUEST`. No token, credential or message content is recorded |
| Login (B-03) | `backend/app/api/v1/endpoints/auth.py` | `/auth/login` and `/auth/token` refuse INACTIVE accounts with the same 401 `INVALID_CREDENTIALS` as a wrong password |
| Field visibility (B-01) | `backend/app/api/v1/endpoints/employees.py` | `DETAIL_FINANCIAL_FIELDS`, `DETAIL_PERSONAL_FIELDS`, `_hidden_detail_fields()`; restricted fields returned as `null` |
| Project scope (B-05) | `backend/app/api/v1/endpoints/employees.py` | `_ensure_can_manage_projects_of()` used by **assign** (`POST`) and **remove** (`DELETE /employees/{id}/projects/{pid}`). Removal checks scope **before** looking up the assignment |
| Leave scope (B-05) | `backend/app/api/v1/endpoints/leaves.py` | `_ensure_can_decide_leave()`: own request → 403; manager outside direct reports → **404 `LEAVE_NOT_FOUND`**, identical to an unknown id. `GET /leaves/requests/pending`: one filter for items, count and pagination — PENDING, not the caller's own, and (non-admin) owner ∈ caller's direct reports |
| PDF bounds (B-22) | `backend/app/services/ai/rag/loader.py`, `backend/scripts/migrate_payslips_to_encrypted_pdf.py` | Policy PDFs ≤2 MB and ≤200 pages, else skipped and reported; payslip migration skips files >5 MB |
| Dependencies | `backend/requirements.txt` | `pypdf` 5.1.0 → **6.19.0**; `python-multipart` 0.0.20 → **0.0.31** |
| Dependencies | `frontend/package.json`, `frontend/package-lock.json` | `next` 15.5.2 → **15.5.27** (exact pin); `sharp` 0.34.5 → **0.35.5** (lockfile only, within Next's range). The lockfile diff touches only the `next`/`@next/*` and `sharp`/`@img/*` families |

**Dependency selection (fresh check, 2026-10-11):**
- `next` 15.5.27 is the newest 15.5.x.
- pip-audit found no known vulnerabilities for `python-multipart==0.0.31 + pypdf==6.19.0` (nor for 0.0.32 + 6.20.0), so the smallest pair was chosen.

**Reviewed, not changed:**
- `GET /calendar/team` shows only "LEAVE" day markers of **approved** leave (no reasons, types or pending requests). It's the existing availability feature.
- There is no leave-request detail endpoint.

## 3. API contract changes (document for clients)

| Change | Before | After | Client impact |
|---|---|---|---|
| Access token | `{sub, role, exp}` | `{sub, role, type:"access", exp}`; type **required** | **One-time re-login** for every user after deploy (15-min tokens; the frontend handles 401 by redirecting to login). No refresh flow exists |
| Login of INACTIVE account | 200 + tokens | 401 `INVALID_CREDENTIALS` | Same as a wrong password |
| Any request by INACTIVE account (business + AI) | Business 200; AI 403 `AI_USER_INACTIVE` | 403 `USER_INACTIVE` (`{"detail": {...}}` envelope) | New code for AI clients; the frontend maps unknown codes to a generic message |
| `GET /employees/{id}` | All fields for every caller | Restricted fields `null` by viewer tier | No page loses data (financial fields render only on the ADMIN-only edit page) |
| Leave approve/reject | Any manager / admin, any request | Own → 403; manager outside direct reports → **404 `LEAVE_NOT_FOUND`** | — |
| `GET /leaves/requests/pending` | All pending requests (with reasons) for every manager / admin | Only requests the caller may decide; `meta.total` matches | Managers see only direct reports; admins see everyone else's |
| `POST` / `DELETE /employees/{id}/projects[/{pid}]` | Any manager / admin | Manager outside self + direct reports → 403 | — |

## 4. Baseline correction (B-22)

The baseline said uploaded payslips are parsed by `pypdf` at request time. **That was wrong.**
- At request time `pypdf` parses only **system-generated** (reportlab) payslips, to encrypt them.
- Uploaded payslips are parsed only by the operator script `scripts/migrate_payslips_to_encrypted_pdf.py`.
- Policy PDFs are parsed only by the operator ingestion script.

**Re-rated Medium.** **Residual risk:** there's no wall-clock limit on a single PDF parse in these operator scripts. That would need subprocess isolation.

## 5. Tests and results

Changed tests record the old behaviour and the new invariant. **No test deleted, no assertion weakened.** In particular, `test_inactive_user_denial_is_recorded_with_server_identity` is **unchanged** and passes again.

| Test file | Change |
|---|---|
| `tests/characterization/test_auth.py` | R9 test → inactive login / token-endpoint / pre-issued-token / deactivate-mid-session denials. Hand-made tokens carry `type:"access"`. Token confusion: missing / refresh / wrong-case / unknown / empty type (business + AI), malformed `sub`, HS384 / HS512, `alg:none`, positive case |
| `tests/characterization/test_employees.py` | R1 test → visibility for self, admin, direct manager, peer, manager-of-other, enumeration. Project **assign**: manager outside team 403 (nothing written), self, admin. Project **remove**: manager outside team 403 and the assignment remains; identical 403 whether or not the assignment exists; direct report removal; admin; employee forbidden; unknown employee 404. System payslip DOB-encryption round trip (pypdf 6) |
| `tests/characterization/test_leaves.py` | Decide outside team → 404 **identical to an unknown id**, balance unchanged, still pending. Admin decides for others. Manager and admin can't decide their own (seeded pending). R9 leave creation → 403, nothing written. Pending queue: manager sees only direct reports (no outsider reason in the body, `total` matches), pagination never yields out-of-scope ids, admin sees others but not own, manager doesn't see own |
| `tests/ai/audit/test_audit_events.py` | New: anonymous denial (no token), malformed tokens (garbage / three-part / empty), spoofed identity headers and body fields, the inactive user's **refresh** token (all → no identity, no `AUTHORIZATION_DENIED`); inactive denial with spoofed headers still attributed to the validated account; an active user gets no inactive denial |
| `tests/ai/rag/test_chunking_and_loading.py` | Oversized PDF skipped without reaching the parser; >200 pages skipped; malformed PDF fails safely while the other policies load |
| `tests/test_infrastructure.py` + `tests/conftest.py` | Dev DB: absent → still absent; present → sha256 unchanged (fingerprint before app import). `login` fixture mints the INACTIVE user's token (models a pre-deactivation token) |
| `tests/ai/hardening/test_production_config.py` | `.env` not tracked + git-ignored (via `git`), excluded by `.dockerignore`. **Requires a git checkout** |
| 8 AI API test files | HTTP-level inactive code `AI_USER_INACTIVE` → `USER_INACTIVE` (403 and "no LLM / service call" kept). Service-level inactive tests unchanged (defence in depth remains) |

### Results (actual)

| Run | Environment | Result |
|---|---|---|
| Focused (2026-10-12): `tests/ai/audit`, `tests/characterization`, `tests/ai/authorization`, `tests/ai/hardening`, `tests/test_infrastructure.py` | Throwaway `hrms-main-api` container, **Python 3.11.17**, whole repo mounted, git installed, pypdf 6.19.0, python-multipart 0.0.31 | **728 passed, 0 failed** |
| **Full backend** (2026-10-12) | Same | **1744 passed, 0 failed** (174 s) |
| Earlier runs (2026-10-11, for the record) | Same | Focused 570 / 2 failed (two **new**-test bugs, fixed in the tests); full 1727 / 1 failed (the audit-identity issue, fixed by option a) |
| Frontend unit / integration (2026-10-12) | Host Node 24.13.0, `next@15.5.27` | **86 passed** |
| `tsc --noEmit` | Host Node 24 | **0 errors** |
| `next build` | Host Node 24 | **PASS**, Next.js 15.5.27, 22 routes |
| Docker images (2026-10-11) | `docker compose build web/api` | PASS; containers report next 15.5.27 / pypdf 6.19.0 / python-multipart 0.0.31; `/health` 200, `/login` 200 |
| `npm audit` | After upgrade | Criticals **0** (was 1). Remaining: nested `next/node_modules/postcss@8.4.31` (pinned by Next; fix needs `next@16`) → DEFERRED; Tailwind 3 build chain → DEFERRED |
| Runtime B-01 (values not printed) | Dev stack | Peer: financial + personal empty. Self: all populated. Direct manager: financial empty, personal populated. Admin: all populated |
| Runtime B-04 | Dev stack | Access token `type: access`; refresh token as bearer → 401 |
| Runtime pending queue (read-only) | Dev stack | Employee 403; manager 200 (1 item, not own); admin 200; no errors in API logs after reload |

### Not verified (explicit)
- **B-03 and the audit identity at runtime:** the dev seed has no INACTIVE user, and creating one would modify dev data. Covered by tests only.
- **B-05 out-of-team cases at runtime:** the dev seed's manager is the manager of every employee. Tests only.
- **Frontend tests on the Node 20 image:** the known toolchain gap (B-08) remains, deferred.
- **Browser / UI flows:** no browser automation was available; the HTTP level and the build were checked.
- **Live-model evaluation:** no `ANTHROPIC_API_KEY`.
- **Behaviour behind a reverse proxy or with multiple workers:** not tested.

## 6. Decisions log

| Date | Decision |
|---|---|
| 2026-10-11 | Decisions 1–7 approved (1b = option ii) |
| 2026-10-12 | Audit identity: **option (a)** approved and implemented; the strict test is unchanged and passing |
| 2026-10-12 | Pending-leave queue scope and project-removal scope approved and implemented |
| 2026-10-12 | Phase 1 commit approved after all checks pass |

## 7. Remaining risks and deferred work

- **DEFERRED (approved):** FastAPI/Starlette upgrade (remaining B-23); `python-jose` → PyJWT (B-24); Tailwind 4 (B-21); Node LTS image (B-07/B-08); nested `postcss` (needs Next 16).
- **Open baseline findings for later phases:** B-06 default JWT secret outside production, B-07 production containers, B-09 rate limits / budgets, B-10 action timeout, B-11 unknown-outcome reconciliation, B-12 audit ordering on replay, B-13 fingerprint key, B-14 retention, B-15 provider data governance, B-16 LangSmith guard, B-17 token storage, B-18 `file_path` exposure + legacy stubs, B-20 CI / live eval / Python drift.
- **Residual:**
  - no PDF parse wall-clock limit (operator scripts);
  - no token revocation list;
  - the approval queue now hides other teams' requests, so cross-team coverage (e.g. a manager on leave) needs an admin.
- `CLAUDE.md` statements about R1 / R4 / R9 are now stale (user-owned file, unchanged).

## 8. Phase 1 exit criteria

| Criterion | Met? |
|---|---|
| Critical / High findings in scope fixed (B-01, B-02, B-03; B-22 re-rated) | **Yes** |
| Approved Medium items and follow-ups (B-04, B-05 incl. queue + removal) | **Yes** |
| Audit identity preserved without weakening the test | **Yes** |
| Focused tests, then full backend (Python 3.11 Docker), frontend tests, `tsc`, build | **Yes, all green** |
| Diff reviewed; no secrets or unrelated files | **Yes** |

Phase 2 not started (separate approval required).
