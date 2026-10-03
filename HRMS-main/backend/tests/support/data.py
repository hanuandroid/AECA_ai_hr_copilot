"""Minimal deterministic dataset for tests (independent of scripts/seed.py)."""

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.hr_policy import HRPolicy
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import (
    EmployeeStatus,
    LeaveRequestStatus,
    LeaveType,
    ProjectStatus,
    Role,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)
from app.models.leave_balance import LeaveBalance
from app.models.leave_request import LeaveRequest
from app.models.payroll_record import PayrollRecord
from app.models.project import Project
from app.models.ticket import Ticket

PASSWORD = "password123"


@dataclass(frozen=True)
class TestUser:
    key: str
    id: int
    email: str
    role: Role


ADMIN = TestUser("admin", 1, "admin@hrms-test.dev", Role.ADMIN)
MANAGER = TestUser("manager", 2, "manager@hrms-test.dev", Role.MANAGER)
EMPLOYEE = TestUser("employee", 3, "employee@hrms-test.dev", Role.EMPLOYEE)  # reports to MANAGER
OUTSIDER = TestUser("outsider", 4, "outsider@hrms-test.dev", Role.EMPLOYEE)  # no manager: outside MANAGER's team
INACTIVE = TestUser("inactive", 5, "inactive@hrms-test.dev", Role.EMPLOYEE)  # INACTIVE, reports to MANAGER

USERS = {user.key: user for user in (ADMIN, MANAGER, EMPLOYEE, OUTSIDER, INACTIVE)}

# Sensitive values seeded on EMPLOYEE so exposure can be asserted (R1).
EMPLOYEE_SENSITIVE = {
    "current_salary_usd": 72000.0,
    "bank_account_number": "00031000000002",
    "bank_ifsc": "MOCK0000003",
    "pan_number": "CFJNR1002V",
    "date_of_birth": "1990-03-07",
    "pf_uan": "100200300003",
}

PROJECT_ID = 1
PROJECT_NAME = "HR Policy Copilot"
EMPLOYEE_PENDING_LEAVE_ID = 1
OUTSIDER_PENDING_LEAVE_ID = 2
EMPLOYEE_APPROVED_LEAVE_ID = 3
EMPLOYEE_TICKET_ID = 1

TODAY = date.today()

POLICY_DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"
# (title, category, demo file) - same demo documents the real seed uses (D3).
TEST_POLICIES = [
    ("Leave Policy", "LEAVE", "leave_policy.md"),
    ("Attendance Policy", "ATTENDANCE", "attendance_policy.md"),
    ("WFH Policy", "WFH", "wfh_policy.md"),
    ("Document Upload Policy", "DOCUMENT", "document_upload_policy.md"),
    ("Employee Data Privacy Policy", "PRIVACY", "data_privacy_policy.md"),
]
LEGACY_POLICY = ("Travel Reimbursement", "TRAVEL", "Travel claims must be submitted within 15 days of return.")


def future(days: int) -> date:
    return TODAY + timedelta(days=days)


