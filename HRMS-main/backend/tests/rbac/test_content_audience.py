"""Announcement and poll audience (RBAC remediation step 6, approved decision 5 / X-2; migration 0022).

Old behaviour (until 2026-10-17): every announcement and poll was visible to everyone, any MANAGER published to the
whole organisation, and anyone could vote on or read the results of any poll id.

Invariant now (app/services/content_audience.py):
- ORG: everyone. Only ADMIN publishes ORG; ADMIN's default.
- TEAM: the team manager, their direct reports (one level) and admins. MANAGER creates TEAM only (default); asking for
  ORG is 403 AUDIENCE_FORBIDDEN, never a silent downgrade. The team is always the creator's own.
- Lists and their totals use the same filter; voting on or reading results of a hidden poll is the same 404 as an
  unknown id. Client-supplied team/author/creator fields are ignored.
- There are no update, delete or publish routes for announcements or polls; creation is the only write besides voting.
"""

import pytest
from sqlalchemy import func, select, update

from app.core.security import create_access_token
from app.models.announcement import Announcement
from app.models.employee import Employee
from app.models.poll import Poll
from app.models.poll_response import PollResponse
from tests.support.data import ADMIN, EMPLOYEE, MANAGER, OUTSIDER
from tests.support.http import data, error_code

NEWS = {"title": "Team offsite", "body": "Offsite planning on Friday."}
QUESTION = {"question": "Which offsite venue?", "options": ["Lake", "Hills"]}


async def _announce(client, headers, **extra) -> dict:
    response = await client.post("/api/v1/announcements", headers=headers, json={**NEWS, **extra})
    assert response.status_code == 200, response.text
    return data(response)


async def _poll(client, headers, **extra) -> dict:
    response = await client.post("/api/v1/polls", headers=headers, json={**QUESTION, **extra})
    assert response.status_code == 200, response.text
    return data(response)


async def _ids(client, path: str, headers, **params) -> tuple[set[int], int]:
    response = await client.get(path, headers=headers, params={"limit": 100, **params})
    assert response.status_code == 200
    body = data(response)
    return {item["id"] for item in body["items"]}, body["meta"]["total"]


async def _row(db, model, row_id):
    db.expire_all()
    return (await db.execute(select(model).where(model.id == row_id))).scalar_one()


# --- creation rules -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path,make", [("announcements", _announce), ("polls", _poll)])
async def test_manager_content_defaults_to_their_team(client, auth, db, path, make):
    created = await make(client, await auth("manager"))
    assert created["audience"] == "TEAM"
    model = Announcement if path == "announcements" else Poll
    row = await _row(db, model, created["id"])
    assert (row.audience, row.team_manager_id) == ("TEAM", MANAGER.id)


@pytest.mark.parametrize("path", ["announcements", "polls"])
@pytest.mark.parametrize("audience", ["ORG", "org", " Org "])
async def test_manager_cannot_publish_org_wide(client, auth, db, path, audience):
    model = Announcement if path == "announcements" else Poll
    before = (await db.execute(select(func.count(model.id)))).scalar_one()
    body = {**(NEWS if path == "announcements" else QUESTION), "audience": audience}
    response = await client.post(f"/api/v1/{path}", headers=await auth("manager"), json=body)
    assert response.status_code == 403 and error_code(response) == "AUDIENCE_FORBIDDEN"
    db.expire_all()
    assert (await db.execute(select(func.count(model.id)))).scalar_one() == before  # refused, not downgraded


@pytest.mark.parametrize("path,make", [("announcements", _announce), ("polls", _poll)])
async def test_admin_defaults_to_org_and_may_choose_team(client, auth, db, path, make):
    model = Announcement if path == "announcements" else Poll
    org = await make(client, await auth("admin"))
    team = await make(client, await auth("admin"), audience="team")
    assert (org["audience"], team["audience"]) == ("ORG", "TEAM")
    assert (await _row(db, model, org["id"])).team_manager_id is None
    assert (await _row(db, model, team["id"])).team_manager_id == ADMIN.id  # the admin's own team


