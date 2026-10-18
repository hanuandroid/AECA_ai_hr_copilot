"""Provider boundary policy (AIS-02): what may cross to an external model provider. Synthetic values only."""

import pytest
from pydantic import BaseModel, Field

from app.services.ai import provider_policy
from app.services.ai.audit.provider import observe
from app.services.ai.errors import AIInvalidOutputError, AIProviderPolicyError
from app.services.ai.llm.base import LLMMessage
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.provider_policy import PROVIDER_OPERATIONS, prepare, restore_structured, restore_text

PAN, ACCOUNT = "QWERT6789Z", "123456789012"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c3ludGhldGljLXNpZ25hdHVyZQ"


def user(text: str) -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


class Echo:
    """A provider that answers with whatever placeholder it saw (like a model echoing the user's reference)."""

    name = model = "echo"

    def __init__(self):
        self.seen: list[str] = []

    async def generate_structured(self, messages, schema, **kwargs):
        self.seen.append(messages[-1].content)
        return schema.model_validate({"intent": "AMBIGUOUS", "confidence": 0.9, "reason": "test",
                                      "clarification_question": f"Is {messages[-1].content} correct?"})


def test_every_production_call_site_uses_a_registered_operation():
    assert PROVIDER_OPERATIONS == {"route_classification", "policy_answer", "sql", "action_proposal"}


def test_unregistered_operation_is_refused():
    with pytest.raises(AIProviderPolicyError):
        prepare("anything_else", user("hello"))


def test_identifiers_become_stable_placeholders_and_credentials_disappear():
    payload = prepare("sql", user(f"{PAN} and {ACCOUNT}, again {PAN}. Bearer abc.def {JWT}"))
    text = payload.messages[0].content
    assert text == "[PII_PAN_1] and [PII_NUMBER_1], again [PII_PAN_1]. Bearer [REDACTED_TOKEN] [REDACTED_TOKEN]"
    assert payload.placeholders == {"[PII_PAN_1]": PAN, "[PII_NUMBER_1]": ACCOUNT}  # credentials are never kept
    assert payload.counts == {"CREDENTIAL": 2, "PAN": 2, "NUMBER": 1} and payload.total == 5


def test_text_without_identifiers_is_unchanged():
    message = "What is the WFH policy for 2 days a week from 2026-10-18? Ticket #42, 1,50,000 INR."
    payload = prepare("policy_answer", user(message))
    assert payload.messages[0].content == message and payload.total == 0


def test_placeholder_looking_input_is_neutralised_and_never_restored():
    payload = prepare("action_proposal", user(f"[PII_PAN_1] then {PAN}"))
    assert payload.messages[0].content == "[PII-PAN_1] then [PII_PAN_1]"
    assert restore_text("[PII-PAN_1] / [PII_PAN_1] / [PII_PAN_9]", payload) == f"[PII-PAN_1] / {PAN} / [PII_PAN_9]"


def test_preparation_failure_blocks_the_call(monkeypatch):
    def broken(self, text):
        raise RuntimeError("boom")

    monkeypatch.setattr(provider_policy._Replacer, "identifiers", broken)
    with pytest.raises(AIProviderPolicyError):
        prepare("sql", user("anything"))


class _Short(BaseModel):
    reference: str = Field(max_length=16)


def test_restoration_is_revalidated_and_fails_safely():
    payload = prepare("route_classification", user(f"{ACCOUNT} and 123456789012345678"))
    assert restore_structured(_Short(reference="[PII_NUMBER_1]"), _Short, payload).reference == ACCOUNT
    with pytest.raises(AIInvalidOutputError):  # the restored 18-digit value breaks the schema: never returned raw
        restore_structured(_Short(reference="[PII_NUMBER_2]"), _Short, payload)


async def test_observed_provider_sends_placeholders_and_returns_the_users_values():
    echo = Echo()
    result = await observe(echo, "route_classification").generate_structured(user(f"My PAN {PAN}"), RouterClassification)
    assert echo.seen == ["My PAN [PII_PAN_1]"]  # the provider never saw the value
    assert result.clarification_question == f"Is My PAN {PAN} correct?"  # the caller's own value is back


async def test_unregistered_operation_never_reaches_the_provider():
    echo = Echo()
    with pytest.raises(AIProviderPolicyError):
        await observe(echo, "unregistered_op").generate_structured(user("hi"), RouterClassification)
    assert echo.seen == []