def seed_test_data(session: Session) -> None:
    engineering = Department(id=1, name="Engineering", location="Bengaluru")
    finance = Department(id=2, name="Finance", location="London")
    hr = Department(id=3, name="HR", location="Dubai")
    session.add_all([engineering, finance, hr])
    session.flush()

    hashed = hash_password(PASSWORD)
    rows = [
        (ADMIN, "Admin Test", hr.id, None, EmployeeStatus.ACTIVE),
        (MANAGER, "Manager Test", finance.id, None, EmployeeStatus.ACTIVE),
        (EMPLOYEE, "Employee Test", engineering.id, MANAGER.id, EmployeeStatus.ACTIVE),
        (OUTSIDER, "Outsider Test", engineering.id, None, EmployeeStatus.ACTIVE),
        (INACTIVE, "Inactive Test", engineering.id, MANAGER.id, EmployeeStatus.INACTIVE),
    ]
    for user, name, department_id, manager_id, status in rows:
        session.add(
            Employee(
                id=user.id,
                name=name,
                email=user.email,
                hashed_password=hashed,
                department_id=department_id,
                manager_id=manager_id,
                role=user.role,
                status=status,
                joining_date=date(2022, 1, 1),
            )
        )
    session.flush()

    employee = session.get(Employee, EMPLOYEE.id)
    employee.current_salary_usd = EMPLOYEE_SENSITIVE["current_salary_usd"]
    employee.bank_name = "Test Bank"
    employee.bank_account_number = EMPLOYEE_SENSITIVE["bank_account_number"]
    employee.bank_account_name = "Employee Test"
    employee.bank_branch = "Central"
    employee.bank_ifsc = EMPLOYEE_SENSITIVE["bank_ifsc"]
    employee.pan_number = EMPLOYEE_SENSITIVE["pan_number"]
    employee.pan_name = "Employee Test"
    employee.date_of_birth = date.fromisoformat(EMPLOYEE_SENSITIVE["date_of_birth"])
    employee.pan_dob = employee.date_of_birth

    for user in (ADMIN, MANAGER, EMPLOYEE, OUTSIDER, INACTIVE):
        session.add(LeaveBalance(employee_id=user.id, leave_type=LeaveType.CASUAL, total=12, used=2, remaining=10))
        session.add(LeaveBalance(employee_id=user.id, leave_type=LeaveType.SICK, total=10, used=1, remaining=9))
        session.add(LeaveBalance(employee_id=user.id, leave_type=LeaveType.EARNED, total=15, used=4, remaining=11))

    session.add_all(
        [
            LeaveRequest(
                id=EMPLOYEE_PENDING_LEAVE_ID,
                employee_id=EMPLOYEE.id,
                leave_type=LeaveType.CASUAL,
                start_date=future(10),
                end_date=future(11),
                reason="Family function",
                status=LeaveRequestStatus.PENDING,
            ),
            LeaveRequest(
                id=OUTSIDER_PENDING_LEAVE_ID,
                employee_id=OUTSIDER.id,
                leave_type=LeaveType.SICK,
                start_date=future(12),
                end_date=future(12),
                reason="Medical appointment",
                status=LeaveRequestStatus.PENDING,
            ),
            LeaveRequest(
                id=EMPLOYEE_APPROVED_LEAVE_ID,
                employee_id=EMPLOYEE.id,
                leave_type=LeaveType.SICK,
                start_date=future(-20),
                end_date=future(-20),
                reason="Recovery",
                status=LeaveRequestStatus.APPROVED,
                approver_id=MANAGER.id,
            ),
        ]
    )

    session.add(Project(id=PROJECT_ID, name=PROJECT_NAME, description="Policy assistant", status=ProjectStatus.ONGOING))
    session.add(
        Ticket(
            id=EMPLOYEE_TICKET_ID,
            employee_id=EMPLOYEE.id,
            assignee_id=None,
            title="VPN not working",
            description="Cannot connect to VPN from home.",
            category=TicketCategory.IT,
            priority=TicketPriority.HIGH,
            status=TicketStatus.OPEN,
        )
    )
    session.add(
        PayrollRecord(
            employee_id=EMPLOYEE.id,
            month=TODAY.replace(day=1),
            gross=6000.0,
            deductions={"pf": 270.0, "esi": 48.0, "tax": 600.0},
            net=5082.0,
            pan=EMPLOYEE_SENSITIVE["pan_number"],
            pf_uan=EMPLOYEE_SENSITIVE["pf_uan"],
            esi_no="ESI100003",
        )
    )
    seed_test_policies(session)
    session.commit()


def seed_test_policies(session: Session) -> None:
    """Policy library rows + files in the (temp) POLICY_UPLOAD_DIR, mirroring scripts/seed.py."""
    upload_dir = Path(settings.policy_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for index, (title, category, filename) in enumerate(TEST_POLICIES, start=1):
        raw = (POLICY_DOCS_DIR / filename).read_bytes()
        stored_name = f"seed_policy_{index:02d}.md"
        (upload_dir / stored_name).write_bytes(raw)
        session.add(
            HRPolicy(
                title=title,
                category=category,
                content=None,
                original_filename=stored_name,
                file_path=str(upload_dir / stored_name),
                mime_type="text/markdown",
                size_bytes=len(raw),
                uploaded_by=ADMIN.id,
                checksum=hashlib.sha256(raw).hexdigest(),
            )
        )
    title, category, content = LEGACY_POLICY
    session.add(HRPolicy(title=title, category=category, content=content, uploaded_by=ADMIN.id))
