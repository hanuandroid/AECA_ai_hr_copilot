import enum


class Capability(str, enum.Enum):
    """Everything the AI layer can do. Code checks capabilities, never raw roles."""

    # Knowledge
    POLICY_READ = "POLICY_READ"
    # Restricted organisation-wide skill search (RBAC step 3): approved fields only (employee id, name, department,
    # skill, level). Not a directory: general employee data stays SELF/TEAM/ORG-scoped by the SQL and HR capabilities.
    SKILL_SEARCH_ORG = "SKILL_SEARCH_ORG"

    # Protected HR data about a specific employee (leave, projects, job history, tickets)
    HR_READ_SELF = "HR_READ_SELF"
    HR_READ_TEAM = "HR_READ_TEAM"
    HR_READ_ORG = "HR_READ_ORG"

    # SQL agent row scope
    SQL_READ_SELF = "SQL_READ_SELF"
    SQL_READ_TEAM = "SQL_READ_TEAM"
    SQL_READ_ORG = "SQL_READ_ORG"
    SQL_VIEW_RAW = "SQL_VIEW_RAW"

    # Payroll amounts (gross/net/month). PAN/UAN/ESI/deductions stay forbidden for every role.
    PAYROLL_READ_ORG = "PAYROLL_READ_ORG"

    # Actions (each wraps an existing endpoint; all require confirmation - D6)
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

    # AI audit trail
    AUDIT_READ_OWN = "AUDIT_READ_OWN"
    AUDIT_READ_ORG = "AUDIT_READ_ORG"


class TargetRule(str, enum.Enum):
    """Which employees a granted capability may target. Evaluated against server-side team data."""

    NONE = "NONE"  # not about a specific employee (policy, catalog, broadcast)
    SELF = "SELF"  # only the caller
    TEAM = "TEAM"  # caller + direct reports (employees.manager_id == caller)
    REPORTS = "REPORTS"  # direct reports only, never the caller (e.g. approving leave)
    ORG = "ORG"  # any employee
    ORG_EXCEPT_SELF = "ORG_EXCEPT_SELF"  # any employee except the caller


class ScopeLevel(str, enum.Enum):
    NONE = "NONE"
    SELF = "SELF"
    TEAM = "TEAM"
    ORG = "ORG"
