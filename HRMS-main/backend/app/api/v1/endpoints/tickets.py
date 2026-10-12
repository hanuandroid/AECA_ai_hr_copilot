from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import error_response, success_response
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import Role, TicketCategory, TicketPriority, TicketStatus
from app.models.onboarding_task import OnboardingTask
from app.models.ticket import Ticket
from app.schemas.ticket import OnboardingTaskCreate, TicketAssign, TicketCreate, TicketStatusUpdate
from app.services.auth import get_current_user

router = APIRouter()


# --- Manager ticket scope (production hardening S-01a) ---------------------------------------------------------
# Admins: every ticket. Managers: tickets raised by or assigned to themselves, or raised by their direct reports
# (employees.manager_id) - the approved AI matrix scope, now enforced in the business API. Employees keep the existing
# owner/assignee rules.


def _manager_ticket_filter(manager_id: int):
    reports = select(Employee.id).where(Employee.manager_id == manager_id)
    return or_(Ticket.employee_id == manager_id, Ticket.assignee_id == manager_id, Ticket.employee_id.in_(reports))


def _ticket_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TICKET_NOT_FOUND", "Ticket not found"))


async def _ensure_manager_ticket_scope(current_user: Employee, ticket: Ticket, db: AsyncSession, not_found: HTTPException) -> None:
    """Out-of-scope tickets look exactly like unknown ones (ticket ids are not public)."""
    if current_user.role != Role.MANAGER:
        return
    if current_user.id in (ticket.employee_id, ticket.assignee_id):
        return
    owner_manager_id = (await db.execute(select(Employee.manager_id).where(Employee.id == ticket.employee_id))).scalar_one_or_none()
    if owner_manager_id != current_user.id:
        raise not_found


def _ticket_payload(ticket: Ticket, tasks: list[OnboardingTask] | None = None):
    payload = {
        "id": ticket.id,
        "employee_id": ticket.employee_id,
        "assignee_id": ticket.assignee_id,
        "title": ticket.title,
        "description": ticket.description,
        "category": ticket.category.value,
        "priority": ticket.priority.value,
        "status": ticket.status.value,
        "created_at": ticket.created_at,
    }
    if tasks is not None:
        payload["onboarding_tasks"] = [
            {"id": task.id, "ticket_id": task.ticket_id, "task_name": task.task_name, "is_completed": task.is_completed, "due_date": task.due_date}
            for task in tasks
        ]
    return payload


@router.get("")
async def list_tickets(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    mine: bool = Query(default=False),
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Ticket)
    count_query = select(func.count(Ticket.id))

    if current_user.role not in {Role.ADMIN, Role.MANAGER}:
        ownership_filter = or_(Ticket.employee_id == current_user.id, Ticket.assignee_id == current_user.id)
        query = query.where(ownership_filter)
        count_query = count_query.where(ownership_filter)
    elif current_user.role == Role.MANAGER and not mine:
        # S-01a: managers see their team's tickets only (items, count and pagination share the filter).
        query = query.where(_manager_ticket_filter(current_user.id))
        count_query = count_query.where(_manager_ticket_filter(current_user.id))
    elif mine:
        ownership_filter = or_(Ticket.employee_id == current_user.id, Ticket.assignee_id == current_user.id)
        query = query.where(ownership_filter)
        count_query = count_query.where(ownership_filter)

    if status_filter:
        try:
            status_enum = TicketStatus(status_filter.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response("INVALID_TICKET_STATUS", "status must be OPEN, IN_PROGRESS, or RESOLVED"),
            )
        query = query.where(Ticket.status == status_enum)
        count_query = count_query.where(Ticket.status == status_enum)

    total = (await db.execute(count_query)).scalar_one()
    tickets = (await db.execute(query.order_by(desc(Ticket.created_at), desc(Ticket.id)).limit(limit).offset(offset))).scalars().all()
    if not tickets:
        return success_response({"items": [], "meta": {"total": total, "limit": limit, "offset": offset}})

    ticket_ids = [ticket.id for ticket in tickets]
    tasks = (
        await db.execute(select(OnboardingTask).where(OnboardingTask.ticket_id.in_(ticket_ids)).order_by(OnboardingTask.id.asc()))
    ).scalars().all()
    tasks_by_ticket: dict[int, list[OnboardingTask]] = {}
    for task in tasks:
        tasks_by_ticket.setdefault(task.ticket_id, []).append(task)

    return success_response(
        {
            "items": [_ticket_payload(ticket, tasks_by_ticket.get(ticket.id, [])) for ticket in tickets],
            "meta": {"total": total, "limit": limit, "offset": offset},
        }
    )


