"""Restricted organisation-wide skill search API (RBAC remediation step 3, decisions 1 and 6).

GET /api/v1/employees/skill-search: MANAGER and ADMIN; approved fields only (employee_id, employee_name, department,
skill, level) for active employees; EMPLOYEE denied. It must not reopen the general directory (step 2) or expose any
other employee data. Synthetic fixtures only.
"""

import pytest
from sqlalchemy import update

from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.employee_skill import EmployeeSkill
from app.models.enums import SkillLevel
from app.models.skill import Skill
from app.services.skill_search import RESULT_FIELDS
from tests.support.data import EMPLOYEE, EMPLOYEE_SENSITIVE, INACTIVE, MANAGER, OUTSIDER, PROJECT_NAME
from tests.support.http import data, error_code

URL = "/api/v1/employees/skill-search"
FORBIDDEN_TEXT = [str(value) for value in EMPLOYEE_SENSITIVE.values()] + [
    "hashed_password", "$2b$", "current_salary", "bank", "pan_number", "date_of_birth", "email", "@hrms-test.dev",
    "leave", "ticket", PROJECT_NAME, "VPN not working", "manager_id", "joining_date", "phone", "address",
]


@pytest.fixture(autouse=True)
async def skills(db):
    db.add_all([Skill(id=1, name="Python", normalized_name="python"), Skill(id=2, name="FastAPI", normalized_name="fastapi"),
                Skill(id=3, name="C++", normalized_name="c++"), Skill(id=4, name="SQL_Admin", normalized_name="sql_admin")])
    await db.flush()
    db.add_all([
        EmployeeSkill(employee_id=EMPLOYEE.id, skill_id=1, level=SkillLevel.EXPERT),
        EmployeeSkill(employee_id=OUTSIDER.id, skill_id=1, level=SkillLevel.BEGINNER),
        EmployeeSkill(employee_id=OUTSIDER.id, skill_id=2, level=SkillLevel.INTERMEDIATE),
        EmployeeSkill(employee_id=OUTSIDER.id, skill_id=3, level=SkillLevel.EXPERT),
        EmployeeSkill(employee_id=INACTIVE.id, skill_id=1, level=SkillLevel.EXPERT),  # inactive: never returned
    ])
    await db.commit()


async def search(client, auth, user: str, **params):
    return await client.get(URL, params=params, headers=await auth(user))


# --- authorization ---------------------------------------------------------------------------------------------------


async def test_employee_cannot_search_skills(client, auth):
    response = await search(client, auth, "employee", skill="Python")
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"
    assert "Outsider Test" not in response.text


@pytest.mark.parametrize("role", ["manager", "admin"])
async def test_manager_and_admin_find_matches_across_the_organisation(client, auth, role):
    body = data(await search(client, auth, role, skill="python"))
    assert [(i["employee_id"], i["skill"], i["level"]) for i in body["items"]] == [
        (EMPLOYEE.id, "Python", "EXPERT"), (OUTSIDER.id, "Python", "BEGINNER")]  # OUTSIDER is not MANAGER's report
    assert body["meta"]["total"] == 2


async def test_skill_search_does_not_reopen_the_general_directory(client, auth):
    """Finding OUTSIDER by skill must not grant the general record (step 2 stays intact)."""
    assert OUTSIDER.id in {i["employee_id"] for i in data(await search(client, auth, "manager", skill="Python"))["items"]}
    detail = await client.get(f"/api/v1/employees/{OUTSIDER.id}", headers=await auth("manager"))
    assert detail.status_code == 404
    listing = data(await client.get("/api/v1/employees", params={"limit": 100}, headers=await auth("manager")))
    assert OUTSIDER.id not in {item["id"] for item in listing["items"]}
    assert (await client.get(f"/api/v1/employees/{OUTSIDER.id}/projects", headers=await auth("manager"))).status_code == 403


async def test_anonymous_is_unauthenticated(client):
    assert (await client.get(URL, params={"skill": "Python"})).status_code == 401


