"""The code-controlled allow-list of AI actions. The Action Agent can call only registered HRMS APIs.

Every action maps to exactly one Phase 2 Capability (the authorization source of truth), one fixed
HTTP method and route template of an EXISTING business endpoint, one typed parameter schema, and
requires confirmation. The model can only pick an ActionName; it can never supply a URL, method,
header or body shape. Routes are checked against the AI route deny-list at import time.
"""

from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel

from app.services.ai.actions.models import (
    ActionName,
    AnnouncementCreateParams,
    LeaveCreateParams,
    NoParams,
    ProjectAssignParams,
    ProjectCreateParams,
    TargetType,
    TicketAssignParams,
    TicketCreateParams,
    TicketStatusParams,
)
from app.services.ai.authorization import Capability
from app.services.ai.guards import is_route_forbidden


@dataclass(frozen=True)
class ActionSpec:
    name: ActionName
    capability: Capability
    method: str  # fixed per action; never from the model
    path: str  # route template of the existing endpoint, e.g. /api/v1/leaves/requests/{leave_id}/approve
    params_model: type[BaseModel]
    target_type: TargetType
    requires_confirmation: bool
    description: str  # shown to the model


_SPECS = (
    ActionSpec(ActionName.LEAVE_CREATE, Capability.LEAVE_CREATE, "POST", "/api/v1/leaves/requests",
               LeaveCreateParams, TargetType.SELF, True,
               "Submit a leave request for the current user (leave_type CASUAL|SICK|EARNED, start_date, end_date "
               "YYYY-MM-DD, reason, optional is_half_day + half_day_period FIRST_HALF|SECOND_HALF)."),
    ActionSpec(ActionName.LEAVE_APPROVE, Capability.LEAVE_APPROVE, "POST", "/api/v1/leaves/requests/{leave_id}/approve",
               NoParams, TargetType.LEAVE_REQUEST, True,
               "Approve a pending leave request (identify it by the employee's name, or its id if the user gave one)."),
    ActionSpec(ActionName.LEAVE_REJECT, Capability.LEAVE_REJECT, "POST", "/api/v1/leaves/requests/{leave_id}/reject",
               NoParams, TargetType.LEAVE_REQUEST, True,
               "Reject a pending leave request (identify it by the employee's name, or its id if the user gave one)."),
    ActionSpec(ActionName.TICKET_CREATE, Capability.TICKET_CREATE, "POST", "/api/v1/tickets",
               TicketCreateParams, TargetType.SELF, True,
               "Create a support ticket for the current user (title, description, category IT|HR|ONBOARDING, "
               "priority LOW|MEDIUM|HIGH)."),
    ActionSpec(ActionName.TICKET_ASSIGN, Capability.TICKET_ASSIGN, "POST", "/api/v1/tickets/{ticket_id}/assign",
               TicketAssignParams, TargetType.TICKET, True,
               "Assign an existing ticket (by id or title) to an employee (assignee_name)."),
    ActionSpec(ActionName.TICKET_STATUS_UPDATE, Capability.TICKET_STATUS_UPDATE, "POST", "/api/v1/tickets/{ticket_id}/status",
               TicketStatusParams, TargetType.TICKET, True,
               "Change an existing ticket's status (OPEN|IN_PROGRESS|RESOLVED)."),
    ActionSpec(ActionName.ANNOUNCEMENT_CREATE, Capability.ANNOUNCEMENT_CREATE, "POST", "/api/v1/announcements",
               AnnouncementCreateParams, TargetType.NONE, True,
               "Publish a company announcement (title, body)."),
    ActionSpec(ActionName.PROJECT_ASSIGN, Capability.PROJECT_ASSIGN, "POST", "/api/v1/employees/{employee_id}/projects",
               ProjectAssignParams, TargetType.EMPLOYEE, True,
               "Assign an employee (target_employee_name) to an existing project (project_name), optional role_on_project."),
    ActionSpec(ActionName.PROJECT_CREATE, Capability.PROJECT_CREATE, "POST", "/api/v1/employees/projects/catalog",
               ProjectCreateParams, TargetType.NONE, True,
               "Create a project in the catalog (project_name, optional project_description, project_status)."),
    ActionSpec(ActionName.EMPLOYEE_DEACTIVATE, Capability.EMPLOYEE_DEACTIVATE, "DELETE", "/api/v1/employees/{employee_id}",
               NoParams, TargetType.EMPLOYEE, True,
               "Deactivate an employee account (target_employee_name)."),
)

ACTION_REGISTRY: MappingProxyType[ActionName, ActionSpec] = MappingProxyType({spec.name: spec for spec in _SPECS})
ALLOWED_METHODS = frozenset({"POST", "DELETE"})


def _validate_registry() -> None:
    for spec in ACTION_REGISTRY.values():
        if spec.method not in ALLOWED_METHODS or not spec.path.startswith("/api/v1/") or "://" in spec.path:
            raise RuntimeError(f"invalid route for {spec.name}")
        if is_route_forbidden(spec.method, spec.path.format(leave_id=1, ticket_id=1, employee_id=1)):
            raise RuntimeError(f"registered route is on the AI deny-list: {spec.name}")
        if not spec.requires_confirmation:
            raise RuntimeError("every data-changing AI action requires confirmation (D6)")
        if spec.name.value != spec.capability.value:
            raise RuntimeError(f"action {spec.name} must map to capability of the same name")


_validate_registry()
