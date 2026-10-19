"""Live-model router evaluation (opt-in, costs money; run via `python -m evals.run_evals --live`).

Classifies every case of router_eval.json with the REAL classifier provider (AI_LLM_FAST_MODEL) and compares
the routing plan with the expected route. It never calls the Policy/SQL/Action services and never opens a
database. Most cases are decided by deterministic rules before the model is consulted; `llm_cases` reports how
many actually reached the model. Prints one JSON line (the SuiteResult) on stdout.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from evals.reporting import SuiteResult
from evals.router_eval import load_cases

_OUTCOME_ROUTE = {"clarify": "CLARIFICATION", "refuse": "REFUSAL"}


def planned_route(plan: Any) -> str:
    if plan.outcome in _OUTCOME_ROUTE:
        return _OUTCOME_ROUTE[plan.outcome]
    return "+".join(step.intent.value for step in plan.steps)


async def evaluate_live(provider_factory: Callable[[], Any], cases: list[dict[str, Any]] | None = None) -> SuiteResult:
    from app.services.ai.orchestration.classifier import RouterClassifier

    cases = cases if cases is not None else load_cases()
    passed, failures, llm_cases, outcomes = 0, [], 0, []
    for case in cases:
        classifier = RouterClassifier(llm_factory=provider_factory)
        try:
            route = planned_route(await classifier.plan(case["question"]))
        except Exception as error:  # noqa: BLE001 - a provider failure is a failed case, not a crash
            route = f"error:{type(error).__name__}"
        llm_cases += int(classifier.llm_calls > 0)
        ok = route == case["expected_route"]
        passed += int(ok)
        if not ok:
            failures.append(case["id"])
        outcomes.append({"id": case["id"], "expected_route": case["expected_route"], "route": route, "passed": ok,
                         "reached_model": classifier.llm_calls > 0})
    return SuiteResult("router_live", len(cases), passed, {"route_accuracy": round(passed / len(cases), 4) if cases else 0.0},
                       failures, mode="live-model",
                       notes=f"{llm_cases} of {len(cases)} cases reached the live model; the rest were decided by rules",
                       details={"cases": outcomes})


def main() -> None:
    from app.services.ai.providers import get_fast_llm_provider

    provider = get_fast_llm_provider()  # fails closed (AI_UNAVAILABLE) on a missing key or model: no call is made
    result = asyncio.run(evaluate_live(lambda: provider))
    result.details = {"provider": getattr(provider, "name", None), "model": getattr(provider, "model", None),
                      **(result.details or {})}
    print(json.dumps(result.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
