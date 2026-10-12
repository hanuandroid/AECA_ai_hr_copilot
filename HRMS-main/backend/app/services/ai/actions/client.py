"""HRMSActionClient: typed commands -> the EXISTING HRMS endpoints, as the authenticated user.

Integration choice (documented in ai_architecture.md): in-process HTTP via httpx.ASGITransport
against the running FastAPI app. This reuses the real routes end-to-end (their own get_current_user,
role checks, validation and DB logic) without a network hop, and the request carries the CALLER's
own bearer token - there is no service/admin identity. Methods and paths come only from the action
registry; the model can never supply a URL, method, header or body shape.

Mutations are never retried (no idempotency keys exist in the business API); a timeout or 5xx is
reported as AI_BACKEND_UNAVAILABLE without re-sending.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.services.ai.actions.models import (
    ActionName,
    AnnouncementCreateParams,
    LeaveCreateParams,
    PreparedAction,
    ProjectAssignParams,
    ProjectCreateParams,
    TicketAssignParams,
    TicketCreateParams,
    TicketStatusParams,
)
from app.services.ai.actions.registry import ACTION_REGISTRY
from app.services.ai.errors import AIActionOutcomeUnknownError, AIBackendUnavailableError
from app.services.ai.guards import assert_route_allowed, strip_sensitive_fields

logger = structlog.get_logger()
INTERNAL_BASE_URL = "http://hrms.internal"
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ApiOutcome:
    ok: bool
    status_code: int
    data: dict[str, Any] | None
    error_code: str | None
    message: str | None


class HRMSActionClient:
    def __init__(self, app: Any, access_token: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._app = app
        self._token = access_token
        self._timeout = timeout_seconds

    # --- typed commands (one per registered action) ---------------------------------------------

    async def create_leave_request(self, params: LeaveCreateParams) -> ApiOutcome:
        return await self._send(ActionName.LEAVE_CREATE, {}, params.model_dump(mode="json"))

    async def approve_leave(self, leave_id: int) -> ApiOutcome:
        return await self._send(ActionName.LEAVE_APPROVE, {"leave_id": leave_id}, None)

    async def reject_leave(self, leave_id: int) -> ApiOutcome:
        return await self._send(ActionName.LEAVE_REJECT, {"leave_id": leave_id}, None)

    async def create_ticket(self, params: TicketCreateParams) -> ApiOutcome:
        return await self._send(ActionName.TICKET_CREATE, {}, params.model_dump(mode="json"))

    async def assign_ticket(self, ticket_id: int, params: TicketAssignParams) -> ApiOutcome:
        return await self._send(ActionName.TICKET_ASSIGN, {"ticket_id": ticket_id}, params.model_dump(mode="json"))

    async def update_ticket_status(self, ticket_id: int, params: TicketStatusParams) -> ApiOutcome:
        return await self._send(ActionName.TICKET_STATUS_UPDATE, {"ticket_id": ticket_id}, params.model_dump(mode="json"))

    async def create_announcement(self, params: AnnouncementCreateParams) -> ApiOutcome:
        return await self._send(ActionName.ANNOUNCEMENT_CREATE, {}, params.model_dump(mode="json"))

    async def assign_employee_to_project(self, employee_id: int, params: ProjectAssignParams) -> ApiOutcome:
        return await self._send(ActionName.PROJECT_ASSIGN, {"employee_id": employee_id}, params.model_dump(mode="json"))

    async def create_project(self, params: ProjectCreateParams) -> ApiOutcome:
        return await self._send(ActionName.PROJECT_CREATE, {}, params.model_dump(mode="json"))

    async def deactivate_employee(self, employee_id: int) -> ApiOutcome:
        return await self._send(ActionName.EMPLOYEE_DEACTIVATE, {"employee_id": employee_id}, None)

    async def execute(self, prepared: PreparedAction) -> ApiOutcome:
        """Dispatch a confirmed action to its typed command (payload re-validated by the params model)."""
        spec = ACTION_REGISTRY[prepared.action]
        body = spec.params_model.model_validate(prepared.body or {})
        path = prepared.path_params
        dispatch = {
            ActionName.LEAVE_CREATE: lambda: self.create_leave_request(body),
            ActionName.LEAVE_APPROVE: lambda: self.approve_leave(path["leave_id"]),
            ActionName.LEAVE_REJECT: lambda: self.reject_leave(path["leave_id"]),
            ActionName.TICKET_CREATE: lambda: self.create_ticket(body),
            ActionName.TICKET_ASSIGN: lambda: self.assign_ticket(path["ticket_id"], body),
            ActionName.TICKET_STATUS_UPDATE: lambda: self.update_ticket_status(path["ticket_id"], body),
            ActionName.ANNOUNCEMENT_CREATE: lambda: self.create_announcement(body),
            ActionName.PROJECT_ASSIGN: lambda: self.assign_employee_to_project(path["employee_id"], body),
            ActionName.PROJECT_CREATE: lambda: self.create_project(body),
            ActionName.EMPLOYEE_DEACTIVATE: lambda: self.deactivate_employee(path["employee_id"]),
        }
        return await dispatch[prepared.action]()

    # --- the single, registry-bound transport ------------------------------------------------------

    async def _send(self, action: ActionName, path_params: dict[str, int], body: dict[str, Any] | None) -> ApiOutcome:
        spec = ACTION_REGISTRY[action]
        if any(type(value) is not int or value <= 0 for value in path_params.values()):
            raise ValueError("path parameters must be positive integers")
        path = spec.path.format(**path_params)
        assert_route_allowed(spec.method, path)
        transport = httpx.ASGITransport(app=self._app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url=INTERNAL_BASE_URL, timeout=self._timeout) as client:
                # httpx timeouts are not applied by the in-process ASGITransport, so the deadline is enforced here
                # (production hardening B-10). On expiry the outcome is UNKNOWN: the handler may have committed.
                response = await asyncio.wait_for(
                    client.request(spec.method, path, json=body, headers={"Authorization": f"Bearer {self._token}"}),
                    timeout=self._timeout,
                )
        except TimeoutError as exc:
            logger.error("ai.action.deadline_exceeded", action=action.value, timeout_seconds=self._timeout)
            raise AIActionOutcomeUnknownError() from exc
        except httpx.HTTPError as exc:
            logger.error("ai.action.transport_failed", action=action.value, error_type=type(exc).__name__)
            raise AIBackendUnavailableError() from exc
        return _outcome(action, response)


def _outcome(action: ActionName, response: httpx.Response) -> ApiOutcome:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 500:
        logger.error("ai.action.backend_error", action=action.value, status=response.status_code)
        raise AIBackendUnavailableError()
    if response.is_success:
        data = payload.get("data") if isinstance(payload, dict) else None
        return ApiOutcome(True, response.status_code, strip_sensitive_fields(data) if isinstance(data, dict) else None, None, None)

    detail = payload.get("detail") if isinstance(payload, dict) else None
    error = detail.get("error") if isinstance(detail, dict) else None
    if response.status_code in (401, 403):
        return ApiOutcome(False, response.status_code, None, "FORBIDDEN", "You do not have permission to perform this action.")
    if isinstance(error, dict):
        return ApiOutcome(False, response.status_code, None, str(error.get("code") or "REQUEST_REJECTED"),
                          str(error.get("message") or "The HR system rejected this request."))
    return ApiOutcome(False, response.status_code, None, "REQUEST_REJECTED", "The HR system rejected the request details.")
