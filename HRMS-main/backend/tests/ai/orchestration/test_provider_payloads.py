"""What leaves the backend for the model provider (production hardening Phase 2, workstream C9, B-15).

Every model call goes through ObservedLLMProvider (audit/provider.observe). The `payloads` fixture records what the
provider itself receives (system prompt + messages, AFTER the provider boundary of AIS-02) for every call made by the
router, Policy RAG, SQL agent and Action agent.
See docs/production-hardening/data-processing.md for the per-call-site register these tests back up.
"""

import json
import re

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai_audit_event import AIAuditEvent
from app.models.ai_pending_action import AIPendingAction
from app.services.ai.audit.provider import ObservedLLMProvider
from app.services.ai.conversation.service import SQL_REPLY_IN_CONTEXT
from app.services.ai.orchestration.followup import escape
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.sql.models import SQLFormatOutput, SQLGenerationOutput
from tests.ai.orchestration.conftest import MultiLLM, action_proposal, classification, full_llm
from tests.support.data import EMPLOYEE, EMPLOYEE_SENSITIVE
from tests.support.http import data

CHAT = "/api/v1/chat"
# Prohibited everywhere: financial / government identifiers and credentials (matches the seeded EMPLOYEE record).
PROHIBITED = [str(value) for value in EMPLOYEE_SENSITIVE.values()] + ["72000", "$2b$", "Bearer "]


class _Recording:
    """Sits between ObservedLLMProvider and the provider: records exactly what the provider receives, i.e. the
    payload AFTER the provider boundary (AIS-02)."""

    def __init__(self, inner, captured):
        self._inner, self._captured = inner, captured
        self.name, self.model = getattr(inner, "name", "unknown"), getattr(inner, "model", "unknown")

    async def generate_structured(self, messages, schema, **kwargs):
        text = (kwargs.get("system") or "") + "\n" + "\n".join(message.content for message in messages)
        self._captured.append((schema.__name__, text))
        return await self._inner.generate_structured(messages, schema, **kwargs)


@pytest.fixture
def payloads(monkeypatch):
    captured: list[tuple[str, str]] = []
    original_init = ObservedLLMProvider.__init__

    def init(self, inner, operation):
        original_init(self, _Recording(inner, captured), operation)

    monkeypatch.setattr(ObservedLLMProvider, "__init__", init)
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


# --- AIS-02: the provider boundary on every entry point ---------------------------------------
# Synthetic values only. Identifiers are replaced by per-call placeholders before the provider is called; the model's
# output gets the caller's own values back (same call only); credentials are removed for good.
SYNTHETIC_PAN, SYNTHETIC_ACCOUNT = "QWERT6789Z", "123456789012"
SYNTHETIC_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c3ludGhldGljLXNpZ25hdHVyZQ"
SYNTHETIC_VALUES = (SYNTHETIC_PAN, SYNTHETIC_ACCOUNT, SYNTHETIC_JWT)


def _assert_boundary(payloads, token: str) -> None:
    _assert_nothing_prohibited(payloads, token)
    for _, text in payloads:
        for value in SYNTHETIC_VALUES:
            assert value not in text


