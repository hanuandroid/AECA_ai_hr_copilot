"""Helpers for the live-model Policy RAG answer evaluation (tests/ai/rag/test_policy_eval_live.py).

Opt-in only (`python -m evals.run_evals --live`). Scoring is the offline scorer (evals/policy_rag_eval.py); these
helpers add what a live run needs: usage metering without fabricated numbers, one AIS-10 request budget per question,
and the rule that a provider error is a failed case, never a correct refusal.
"""

from types import SimpleNamespace
from typing import Any

from app.services.ai import limits
from app.services.ai.audit.provider import estimate_cost_usd
from app.services.ai.errors import AIError
from app.services.ai.llm.base import usage_sink
from evals.reporting import SuiteResult, from_policy


class UsageMeter:
    """Wraps the real provider (it runs inside the observability wrapper) and adds up the usage the provider reported."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.name, self.model = getattr(inner, "name", "unknown"), getattr(inner, "model", "unknown")
        self.calls = self.input_tokens = self.output_tokens = self.unreported = 0

    async def generate(self, messages, **kwargs):
        result = await self._inner.generate(messages, **kwargs)
        self._count()
        return result

    async def generate_structured(self, messages, schema, **kwargs):
        result = await self._inner.generate_structured(messages, schema, **kwargs)
        self._count()
        return result

    def _count(self) -> None:
        self.calls += 1
        sink = usage_sink.get()
        if sink is None or sink.input_tokens is None or sink.output_tokens is None:
            self.unreported += 1
        else:
            self.input_tokens += sink.input_tokens
            self.output_tokens += sink.output_tokens

    def usage(self) -> dict[str, Any]:
        complete = self.unreported == 0
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "calls_without_usage": self.unreported,
                # never fabricated: null when a call reported no usage or when no prices are configured
                "estimated_cost_usd": estimate_cost_usd(str(self.model), self.input_tokens, self.output_tokens,
                                                        provider=str(self.name)) if complete else None}


class BudgetedService:
    """One request budget per question (as the HTTP middleware gives each request); provider errors are recorded."""

    def __init__(self, service: Any):
        self._service = service
        self.errors: dict[str, str] = {}

    async def answer(self, ctx, question: str):
        token = limits._request_budget.set(limits.new_request_budget())
        try:
            return await self._service.answer(ctx, question)
        except AIError as error:
            self.errors[question] = error.code
            return SimpleNamespace(answer="", grounded=False, sources=[])
        finally:
            limits._request_budget.reset(token)


def live_suite(report: Any, cases: list[dict], errors: dict[str, str], meter: UsageMeter) -> SuiteResult:
    """Per-case outcomes plus the policy metrics. A case whose model call failed is failed on every check."""
    question = {case["id"]: case["question"] for case in cases}
    outcomes = []
    for result in report.results:
        error = errors.get(question[result.id])
        if error:
            result.citation_valid = False
            result.grounded_ok = False if result.grounded_ok is not None else None
            result.refusal_ok = False if result.refusal_ok is not None else None
        passed = (result.citation_valid and result.retrieval_hit is not False and result.grounded_ok is not False
                  and result.refusal_ok is not False)
        outcomes.append({"id": result.id, "behavior": result.behavior, "passed": passed, "error_code": error,
                         "retrieval_hit": result.retrieval_hit, "citation_valid": result.citation_valid,
                         "grounded_with_fact": result.grounded_ok, "refused": result.refusal_ok,
                         "answer_excerpt": result.answer[:300]})
    suite = from_policy(report)
    return SuiteResult("policy_live", suite.total, suite.passed, suite.metrics, suite.failures, mode="live-model",
                       notes="Real model answers on the synthetic demo policies; retrieval uses offline hash embeddings.",
                       details={"provider": meter.name, "model": meter.model, "usage": meter.usage(), "cases": outcomes})
