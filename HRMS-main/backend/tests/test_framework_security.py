"""Framework-level security behaviour after the FastAPI 0.133.1 / Starlette 1.3.1 upgrade (production hardening Phase 2,
workstream C4, B-23). These tests exercise the patched code paths through this application's own routes, so a fix is not
claimed from a version number alone."""

import time

import pytest

from app.main import app
from tests.support.http import data


async def _uploaded_policy_id(client, auth) -> int:
    # ~120 KB so every range in the hostile header lies inside the file (out-of-file ranges are rejected early with 416).
    files = {"file": ("range.md", b"# Range\n\n" + b"policy text " * 10000, "text/markdown")}
    response = await client.post("/api/v1/hr-policies/upload", data={"title": "Range Policy", "category": "TEST"},
                                 files=files, headers=await auth("admin"))
    assert response.status_code == 200
    return data(response)["id"]


async def test_many_overlapping_ranges_do_not_cause_quadratic_work(client, auth):
    """CVE-2025-62727 (GHSA-7f5h-v6xp-fcq8): Range-header merging in FileResponse was O(n^2). The download endpoint uses
    FileResponse; a header with thousands of overlapping ranges must be answered promptly with a valid status."""
    policy_id = await _uploaded_policy_id(client, auth)
    # Interleaved (unsorted) ranges hit the quadratic merge path: measured 0.41s vs 0.02s at n=8000 on Starlette
    # 0.47.3 vs 1.3.1, so n=30000 takes several seconds on the vulnerable version and well under the bound when fixed.
    n = 30000
    hostile = "bytes=" + ",".join(f"{(i * 7919) % (n * 3)}-{(i * 7919) % (n * 3) + 1}" for i in range(n))
    started = time.monotonic()
    response = await client.get(f"/api/v1/hr-policies/{policy_id}/download", headers={**await auth("employee"), "Range": hostile})
    elapsed = time.monotonic() - started
    # B-26: even the patched Starlette took ~15 s here (multipart amplification); RangeHeaderLimitMiddleware now ignores
    # the abusive Range header and serves the plain file (RFC 9110 allows ignoring Range).
    assert response.status_code == 200
    assert elapsed < 2, f"range handling took {elapsed:.1f}s"


async def test_a_few_ranges_are_still_served_as_multipart(client, auth):
    policy_id = await _uploaded_policy_id(client, auth)
    response = await client.get(f"/api/v1/hr-policies/{policy_id}/download",
                                headers={**await auth("employee"), "Range": "bytes=0-1,10-11,20-21"})
    assert response.status_code == 206
    assert response.headers["content-type"].startswith("multipart/byteranges")


async def test_single_range_request_still_works(client, auth):
    policy_id = await _uploaded_policy_id(client, auth)
    response = await client.get(f"/api/v1/hr-policies/{policy_id}/download", headers={**await auth("employee"), "Range": "bytes=0-6"})
    assert response.status_code == 206 and response.content == b"# Range"


async def test_urlencoded_form_field_limit_is_enforced(client):
    """CVE-2026-54283 (GHSA-82w8-qh3p-5jfq): request.form() limits were ignored for urlencoded bodies. /auth/token parses an
    urlencoded form (OAuth2PasswordRequestForm); a body far beyond the field limit must be rejected, not parsed."""
    body = "&".join(f"f{i}=x" for i in range(5000)) + "&username=a@b.c&password=x"
    response = await client.post("/api/v1/auth/token", content=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400


async def test_normal_token_form_still_works(client):
    from tests.support.data import EMPLOYEE, PASSWORD

    response = await client.post("/api/v1/auth/token", data={"username": EMPLOYEE.email, "password": PASSWORD})
    assert response.status_code == 200 and response.json()["token_type"] == "bearer"


async def test_lifespan_still_runs_startup_checks(monkeypatch):
    """The app's lifespan (not on_startup, removed in Starlette 1.x) still runs on the upgraded stack, and still enforces
    the production configuration checks."""
    from app.core.config import settings

    async with app.router.lifespan_context(app):
        pass  # dev/test configuration starts cleanly

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        async with app.router.lifespan_context(app):
            pass
