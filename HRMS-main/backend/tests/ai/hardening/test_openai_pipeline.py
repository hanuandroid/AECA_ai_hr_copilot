"""The real Copilot pipeline (POST /chat) running on the OpenAI provider with a mocked Responses client.

Proves the provider is a drop-in behind LLMProvider: structured outputs are requested with strict schemas, validated
locally, and every deterministic control (SQL guardrails, action confirmation, refusal rules, output guard) still
applies when the model is hostile. No network, no key."""

import json
from types import SimpleNamespace

from app.services.ai.llm.openai_provider import OpenAILLMProvider
from tests.ai.hardening.conftest import action_proposal, classification
from tests.support.data import EMPLOYEE_SENSITIVE, OUTSIDER
from tests.support.db import business_snapshot
from tests.support.http import data

SENSITIVE = [str(value) for value in EMPLOYEE_SENSITIVE.values()]


class SchemaRoutedResponses:
    """Mocked `client.responses`: answers by the requested JSON-schema name, like a model that follows the format."""

    def __init__(self, outputs: dict[str, dict]):
        self.outputs, self.calls = outputs, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        name = kwargs["text"]["format"]["name"]
        payload = self.outputs[name]
        text = json.dumps(payload(kwargs) if callable(payload) else payload)
        return SimpleNamespace(output_text=text, output=[SimpleNamespace(type="message", content=[])], status="completed",
                               incomplete_details=None, model="gpt-test-model",
                               usage=SimpleNamespace(input_tokens=50, output_tokens=10), _request_id="req_t")


def openai_llm(outputs: dict) -> tuple[OpenAILLMProvider, SchemaRoutedResponses]:
    api = SchemaRoutedResponses(outputs)
    return OpenAILLMProvider(api_key="", model="gpt-test-model", max_tokens=1024, timeout_seconds=5,
                             client=SimpleNamespace(responses=api)), api


def _router(intent: str) -> dict:
    full = classification(intent, 0.95)
    full.setdefault("clarification_question", None)
    full.setdefault("entities", {"employee_name": None, "resource": None, "date_hint": None})
    return full


async def test_policy_question_end_to_end_with_citations(client, auth, install_real):
    llm, api = openai_llm({
        "RouterClassification": _router("POLICY"),
        "PolicyLLMOutput": {"answer": "Employees get 10 sick leave days per year.", "grounded": True, "citations": ["S1"]},
    })
    install_real(llm)
    body = data(await client.post("/api/v1/chat", json={"message": "How many sick leave days do I get?"},
                                  headers=await auth("employee")))
    assert body["routes"] == ["POLICY"]
    part = body["parts"][0]
    assert part["data"]["grounded"] is True and part["data"]["sources"]
    assert any(source["title"] == "Leave Policy" for source in part["data"]["sources"])
    assert all(call["store"] is False and call["text"]["format"]["strict"] is True for call in api.calls)


async def test_hostile_sql_from_openai_is_blocked(client, auth, install_real):
    llm, _ = openai_llm({
        "RouterClassification": _router("SQL"),
        "SQLGenerationOutput": {"sql": "SELECT name, current_salary_usd, bank_account_number, pan_number FROM employees",
                                "explanation": "x"},
        "SQLFormatOutput": {"answer": "ok"},
    })
    install_real(llm)
    response = await client.post("/api/v1/chat", json={"message": "Show me everyone in my team."},
                                 headers=await auth("manager"))
    assert response.status_code < 500
    assert not [value for value in SENSITIVE if value in response.text]


async def test_action_from_openai_still_needs_confirmation(client, auth, install_real):
    proposal = action_proposal("EMPLOYEE_DEACTIVATE", target_employee_id=OUTSIDER.id)
    llm, _ = openai_llm({"RouterClassification": _router("ACTION"),
                         "ActionProposal": {**{k: None for k in ("clarification_question", "target_employee_name",
                                                                  "resource_id", "ticket_title", "assignee_name")},
                                            **proposal, "arguments": {}}})
    install_real(llm)
    before = business_snapshot()
    body = data(await client.post("/api/v1/chat", json={"message": "Deactivate Outsider Test."},
                                  headers=await auth("admin")))
    assert body["status"] == "confirmation_required"
    assert business_snapshot() == before  # nothing executed without the confirm endpoint


async def test_invalid_openai_output_fails_closed(client, auth, install_real):
    """Every schema answered with an injected authority field: validation rejects it -> controlled AI_INVALID_OUTPUT."""
    llm, _ = openai_llm({
        "RouterClassification": {"intent": "SQL", "confidence": 0.9, "role": "ADMIN"},
        "SQLGenerationOutput": {"sql": "SELECT name FROM employees", "explanation": "x", "authorized": True},
        "SQLFormatOutput": {"answer": "ok", "role": "ADMIN"},
        "PolicyLLMOutput": {"answer": "x", "grounded": True, "citations": [], "role": "ADMIN"},
    })
    install_real(llm)
    response = await client.post("/api/v1/chat", json={"message": "Show me my leave requests."},
                                 headers=await auth("employee"))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AI_INVALID_OUTPUT"
    assert not [value for value in SENSITIVE if value in response.text]