@pytest.mark.parametrize("path", ["announcements", "polls"])
@pytest.mark.parametrize("audience", ["EVERYONE", "MANAGERS", "TEAM;DROP", "x" * 11])
async def test_unknown_audience_values_fail_closed(client, auth, path, audience):
    body = {**(NEWS if path == "announcements" else QUESTION), "audience": audience}
    response = await client.post(f"/api/v1/{path}", headers=await auth("admin"), json=body)
    assert response.status_code in (400, 422)
    if response.status_code == 400:
        assert error_code(response) == "INVALID_AUDIENCE"


@pytest.mark.parametrize("path", ["announcements", "polls"])
async def test_employees_still_cannot_create(client, auth, path):
    body = {**(NEWS if path == "announcements" else QUESTION), "audience": "TEAM"}
    response = await client.post(f"/api/v1/{path}", headers=await auth("employee"), json=body)
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"


@pytest.mark.parametrize("path", ["announcements", "polls"])
async def test_forged_team_author_and_creator_fields_are_ignored(client, auth, db, people, path):
    model = Announcement if path == "announcements" else Poll
    forged = {"team_manager_id": people.manager_b, "author_id": ADMIN.id, "created_by": ADMIN.id, "manager_id": people.manager_b}
    make = _announce if path == "announcements" else _poll
    created = await make(client, await auth("manager"), audience="TEAM", **forged)
    row = await _row(db, model, created["id"])
    owner = row.author_id if path == "announcements" else row.created_by
    assert (row.audience, row.team_manager_id, owner) == ("TEAM", MANAGER.id, MANAGER.id)
    # ...and the forged team cannot see it.
    ids, _ = await _ids(client, f"/api/v1/{path}", people.headers(people.report_b))
    assert created["id"] not in ids


async def test_forged_admin_role_claim_cannot_publish_org(client, db):
    token = create_access_token(subject=str(MANAGER.id), role="ADMIN")
    response = await client.post("/api/v1/announcements", headers={"Authorization": f"Bearer {token}"},
                                 json={**NEWS, "audience": "ORG"})
    assert response.status_code == 403 and error_code(response) == "AUDIENCE_FORBIDDEN"


# --- visibility: lists and counts ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path,make", [("/api/v1/announcements", _announce), ("/api/v1/polls", _poll)])
async def test_team_content_visibility_matrix(client, auth, people, path, make):
    org = (await make(client, await auth("admin")))["id"]
    team_a = (await make(client, await auth("manager")))["id"]
    team_b = (await make(client, people.headers(people.manager_b)))["id"]
    expected = {
        "admin": {org, team_a, team_b},
        "manager": {org, team_a},
        "employee": {org, team_a},  # direct report of MANAGER
        "outsider": {org},  # no manager
    }
    for user, visible in expected.items():
        ids, total = await _ids(client, path, await auth(user))
        assert ids == visible and total == len(visible), user  # the total never counts hidden items
    for employee_id, visible in ((people.manager_b, {org, team_b}), (people.report_b, {org, team_b}),
                                 (people.indirect, {org})):  # indirect reports are not part of the team
        ids, total = await _ids(client, path, people.headers(employee_id))
        assert ids == visible and total == len(visible), employee_id


@pytest.mark.parametrize("path,make", [("/api/v1/announcements", _announce), ("/api/v1/polls", _poll)])
async def test_pagination_does_not_reveal_hidden_items(client, auth, people, path, make):
    for _ in range(3):
        await make(client, people.headers(people.manager_b))
    visible = (await make(client, await auth("admin")))["id"]
    for offset in range(0, 4):
        response = await client.get(path, headers=await auth("outsider"), params={"limit": 1, "offset": offset})
        body = data(response)
        assert body["meta"]["total"] == 1
        assert [item["id"] for item in body["items"]] == ([visible] if offset == 0 else [])


async def test_admin_team_content_reaches_the_admins_direct_reports(client, auth, db, people):
    team = (await _announce(client, await auth("admin"), audience="TEAM"))["id"]
    assert team in (await _ids(client, "/api/v1/announcements", people.headers(people.manager_b)))[0]  # reports to ADMIN
    assert team not in (await _ids(client, "/api/v1/announcements", await auth("outsider")))[0]