@pytest.mark.parametrize("path,body", [
    (f"{CHAT}/policy", {"question": "How many work from home WFH days per week does the WFH policy allow for remote "
                                    f"work? {SYNTHETIC_PAN} {SYNTHETIC_ACCOUNT} {SYNTHETIC_JWT}"}),
    (f"{CHAT}/sql", {"question": f"Show my email address. Reference {SYNTHETIC_PAN} {SYNTHETIC_ACCOUNT} {SYNTHETIC_JWT}"}),
    (CHAT, {"message": f"Something else came up regarding {SYNTHETIC_PAN} {SYNTHETIC_ACCOUNT} {SYNTHETIC_JWT} today"}),
], ids=["policy", "sql", "chat-router"])
async def test_identifiers_and_credentials_never_reach_the_provider(client, auth, install_real, payloads, path, body):
    install_real(full_llm(router=classification("AMBIGUOUS", 0.9, clarification_question="What would you like to do?"),
                          sql="SELECT email FROM employees WHERE id = :me"))
    headers, token = await _token(auth, "employee")
    response = await client.post(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert payloads, "the scenario must actually call the model"
    joined = "\n".join(text for _, text in payloads)
    assert "[PII_PAN_1]" in joined and "[PII_NUMBER_1]" in joined and "[REDACTED_TOKEN]" in joined
    _assert_boundary(payloads, token)


async def test_action_arguments_keep_the_users_values_while_the_provider_sees_placeholders(client, auth, install_real,
                                                                                         payloads, db):
    def proposal(prompt: str) -> dict:
        reference = re.search(r"\[PII_NUMBER_\d+\]", prompt).group(0)  # the model only ever sees the placeholder
        return action_proposal("TICKET_CREATE", arguments={
            "title": "Account number update", "description": f"Please update my account to {reference}.",
            "category": "HR", "priority": "MEDIUM"})

    install_real(full_llm(action=proposal))
    headers, token = await _token(auth, "employee")
    result = data(await client.post(f"{CHAT}/actions", headers=headers, json={
        "question": f"Create an HR ticket: please update my account to {SYNTHETIC_ACCOUNT}. {SYNTHETIC_JWT}"}))
    assert result["status"] == "confirmation_required"
    pending = (await db.execute(select(AIPendingAction))).scalars().one()
    assert json.loads(pending.payload_json)["body"]["description"] == f"Please update my account to {SYNTHETIC_ACCOUNT}."
    assert SYNTHETIC_JWT not in pending.payload_json
    _assert_boundary(payloads, token)

    events = (await db.execute(select(AIAuditEvent).where(AIAuditEvent.event_type == "AI_PROVIDER_REQUEST"))).scalars().all()
    metadata = [json.loads(event.metadata_json) for event in events]
    assert any(m.get("redacted_values") == 2 and m.get("redacted_categories") == ["CREDENTIAL", "NUMBER"] for m in metadata)
    assert all(value not in event.metadata_json for event in events for value in SYNTHETIC_VALUES)


async def test_unregistered_operation_fails_closed_before_the_provider(payloads):
    from app.services.ai.audit.provider import observe
    from app.services.ai.errors import AIProviderPolicyError
    from app.services.ai.llm.base import LLMMessage

    llm = MultiLLM({RouterClassification: classification("SQL")})
    with pytest.raises(AIProviderPolicyError):
        await observe(llm, "free_form").generate_structured([LLMMessage(role="user", content="hi")], RouterClassification)
    assert llm.calls == [] and payloads == []


async def test_contact_details_in_current_and_earlier_turns_never_reach_the_classifier(client, auth, install_real, payloads):
    """AIS-03: the current message and the history are minimised the same way (synthetic contact details)."""
    email, phone = "someone.else@example.test", "+91 98765 43210"
    install_real(full_llm(router=_router, sql="SELECT email FROM employees WHERE id = :me"))
    headers, token = await _token(auth, "employee")
    first = data(await client.post(CHAT, json={"message": f"What is my email address? Mine is not {email}"}, headers=headers))
    await client.post(CHAT, json={"message": f"{UNCLASSIFIABLE} call me on {phone}",
                                  "conversation_id": first["conversation_id"]}, headers=headers)
    router_payloads = [text for schema, text in payloads if schema == RouterClassification.__name__]
    assert len(router_payloads) == 2 and "<conversation_context>\nUser: " in router_payloads[1]
    assert "[PII_EMAIL_1]" in router_payloads[1] and "[PII_PHONE_1]" in router_payloads[1]
    for _, text in payloads:
        assert email not in text and phone not in text and "98765 43210" not in text
    _assert_nothing_prohibited(payloads, token)
