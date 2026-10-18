"""What leaves the backend for the model provider (production hardening Phase 2, workstream C9, B-15).

Every model call goes through ObservedLLMProvider (audit/provider.observe), so wrapping it captures the complete
payload (system prompt + messages) of every call made by the router, Policy RAG, SQL agent and Action agent.
See docs/production-hardening/data-processing.md for the per-call-site register these tests back up.
"""

import pytest

from app.core.config import settings
from app.services.ai.audit.provider import ObservedLLMProvider
from app.services.ai.conversation.service import SQL_REPLY_IN_CONTEXT
from app.services.ai.orchestration.followup import escape
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.sql.models import SQLFormatOutput, SQLGenerationOutput
from tests.ai.orchestration.conftest import action_proposal, classification, full_llm
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


# R1: a single-value SQL answer ("The result is <value>.") is stored as the assistant reply. A later turn that the rules
# cannot classify sends the conversation history to the classifier model; the value must not be in that payload.
# Synthetic seeded values only (tests/support/data.py): the employee's email, pending leave reason and ticket text.
SQL_HISTORY_CASES = [  # (result column, question, model SQL, synthetic value)
    ("email", "What is my email address?", "SELECT email FROM employees WHERE id = :me", EMPLOYEE.email),
    ("reason", "What reason did I give for my pending leave?",
     "SELECT reason FROM leave_requests WHERE employee_id = :me AND status = 'PENDING'", "Family function"),
    ("description", "What does my ticket description say?",
     "SELECT description FROM tickets WHERE employee_id = :me", "Cannot connect to VPN from home."),
]
UNCLASSIFIABLE = "Something else entirely unrelated came up for me today friend"  # no rule signal, not a follow-up


def _router(prompt: str) -> dict:
    if UNCLASSIFIABLE in prompt:
        return classification("AMBIGUOUS", 0.9, clarification_question="What would you like to do?")
    return classification("SQL")


@pytest.mark.parametrize("column,question,sql,value", SQL_HISTORY_CASES, ids=[case[0] for case in SQL_HISTORY_CASES])
async def test_sql_values_never_reach_the_classifier_through_history(client, auth, install_real, payloads,
                                                                     column, question, sql, value):
    install_real(full_llm(router=_router, sql=sql))
    headers, token = await _token(auth, "employee")

    first = data(await client.post(CHAT, json={"message": question}, headers=headers))
    assert first["routes"] == ["SQL"] and first["message"] == f"The result is {value}."  # the user gets the answer
    assert first["parts"][0]["data"]["rows"] == [{column: value}]
    assert len(payloads) >= 1 and all(value not in text for _, text in payloads)  # turn 1: rows stay in the backend

    second = await client.post(CHAT, json={"message": UNCLASSIFIABLE, "conversation_id": first["conversation_id"]},
                               headers=headers)
    assert data(second)["status"] == "clarification_required"
    history_payloads = [text for schema, text in payloads
                        if schema == RouterClassification.__name__ and UNCLASSIFIABLE in text]
    assert len(history_payloads) == 1, "the follow-up must reach the classifier model"
    assert "<conversation_context>\nUser: " in history_payloads[0], "... with the conversation history"
    assert SQL_REPLY_IN_CONTEXT in history_payloads[0] and f"User: {escape(question)}" in history_payloads[0]
    for _, text in payloads:  # every call of both turns, not only the classifier
        assert value not in text
    _assert_nothing_prohibited(payloads, token)

    replay = data(await client.get(f"{CHAT}/conversations/{first['conversation_id']}", headers=headers))
    assert replay["messages"][1]["content"] == f"The result is {value}."  # the owner's own history still shows it


async def test_direct_sql_endpoint_response_is_unchanged(client, auth, install_real, payloads):
    install_real(full_llm(sql="SELECT email FROM employees WHERE id = :me"))
    headers, _ = await _token(auth, "employee")
    result = data(await client.post(f"{CHAT}/sql", json={"question": "What is my email address?"}, headers=headers))
    assert result == {"answer": f"The result is {EMPLOYEE.email}.", "scope": "SELF", "columns": ["email"],
                      "rows": [{"email": EMPLOYEE.email}], "row_count": 1, "truncated": False}  # no raw SQL for employees
    assert all(EMPLOYEE.email not in text for _, text in payloads)


async def test_sensitive_columns_requested_by_the_model_never_execute_or_reach_the_provider(client, auth, install_real, payloads):
    install_real(full_llm(sql="SELECT pan_number, bank_account_number FROM employees WHERE id = :me"))
    headers, token = await _token(auth, "employee")
    response = await client.post(CHAT, json={"message": "Show my employee record details."}, headers=headers)
    assert EMPLOYEE_SENSITIVE["pan_number"] not in response.text
    _assert_nothing_prohibited(payloads, token)
