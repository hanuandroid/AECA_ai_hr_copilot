"""Copilot path for the restricted organisation-wide skill search (RBAC step 3, decisions 1 and 6).

When a caller whose SQL scope is narrower than the organisation (a MANAGER) asks a *pure* skill-search question
("Which employees know Python?"), the SQL agent answers it from the dedicated query in `app.services.skill_search`
instead of model-generated SQL:

- authorization is checked by the backend (`SKILL_SEARCH_ORG`), never inferred from the model or the text;
- the skill is taken from the skills catalogue (whole-word, case-insensitive match in the question); user text is
  never placed into SQL, so injection and prompt instructions in the question have no effect on the query;
- the query is fixed and projects only employee_id, employee_name, department, skill and level for active employees;
  it runs on the SQL agent's read-only connection with the per-query table/column authorizer and row cap;
- anything else (other data, team questions, no catalogue skill) falls through to the existing scoped SQL path, so
  this is never a route to leave, tickets, pay, projects, job history or the general directory.
"""

import re

from app.services import skill_search
from app.services.ai.sql.executor import ReadOnlySQLExecutor

MAX_SKILLS_PER_QUESTION = 5
_SKILL_WORDS = re.compile(
    r"\b(know|knows|knowing|skill|skills|skilled|expert|experts|expertise|proficient|proficiency|experience|"
    r"experienced|familiar|specialist|specialists)\b", re.IGNORECASE)
_SEARCH_WORDS = re.compile(
    r"\b(who|which|find|list|show|search|anyone|anybody|employees?|people|staff|colleagues?|engineers?|developers?)\b",
    re.IGNORECASE)
# Any reference to other data means this is not a pure skill search: let the scoped SQL path handle it.
_OTHER_DATA = re.compile(
    r"\b(leave|leaves|ticket|tickets|salary|salaries|compensation|payroll|payslips?|pay|bank|pan|attendance|"
    r"projects?|job|history|balance|balances|manager|managers|reports?|report|email|emails|phone|address|birth|"
    r"birthday|dob|title|titles|joined|joining|department|departments|team|teams|my|me|mine|role|roles)\b",
    re.IGNORECASE)


def is_skill_search_question(question: str) -> bool:
    return bool(_SKILL_WORDS.search(question) and _SEARCH_WORDS.search(question) and not _OTHER_DATA.search(question))


def match_catalog_skills(question: str, catalog: list[str]) -> list[str]:
    """Catalogue skill names that appear as whole words in the question (longest first, at most 5). A matched span is
    consumed, so a shorter name inside it ("C" inside "C++") is not matched again."""
    found = []
    remaining = question
    for name in sorted({n for n in catalog if n}, key=len, reverse=True):
        pattern = re.compile(r"(?<![A-Za-z0-9+#.])" + re.escape(name) + r"(?![A-Za-z0-9+#])", re.IGNORECASE)
        if pattern.search(remaining):
            found.append(name)
            remaining = pattern.sub(lambda m: " " * len(m.group(0)), remaining)
    return found[:MAX_SKILLS_PER_QUESTION]


async def catalog_skills(executor: ReadOnlySQLExecutor) -> list[str]:
    result = await executor.execute("SELECT name FROM skills ORDER BY name", {}, allowed_tables=frozenset({"skills"}))
    return [row["name"] if hasattr(row, "keys") else row[0] for row in result.rows]


async def search(executor: ReadOnlySQLExecutor, skills: list[str], *, limit: int) -> tuple[list[dict], bool]:
    """Exact (case-insensitive) catalogue-skill matches; approved fields only. Returns (items, truncated)."""
    items: dict[tuple[int, str], dict] = {}
    truncated = False
    for name in skills:
        params = skill_search.search_params(skill_search.like_pattern(name, exact=True), limit=limit)
        result = await executor.execute(skill_search.SEARCH_SQL, params, allowed_tables=skill_search.TABLES)
        truncated = truncated or result.truncated or len(result.rows) >= params["limit"]
        for row in result.rows:
            item = skill_search.to_item(row)
            items[(item["employee_id"], item["skill"])] = item
    ordered = sorted(items.values(), key=lambda i: (i["employee_name"], i["employee_id"], i["skill"]))
    return ordered[:limit], truncated or len(ordered) > limit
