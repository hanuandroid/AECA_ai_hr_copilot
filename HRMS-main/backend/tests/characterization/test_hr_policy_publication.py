"""ADMIN-only publication of organisation-wide HR policies (production hardening closure, approved 2026-10-14).

Old behaviour (until 2026-10-14): any MANAGER upload was immediately listed to every employee, downloadable by
everyone and, after the next ingestion run, used for Copilot answers. Invariant now: a MANAGER upload is a SUBMITTED
draft, visible only to ADMINs and its submitter (everyone else gets the same 404 as an unknown id) until an ADMIN
publishes it; publishing a title archives its previous version; every transition is in the append-only audit trail.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from tests.support.http import data, error_code

POLICIES = "/api/v1/hr-policies"


def _upload(title: str = "Remote Work Policy", body: bytes = b"# Remote work\n\nWork from home on Fridays."):
    return {"data": {"title": title, "category": "WORKPLACE"}, "files": {"file": ("remote.md", body, "text/markdown")}}


async def _upload_as(client, auth, role: str, **kwargs) -> dict:
    response = await client.post(f"{POLICIES}/upload", headers=await auth(role), **_upload(**kwargs))
    assert response.status_code == 200, response.text
    return data(response)


async def _listed_ids(client, auth, role: str, **params) -> set[int]:
    response = await client.get(POLICIES, params={"limit": 100, **params}, headers=await auth(role))
    assert response.status_code == 200
    body = data(response)
    assert body["meta"]["total"] == len(body["items"])  # count matches what the caller may see
    return {item["id"] for item in body["items"]}


async def test_admin_upload_is_published_directly(client, auth):
    policy = await _upload_as(client, auth, "admin")
    assert (policy["status"], policy["version"], policy["reviewed_by"]) == ("PUBLISHED", 1, 1)
    assert policy["id"] in await _listed_ids(client, auth, "employee")


async def test_manager_upload_is_a_draft_hidden_from_employees(client, auth):
    draft = await _upload_as(client, auth, "manager")
    assert draft["status"] == "SUBMITTED" and draft["reviewed_by"] is None

    assert draft["id"] not in await _listed_ids(client, auth, "employee")
    assert draft["id"] not in await _listed_ids(client, auth, "employee", status="SUBMITTED")  # filter can't widen
    assert draft["id"] in await _listed_ids(client, auth, "manager")  # the submitter sees their own submission
    assert draft["id"] in await _listed_ids(client, auth, "admin", status="SUBMITTED")


async def test_draft_download_is_indistinguishable_from_an_unknown_policy(client, auth):
    draft = await _upload_as(client, auth, "manager")
    hidden = await client.get(f"{POLICIES}/{draft['id']}/download", headers=await auth("employee"))
    unknown = await client.get(f"{POLICIES}/999999/download", headers=await auth("employee"))
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    for role in ("manager", "admin"):
        assert (await client.get(f"{POLICIES}/{draft['id']}/download", headers=await auth(role))).status_code == 200


@pytest.mark.parametrize("role", ["manager", "employee"])
@pytest.mark.parametrize("decision", ["publish", "reject"])
async def test_only_admins_review(client, auth, role, decision):
    draft = await _upload_as(client, auth, "manager")
    response = await client.post(f"{POLICIES}/{draft['id']}/{decision}", headers=await auth(role))
    assert response.status_code == 403
    assert draft["id"] not in await _listed_ids(client, auth, "employee")


async def test_admin_publishes_a_submission(client, auth):
    draft = await _upload_as(client, auth, "manager")
    response = await client.post(f"{POLICIES}/{draft['id']}/publish", headers=await auth("admin"))
    assert response.status_code == 200
    published = data(response)
    assert published["status"] == "PUBLISHED" and published["reviewed_by"] == 1
    assert published["index_refresh_required"] is True
    assert draft["id"] in await _listed_ids(client, auth, "employee")


async def test_admin_rejects_a_submission(client, auth):
    draft = await _upload_as(client, auth, "manager")
    response = await client.post(f"{POLICIES}/{draft['id']}/reject", headers=await auth("admin"))
    assert data(response)["status"] == "REJECTED"
    assert draft["id"] not in await _listed_ids(client, auth, "employee")
    again = await client.post(f"{POLICIES}/{draft['id']}/publish", headers=await auth("admin"))
    assert again.status_code == 409 and error_code(again) == "POLICY_NOT_SUBMITTED"


async def test_review_of_unknown_or_published_policy(client, auth):
    published = await _upload_as(client, auth, "admin")
    assert (await client.post(f"{POLICIES}/{published['id']}/publish", headers=await auth("admin"))).status_code == 409
    unknown = await client.post(f"{POLICIES}/999999/publish", headers=await auth("admin"))
    assert unknown.status_code == 404 and error_code(unknown) == "POLICY_NOT_FOUND"


async def test_publishing_a_new_version_archives_the_previous_one(client, auth):
    first = await _upload_as(client, auth, "admin", title="Expense Policy")
    draft = await _upload_as(client, auth, "manager", title="  expense policy ", body=b"# Expenses\n\nVersion two.")
    assert draft["version"] == 2
    assert first["id"] in await _listed_ids(client, auth, "employee")  # still active while v2 awaits review

    await client.post(f"{POLICIES}/{draft['id']}/publish", headers=await auth("admin"))
    employee_view = await _listed_ids(client, auth, "employee")
    assert draft["id"] in employee_view and first["id"] not in employee_view
    archived = await _listed_ids(client, auth, "admin", status="ARCHIVED")
    assert first["id"] in archived


async def test_every_transition_is_in_the_audit_trail(client, auth):
    old = await _upload_as(client, auth, "admin", title="Travel Rules")
    draft = await _upload_as(client, auth, "manager", title="Travel Rules")
    await client.post(f"{POLICIES}/{draft['id']}/publish", headers=await auth("admin"))

    events = data(await client.get(f"{POLICIES}/{draft['id']}/events", headers=await auth("admin")))
    assert [(e["action"], e["from_status"], e["to_status"], e["actor_employee_id"]) for e in events] == [
        ("SUBMITTED", None, "SUBMITTED", 2), ("PUBLISHED", "SUBMITTED", "PUBLISHED", 1)]
    assert all(e["checksum"] == draft["checksum"] and e["version"] == 2 for e in events)
    old_events = data(await client.get(f"{POLICIES}/{old['id']}/events", headers=await auth("admin")))
    assert [e["action"] for e in old_events] == ["SUBMITTED", "PUBLISHED", "ARCHIVED"]


@pytest.mark.parametrize("role", ["manager", "employee"])
async def test_audit_trail_is_admin_only(client, auth, role):
    policy = await _upload_as(client, auth, "admin")
    assert (await client.get(f"{POLICIES}/{policy['id']}/events", headers=await auth(role))).status_code == 403


@pytest.mark.parametrize("statement", ["UPDATE hr_policy_events SET action = 'PUBLISHED'", "DELETE FROM hr_policy_events"])
async def test_audit_trail_is_append_only(client, auth, db, statement):
    await _upload_as(client, auth, "manager")
    with pytest.raises(DatabaseError, match="append-only"):
        await db.execute(text(statement))
    await db.rollback()
