"""Live-model Policy RAG answer evaluation (capstone phase 4). OPT-IN ONLY: external calls that cost money.

Runs only when `python -m evals.run_evals --live` starts it (AI_LIVE_EVAL=1 plus the selected real provider); in every
normal test run it is skipped. Real model answers are scored with the offline scorer (evals/policy_rag_eval.py):
retrieval of the expected source, citation validity, grounding with the expected fact, and refusal of out-of-scope
questions, on the synthetic demo policies and the throwaway test database.

The model is called through PolicyRagService, so every call passes the provider boundary (observe -> provider_policy),
and each question runs under its own AIS-10 request budget (evals/live_policy_eval.py). Quality is reported, not
asserted. The helpers are unit-tested offline in tests/ai/audit/test_eval_reporting.py.
"""

import os

import pytest

from app.core.config import settings
from app.services.ai.providers import build_llm_provider
from evals.live_policy_eval import BudgetedService, UsageMeter, live_suite
from evals.policy_rag_eval import evaluate, load_cases
from evals.reporting import publish

LIVE_PROVIDERS = {"anthropic", "openai"}

pytestmark = pytest.mark.skipif(os.environ.get("AI_LIVE_EVAL") != "1",
                                reason="live-model evaluation: opt-in via `python -m evals.run_evals --live` (costs money)")


async def test_policy_rag_live_eval(ctx_for, make_service, retriever, ingested):
    provider_name = os.environ.get("AI_LIVE_EVAL_PROVIDER", "")
    if provider_name not in LIVE_PROVIDERS:  # never score scripted output as if it were a live model
        pytest.fail("AI_LIVE_EVAL_PROVIDER must be anthropic or openai")
    # The harness forces AI_LLM_PROVIDER=fake for normal tests, so the live provider is selected explicitly here.
    meter = UsageMeter(build_llm_provider(settings.model_copy(update={"ai_llm_provider": provider_name})))
    service, _, _ = make_service(meter)
    budgeted = BudgetedService(service)
    cases = load_cases()
    contexts = {role: await ctx_for(role.lower()) for role in ("EMPLOYEE", "MANAGER", "ADMIN")}

    report = await evaluate(cases, budgeted, retriever, contexts)
    suite = live_suite(report, cases, budgeted.errors, meter)
    publish(suite)
    assert suite.total == len(cases)  # every case produced a scored result