@router.post("")
async def create_ticket(
    payload: TicketCreate,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        category = TicketCategory(payload.category.upper())
        priority = TicketPriority(payload.priority.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_TICKET_ENUM", "category/priority is invalid"),
        )

    ticket = Ticket(
        employee_id=current_user.id,
        assignee_id=None,
        title=payload.title.strip(),
        description=payload.description.strip(),
        category=category,
        priority=priority,
        status=TicketStatus.OPEN,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return success_response(_ticket_payload(ticket, []))


@router.post("/{ticket_id}/assign")
async def assign_ticket(
    ticket_id: int,
    payload: TicketAssign,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in {Role.ADMIN, Role.MANAGER}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_response("FORBIDDEN", "Only manager/admin can assign tickets"))

    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TICKET_NOT_FOUND", "Ticket not found"))
    await _ensure_manager_ticket_scope(current_user, ticket, db, _ticket_not_found())

    assignee = (await db.execute(select(Employee).where(Employee.id == payload.assignee_id))).scalar_one_or_none()
    if assignee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("ASSIGNEE_NOT_FOUND", "Assignee not found"))
    # S-01a: managers assign only to themselves or their direct reports (employee existence is public via the directory).
    if current_user.role == Role.MANAGER and assignee.id != current_user.id and assignee.manager_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response("FORBIDDEN", "You can only assign tickets to yourself or your direct reports"),
        )

    ticket.assignee_id = payload.assignee_id
    if ticket.status == TicketStatus.OPEN:
        ticket.status = TicketStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(ticket)
    return success_response(_ticket_payload(ticket))


@router.post("/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: int,
    payload: TicketStatusUpdate,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TICKET_NOT_FOUND", "Ticket not found"))

    if current_user.role not in {Role.ADMIN, Role.MANAGER} and ticket.assignee_id != current_user.id and ticket.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_response("FORBIDDEN", "Not allowed to update this ticket"))
    await _ensure_manager_ticket_scope(current_user, ticket, db, _ticket_not_found())

    try:
        next_status = TicketStatus(payload.status.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_TICKET_STATUS", "status must be OPEN, IN_PROGRESS, or RESOLVED"),
        )
    ticket.status = next_status
    await db.commit()
    await db.refresh(ticket)
    return success_response(_ticket_payload(ticket))


@router.post("/{ticket_id}/onboarding-tasks")
async def create_onboarding_task(
    ticket_id: int,
    payload: OnboardingTaskCreate,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in {Role.ADMIN, Role.MANAGER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response("FORBIDDEN", "Only manager/admin can create onboarding tasks"),
        )

    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TICKET_NOT_FOUND", "Ticket not found"))
    await _ensure_manager_ticket_scope(current_user, ticket, db, _ticket_not_found())
    if ticket.category != TicketCategory.ONBOARDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_TICKET_CATEGORY", "Onboarding tasks are only allowed for ONBOARDING tickets"),
        )

    task = OnboardingTask(ticket_id=ticket_id, task_name=payload.task_name.strip(), due_date=payload.due_date, is_completed=False)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return success_response({"id": task.id, "ticket_id": task.ticket_id, "task_name": task.task_name, "is_completed": task.is_completed, "due_date": task.due_date})


@router.post("/{ticket_id}/onboarding-tasks/{task_id}/complete")
async def complete_onboarding_task(
    ticket_id: int,
    task_id: int,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = (await db.execute(select(OnboardingTask).where(OnboardingTask.id == task_id, OnboardingTask.ticket_id == ticket_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TASK_NOT_FOUND", "Onboarding task not found"))

    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TICKET_NOT_FOUND", "Ticket not found"))

    if current_user.role not in {Role.ADMIN, Role.MANAGER} and ticket.assignee_id != current_user.id and ticket.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_response("FORBIDDEN", "Not allowed to complete this task"))
    # Same answer as an unknown task id, so task ids of other teams' tickets cannot be probed.
    await _ensure_manager_ticket_scope(
        current_user, ticket, db,
        HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("TASK_NOT_FOUND", "Onboarding task not found")),
    )

    task.is_completed = True
    await db.commit()
    await db.refresh(task)
    return success_response({"id": task.id, "ticket_id": task.ticket_id, "task_name": task.task_name, "is_completed": task.is_completed, "due_date": task.due_date})
