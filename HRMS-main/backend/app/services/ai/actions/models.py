"""Action-agent data models.

ActionProposal is the ONLY thing accepted from the model. It can name a registered action and give
*hints* (names, ids, arguments). Every id in it is untrusted and is resolved/validated by backend
code; unknown fields (e.g. "authorized", "role", "url", "method") make the proposal invalid.
"""

import enum
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionName(str, enum.Enum):
    LEAVE_CREATE = "LEAVE_CREATE"
    LEAVE_APPROVE = "LEAVE_APPROVE"
    LEAVE_REJECT = "LEAVE_REJECT"
    TICKET_CREATE = "TICKET_CREATE"
    TICKET_ASSIGN = "TICKET_ASSIGN"
    TICKET_STATUS_UPDATE = "TICKET_STATUS_UPDATE"
    ANNOUNCEMENT_CREATE = "ANNOUNCEMENT_CREATE"
    PROJECT_ASSIGN = "PROJECT_ASSIGN"
    PROJECT_CREATE = "PROJECT_CREATE"
    EMPLOYEE_DEACTIVATE = "EMPLOYEE_DEACTIVATE"


class TargetType(str, enum.Enum):
    SELF = "SELF"  # the caller (the API uses the caller's token)
    LEAVE_REQUEST = "LEAVE_REQUEST"
    TICKET = "TICKET"
    EMPLOYEE = "EMPLOYEE"
    NONE = "NONE"  # organisation-level (announcement, project catalog)


# --- untrusted model output -----------------------------------------------------------------


class ActionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leave_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_half_day: bool | None = None
    half_day_period: str | None = None
    reason: str | None = None
    title: str | None = None
    description: str | None = None
    category: str | None = None
    priority: str | None = None
    status: str | None = None
    body: str | None = None
    role_on_project: str | None = None
    project_name: str | None = None
    project_description: str | None = None
    project_status: str | None = None


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "LEAVE_CREATE", "LEAVE_APPROVE", "LEAVE_REJECT", "TICKET_CREATE", "TICKET_ASSIGN",
        "TICKET_STATUS_UPDATE", "ANNOUNCEMENT_CREATE", "PROJECT_ASSIGN", "PROJECT_CREATE",
        "EMPLOYEE_DEACTIVATE", "NONE",
    ]
    needs_clarification: bool = False
    clarification_question: str | None = None
    target_employee_name: str | None = None
    target_employee_id: int | None = None  # untrusted hint
    resource_id: int | None = None  # untrusted hint: leave request / ticket id stated by the user
    ticket_title: str | None = None
    assignee_name: str | None = None
    arguments: ActionArguments = Field(default_factory=ActionArguments)


# --- typed, validated request parameters (mirror the existing API request schemas) ------------


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeaveCreateParams(_Params):
    leave_type: Literal["CASUAL", "SICK", "EARNED"]
    start_date: date
    end_date: date
    is_half_day: bool = False
    half_day_period: Literal["FIRST_HALF", "SECOND_HALF"] | None = None
    reason: str = Field(min_length=3, max_length=500)


class TicketCreateParams(_Params):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=5, max_length=5000)
    category: Literal["IT", "HR", "ONBOARDING"]
    priority: Literal["LOW", "MEDIUM", "HIGH"]


class TicketAssignParams(_Params):
    assignee_id: int = Field(gt=0)


class TicketStatusParams(_Params):
    status: Literal["OPEN", "IN_PROGRESS", "RESOLVED"]


class AnnouncementCreateParams(_Params):
    title: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=5, max_length=5000)
    # Set by the validator from the caller's ANNOUNCEMENT_CREATE grant (ORG -> ORG, TEAM -> TEAM), never by the model.
    # The announcements endpoint enforces the same rule again (RBAC step 6).
    audience: Literal["ORG", "TEAM"]


class ProjectAssignParams(_Params):
    project_id: int = Field(gt=0)
    role_on_project: str | None = Field(default=None, min_length=2, max_length=120)


class ProjectCreateParams(_Params):
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["ONGOING", "COMPLETED", "ON_HOLD", "PLANNED"] = "ONGOING"


class NoParams(_Params):
    pass


# --- prepared action / API responses -------------------------------------------------------


class PreparedAction(BaseModel):
    """Fully resolved, authorized, validated action awaiting confirmation (server-side only)."""

    action: ActionName
    target_employee_id: int | None
    resource_id: int | None
    path_params: dict[str, int]
    body: dict[str, Any] | None
    summary: str


class ActionResponse(BaseModel):
    status: Literal["clarification_required", "confirmation_required", "completed", "failed"]
    message: str
    action: str | None = None
    summary: str | None = None
    confirmation_id: str | None = None
    expires_at: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