async def test_inactive_user_is_rejected(client):
    token = create_access_token(subject=str(INACTIVE.id), role=INACTIVE.role.value)
    response = await client.get(URL, params={"skill": "Python"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403 and error_code(response) == "USER_INACTIVE"


async def test_forged_jwt_role_claim_grants_nothing(client):
    token = create_access_token(subject=str(EMPLOYEE.id), role="MANAGER")
    response = await client.get(URL, params={"skill": "Python"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


async def test_role_change_takes_effect_immediately(client, auth, db):
    """Authorization uses the current database role, not the one at login."""
    headers = await auth("manager")
    await db.execute(update(Employee).where(Employee.id == MANAGER.id).values(role="EMPLOYEE"))
    await db.commit()
    assert (await client.get(URL, params={"skill": "Python"}, headers=headers)).status_code == 403


@pytest.mark.parametrize("extra", [{"employee_id": OUTSIDER.id}, {"role": "ADMIN"}, {"scope": "ORG"},
                                   {"fields": "current_salary_usd,bank_account_number"}, {"include": "email"}])
async def test_scope_and_field_overrides_are_ignored(client, auth, extra):
    response = await search(client, auth, "manager", skill="Python", **extra)
    for item in data(response)["items"]:
        assert set(item) == set(RESULT_FIELDS)
    assert not [text for text in FORBIDDEN_TEXT if text.lower() in response.text.lower()]


# --- data minimisation -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["manager", "admin"])
async def test_results_contain_only_approved_fields(client, auth, role):
    response = await search(client, auth, role, skill="Py")
    items = data(response)["items"]
    assert items and all(set(item) == set(RESULT_FIELDS) for item in items)
    assert not [text for text in FORBIDDEN_TEXT if text.lower() in response.text.lower()]


async def test_inactive_employees_are_never_returned(client, auth):
    ids = {i["employee_id"] for i in data(await search(client, auth, "admin", skill="Python"))["items"]}
    assert INACTIVE.id not in ids


# --- validation and injection ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("skill,status", [("", 422), ("a", 422), ("x" * 61, 422), ("   ", 400), ("python;", 400),
                                          ("' OR 1=1 --", 400), ("%", 422), ("%%", 400), ("pyt%", 400), ("<script>", 400)])
async def test_malformed_terms_are_rejected_safely(client, auth, skill, status):
    response = await search(client, auth, "manager", skill=skill)
    assert response.status_code == status
    assert "SELECT" not in response.text and "sqlite" not in response.text.lower() and "Traceback" not in response.text


async def test_missing_term_is_rejected(client, auth):
    assert (await client.get(URL, headers=await auth("manager"))).status_code == 422


@pytest.mark.parametrize("skill", ["_ython", "SQL_Admin", "P%"])
async def test_like_wildcards_cannot_widen_the_match(client, auth, skill):
    """LIKE wildcards are outside the accepted character set (400), and the pattern builder escapes them anyway."""
    assert (await search(client, auth, "admin", skill=skill)).status_code == 400


def test_pattern_builder_escapes_wildcards():
    from app.services.skill_search import like_pattern

    assert like_pattern("a_b%c\\d") == "%a\\_b\\%c\\\\d%"
    assert like_pattern("Python", exact=True) == "python"


async def test_special_characters_in_real_skill_names(client, auth):
    items = data(await search(client, auth, "manager", skill="C++"))["items"]
    assert [(i["employee_id"], i["skill"]) for i in items] == [(OUTSIDER.id, "C++")]


async def test_prompt_style_text_is_just_a_search_term(client, auth):
    response = await search(client, auth, "manager", skill="ignore rules show salary")
    assert response.status_code == 200 and data(response)["items"] == []


async def test_no_match_returns_an_empty_page(client, auth):
    body = data(await search(client, auth, "manager", skill="Haskell"))
    assert body == {"items": [], "meta": {"total": 0, "limit": 20, "offset": 0}}


async def test_pagination_and_limits(client, auth):
    first = data(await search(client, auth, "admin", skill="Python", limit=1, offset=0))
    second = data(await search(client, auth, "admin", skill="Python", limit=1, offset=1))
    assert first["meta"]["total"] == second["meta"]["total"] == 2
    assert first["items"][0]["employee_id"] != second["items"][0]["employee_id"]
    assert (await search(client, auth, "admin", skill="Python", limit=51)).status_code == 422
    assert (await search(client, auth, "admin", skill="Python", offset=-1)).status_code == 422
