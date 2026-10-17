"""HR policy audiences in the business API (RBAC remediation step 7, approved decision 3; migration 0023).

Old behaviour (until 2026-10-17): every PUBLISHED policy was listed to and downloadable by everyone.

Invariant now (app/services/policy_audience.py):
- ALL: everyone; MANAGERS: managers and admins; ADMINS: admins only. Existing policies are ALL.
- GET /hr-policies (items AND total) and GET /hr-policies/{id}/download apply it; a hidden policy is the same 404 as an
  unknown id. ADMIN sees every row; a MANAGER also keeps seeing their own submissions (any audience they could choose).
- Upload: audience optional (ALL); an uploader may only choose an audience that includes them (MANAGER: ALL or
  MANAGERS); unknown values are 400. Both refusals happen before any file or row is stored.
- The Copilot derives the same sets from its matrix (parity test below).
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.enums import Role
from app.models.hr_policy import HRPolicy
from app.services.ai.authorization import build_ai_user_context, policy_audiences
from app.services.policy_audience import readable_audiences
from tests.support.data import USERS
from tests.support.http import data, error_code

BODY = b"# Synthetic policy\n\nSynthetic restricted content for audience tests."


async def _upload(client, auth, user: str, title: str, audience: str | None = None):
    form = {"title": title, "category": "OTHER"}
    if audience is not None:
        form["audience"] = audience
    return await client.post("/api/v1/hr-policies/upload", headers=await auth(user), data=form,
                             files={"file": ("p.md", BODY, "text/markdown")})


async def _ids(client, auth, user: str, **params) -> tuple[set[int], int]:
    response = await client.get("/api/v1/hr-policies", headers=await auth(user), params={"limit": 100, **params})
    assert response.status_code == 200
    body = data(response)
    return {item["id"] for item in body["items"]}, body["meta"]["total"]


def _stored_files() -> int:
    root = Path(settings.policy_upload_dir)
    return sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0


@pytest.fixture
async def published(client, auth) -> dict[str, int]:
    ids = {}
    for audience in ("ALL", "MANAGERS", "ADMINS"):
        response = await _upload(client, auth, "admin", f"Audience {audience} policy", audience)
        assert response.status_code == 200 and data(response)["status"] == "PUBLISHED"
        ids[audience] = data(response)["id"]
    return ids


# --- existing data and upload rules ---------------------------------------------------------------------------------


async def test_existing_policies_are_all(db):
    audiences = set((await db.execute(select(HRPolicy.audience))).scalars())
    assert audiences == {"ALL"}


@pytest.mark.parametrize("user", ["admin", "manager"])
async def test_omitted_audience_defaults_to_all(client, auth, user):
    response = await _upload(client, auth, user, f"Default audience {user}")
    assert response.status_code == 200 and data(response)["audience"] == "ALL"


@pytest.mark.parametrize("user,audience,status,code", [
    ("admin", "ADMINS", 200, None), ("admin", "managers", 200, None), ("admin", " all ", 200, None),
    ("manager", "MANAGERS", 200, None), ("manager", "ALL", 200, None),
    ("manager", "ADMINS", 403, "POLICY_AUDIENCE_FORBIDDEN"),
    ("admin", "EVERYONE", 400, "INVALID_POLICY_AUDIENCE"), ("manager", "TEAM", 400, "INVALID_POLICY_AUDIENCE"),
    ("admin", "ALL;DROP", 400, "INVALID_POLICY_AUDIENCE"),
])
async def test_upload_audience_rules(client, auth, db, user, audience, status, code):
    rows, files = (await db.execute(select(func.count(HRPolicy.id)))).scalar_one(), _stored_files()
    response = await _upload(client, auth, user, f"Rules {user} {audience.strip()}", audience)
    assert response.status_code == status, response.text
    if code:
        assert error_code(response) == code
        db.expire_all()
        assert (await db.execute(select(func.count(HRPolicy.id)))).scalar_one() == rows  # nothing stored
        assert _stored_files() == files


async def test_employee_still_cannot_upload(client, auth):
    response = await _upload(client, auth, "employee", "Employee policy", "ALL")
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"


async def test_overlong_audience_is_rejected(client, auth):
    response = await _upload(client, auth, "admin", "Overlong audience", "A" * 21)
    assert response.status_code == 422


# --- list and download --------------------------------------------------------------------------------------------


async def test_list_and_total_follow_the_audience(client, auth, published):
    expected = {"employee": {"ALL"}, "outsider": {"ALL"}, "manager": {"ALL", "MANAGERS"}, "admin": {"ALL", "MANAGERS", "ADMINS"}}
    for user, visible in expected.items():
        ids, total = await _ids(client, auth, user)
        assert {audience for audience, pid in published.items() if pid in ids} == visible, user
        assert total == len(ids), user  # the total never counts hidden policies
    filtered, _ = await _ids(client, auth, "employee", status="PUBLISHED")
    assert published["ADMINS"] not in filtered and published["MANAGERS"] not in filtered


async def test_pagination_does_not_reveal_hidden_policies(client, auth, published):
    ids, total = await _ids(client, auth, "employee")
    seen = set()
    for offset in range(total + 2):
        response = await client.get("/api/v1/hr-policies", headers=await auth("employee"), params={"limit": 1, "offset": offset})
        seen |= {item["id"] for item in data(response)["items"]}
        assert data(response)["meta"]["total"] == total
    assert seen == ids and published["ADMINS"] not in seen


@pytest.mark.parametrize("user,audience,visible", [
    ("employee", "MANAGERS", False), ("employee", "ADMINS", False), ("manager", "ADMINS", False),
    ("employee", "ALL", True), ("manager", "MANAGERS", True), ("admin", "ADMINS", True),
])
async def test_download_follows_the_audience(client, auth, published, user, audience, visible):
    response = await client.get(f"/api/v1/hr-policies/{published[audience]}/download", headers=await auth(user))
    if visible:
        assert response.status_code == 200 and b"Synthetic restricted content" in response.content
    else:
        unknown = await client.get("/api/v1/hr-policies/999999/download", headers=await auth(user))
        assert response.status_code == unknown.status_code == 404 and response.json() == unknown.json()
        assert b"Synthetic" not in response.content


async def test_reclassification_applies_immediately(client, auth, db, published):
    await db.execute(update(HRPolicy).where(HRPolicy.id == published["ALL"]).values(audience="ADMINS"))
    await db.commit()
    assert published["ALL"] not in (await _ids(client, auth, "employee"))[0]
    response = await client.get(f"/api/v1/hr-policies/{published['ALL']}/download", headers=await auth("employee"))
    assert response.status_code == 404


async def test_manager_keeps_their_own_submission(client, auth, db):
    submitted = data(await _upload(client, auth, "manager", "Manager draft", "MANAGERS"))
    assert submitted["status"] == "SUBMITTED"
    assert submitted["id"] in (await _ids(client, auth, "manager"))[0]
    assert submitted["id"] not in (await _ids(client, auth, "employee"))[0]
    published = await client.post(f"/api/v1/hr-policies/{submitted['id']}/publish", headers=await auth("admin"))
    assert published.status_code == 200 and data(published)["audience"] == "MANAGERS"  # publishing keeps the audience
    assert submitted["id"] not in (await _ids(client, auth, "employee"))[0]


async def test_demoted_manager_loses_managers_policies(client, auth, db, published):
    await db.execute(update(Employee).where(Employee.id == USERS["manager"].id).values(role=Role.EMPLOYEE))
    await db.commit()
    assert published["MANAGERS"] not in (await _ids(client, auth, "manager"))[0]


async def test_forged_admin_role_claim_is_ignored(client, published):
    token = create_access_token(subject=str(USERS["employee"].id), role="ADMIN")
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.get(f"/api/v1/hr-policies/{published['ADMINS']}/download", headers=headers)
    assert response.status_code == 404


async def test_anonymous_and_inactive(client, auth, published):
    assert (await client.get("/api/v1/hr-policies")).status_code == 401
    inactive = await client.get(f"/api/v1/hr-policies/{published['ALL']}/download", headers=await auth("inactive"))
    assert inactive.status_code == 403 and error_code(inactive) == "USER_INACTIVE"


# --- API / Copilot parity -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("user", ["admin", "manager", "employee", "outsider"])
async def test_copilot_audiences_match_the_business_api(db, user):
    employee = (await db.execute(select(Employee).where(Employee.id == USERS[user].id))).scalar_one()
    assert policy_audiences(await build_ai_user_context(employee, db)) == readable_audiences(Role(employee.role))
