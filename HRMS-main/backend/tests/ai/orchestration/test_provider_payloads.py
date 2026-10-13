"""What leaves the backend for the model provider (production hardening Phase 2, workstream C9, B-15).

Every model call goes through ObservedLLMProvider (audit/provider.observe), so wrapping it captures the complete
payload (system prompt + messages) of every call made by the router, Policy RAG, SQL agent and Action agent.
See docs/production-hardening/data-processing.md for the per-call-site register these tests back up.
"""

import pytest

from app.core.config import settings
from app.services.ai.audit.provider import ObservedLLMProvider
from app.services.ai.sql.models import SQLFormatOutput, SQLGenerationOutput
from tests.ai.orchestration.conftest import action_proposal, full_llm
from tests.support.data import EMPLOYEE, EMPLOYEE_SENSITIVE
from tests.support.http import data

CHAT = "/api/v1/chat"
# Prohibited everywhere: financial / government identifiers and credentials (matches the seeded EMPLOYEE record).
PROHIBITED = [str(value) for value in EMPLOYEE_SENSITIVE.values()] + ["72000", "$2b$", "Bearer "]


@pytest.fixture
def payloads(monkeypatch):
    captured: list[tuple[str, str]] = []
    original = ObservedLLMProvider.generate_structured

    async def capture(self, messages, schema, **kwargs):
        text = (kwargs.get("system") or "") + "\n" + "\n".join(message.content for message in messages)
        captured.append((schema.__name__, text))
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(ObservedLLMProvider, "generate_structured", capture)
    return captured


def _assert_nothing_prohibited(captured, token: str) -> None:
    assert captured, "the scenario must actually call the model"
    for schema, text in captured:
        for value in [*PROHIBITED, token]:
            assert value not in text, (schema, value)


async def _token(auth, user_key: str) -> tuple[dict, str]:
    headers = await auth(user_key)
    return headers, headers["Authorization"].split(" ", 1)[1]


async def test_sql_rows_never_reach_the_provider_by_default(client, auth, install_real, payloads):
    assert settings.ai_sql_result_formatting == "deterministic"  # safe default (no approved data-processing policy)
    install_real(full_llm(sql="SELECT name, email FROM employees WHERE id = :me"))
    headers, token = await _token(auth, "employee")
    response = await client.post(CHAT, json={"message": "Show my name and email from my employee record."}, headers=headers)
    part = data(response)["parts"][0]
    assert part["data"]["rows"] == [{"name": "Employee Test", "email": EMPLOYEE.email}]  # the user still gets the data
    schemas = [schema for schema, _ in payloads]
    assert SQLGenerationOutput.__name__ in schemas and SQLFormatOutput.__name__ not in schemas
    for _, text in payloads:
        assert "Employee Test" not in text and EMPLOYEE.email not in text  # result rows stayed in the backend
    _assert_nothing_prohibited(payloads, token)


async def test_llm_formatting_is_opt_in_and_still_excludes_prohibited_fields(client, auth, install_real, payloads, monkeypatch):
    monkeypatch.setattr(settings, "ai_sql_result_formatting", "llm")
    install_real(full_llm(sql="SELECT name, email FROM employees WHERE id = :me", sql_answer="Your record was found."))
    headers, token = await _token(auth, "employee")
    await client.post(CHAT, json={"message": "Show my name and email from my employee record."}, headers=headers)
    format_payloads = [text for schema, text in payloads if schema == SQLFormatOutput.__name__]
    assert format_payloads and "Employee Test" in format_payloads[0]  # documented opt-in: redacted rows are sent
    _assert_nothing_prohibited(payloads, token)


async def test_policy_payload_contains_policy_text_not_employee_data(client, auth, install_real, payloads):
    install_real(full_llm())
    headers, token = await _token(auth, "employee")
    response = await client.post(CHAT, json={"message": "What is the WFH policy?"}, headers=headers)
    assert response.status_code == 200
    _assert_nothing_prohibited(payloads, token)
    for _, text in payloads:
        assert EMPLOYEE.email not in text


async def test_action_proposal_payload_contains_no_prohibited_fields(client, auth, install_real, payloads):
    install_real(full_llm(action=action_proposal("TICKET_CREATE", arguments={
        "title": "VPN access", "description": "VPN is down since morning", "category": "IT", "priority": "HIGH"})))
    headers, token = await _token(auth, "employee")
    response = await client.post(CHAT, json={"message": "Create a ticket for my VPN access issue."}, headers=headers)
    assert response.status_code == 200
    _assert_nothing_prohibited(payloads, token)


async def test_sensitive_columns_requested_by_the_model_never_execute_or_reach_the_provider(client, auth, install_real, payloads):
    install_real(full_llm(sql="SELECT pan_number, bank_account_number FROM employees WHERE id = :me"))
    headers, token = await _token(auth, "employee")
    response = await client.post(CHAT, json={"message": "Show my employee record details."}, headers=headers)
    assert EMPLOYEE_SENSITIVE["pan_number"] not in response.text
    _assert_nothing_prohibited(payloads, token)
