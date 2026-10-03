"""Turn an untrusted proposal into a resolved, authorized, typed PreparedAction.

Only shape/type validation happens here (mirroring the existing request schemas). Business rules
(overlaps, balances, date order, half-day rules, duplicate projects, self-deactivation, ...) stay in
the existing endpoints, which remain the final authority.
"""

from pydantic import ValidationError

from app.services.ai.actions.models import (
    ActionName,
    ActionProposal,
    AnnouncementCreateParams,
    LeaveCreateParams,
    NoParams,
    PreparedAction,
    ProjectAssignParams,
    ProjectCreateParams,
    TicketAssignParams,
    TicketCreateParams,
    TicketStatusParams,
)
from app.services.ai.actions.registry import ActionSpec
from app.services.ai.actions.resolver import ClarificationNeeded, TargetResolver
from app.services.ai.authorization import AIUserContext, TargetEmployee, require

_FIELD_LABELS = {
    "leave_type": "leave type (CASUAL, SICK or EARNED)", "start_date": "start date", "end_date": "end date",
    "half_day_period": "half-day period (FIRST_HALF or SECOND_HALF)", "reason": "reason",
    "title": "title", "description": "description", "category": "category (IT, HR or ONBOARDING)",
    "priority": "priority (LOW, MEDIUM or HIGH)", "status": "status", "body": "announcement text", "name": "project name",
    "role_on_project": "role on the project",
}


def _upper(value: str | None) -> str | None:
    return value.strip().upper().replace(" ", "_") if isinstance(value, str) and value.strip() else None


def _clean(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _validate(model, data: dict):
    try:
        return model.model_validate({k: v for k, v in data.items() if v is not None})
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error.get("loc")})
        labels = ", ".join(_FIELD_LABELS.get(field, field) for field in fields) or "some details"
        raise ClarificationNeeded(f"I need a valid {labels} to do that.") from exc


