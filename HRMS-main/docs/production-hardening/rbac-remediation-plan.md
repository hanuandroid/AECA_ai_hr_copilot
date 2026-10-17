# RBAC remediation plan (approved 2026-10-16)

**Inputs:**
- `assignment_05_ai_hr_copilot.md`
- The Phase 0 RBAC audit (findings A-01 to A-09)
- `authorization-matrix.md`
- The assignment-compliance review (C-01, D1–D5)

**Status:** IN PROGRESS. The release status stays **NOT YET PRODUCTION READY**.

**Rules:**
- The backend is the authorization boundary, and backend changes come before frontend changes.
- Every intentional behaviour change updates the affected tests, with a docstring recording the old behaviour.
- New negative tests cover IDOR, forged ids, direct URLs, restricted RAG documents, unauthorized SQL and action execution.
- Small local commits. No push.

## 1. Approved decisions

| # | Decision (as approved) |
|---|---|
| 1 | **Employee directory.** ADMIN: organisation-wide directory and authorized details. MANAGER: self and direct reports for general employee details. EMPLOYEE: own record only; the employee list and other employees' detail endpoints are denied. The limited organisation chart and birthdays stay available to all roles. Photos are available only for people visible in the chart. A separate organisation-wide **manager skill search** returns only approved directory fields and skills. |
| 2 | **Admin self-actions.** No self role changes. Never demote or deactivate the last active admin. Block self-edits to salary, bank and PAN fields, and self payslip uploads; these require another admin. Password changes go through a secure self-service flow. |
| 3 | **RAG audiences.** ALL, MANAGERS and ADMINS. Existing policies migrate to ALL. Unknown or malformed values fail closed. Enforced in listing, downloads, ingestion and indexing, retrieval, citations, and any cache or conversation history that could expose restricted content. Synthetic negative tests cover title, quotation, synonym and citation-based retrieval attempts. |
| 4 | **Tickets.** Employees create tickets and check status, but cannot change status. Authorized managers and assignees keep status updates within their scope. The same rule applies to the API, the frontend and AI tools. |
| 5 | **Announcements, polls, projects.** Managers create **team-scoped** announcements and polls. Only admins publish organisation-wide announcements and manage organisation-wide polls. Managers assign employees to projects within their scope. Project catalogue creation is admin-only. Managers change status only on projects they are authorized to manage. All assignment-required manager capabilities are preserved. |
| 6 | **Skill search.** Managers search skills organisation-wide through a dedicated restricted query path that returns only approved directory fields and skills. No wider manager access to payroll, leave, tickets, job history or other restricted data. Employee SQL stays self-scoped, and the existing SQL controls are preserved. |

## 2. Conflicts with existing rules and how they are resolved (documented, not silently changed)

| # | Existing behaviour or gap | Resolution |
|---|---|---|
| X-1 | **No self-service password change exists.** Passwords change only through the admin `PUT /employees/{id}`. | Add `POST /api/v1/auth/change-password`. It requires the current password and a new one of at least 8 characters, is throttled like login, never returns or logs the passwords, and is audited by structured log. The admin endpoint refuses `password` for the caller's own record. Existing tokens stay valid until they expire (15 minutes); there's no revocation list (B-17). |
| X-2 | **Announcements and polls have no audience or team field.** | New migration adds `audience` (`ORG` or `TEAM`) and `team_manager_id`. Existing rows become `ORG`. TEAM items are visible to the authoring manager and their direct reports; ORG items to everyone. Managers can only create TEAM items; admins create either. Results of a TEAM poll are visible only within that team, plus admins. |
| X-3 | **Projects have no owner.** "Authorized to manage" isn't modelled. | **Interpretation, recorded here:** a manager may change a project's status only if they or one of their direct reports is currently assigned to it. No ownership data is invented. If a product owner wants an explicit owner, that's a later schema change. |
| X-4 | The org chart lists **all** employees, including inactive ones; the photo endpoint serves anyone's photo. | "Limited chart" means **active** employees with `id`, `name`, `role` and `manager_id` only. Photos are served only for active employees, the people visible in the chart. |
| X-5 | Topbar search, the payslips and projects pages, and the AI use `GET /employees`. | They inherit the new scope. Managers see self and reports, which matches what they may act on. The topbar search is hidden for employees. Skill search becomes a separate endpoint. |
| X-6 | The AI capability `DIRECTORY_READ` is granted to all roles but used nowhere. | Replaced by a manager and admin `SKILL_SEARCH_ORG` capability for the new restricted path. The AI matrix doc is corrected, including the announcement entry: managers **are** allowed, with TEAM scope after X-2. |
| X-7 | Characterization tests pin today's behaviour: the directory open to all, owners changing ticket status, admin self-actions, managers creating projects. | Updated in the step that changes each behaviour, with the old behaviour documented in the test docstring. |

