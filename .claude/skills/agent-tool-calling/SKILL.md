---
name: agent-tool-calling
description: Add or change HR Action Agent tools that wrap existing CB Nest endpoints (api_tools.py, action_agent.py, confirmations.py) — typed arg schemas, role filtering, team pre-checks, confirmation flow, httpx calls with the user's token, and error mapping. Use whenever an AI feature must perform an HR mutation.
---

# Agent Tool Calling

## Purpose
Let users perform HR tasks by chat while every mutation still passes through the existing endpoint's validation and authorization.

## When to use
Adding a tool, changing a tool's args/permissions, or changing the propose/confirm/execute flow.

## Workflow (adding a tool)
1. **Verify the endpoint** (use the repository-audit skill): method, path, body schema, role check and error codes. Never invent routes.
2. **Define** in `api_tools.py`:
   ```python
   class ApproveLeaveArgs(BaseModel):
       leave_request_id: int = Field(gt=0)

   TOOL = ToolSpec(
       name="approve_leave",
       description="Approve a pending leave request by id.",
       args_model=ApproveLeaveArgs,
       method="POST", path="/api/v1/leaves/requests/{leave_request_id}/approve",
       mutating=True, capability=Capability.LEAVE_APPROVE,
   )
   ```
3. **Register permissions** in `services/ai/authorization/policy.py` (`AI_PERMISSION_MATRIX`: role → `TargetRule`), and update `ai_permissions_matrix.md` §0 plus `tests/ai/authorization/test_matrix.py::EXPECTED`. The target employee (e.g. the leave's owner, resolved server-side) is checked with `TargetEmployee(owner_id)`.
4. **Flow**:
   1. Filter tools by role, and give the LLM only those schemas.
   2. Validate the LLM tool call with `args_model`. On failure, ask for clarification.
   3. Run `require(ctx, tool.capability, TargetEmployee(owner_id))` from `services/ai/authorization`.
   4. If mutating, `confirmations.create(...)` and return `needs_confirmation` with a readable preview.
   5. On confirm, re-validate the user, TTL and single-use status, re-check permissions, execute, and summarize.
5. **Execute**: `httpx.AsyncClient(transport=ASGITransport(app))` (or `AI_BACKEND_BASE_URL`) with header `Authorization: Bearer <user token>`, timeout 10 s. Retry idempotent GETs once. **Never retry mutations.**
6. **Map errors**: parse `detail.error.code` from the existing envelope. 403 → `AI_PERMISSION_DENIED`, 404 → not found (generic), 409/400 → the backend's own message (it's user-safe), and 5xx/timeout → `AI_BACKEND_UNAVAILABLE`.

## Constraints
- No DB writes in the agent. No SQLAlchemy session with write intent is imported in `action_agent.py` or `api_tools.py`.
- Never call `GET /employees/{id}`, `/finance/*` or document endpoints.
- Never log the token or the Authorization header.
- The outer request must not hold an open DB write transaction while calling the endpoint, because of SQLite locks.

## Examples
- EMPLOYEE: "Apply sick leave May 6–7, fever" → `create_leave_request{SICK, 2026-05-06, 2026-05-07, "Fever"}` → confirmation → POST `/leaves/requests` → "Submitted. Status: PENDING."
- EMPLOYEE: "Approve Rahul's leave" → `approve_leave` isn't in the employee's toolset → `AI_PERMISSION_DENIED`, and no lookup is performed.
- MANAGER approving a non-team employee's leave → team pre-check fails → `AI_OUT_OF_SCOPE`.

## Validation checklist
- [ ] Tool appears only for allowed roles (parametrized test)
- [ ] Arg validation rejects bad enums, dates and ids
- [ ] Confirmation: single-use, expiry, wrong user, cancelled
- [ ] Backend 400/403/404/409/timeout mapped
- [ ] Static test: tool registry has no forbidden paths
- [ ] Audit row written for propose and for execute

## Common failure modes
- Using the assignment's non-existent `PATCH` routes.
- Letting the LLM pick numeric ids from free text without a resolution step.
- Executing on first request because "it's low impact". All mutations confirm (decision D6).
