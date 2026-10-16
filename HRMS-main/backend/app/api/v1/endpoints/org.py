from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success_response
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from app.services.auth import get_current_user

router = APIRouter()


def _build_tree(nodes: dict[int, dict], children_map: dict[int | None, list[int]], parent_id: int | None = None):
    result = []
    for node_id in children_map.get(parent_id, []):
        node = nodes[node_id]
        result.append(
            {
                "id": node["id"],
                "name": node["name"],
                "role": node["role"],
                "manager_id": node["manager_id"],
                "children": _build_tree(nodes, children_map, node_id),
            }
        )
    return result


@router.get("/tree")
async def org_tree(
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    # Decision 1: the limited chart shows ACTIVE employees only, with id, name, role and manager reference. One query,
    # only the four columns (no full rows or sensitive fields are loaded). An employee whose manager is not visible
    # (inactive) becomes a root with manager_id null, so no hidden employee is referenced.
    rows = (await db.execute(
        select(Employee.id, Employee.name, Employee.role, Employee.manager_id)
        .where(Employee.status == EmployeeStatus.ACTIVE)
        .order_by(Employee.id.asc())
    )).all()
    visible_ids = {row.id for row in rows}

    nodes: dict[int, dict] = {}
    children_map: dict[int | None, list[int]] = defaultdict(list)
    for row in rows:
        manager_id = row.manager_id if row.manager_id in visible_ids else None
        nodes[row.id] = {
            "id": row.id,
            "name": row.name,
            "role": row.role.value,
            "manager_id": manager_id,
        }
        children_map[manager_id].append(row.id)

    tree = _build_tree(nodes, children_map, None)
    return success_response({"items": tree})
