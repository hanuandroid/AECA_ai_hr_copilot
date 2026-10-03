"""Characterization of api/v1/endpoints/announcements.py (wrapped by AI tools in Phase 5)."""

import pytest

from tests.support.data import ADMIN, MANAGER
from tests.support.http import data, error_code

PAYLOAD = {"title": "Townhall moved", "body": "Friday's townhall is moved to 5 PM."}


async def test_employee_cannot_create_announcement(client, auth):
    response = await client.post("/api/v1/announcements", json=PAYLOAD, headers=await auth("employee"))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


@pytest.mark.parametrize("user_key,user", [("manager", MANAGER), ("admin", ADMIN)])
async def test_manager_and_admin_create_announcement(client, auth, user_key, user):
    response = await client.post("/api/v1/announcements", json=PAYLOAD, headers=await auth(user_key))
    assert response.status_code == 200
    announcement = data(response)
    assert announcement["author_id"] == user.id
    assert announcement["title"] == PAYLOAD["title"]

    listing = await client.get("/api/v1/announcements", headers=await auth("employee"))
    assert [item["id"] for item in data(listing)["items"]] == [announcement["id"]]


async def test_whitespace_only_title_rejected_after_strip(client, auth):
    response = await client.post(
        "/api/v1/announcements", json={"title": "   a   ", "body": PAYLOAD["body"]}, headers=await auth("manager")
    )
    assert response.status_code == 400
    assert error_code(response) == "INVALID_ANNOUNCEMENT"


async def test_too_short_title_is_422(client, auth):
    response = await client.post("/api/v1/announcements", json={"title": "ab", "body": PAYLOAD["body"]}, headers=await auth("manager"))
    assert response.status_code == 422
