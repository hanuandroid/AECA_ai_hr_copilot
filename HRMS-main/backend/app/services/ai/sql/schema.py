"""SQL-agent schema registry: the ONLY tables, columns and joins the SQL agent may use.

Built from the real SQLAlchemy models (app.models). Every table in the database and every column
of every queryable table is classified explicitly; tests fail when a new table/column appears
without a classification (tests/ai/sql/test_schema_registry.py).

Kinds:
- SCOPED   rows belong to an employee (owner columns); rows are limited to the caller's SQL scope
           (SELF: the caller; TEAM: caller + direct reports via employees.manager_id; ORG: all).
- CATALOG  organisation reference data with no personal fields (departments, skills, projects).
- PAYROLL  payroll amounts; the table requires PAYROLL_READ_ORG (ADMIN only).
"""

import enum
from dataclasses import dataclass

from app.db.base import Base
import app.models  # noqa: F401  (register all models on Base.metadata)
from app.services.ai.authorization.sensitive import is_sensitive_field

MY_TEAM = "my_team"  # virtual table injected by the backend: employee_id of caller + direct reports
ME_PARAM = "me"  # the only bind parameter the SQL may use: the authenticated employee id


class TableKind(str, enum.Enum):
    SCOPED = "SCOPED"
    CATALOG = "CATALOG"
    PAYROLL = "PAYROLL"


@dataclass(frozen=True)
class TablePolicy:
    name: str
    kind: TableKind
    allowed_columns: tuple[str, ...]
    owner_columns: tuple[str, ...] = ()  # columns holding the owning employee id
    description: str = ""


TABLES: dict[str, TablePolicy] = {
    policy.name: policy
    for policy in (
        TablePolicy(
            "employees", TableKind.SCOPED,
            ("id", "name", "email", "department_id", "manager_id", "role", "status", "joining_date", "occupancy"),
            owner_columns=("id",),
            description="Employee directory. role: ADMIN|MANAGER|EMPLOYEE; status: ACTIVE|INACTIVE; manager_id -> employees.id",
        ),
        TablePolicy(
            "job_history", TableKind.SCOPED,
            ("id", "employee_id", "designation", "business_unit", "department", "start_date", "end_date", "is_current"),
            owner_columns=("employee_id",),
            description="Job titles over time; is_current = 1 for the current designation",
        ),
        TablePolicy(
            "leave_balances", TableKind.SCOPED,
            ("id", "employee_id", "leave_type", "total", "used", "remaining"),
            owner_columns=("employee_id",),
            description="leave_type: CASUAL|SICK|EARNED",
        ),
        TablePolicy(
            "leave_requests", TableKind.SCOPED,
            ("id", "employee_id", "leave_type", "start_date", "end_date", "is_half_day", "half_day_period",
             "reason", "status", "approver_id"),
            owner_columns=("employee_id",),
            description="status: PENDING|APPROVED|REJECTED; leave_type: CASUAL|SICK|EARNED; half_day_period: FIRST_HALF|SECOND_HALF",
        ),
        TablePolicy(
            "tickets", TableKind.SCOPED,
            ("id", "employee_id", "assignee_id", "title", "description", "category", "priority", "status", "created_at"),
            owner_columns=("employee_id", "assignee_id"),
            description="employee_id = raised by; assignee_id = assigned to. status: OPEN|IN_PROGRESS|RESOLVED; "
            "category: IT|HR|ONBOARDING; priority: LOW|MEDIUM|HIGH",
        ),
        TablePolicy(
            "employee_projects", TableKind.SCOPED,
            ("id", "employee_id", "project_id", "role_on_project"),
            owner_columns=("employee_id",),
            description="Project assignments; project_id -> projects.id",
        ),
        TablePolicy(
            "employee_skills", TableKind.SCOPED,
            ("id", "employee_id", "skill_id", "level"),
            owner_columns=("employee_id",),
            description="level: BEGINNER|INTERMEDIATE|EXPERT; skill_id -> skills.id",
        ),
        TablePolicy("departments", TableKind.CATALOG, ("id", "name", "location"), description="Departments"),
        TablePolicy("skills", TableKind.CATALOG, ("id", "name", "normalized_name"), description="Skill catalog"),
        TablePolicy(
            "projects", TableKind.CATALOG, ("id", "name", "description", "status"),
            description="Project catalog; status: ONGOING|COMPLETED|ON_HOLD|PLANNED",
        ),
        TablePolicy(
            "payroll_records", TableKind.PAYROLL, ("id", "employee_id", "month", "gross", "net"),
            owner_columns=("employee_id",),
            description="Monthly payroll amounts (ADMIN only). PAN/UAN/ESI/deductions are never available.",
        ),
    )
}

