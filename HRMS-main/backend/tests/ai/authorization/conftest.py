from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.enums import Role
from app.services.ai.authorization import AIUserContext, build_ai_user_context
from tests.support.data import USERS


@pytest.fixture
def ctx_for(db) -> Callable[[str], Awaitable[AIUserContext]]:
    """Build the AI context from the real DB row (the only trusted source)."""

    async def _ctx_for(user_key: str) -> AIUserContext:
        employee = (await db.execute(select(Employee).where(Employee.id == USERS[user_key].id))).scalar_one()
        return await build_ai_user_context(employee, db)

    return _ctx_for


def make_ctx(role: Role, *, employee_id: int = 100, active: bool = True, reports: set[int] | None = None) -> AIUserContext:
    """Synthetic context for pure matrix tests (no DB needed)."""
    return AIUserContext(
        employee_id=employee_id,
        role=role,
        is_active=active,
        manager_id=None,
        department_id=None,
        direct_report_ids=frozenset(reports or set()),
    )
