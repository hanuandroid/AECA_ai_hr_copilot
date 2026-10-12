"""HR policy library responses (production hardening B-18, Phase 2).

Old behaviour (until 2026-10-12): GET /hr-policies and POST /hr-policies/upload returned each policy's server-side
`file_path` (internal storage layout). Invariant now: no server paths in responses; listing, uploading and downloading
keep working.
"""

from app.core.config import settings
from tests.support.http import data


def _upload():
    return {"data": {"title": "Travel Policy", "category": "TRAVEL"},
            "files": {"file": ("travel.md", b"# Travel\n\nBook economy class.", "text/markdown")}}


async def test_policy_list_does_not_expose_server_paths(client, auth):
    response = await client.get("/api/v1/hr-policies", params={"limit": 100}, headers=await auth("employee"))
    assert response.status_code == 200
    items = data(response)["items"]
    assert items and all("file_path" not in item for item in items)
    assert settings.policy_upload_dir not in response.text


async def test_policy_upload_response_does_not_expose_server_path(client, auth):
    response = await client.post("/api/v1/hr-policies/upload", headers=await auth("admin"), **_upload())
    assert response.status_code == 200
    body = data(response)
    assert "file_path" not in body and settings.policy_upload_dir not in response.text
    assert body["original_filename"] == "travel.md"


async def test_uploaded_policy_is_still_downloadable(client, auth):
    policy_id = data(await client.post("/api/v1/hr-policies/upload", headers=await auth("admin"), **_upload()))["id"]
    response = await client.get(f"/api/v1/hr-policies/{policy_id}/download", headers=await auth("employee"))
    assert response.status_code == 200
    assert b"Book economy class." in response.content