# Every other table is denied. Listed explicitly so new tables must be classified (see tests).
DENIED_TABLES: dict[str, str] = {
    "attendance_logs": "not part of the SQL agent scope",
    "announcements": "not part of the SQL agent scope",
    "polls": "not part of the SQL agent scope",
    "poll_responses": "individual votes",
    "holidays": "not part of the SQL agent scope",
    "onboarding_tasks": "not part of the SQL agent scope",
    "hr_policies": "served by Policy RAG, internal file metadata",
    "policy_chunks": "AI-owned index",
    "ai_pending_actions": "AI-owned action confirmations",
    "ai_conversations": "AI-owned private chat history (owner-only)",
    "ai_audit_events": "AI-owned audit trail (append-only; admin audit API only)",
    "ai_conversation_messages": "AI-owned private chat history (owner-only)",
    "employee_documents": "personal documents and file paths",
}

# Columns that reference an employee id (used for approved joins, literal-id and scope checks).
EMPLOYEE_REFERENCE_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {("employees", "id"), ("employees", "manager_id"), ("tickets", "assignee_id"), ("leave_requests", "approver_id"),
     (MY_TEAM, "employee_id")}
    | {(name, "employee_id") for name, policy in TABLES.items() if "employee_id" in policy.allowed_columns}
)

# Approved non-employee relationships (foreign keys from the models). Employee-reference columns
# may be joined to each other (every such column references employees.id).
APPROVED_FOREIGN_KEYS: frozenset[frozenset[tuple[str, str]]] = frozenset(
    frozenset(pair)
    for pair in (
        (("employees", "department_id"), ("departments", "id")),
        (("employee_projects", "project_id"), ("projects", "id")),
        (("employee_skills", "skill_id"), ("skills", "id")),
    )
)


def is_approved_join(left: tuple[str, str], right: tuple[str, str]) -> bool:
    if left in EMPLOYEE_REFERENCE_COLUMNS and right in EMPLOYEE_REFERENCE_COLUMNS:
        return True
    return frozenset((left, right)) in APPROVED_FOREIGN_KEYS


def all_database_columns(table: str) -> tuple[str, ...]:
    return tuple(column.name for column in Base.metadata.tables[table].columns)


def forbidden_columns(table: str) -> tuple[str, ...]:
    allowed = set(TABLES[table].allowed_columns)
    return tuple(c for c in all_database_columns(table) if c not in allowed)


def sqlglot_schema() -> dict[str, dict[str, str]]:
    """Full real column list per allowed table (so forbidden columns resolve and are then rejected)."""
    schema = {name: {column: "UNKNOWN" for column in all_database_columns(name)} for name in TABLES}
    schema[MY_TEAM] = {"employee_id": "INT"}
    return schema


def validate_registry() -> None:
    """Fail fast at import if the registry drifts from the models or allows a sensitive column."""
    for name, policy in TABLES.items():
        real = set(all_database_columns(name))
        unknown = set(policy.allowed_columns) - real
        if unknown:
            raise RuntimeError(f"SQL registry lists unknown columns for {name}: {sorted(unknown)}")
        sensitive = [c for c in policy.allowed_columns if is_sensitive_field(c)]
        if sensitive:
            raise RuntimeError(f"SQL registry allows sensitive columns for {name}: {sensitive}")
        if not set(policy.owner_columns) <= set(policy.allowed_columns):
            raise RuntimeError(f"owner columns must be allowed columns for {name}")


validate_registry()
