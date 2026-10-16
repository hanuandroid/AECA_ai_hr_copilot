"""Last-active-administrator invariant (RBAC remediation step 4, approved Decision 2).

The application is single-tenant (no organisation model), so the invariant is system-wide: at least one employee with
role ADMIN and status ACTIVE must always remain. Every demotion or deactivation of an ADMIN goes through one
conditional UPDATE (`_remove_admin_guarded`) whose WHERE clause requires another active admin, so the check and the
write cannot be separated by a concurrent request. Self role change and self-deactivation are refused separately.

What these tests prove and what they do not:
- the guard refuses when no other active admin exists, and inactive admins do not count;
- both HTTP paths (PUT role, DELETE deactivate) use it, and AI actions reach the same HTTP handlers;
- concurrent cross-removal by two admins, issued together on the async test client against the real SQLite engine,
  never leaves zero active admins (repeated runs, demotion and deactivation mixed);
- NOT proven: behaviour on other database engines or with several API processes/instances (unsupported deployment).
"""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.api.v1.endpoints.employees import _remove_admin_guarded
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from tests.support.data import ADMIN, MANAGER, OUTSIDER
from tests.support.http import error_code


async def _active_admins(db) -> set[int]:
    db.expire_all()
    rows = await db.execute(select(Employee.id).where(Employee.role == Role.ADMIN, Employee.status == EmployeeStatus.ACTIVE))
    return set(rows.scalars())


async def _make_admin(db, employee_id: int, status: EmployeeStatus = EmployeeStatus.ACTIVE) -> None:
    await db.execute(update(Employee).where(Employee.id == employee_id).values(role=Role.ADMIN, status=status))
    await db.commit()


async def test_guard_refuses_the_only_active_admin(db):
    with pytest.raises(HTTPException) as caught:
        await _remove_admin_guarded(db, ADMIN.id, {"status": EmployeeStatus.INACTIVE})
    assert caught.value.status_code == 409 and caught.value.detail["error"]["code"] == "LAST_ACTIVE_ADMIN"
    assert await _active_admins(db) == {ADMIN.id}


async def test_inactive_admins_do_not_count(db):
    await _make_admin(db, OUTSIDER.id, EmployeeStatus.INACTIVE)
    with pytest.raises(HTTPException):
        await _remove_admin_guarded(db, ADMIN.id, {"role": Role.EMPLOYEE})
    assert await _active_admins(db) == {ADMIN.id}


async def test_guard_allows_removal_when_another_active_admin_remains(db):
    await _make_admin(db, OUTSIDER.id)
    await _remove_admin_guarded(db, OUTSIDER.id, {"role": Role.EMPLOYEE})
    await db.commit()
    assert await _active_admins(db) == {ADMIN.id}


async def test_demoting_another_admin_over_http_when_one_remains(client, auth, db):
    await _make_admin(db, OUTSIDER.id)
    response = await client.put(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"),
                                json={"role": "EMPLOYEE", "name": "Former Admin"})
    assert response.status_code == 200
    assert await _active_admins(db) == {ADMIN.id}
    row = (await db.execute(select(Employee).where(Employee.id == OUTSIDER.id))).scalar_one()
    assert (row.role, row.name) == (Role.EMPLOYEE, "Former Admin")


async def test_deactivating_another_admin_over_http_when_one_remains(client, auth, db):
    await _make_admin(db, OUTSIDER.id)
    response = await client.delete(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"))
    assert response.status_code == 200 and await _active_admins(db) == {ADMIN.id}


@pytest.mark.parametrize("round_", range(5))
@pytest.mark.parametrize("kind", ["deactivate", "demote", "mixed"])
async def test_concurrent_cross_removal_never_leaves_zero_admins(client, auth, db, kind, round_):
    """Two active admins remove each other at the same moment: at most one request may succeed."""
    await _make_admin(db, OUTSIDER.id)
    admin, other = await auth("admin"), await auth("outsider")

    def remove(headers, target, how):
        if how == "deactivate":
            return client.delete(f"/api/v1/employees/{target}", headers=headers)
        return client.put(f"/api/v1/employees/{target}", headers=headers, json={"role": "EMPLOYEE"})

    first, second = {"deactivate": ("deactivate", "deactivate"), "demote": ("demote", "demote"),
                     "mixed": ("deactivate", "demote")}[kind]
    responses = await asyncio.gather(remove(admin, OUTSIDER.id, first), remove(other, ADMIN.id, second))
    statuses = sorted(r.status_code for r in responses)
    remaining = await _active_admins(db)
    assert len(remaining) >= 1, statuses
    assert statuses.count(200) <= 1, statuses
    for response in responses:
        if response.status_code != 200:  # the loser is refused cleanly, never a 500
            assert response.status_code in (403, 409), response.text
            assert error_code(response) in ("LAST_ACTIVE_ADMIN", "CONCURRENT_UPDATE", "USER_INACTIVE", "FORBIDDEN")


async def test_repeated_requests_cannot_drain_admins(client, auth, db):
    await _make_admin(db, OUTSIDER.id)
    await _make_admin(db, MANAGER.id)
    for target in (OUTSIDER.id, MANAGER.id):
        assert (await client.delete(f"/api/v1/employees/{target}", headers=await auth("admin"))).status_code == 200
    again = await client.delete(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("admin"))
    assert again.status_code == 400 and error_code(again) == "ALREADY_INACTIVE"
    assert await _active_admins(db) == {ADMIN.id}


async def test_failed_guard_rolls_back_the_whole_request(db):
    """A refused demotion leaves no partial change behind (same transaction)."""
    await db.execute(update(Employee).where(Employee.id == ADMIN.id).values(name="Pending Rename"))
    with pytest.raises(HTTPException):
        await _remove_admin_guarded(db, ADMIN.id, {"role": Role.EMPLOYEE})
    db.expire_all()
    row = (await db.execute(select(Employee).where(Employee.id == ADMIN.id))).scalar_one()
    assert (row.name, row.role) == ("Admin Test", Role.ADMIN)
    assert (await db.execute(select(func.count(Employee.id)))).scalar_one() == 5