## 3. Implementation sequence (one reviewable local commit per step)

| Step | Content | Status |
|---|---|---|
| 0 | This plan | **done** |
| 1 | **Route-permission regression suite**: every API route (77 method+path pairs) × anonymous, employee, manager, admin with an explicit expected outcome class, plus a completeness test that fails on unclassified routes. It encodes **current** behaviour; later steps edit the expectations. | **done** |
| 2 | Directory scope (decision 1): list, detail, profile pictures, limited org chart; identical 404 for out-of-scope ids | **done** |
| 3 | Manager skill search (decisions 1 and 6): `GET /api/v1/employees/skill-search` (MANAGER, ADMIN) and the Copilot path (MANAGER pure skill questions answered by the same backend-authored query in `app/services/skill_search.py`, not model SQL); capability `SKILL_SEARCH_ORG` replaces `DIRECTORY_READ`; fields employee_id, employee_name, department, skill, level; tests `tests/rbac/test_skill_search.py`, `tests/ai/sql/test_skill_search_path.py` | **done** |
| 4 | Admin self-actions and self-service password change (decision 2, X-1): self role, salary/bank/PAN, admin-endpoint password reset and self payslip refused; last-active-admin invariant via one conditional UPDATE (concurrency-tested); `POST /api/v1/auth/change-password` (throttled; tokens stay valid until expiry - no revocation); see `authorization-matrix.md` §2 | **done** |
| 5 | Ticket status (decision 4): `POST /tickets/{id}/status` allows ADMIN (any), MANAGER (S-01a scope) and an EMPLOYEE only as the current assignee; the EMPLOYEE owner gets 403 (before: allowed). Assignment stays MANAGER/ADMIN. AI `TICKET_STATUS_UPDATE` unchanged and stricter (EMPLOYEE denied even as assignee, D7; re-checked at confirmation). UI shows status buttons only to permitted users and handles 403/404. Tests `tests/rbac/test_ticket_status.py`, `tests/ai/actions/test_ticket_status_ai.py`, `frontend/tests/ticket-status.test.tsx` | **done** |
| 6 | Announcements and polls audience; project catalogue and status rules (decision 5, X-2, X-3): migration `0022_content_audience` (`audience` ORG/TEAM + `team_manager_id`, CHECK constraint, existing rows ORG); MANAGER creates TEAM only (ORG → 403 `AUDIENCE_FORBIDDEN`), ADMIN either; lists, totals, votes and results filtered (hidden poll = unknown-id 404); catalogue creation ADMIN-only; manager project status only while they or an active direct report are assigned; AI `ANNOUNCEMENT_CREATE` grant ADMIN→ORG / MANAGER→TEAM sets the audience; see `authorization-matrix.md` §3 | **done** |
| 7 | RAG audiences (decision 3): migration `0023_policy_audience` (`hr_policies.audience` ALL/MANAGERS/ADMINS, CHECK constraint, existing rows ALL; `ai_conversation_messages.source_policy_ids`); list, total and download filtered (hidden = unknown-id 404); upload audience must include the uploader (default ALL); only known audiences indexed; retrieval requires the caller's audiences (AI matrix `POLICY_READ`/`_MANAGERS`/`_ADMINS`, parity-tested with the API) and filters before scoring; citations carry `policy_id`; replay and follow-up context withhold replies citing a policy the caller can no longer read; title/quotation/synonym/codeword/forged-citation negative tests; see `authorization-matrix.md` §3a | **done** |
| 8 | AI parity review: action registry, capabilities, confirmation-time re-authorization against steps 2–7. Every action executed through the API and the Copilot per role (`tests/ai/actions/test_action_parity.py`, invariant: Copilot allowed ⇒ API allowed); intentional exceptions E1 (approved: no Copilot ticket-status change for any EMPLOYEE, including the assignee) and E2 (owner-based manager ticket scope); confirmation-time re-authorization verified for role, status, reporting-line, target-state changes (`test_confirmation_parity.py`). Fixed: SQL agent `projects`/`skills` were unrestricted for EMPLOYEE, now own linked rows only (S-05); approved deviation from the assignment's employee project catalogue, eval `asg-sql-01` updated. See `ai_permissions_matrix.md` §3.0a | **done** |
| 9 | Frontend: one permission map, role-filtered sidebar, direct-URL guards, access-denied view (all pages, including `/organization` and `/admin/*`). `lib/permissions.ts` (one map, every page, fail closed), `AccessGuard` in the root layout (server-profile role; restricted pages never mount for other roles), sidebar filtered by the map, `/organization` added to the middleware login check; tests `frontend/tests/page-access.test.tsx`; see `authorization-matrix.md` §3b | **done** |
| 10 | Documentation: `authorization-matrix.md`, `ai_permissions_matrix.md`, page and endpoint inventory with verification status; full verification run | pending |
