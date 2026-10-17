"""POST /api/v1/chat/policy over HTTP with the real auth stack and fake providers."""

import pytest

from app.main import app
from app.services.ai.rag.service import INSUFFICIENT_EVIDENCE, get_policy_rag_service
from tests.ai.rag.conftest import answering_llm
from tests.support.http import data, error_code

URL = "/api/v1/chat/policy"
SICK = "Employees are entitled to 10 days of sick leave per year."


@pytest.mark.parametrize("user_key", ["employee", "manager", "admin"])
async def test_policy_answer_with_sources(client, auth, ingested, override_rag, user_key):
    llm_factory = override_rag(answering_llm(SICK))
    response = await client.post(URL, json={"question": "How many sick leaves can I take?"}, headers=await auth(user_key))
    assert response.status_code == 200
    body = data(response)
    assert body["answer"] == SICK and body["grounded"] is True
    assert body["sources"][0]["title"] == "Leave Policy"
    # policy_id added in RBAC step 7 (replay re-checks audiences); still no paths, checksums, uploader ids or embeddings.
    assert set(body["sources"][0]) == {"policy_id", "chunk_id", "title", "category", "filename", "section"}
    assert llm_factory.calls == 1


async def test_assignment_message_field_is_accepted(client, auth, ingested, override_rag):
    override_rag(answering_llm(SICK))
    response = await client.post(URL, json={"message": "What is the sick leave policy?"}, headers=await auth("employee"))
    assert data(response)["grounded"] is True


async def test_inactive_user_rejected_before_rag(client, auth, ingested, override_rag):
    llm_factory = override_rag(answering_llm(SICK))
    response = await client.post(URL, json={"question": "How many sick leaves can I take?"}, headers=await auth("inactive"))
    assert response.status_code == 403
    assert error_code(response) == "USER_INACTIVE"  # B-03: rejected by get_current_user (was AI_USER_INACTIVE)
    assert llm_factory.calls == 0


async def test_unauthenticated_rejected(client, override_rag):
    llm_factory = override_rag(answering_llm(SICK))
    assert (await client.post(URL, json={"question": "hi"})).status_code == 401
    assert llm_factory.calls == 0


@pytest.mark.parametrize(
    "body,expected_message",
    [
        ({"question": ""}, "Please enter a question."),
        ({"question": "   "}, "Please enter a question."),
        ({}, "Please provide a question."),
        ({"question": "x" * 1001}, "Your question is too long. Please keep it under 1000 characters."),
    ],
)
async def test_invalid_questions_use_ai_envelope(client, auth, override_rag, body, expected_message):
    llm_factory = override_rag(answering_llm(SICK))
    response = await client.post(URL, json=body, headers=await auth("employee"))
    assert response.status_code == 400
    assert response.json() == {"success": False, "data": None, "error": {"code": "AI_INVALID_REQUEST", "message": expected_message}}
    assert llm_factory.calls == 0


async def test_insufficient_evidence_response(client, auth, ingested, override_rag):
    override_rag(answering_llm("Vesting is 4 years."))
    body = data(await client.post(URL, json={"question": "What is the stock option vesting schedule?"}, headers=await auth("employee")))
    assert body == {"answer": INSUFFICIENT_EVIDENCE, "grounded": False, "sources": []}


async def test_unconfigured_provider_returns_safe_503(client, auth, ingested, monkeypatch):
    """Real dependency, Anthropic selected but no key: fail closed without leaking internals."""
    from app.core.config import settings
    from app.services.ai import providers

    monkeypatch.setattr(settings, "ai_llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(providers, "_llm_cache", {})
    assert get_policy_rag_service not in app.dependency_overrides
    response = await client.post(URL, json={"question": "How many sick leaves can I take?"}, headers=await auth("employee"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE"
    for leaked in ("ANTHROPIC", "api_key", "Traceback", "anthropic_provider"):
        assert leaked not in response.text


async def test_response_exposes_no_internal_fields(client, auth, ingested, override_rag):
    override_rag(answering_llm(SICK))
    text = (await client.post(URL, json={"question": "How many sick leaves can I take?"}, headers=await auth("employee"))).text
    for internal in ("embedding", "file_path", "checksum", "index_key", "hr-policies", "score", "fake-hash"):
        assert internal not in text


async def test_session_stubs_still_return_501(client, auth):
    response = await client.post("/api/v1/chat/sessions", json={"title": "My chat"}, headers=await auth("employee"))
    assert response.status_code == 501


# Production hardening B-18 review (Phase 2): the legacy 501 stubs are kept for contract compatibility (PRD and
# architecture docs reference them; no client calls them). Since Phase 1 they reject inactive users via
# get_current_user like every other endpoint; removal needs product approval.
async def test_session_stubs_reject_unauthenticated_and_inactive_users(client, auth):
    stubs = [("/api/v1/chat/sessions", {"title": "My chat"}), ("/api/v1/chat/sessions/abc/messages", {"role": "user", "content": "hi"})]
    for path, body in stubs:
        assert (await client.post(path, json=body)).status_code == 401
        inactive = await client.post(path, json=body, headers=await auth("inactive"))
        assert inactive.status_code == 403 and error_code(inactive) == "USER_INACTIVE"
