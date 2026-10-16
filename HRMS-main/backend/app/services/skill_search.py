"""Restricted organisation-wide skill search (RBAC remediation step 3, approved decisions 1 and 6).

The ONE definition of the skill-search query. It is used by:
- GET /api/v1/employees/skill-search (MANAGER and ADMIN), executed on the request's database session;
- the AI SQL agent's dedicated skill-search path, executed on its read-only connection (mode=ro, query_only,
  per-query table/column authorizer, timeout, row cap).

Design constraints:
- Fixed, backend-authored SQL with bound parameters only. The search text never becomes SQL: callers pass a validated
  term (business API) or a skill name taken from the skills catalogue (AI path).
- Projection of the approved fields only: employee_id, employee_name, department, skill, level. No email, contact,
  salary, bank, PAN, DOB, payroll, leave, ticket, project or job-history data, and no full employee rows.
- Active employees only (the same population as the organisation chart).
- This is NOT a directory: it never changes GET /employees or employee-detail scope (step 2).
"""

import re

RESULT_FIELDS = ("employee_id", "employee_name", "department", "skill", "level")
TABLES = frozenset({"employees", "employee_skills", "skills", "departments"})
MIN_TERM, MAX_TERM = 2, 60
MAX_LIMIT = 50
_TERM_RE = re.compile(r"^[A-Za-z0-9 +#./\-]+$")

_FROM_WHERE = (
    "FROM employee_skills es "
    "JOIN skills s ON s.id = es.skill_id "
    "JOIN employees e ON e.id = es.employee_id "
    "LEFT JOIN departments d ON d.id = e.department_id "
    "WHERE e.status = 'ACTIVE' AND lower(s.name) LIKE :pattern ESCAPE '\\'"
)
SEARCH_SQL = (
    "SELECT e.id AS employee_id, e.name AS employee_name, d.name AS department, s.name AS skill, es.level AS level "
    + _FROM_WHERE
    + " ORDER BY e.name, e.id, s.name LIMIT :limit OFFSET :offset"
)
COUNT_SQL = "SELECT count(*) AS total " + _FROM_WHERE


class InvalidSkillQuery(ValueError):
    """The search text is empty, too long or contains unsupported characters."""


def normalize_term(raw: object) -> str:
    if not isinstance(raw, str):
        raise InvalidSkillQuery("skill must be text")
    term = " ".join(raw.split())
    if not MIN_TERM <= len(term) <= MAX_TERM or not _TERM_RE.match(term):
        raise InvalidSkillQuery(f"skill must be {MIN_TERM}-{MAX_TERM} characters of letters, digits, space or + # . / -")
    return term


def like_pattern(term: str, *, exact: bool = False) -> str:
    """Case-insensitive LIKE pattern with LIKE wildcards escaped (the term can never widen the match)."""
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped if exact else f"%{escaped}%"


def search_params(pattern: str, *, limit: int, offset: int = 0) -> dict:
    return {"pattern": pattern, "limit": max(1, min(int(limit), MAX_LIMIT)), "offset": max(0, int(offset))}


def to_item(row) -> dict:
    """Map a result row (mapping or sequence in RESULT_FIELDS order) to the approved fields only."""
    values = [row[field] for field in RESULT_FIELDS] if hasattr(row, "keys") else list(row)
    item = dict(zip(RESULT_FIELDS, values))
    item["level"] = getattr(item["level"], "value", item["level"])
    return item
