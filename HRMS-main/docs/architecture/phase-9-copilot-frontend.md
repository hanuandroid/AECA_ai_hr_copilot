
# Phase 9: Full-Stack HR Copilot UX

> The frontend is a client, never an authorization boundary. It sends only the token and documented body
> fields. Everything it shows or enables by role is a UX hint, and every operation is authorized by the backend.

## 1. Frontend architecture

| Path | Contents |
|---|---|
| `frontend/app/copilot/[[...conversation]]/page.tsx` | `/copilot` and `/copilot/<conversationId>`. One optional catch-all route, so switching conversations updates the URL (`history.pushState`) without remounting the chat. It loads the token and profile like the other pages and handles 401 the same way. |
| `frontend/components/copilot/copilot-shell.tsx` | Conversation list (grouped Today/Yesterday/Earlier, archive), message list, composer, empty state, conversation lifecycle, confirmation flow |
| `frontend/components/copilot/message-parts.tsx` | `SafeText`, `PolicySources`, `DataTable`, `ActionCard`, clarification, refusal and error notices |
| `frontend/components/copilot/audit-view.tsx` and `app/admin/ai-audit/page.tsx` | Admin AI audit page (metrics plus a paginated event table) |
| `frontend/lib/copilot-api.ts` | The only module that calls AI endpoints; typed contracts |
| `frontend/lib/copilot-errors.ts` | Central error-code → copy mapping (real backend codes only) |
| `frontend/lib/copilot-display.ts` | Display masking, sensitive-column filter, UI-hint suggestions, labels |
| `components/layout/sidebar.tsx`, `middleware.ts` | "AI Copilot" nav item; `/copilot` and `/admin` added to the login redirect (UX only) |

The page follows the existing conventions: the `Sidebar` + `Topbar` shell, Tailwind classes, the `Button`/`Card`
primitives, the `localStorage["hrms_access_token"]` token, and per-page `clearAuthAndRedirect` on 401.

**State.** There is no new state library; state is React state in `CopilotShell`:
`activeId`, `messages`, `conversations`, `listState`, `sending`, `loadingConversation`, `banner`, `actionStates`
(the per-confirmation pending/confirming/completed/failed/unknown/cancelled state), `input`, and `listOpen`
(the mobile drawer).

**Not added:** no streaming and no WebSockets (the backend returns complete responses), and no Markdown
renderer. Answers are plain text with line breaks preserved.

## 2. API integration (actual backend contracts)

| Function | Endpoint | Body / params |
|---|---|---|
| `sendChat` | `POST /api/v1/chat` | `{message}` or `{message, conversation_id}`, built from exactly these two fields |
| `listConversations` | `GET /api/v1/chat/conversations?limit=30` | — |
| `getConversation` | `GET /api/v1/chat/conversations/{id}?limit=100` | The id is URL-encoded |
| `archiveConversation` | `DELETE /api/v1/chat/conversations/{id}` | — (backend soft archive) |
| `confirmAction` | `POST /api/v1/chat/actions/confirm` | `{confirmation_id}` only, never retried |
| `fetchAuditEvents` / `fetchAuditMetrics` | `GET /api/v1/ai/audit`, `/api/v1/ai/audit/metrics` | Typed `limit`, `offset`, `event_type`, `status`, `route` |

Every call returns `CopilotResult<T> = {ok, status, data?, error?, requestId?, conversationId?}`:
- the error envelope's `{code, message}` is read, but the message is never displayed;
- FastAPI 422 bodies map to `VALIDATION_ERROR`;
- non-JSON bodies map to `HTTP_<status>`;
- a network failure is `status 0`.

**Backend contract change (minimal, security-neutral).** `CORSMiddleware` now sets
`expose_headers=["X-Request-Id", "X-Conversation-Id"]`, so the browser (`:3000`) can read the correlation headers
from the API (`:8000`). The failed-turn conversation id is otherwise unreachable cross-origin. The AI audit
middleware also skips CORS preflight (`OPTIONS`) requests, which carry no user. Both are covered by
`test_cors_exposes_correlation_headers_and_preflight_is_not_audited`.

## 3. Conversation lifecycle

| Step | Behaviour |
|---|---|
| New | "New conversation" clears messages, input, confirmation state and the active id, and sets the URL to `/copilot`. The next `/chat` call has no `conversation_id`, so the backend creates the conversation and returns its id (never generated in the browser). |
| List | Loaded on mount and refreshed after each turn or archive. It shows the backend's redacted title (masked again on display) and the day group; no ids, users or audit data. |
| Select | `GET /conversations/{id}` loads the history; the URL becomes `/copilot/<id>`; later turns use that id |
| Refresh | The URL id is reloaded **from the backend**; nothing is cached in the browser. Follow-ups continue in the same conversation. |
| Foreign or unknown id (URL or list) | Generic "Conversation unavailable."; the id is dropped (URL reset), and the next message starts a new conversation |
| Archive | Two-step (Archive → Archive/Keep) and calls the backend; there is no frontend-only archive |