async def test_team_membership_is_evaluated_on_every_request(client, auth, db):
    team = (await _announce(client, await auth("manager")))["id"]
    assert team not in (await _ids(client, "/api/v1/announcements", await auth("outsider")))[0]
    await db.execute(update(Employee).where(Employee.id == OUTSIDER.id).values(manager_id=MANAGER.id))  # joins the team
    await db.commit()
    assert team in (await _ids(client, "/api/v1/announcements", await auth("outsider")))[0]
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(manager_id=None))  # leaves the team
    await db.commit()
    assert team not in (await _ids(client, "/api/v1/announcements", await auth("employee")))[0]


# --- polls: voting and results ------------------------------------------------------------------------------------


async def test_hidden_poll_votes_and_results_look_unknown(client, auth, db, people):
    poll = (await _poll(client, people.headers(people.manager_b)))["id"]
    votes_before = (await db.execute(select(func.count(PollResponse.id)))).scalar_one()
    for user in ("manager", "employee", "outsider"):
        headers = await auth(user)
        vote = await client.post(f"/api/v1/polls/{poll}/vote", headers=headers, json={"option_index": 0})
        unknown_vote = await client.post("/api/v1/polls/999999/vote", headers=headers, json={"option_index": 0})
        assert vote.status_code == unknown_vote.status_code == 404 and vote.json() == unknown_vote.json(), user
        results = await client.get(f"/api/v1/polls/{poll}/results", headers=headers)
        unknown = await client.get("/api/v1/polls/999999/results", headers=headers)
        assert results.status_code == unknown.status_code == 404 and results.json() == unknown.json(), user
    out_of_range = await client.post(f"/api/v1/polls/{poll}/vote", headers=await auth("outsider"), json={"option_index": 99})
    assert out_of_range.status_code == 404  # visibility is checked before anything else
    db.expire_all()
    assert (await db.execute(select(func.count(PollResponse.id)))).scalar_one() == votes_before


async def test_team_members_vote_and_read_team_results(client, auth, people):
    poll = (await _poll(client, await auth("manager")))["id"]
    for user in ("manager", "employee"):
        assert (await client.post(f"/api/v1/polls/{poll}/vote", headers=await auth(user), json={"option_index": 1})).status_code == 200
    results = await client.get(f"/api/v1/polls/{poll}/results", headers=await auth("employee"))
    assert results.status_code == 200
    body = data(results)
    assert body["total_votes"] == 2 and [item["votes"] for item in body["items"]] == [0, 2]
    assert "employee_id" not in str(body["items"])  # aggregates only, as before
    admin = await client.get(f"/api/v1/polls/{poll}/results", headers=await auth("admin"))
    assert admin.status_code == 200 and data(admin)["total_votes"] == 2


async def test_org_polls_unchanged_for_everyone(client, auth, people):
    poll = (await _poll(client, await auth("admin")))["id"]
    for headers in (await auth("outsider"), people.headers(people.report_b), people.headers(people.indirect)):
        assert (await client.post(f"/api/v1/polls/{poll}/vote", headers=headers, json={"option_index": 0})).status_code == 200
        assert (await client.get(f"/api/v1/polls/{poll}/results", headers=headers)).status_code == 200


async def test_list_shows_own_vote_only_for_visible_polls(client, auth, people):
    poll = (await _poll(client, await auth("manager")))["id"]
    await client.post(f"/api/v1/polls/{poll}/vote", headers=await auth("employee"), json={"option_index": 0})
    response = await client.get("/api/v1/polls", headers=await auth("employee"))
    assert {item["id"]: item["my_vote"] for item in data(response)["items"]}[poll] == 0


# --- anonymous and inactive ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/announcements", None), ("POST", "/api/v1/announcements", NEWS),
    ("GET", "/api/v1/polls", None), ("POST", "/api/v1/polls", QUESTION),
])
async def test_anonymous_and_inactive(client, auth, method, path, body):
    anonymous = await client.request(method, path, json=body)
    assert anonymous.status_code == 401
    inactive = await client.request(method, path, json=body, headers=await auth("inactive"))
    assert inactive.status_code == 403 and error_code(inactive) == "USER_INACTIVE"
