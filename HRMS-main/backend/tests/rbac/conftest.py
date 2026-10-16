"""Extra people for team-scope tests (RBAC step 6): a second manager with one direct report, and an indirect report
of MANAGER (reports to EMPLOYEE, who reports to MANAGER). Tokens are minted like /auth/login would; the role claim is
ignored by the API, which loads the role from the database."""

from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from tests.support.data import ADMIN, EMPLOYEE, MANAGER


@dataclass(frozen=True)
class People:
    manager_b: int = 20  # MANAGER, reports to ADMIN
    report_b: int = 21  # EMPLOYEE, reports to manager_b
    indirect: int = 22  # EMPLOYEE, reports to EMPLOYEE (an indirect report of MANAGER)

    @staticmethod
    def headers(employee_id: int) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(subject=str(employee_id), role='EMPLOYEE')}"}


@pytest.fixture
async def people(db) -> People:
    hashed = (await db.execute(select(Employee.hashed_password).where(Employee.id == MANAGER.id))).scalar_one()
    rows = ((People.manager_b, "Bina Manager", ADMIN.id, Role.MANAGER), (People.report_b, "Bart Report", People.manager_b, Role.EMPLOYEE),
            (People.indirect, "Ida Indirect", EMPLOYEE.id, Role.EMPLOYEE))
    for employee_id, name, manager_id, role in rows:
        db.add(Employee(id=employee_id, name=name, email=f"{name.split()[0].lower()}@hrms-test.dev", hashed_password=hashed,
                        department_id=1, manager_id=manager_id, role=role, status=EmployeeStatus.ACTIVE,
                        joining_date=date(2023, 1, 1)))
        await db.flush()
    await db.commit()
    return People()