## 4. Message lifecycle

1. **Send.** Enter or the Send button sends; Shift+Enter adds a newline. Input is limited to 1000 characters,
   and empty input is disabled. A synchronous ref guard plus the disabled button prevent double submission.
2. **Thinking.** The user message appears immediately, and the polite live region announces "PeopleOps Copilot
   is thinking…".
3. **Response.** Each part is rendered by route:
   - **POLICY:** answer text plus **Sources**, with the backend's citations deduplicated by title and section.
     Each source links to the HR Policies page. Citations are never generated or invented; `chunk_id` and
     `filename` are not shown.
   - **SQL:** answer text plus a highlighted single value or an accessible table (first 50 rows, with a
     truncation note). The `sql` field is never displayed, and sensitive-named columns are dropped.
   - **ACTION:** a confirmation card (§5) or a clarification notice.
   - **CLARIFICATION / REFUSAL:** labelled notices ("Needs clarification", "Can't help with that"). Reason codes
     such as `PROMPT_INJECTION` are never shown.
   - **Failed part** (partial result): a friendly per-part error.
4. **Error.** A friendly message with `Reference: RQ-<first 8 of request id>` and a manual "Try again"; there is
   no automatic retry. If a failed first turn created a conversation (`X-Conversation-Id`), its id is adopted.
5. **History.** Reloaded turns show the stored, redacted text and a status label. By backend design, history
   keeps no citations, rows or confirmation tokens, so reloaded proposals show "Ask again to get a new
   confirmation."

## 5. Action confirmation UX

- A `confirmation_required` part renders a distinct card: action label, backend summary, and "Nothing has been
  changed yet." Rendering it calls nothing.
- **Confirm** sends `POST /chat/actions/confirm {confirmation_id}` with the exact backend token. It never sends
  ids, action names or parameters from the DOM, never goes through `/chat`, and never sends "yes".
- **One request per token**, enforced by a per-token ref guard and the disabled button. The backend's
  single-use claim is the final protection.
- **Cancel** is local only: nothing is sent, and the card can no longer confirm (the token simply expires
  server-side).
- **Result.** The outcome is taken from the backend response: `completed` → "Done: <backend message>";
  `failed` → "Not completed: <backend message>" (business validation).
- **Errors:**
  - expired or used token (`AI_CONFIRMATION_INVALID`);
  - permission changed (`AI_PERMISSION_DENIED`);
  - target no longer valid (`AI_TARGET_NOT_FOUND`);
  - audit unavailable (`AI_AUDIT_UNAVAILABLE`: "Nothing was changed").

  Each maps to safe copy, and the server message is never shown.
- **No response (timeout or network):** "We couldn't confirm whether this action completed. Please check the
  related page before asking again." It is never retried automatically, and the button stays gone. There is no
  confirmation-status endpoint to poll; this is documented as a limitation.

## 6. Error handling

`lib/copilot-errors.ts` maps every real backend code: `AI_*` for availability, routing, authorization, actions,
conversations and audit, `SQL_*` for data questions, plus `NETWORK_ERROR` and `VALIDATION_ERROR`. A test
verifies that every mapped code exists in `backend/app/services/ai/errors.py`. Unknown codes fall back by
status (400/401/403/404/409/422/500/502/503) to generic copy. The UI never shows server messages, stack traces,
provider errors, capability names or scope details.

## 7. Authentication behaviour

The existing flow is unchanged. A missing token, or a 401 from any AI call (list, load, send, confirm, archive,
audit), calls `clearAuthAndRedirect`: remove the token, clear the `hrms_auth` cookie, go to `/login`. Requests
are never retried and nothing is sent as another user. `middleware.ts` redirects unauthenticated visits to
`/copilot` and `/admin` (a cookie check, UX only).

## 8. Authorization boundary

- Request bodies contain only `message` and `conversation_id`. A test asserts the absence of `user_id`,
  `employee_id`, `role`, `permissions`, `organization_id`, `capability`, `scope`, `authorization_override` and
  `owner_id`, and that only the `Authorization` and `Content-Type` headers are sent.
- **Role as a hint.** The profile role is used only as a hint for:
  - example prompts (`copilotSuggestions`, documented as a UI hint);
  - empty-state wording;
  - showing the "AI audit log" link to admins.

  Changing it in the browser changes no request and unlocks nothing. The audit page still gets a 403 from the
  backend.
