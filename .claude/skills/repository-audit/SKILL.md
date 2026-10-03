---
name: repository-audit
description: Inspect the CB Nest repo before changing it — map endpoints, auth checks, models, migrations and frontend patterns relevant to a task, and verify assumptions from the assignment against the actual code. Use at the start of every phase or before touching an unfamiliar module.
---

# Repository Audit

## Purpose
Ground every change in repository evidence. The repo is the source of truth, and the assignment text is not.

## When to use
- At the start of each phase (the "Inspect" step).
- Before wrapping an endpoint as an AI tool.
- When the assignment names a route, table or field. Verify that it exists.

## Workflow
1. Read `HRMS-main/docs/repository-audit.md` and `docs/phase-status.md`. Don't redo work already recorded there.
2. List routes: grep `@router\.(get|post|put|patch|delete)` in `backend/app/api/v1/endpoints/`, joined with prefixes from `api/v1/router.py`.
3. For each relevant route, record the method, path, request schema, role check (`require_roles` or inline `current_user.role`), ownership filter, side effects and error codes.
4. For models, check `models/*.py` and the latest Alembic head (`alembic/versions/`, `down_revision` chain).
5. For the frontend, check `lib/api.ts` (client functions), `middleware.ts` (protected routes) and `components/layout/sidebar.tsx`.
6. Record any new findings in `repository-audit.md` (append a dated section). Don't silently diverge from it.

## Constraints
- Read-only. The audit never modifies application code.
- Cite file paths and line numbers for every claim.

## Examples
- Assignment says `PATCH /leaves/requests/{id}`. The repo has `POST /leaves/requests/{id}/approve|reject` and no edit API. Use the real routes and mark "update leave" as unsupported.
- Assignment's forbidden list lacks `payroll_records.pan`. The repo has it, so add it to the forbidden set.

## Validation checklist
- [ ] Every route/table used by the task verified to exist
- [ ] Role and ownership checks for each wrapped endpoint documented
- [ ] Current Alembic head identified
- [ ] Findings written to docs

## Common failure modes
- Trusting the assignment's example paths or payloads.
- Missing inline role checks (the repo mixes `require_roles` and `if current_user.role not in {...}`).
- Missing side effects in "read" endpoints (`GET /leaves/balances/me` creates rows).