async def prepare_action(
    spec: ActionSpec, proposal: ActionProposal, ctx: AIUserContext, resolver: TargetResolver
) -> PreparedAction:
    args = proposal.arguments
    capability = spec.capability
    name = spec.name

    if name == ActionName.LEAVE_CREATE:
        start = _clean(args.start_date)
        params = _validate(LeaveCreateParams, {
            "leave_type": _upper(args.leave_type), "start_date": start, "end_date": _clean(args.end_date) or start,
            "is_half_day": args.is_half_day, "half_day_period": _upper(args.half_day_period), "reason": _clean(args.reason),
        })
        await _check_self_target(ctx, proposal, spec, resolver)
        span = params.start_date.isoformat() if params.start_date == params.end_date else f"{params.start_date} to {params.end_date}"
        half = f" ({params.half_day_period.replace('_', ' ').lower()})" if params.is_half_day and params.half_day_period else ""
        return _prepared(spec, ctx.employee_id, None, {}, params.model_dump(mode="json"),
                         f"Submit a {params.leave_type} leave request for {span}{half}. Reason: {params.reason}")

    if name in (ActionName.LEAVE_APPROVE, ActionName.LEAVE_REJECT):
        leave, owner = await resolver.pending_leave(
            capability, leave_id=proposal.resource_id, employee_name=proposal.target_employee_name,
            employee_id=proposal.target_employee_id,
        )
        require(ctx, capability, TargetEmployee(owner.id))
        verb = "Approve" if name == ActionName.LEAVE_APPROVE else "Reject"
        return _prepared(spec, owner.id, leave.id, {"leave_id": leave.id}, None,
                         f"{verb} {owner.name}'s {leave.leave_type.value} leave request #{leave.id} "
                         f"({leave.start_date.isoformat()} to {leave.end_date.isoformat()}).")

    if name == ActionName.TICKET_CREATE:
        await _check_self_target(ctx, proposal, spec, resolver)
        params = _validate(TicketCreateParams, {
            "title": _clean(args.title), "description": _clean(args.description), "category": _upper(args.category),
            "priority": _upper(args.priority) or "MEDIUM",
        })
        return _prepared(spec, ctx.employee_id, None, {}, params.model_dump(mode="json"),
                         f"Create a {params.priority} priority {params.category} ticket: '{params.title}'.")

    if name in (ActionName.TICKET_ASSIGN, ActionName.TICKET_STATUS_UPDATE):
        ticket = await resolver.ticket(capability, ticket_id=proposal.resource_id, title=proposal.ticket_title)
        require(ctx, capability, TargetEmployee(ticket.employee_id))
        if name == ActionName.TICKET_ASSIGN:
            assignee = await resolver.assignee(name=proposal.assignee_name, hint_id=None)
            params = _validate(TicketAssignParams, {"assignee_id": assignee.id})
            summary = f"Assign ticket #{ticket.id} '{ticket.title}' to {assignee.name}."
        else:
            params = _validate(TicketStatusParams, {"status": _upper(args.status)})
            summary = f"Change ticket #{ticket.id} '{ticket.title}' status from {ticket.status.value} to {params.status}."
        return _prepared(spec, ticket.employee_id, ticket.id, {"ticket_id": ticket.id}, params.model_dump(mode="json"), summary)

    if name == ActionName.ANNOUNCEMENT_CREATE:
        params = _validate(AnnouncementCreateParams, {"title": _clean(args.title), "body": _clean(args.body) or _clean(args.description)})
        preview = params.body if len(params.body) <= 160 else params.body[:157] + "..."
        return _prepared(spec, None, None, {}, params.model_dump(mode="json"),
                         f"Publish announcement '{params.title}' to everyone: {preview}")

    if name == ActionName.PROJECT_ASSIGN:
        employee = await resolver.employee(capability, name=proposal.target_employee_name,
                                           hint_id=proposal.target_employee_id, active_only=True)
        require(ctx, capability, TargetEmployee(employee.id))
        project = await resolver.project(name=args.project_name)
        params = _validate(ProjectAssignParams, {"project_id": project.id, "role_on_project": _clean(args.role_on_project)})
        role = f" as {params.role_on_project}" if params.role_on_project else ""
        return _prepared(spec, employee.id, project.id, {"employee_id": employee.id}, params.model_dump(mode="json"),
                         f"Assign {employee.name} to project '{project.name}'{role}.")

    if name == ActionName.PROJECT_CREATE:
        params = _validate(ProjectCreateParams, {
            "name": _clean(args.project_name), "description": _clean(args.project_description),
            "status": _upper(args.project_status),
        })
        return _prepared(spec, None, None, {}, params.model_dump(mode="json"),
                         f"Create project '{params.name}' with status {params.status}.")

    if name == ActionName.EMPLOYEE_DEACTIVATE:
        if proposal.target_employee_id == ctx.employee_id:
            raise ClarificationNeeded("You cannot deactivate your own account.")
        employee = await resolver.employee(capability, name=proposal.target_employee_name, hint_id=proposal.target_employee_id)
        require(ctx, capability, TargetEmployee(employee.id))
        _validate(NoParams, {})
        return _prepared(spec, employee.id, None, {"employee_id": employee.id}, None,
                         f"Deactivate the account of {employee.name} (employee #{employee.id}). They will no longer appear "
                         "in the directory.")

    raise ClarificationNeeded("That action isn't supported by the assistant.")


async def _check_self_target(ctx: AIUserContext, proposal: ActionProposal, spec: ActionSpec, resolver: TargetResolver) -> None:
    """SELF actions act as the caller; a different target (by id or name) is resolved within SELF scope -> denied."""
    if proposal.target_employee_id is not None:
        require(ctx, spec.capability, TargetEmployee(proposal.target_employee_id))
    if proposal.target_employee_name:
        await resolver.employee(spec.capability, name=proposal.target_employee_name, hint_id=None)


def _prepared(spec, target, resource, path_params, body, summary) -> PreparedAction:
    return PreparedAction(action=spec.name, target_employee_id=target, resource_id=resource,
                          path_params=path_params, body=body, summary=summary)