- There are no frontend permission functions such as `canApproveLeave`, and no frontend fallback after a
  denial. A 403 or scope violation is shown once and nothing else is attempted.

## 9. Audit UI (admin)

`/admin/ai-audit` shows the following, using the backend's paginated API (`limit` 25, Previous/Next).
- **Metric tiles:** AI requests, success rate, average and P95 latency, refusals, authorization denials, action
  proposals and executions, provider errors. The page states that these are operational metrics, not
  model-quality scores, and that cost appears only when priced and reported.
- **Event table:** time, `RQ-…` request, short conversation id, route, status, event, detail (only
  `reason_code`/`error_code`/`action`/endpoint), duration.
- **Filters:** event type and status.

Access is decided by the backend (AUDIT_READ_ORG). On 403 the page shows "You don't have access to the AI audit
log." and makes no other request.

## 10. Security considerations

- **XSS.** All AI text (answers, titles, citations, table cells, summaries, notices) is rendered as React text.
  There is no `dangerouslySetInnerHTML` and no HTML or Markdown rendering. Tests render
  `<script>alert(1)</script>`, `<img src=x onerror=alert(1)>` and `javascript:` links from every field and
  assert there are no elements and no `alert`.
- **Sensitive data (defence in depth).** `maskSensitive` hides JWT/Bearer tokens, `sk-…` keys, PAN, IFSC and
  9–18 digit numbers in any AI text. `visibleColumns` drops pan, bank, IFSC, account, salary, password, UAN, ESI,
  DOB, token and secret columns. The backend already prevents these values from leaving, so this guards only
  against malformed data.
- **Path safety.** Conversation ids are URL-encoded, so a crafted id cannot change the endpoint (test).
- **Tokens.** The JWT stays in the existing `localStorage` location and is only sent in the `Authorization`
  header. It is never written into messages, URLs or logs.

## 11. Testing

The repository had no frontend test runner. Phase 9 adds **Vitest 5 + jsdom + Testing Library** (devDependencies
only; `npm test`). Configuration is `vitest.config.mts`, using the threads pool because the forks pool timed out
starting workers on this Windows host. No E2E framework exists, and none was added, so the required E2E
scenarios run as full-flow integration tests against a stateful fake backend.

| File | Tests | Covers |
|---|---|---|
| `tests/copilot-api.test.ts` | 22 | Payload has no identity fields; confirm body is only the token; header extraction; envelope, 422, non-JSON and network errors; id encoding; every mapped error code exists in the backend; masking, columns, grouping, suggestions |
| `tests/copilot-shell.test.tsx` | 39 | Chat (empty state, send, thinking, citations, Enter/Shift+Enter, no double send, friendly error with reference and manual retry, data value and table without SQL or sensitive columns). Conversations (list, create, follow-up id, select, refresh, switch, new, archive, history). Actions (card, confirm, double click and same-batch clicks, cancel, expired/permission/target/audit/business failures, unknown outcome not retried, "yes" is not a confirmation). Security (401, 403/scope, foreign conversation, role hint sends nothing extra). XSS and masking. Accessibility (labels, landmarks, live region, keyboard operation, text not colour). |
| `tests/copilot-scenarios.test.tsx` | 9 | E2E 1–8 (policy with citations, follow-up, SQL, action then confirm, clarification, refresh, unauthorized action, XSS) plus cross-user |
| `tests/audit-view.test.tsx` | 5 | Metrics and events, pagination, typed filters, 403 without fallback, 401, no metadata leakage |

**Total: 75 frontend tests.**

Deliberate-regression checks: 13 mutations of the client-side safeguards, all caught (one needed an extra
same-batch double-click test).

## 12. Known limitations

- **Build (resolved).** Phase 9 frontend production build verified successfully after a minimal pre-existing polls
  page TypeScript narrowing fix. The build then needed `<Suspense>` boundaries around `useSearchParams()` in the four
  existing employees pages (behaviour and static rendering unchanged). Both fixes were approved;
  `next build` passes.
- **Next.js version.** npm flags `next@15.5.2` with a published security advisory. Upgrading is out of Phase 9
  scope and is flagged for a decision.
- **History.** History reloads show text only. Citations, tables and confirmation tokens are not stored (Phase 7
  privacy design), so a pending proposal must be re-asked after a refresh.
- **No status endpoint.** No backend endpoint verifies an unknown confirmation outcome; the UI asks the user to
  check the related page.
- **Rendering.** No Markdown rendering: answers are plain text with line breaks.
- **Mobile navigation.** The main app sidebar is hidden on small screens (existing behaviour). The Copilot's
  conversation list becomes a drawer, but there is no global mobile navigation.
- **No browser E2E.** Responsive layout was implemented with Tailwind breakpoints and checked in code, not in a
  real browser.
