"""Scripted fake model for the isolated E2E stack (llm/fake.py::ScriptedFakeLLM, AI_FAKE_LLM_SCRIPT).

Test-only accommodation for browser tests of the Copilot action flow: it produces a deterministic ActionProposal; every
step after that is the real application. Guarantees pinned here: schema-aware (a rule never answers another model call),
unmatched or invalid output fails like the rule-less fake, used only with AI_LLM_PROVIDER=fake, malformed scripts fail
closed, and production refuses the setting.
"""

import json

import pytest

from app.core.config import Settings
from app.core.production_checks import production_problems
from app.services.ai.actions.models import ActionProposal
from app.services.ai.errors import AIInvalidOutputError, AIUnavailableError
from app.services.ai.llm.base import LLMMessage
from app.services.ai.llm.fake import FakeLLMProvider, ScriptedFakeLLM
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.providers import build_llm_provider

RULES = [
    {"schema": "ActionProposal", "contains": "e2e ticket titled",
     "capture": r"titled ([A-Za-z0-9 -]+?)\s*$", "output": {"action": "TICKET_CREATE", "arguments": {
         "title": "{capture}", "description": "Synthetic E2E ticket", "category": "IT", "priority": "HIGH"}}},
    {"schema": "ActionProposal", "contains": "approve leave request", "capture": r"#(\d+)",
     "output": {"action": "LEAVE_APPROVE", "resource_id": "{capture:int}"}},
    {"schema": "ActionProposal", "contains": "urgent ticket", "output": {"action": "TICKET_CREATE", "arguments": {
        "title": "Bad", "description": "Synthetic", "category": "IT", "priority": "URGENT"}}},
    {"schema": "ActionProposal", "contains": "broken shape", "output": {"action": "NOT_AN_ACTION"}},
]


def _message(text: str) -> list[LLMMessage]:
    # The shape of actions/prompts.py::build_action_message: the action list (whose text must never match) + the request.
    return [LLMMessage(role="user", content="<available_actions>\n- LEAVE_APPROVE: Approve a pending leave request; "
                                            "approve leave request #9 urgent ticket broken shape\n</available_actions>\n\n"
                                            f"Today's date: 2026-10-20\n\n<request>\n{text}\n</request>")]


async def test_matching_rule_returns_the_scripted_proposal_with_captured_values():
    fake = ScriptedFakeLLM.from_json(json.dumps(RULES))
    ticket = await fake.generate_structured(_message("Create an E2E ticket titled VPN outage 42"), ActionProposal)
    assert ticket.action == "TICKET_CREATE"
    assert (ticket.arguments.title, ticket.arguments.priority) == ("VPN outage 42", "HIGH")
    approval = await fake.generate_structured(_message("Approve leave request #317 please"), ActionProposal)
    assert (approval.action, approval.resource_id) == ("LEAVE_APPROVE", 317)


async def test_rules_are_schema_aware_and_unmatched_requests_fail_like_the_plain_fake():
    fake = ScriptedFakeLLM.from_json(json.dumps(RULES))
    with pytest.raises(AIInvalidOutputError):  # the same text, but a different requested model (router)
        await fake.generate_structured(_message("Create an E2E ticket titled VPN outage 42"), RouterClassification)
    with pytest.raises(AIInvalidOutputError):
        await fake.generate_structured(_message("Something no rule covers"), ActionProposal)
    with pytest.raises(AIInvalidOutputError):  # output that does not fit the schema
        await fake.generate_structured(_message("broken shape"), ActionProposal)
    with pytest.raises(AIInvalidOutputError):  # capture pattern not found -> rule does not apply
        await fake.generate_structured(_message("approve leave request without a number"), ActionProposal)
    with pytest.raises(AIInvalidOutputError):  # free text is never scripted
        await fake.generate(_message("Create an E2E ticket titled VPN outage 42"))


async def test_invalid_parameters_pass_through_unvalidated_to_the_real_validators():
    # The fake does not repair arguments: URGENT is not a valid priority, and the action validator must catch it.
    fake = ScriptedFakeLLM.from_json(json.dumps(RULES))
    proposal = await fake.generate_structured(_message("raise an urgent ticket"), ActionProposal)
    assert proposal.arguments.priority == "URGENT"


def _settings(**overrides) -> Settings:
    return Settings(**{"ai_enabled": True, "ai_llm_provider": "fake", **overrides})


def test_provider_selection_uses_the_script_only_with_the_fake_provider():
    assert isinstance(build_llm_provider(_settings(ai_fake_llm_script=json.dumps(RULES))), ScriptedFakeLLM)
    assert isinstance(build_llm_provider(_settings()), FakeLLMProvider)
    with pytest.raises(AIUnavailableError):  # a real provider ignores the script (and here has no key)
        build_llm_provider(_settings(ai_llm_provider="anthropic", anthropic_api_key="", ai_fake_llm_script=json.dumps(RULES)))


@pytest.mark.parametrize("script", ["not json", json.dumps({"schema": "x"}), json.dumps([{"schema": "ActionProposal"}])])
def test_a_malformed_script_fails_closed(script):
    with pytest.raises(AIUnavailableError):
        build_llm_provider(_settings(ai_fake_llm_script=script))


def test_production_refuses_the_script_setting():
    problems = production_problems(_settings(ai_fake_llm_script=json.dumps(RULES)), environ={}, argv=[])
    assert "AI_FAKE_LLM_SCRIPT is for the isolated E2E stack only" in problems
    assert "AI_FAKE_LLM_SCRIPT is for the isolated E2E stack only" not in production_problems(_settings(), environ={}, argv=[])
