"""AIS-10: per-request model-call budget and end-to-end deadline. Scripted models only; no real provider."""

import asyncio
import time

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.ai_pending_action import AIPendingAction
from app.services.ai import limits
from app.services.ai.errors import AIBusyError, AIRequestLimitError, AIRequestTimeoutError
from app.services.ai.limits import RequestBudget, charge_model_call, llm_slots, within_deadline
from app.services.ai.orchestration.models import RouterClassification
from tests.ai.orchestration.conftest import MultiLLM, action_proposal, classification, full_llm
from tests.support.db import business_snapshot

CHAT = "/api/v1/chat"
UNCLASSIFIABLE = "Something else entirely unrelated came up for me today friend"  # needs the classifier model


class SlowLLM(MultiLLM):
    def __init__(self, payloads, delay: float):
        super().__init__(payloads)
        self.delay, self.cancelled = delay, 0

    async def generate_structured(self, messages, schema, **kwargs):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().generate_structured(messages, schema, **kwargs)


def _with_budget(budget: RequestBudget):
    return limits._request_budget.set(budget)


# --- unit ------------------------------------------------------------------------------------------------------------


def test_outside_a_request_there_is_no_budget():
    assert limits._request_budget.get() is None and charge_model_call() is None


def test_the_call_budget_is_enforced_before_the_call():
    token = _with_budget(RequestBudget(max_calls=2, deadline=None))
    try:
        assert charge_model_call() is None and charge_model_call() is None
        with pytest.raises(AIRequestLimitError):
            charge_model_call()
    finally:
        limits._request_budget.reset(token)


def test_a_passed_deadline_refuses_the_next_call():
    token = _with_budget(RequestBudget(max_calls=0, deadline=time.monotonic() - 1))
    try:
        with pytest.raises(AIRequestTimeoutError):
            charge_model_call()
    finally:
        limits._request_budget.reset(token)


async def test_a_slow_provider_call_is_cancelled_at_the_deadline():
    llm = SlowLLM({}, delay=5)
    started = time.monotonic()
    with pytest.raises(AIRequestTimeoutError):
        await within_deadline(llm.generate_structured([], RouterClassification), time.monotonic() + 0.05)
    assert time.monotonic() - started < 2 and llm.cancelled == 1


async def test_queueing_never_outlives_the_deadline(monkeypatch):
    monkeypatch.setattr(settings, "ai_max_concurrent_llm_calls", 1)
    monkeypatch.setattr(settings, "ai_llm_queue_timeout_seconds", 10)
    llm_slots.reset()
    release = asyncio.Event()

    async def hold():
        async with llm_slots.slot():
            await release.wait()

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    with pytest.raises(AIRequestTimeoutError):  # deadline (0.05 s) before the queue timeout (10 s)
        async with llm_slots.slot(time.monotonic() + 0.05):
            pass
    monkeypatch.setattr(settings, "ai_llm_queue_timeout_seconds", 0.05)
    with pytest.raises(AIBusyError):  # queue timeout first: the existing AI_BUSY behaviour is unchanged
        async with llm_slots.slot(time.monotonic() + 10):
            pass
    release.set()
    await holder
    assert llm_slots.in_flight == 0


def test_default_budget_covers_the_longest_legitimate_turn():
    from app.services.ai.orchestration.classifier import MAX_CLAUSES
    assert settings.ai_max_model_calls_per_request == MAX_CLAUSES + 4  # policy + SQL generation + SQL wording + action
    assert settings.ai_request_deadline_seconds > settings.ai_request_timeout_seconds


# --- HTTP ------------------------------------------------------------------------------------------------------------


async def test_exceeding_the_call_budget_fails_safely(client, auth, install_real, monkeypatch, db):
    monkeypatch.setattr(settings, "ai_max_model_calls_per_request", 1)  # classifier only; the SQL call is refused
    llm = install_real(full_llm(router=classification("SQL"), sql="SELECT email FROM employees WHERE id = :me"))
    before = business_snapshot()
    response = await client.post(CHAT, json={"message": UNCLASSIFIABLE}, headers=await auth("employee"))
    assert response.status_code == 422 and response.json()["error"]["code"] == "AI_REQUEST_LIMIT_EXCEEDED"
    assert llm.count(RouterClassification) == 1 and len(llm.calls) == 1
    assert business_snapshot() == before
    assert limits._request_budget.get() is None  # the budget never outlives its request


async def test_a_slow_model_hits_the_deadline_and_no_action_is_prepared(client, auth, install_real, monkeypatch, db):
    monkeypatch.setattr(settings, "ai_request_deadline_seconds", 0.2)
    llm = install_real(SlowLLM(full_llm(action=action_proposal("TICKET_CREATE", arguments={
        "title": "VPN access", "description": "VPN is down", "category": "IT", "priority": "HIGH"})).payloads, delay=3))
    started = time.monotonic()
    response = await client.post(f"{CHAT}/actions", json={"question": "Create a ticket for my VPN access issue."},
                                 headers=await auth("employee"))
    assert response.status_code == 504 and response.json()["error"]["code"] == "AI_REQUEST_TIMEOUT"
    assert time.monotonic() - started < 2 and llm.cancelled == 1
    assert (await db.execute(select(func.count(AIPendingAction.id)))).scalar_one() == 0
