---
name: frontend-ai
description: Build the Next.js AI Copilot UI in CB Nest (app/ai-copilot, components/ai/*, lib/api.ts additions) — chat panel, RAG sources, SQL result table, action confirmation cards, recent AI actions, role-aware UX. Use for any frontend AI work.
---

# Frontend AI

## Purpose
A usable, role-aware chat experience that renders each AI result type clearly and never acts as a security boundary.

## When to use
Phase 9 and any change under `HRMS-main/frontend/app/ai-copilot/` or `frontend/components/ai/`.

## Workflow
1. Follow existing patterns:
   - The page shell is `<main className="flex min-h-screen"><Sidebar/><section className="flex w-full flex-col"><Topbar name title="AI Copilot"/>…`.
   - The token comes from `localStorage.getItem("hrms_access_token")`. On 401, redirect to `/login`.
   - API functions go in `lib/api.ts`, in the existing style: `export async function askPolicy(token, message): Promise<ApiResult<ApiEnvelope<PolicyAnswer> | {detail?: unknown}>>`.
2. Register the route by adding `/ai-copilot` to `middleware.ts` in **both** the condition list and `matcher`. Add a nav item in `sidebar.tsx` (lucide `Sparkles` or `Bot`).
3. Components in `components/ai/`:
   - `chat-panel.tsx`: message history, input (max 2,000 chars), mode tabs (Ask HR Policy / People & Projects / Automate HR Task / Auto-route), loading and error states.
   - `source-list.tsx`: title, category and filename only.
   - `sql-result-table.tsx`: uses `components/ui/table.tsx`, shows a truncation notice, and a collapsible SQL panel only if `sql` is present.
   - `action-result-card.tsx`: the proposal preview with Confirm/Cancel, which calls `/chat/actions/confirm`, and the result state.
   - `recent-actions.tsx`: `GET /chat/audit/recent`.
4. Role awareness (UX only): read the role from `fetchProfile`. Hide "Automate" suggestions the role can't use, but still render backend refusals properly.

## Constraints
- Never decide permissions in the frontend. Never hide backend error messages behind generic text when they are user-safe `AI_*` messages.
- Never render HTML from model output. Render it as text (or the existing `rich-text` component only if it sanitizes).
- Don't store chat content containing personal data in localStorage.

## Examples
- A policy answer shows the answer text plus source chips "Leave Policy · LEAVE · seed_policy_01.md".
- An action proposal shows "Approve CASUAL leave for Employee User, 2026-10-02 → 2026-10-03?" with [Confirm] [Cancel] and an expiry countdown.

## Validation checklist
- [ ] `npm run build` passes (type-check)
- [ ] Manual run as EMPLOYEE/MANAGER/ADMIN documented
- [ ] 401 → login redirect
- [ ] Double-click Confirm doesn't double-submit (disable while pending)

## Common failure modes
- Adding the route to `matcher` but not to the condition list, or the reverse.
- Treating the `hrms_role` cookie as authoritative.
- Parsing error bodies only as `{error}`. Existing endpoints return `{detail:{…}}`, while AI endpoints return the top-level envelope.
