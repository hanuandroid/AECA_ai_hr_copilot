"""Deterministic copilot-router evaluation (foundation for Phase 10).

Each case states the expected route (POLICY, SQL, ACTION, POLICY+SQL, POLICY+ACTION, CLARIFICATION,
REFUSAL) and outcome. Only the model is scripted (classifier output when rules cannot decide, the SQL
text and the action proposal); routing, the specialised services and their security pipelines run
for real. Every case also checks that business data did not change: the router never executes.

This measures the routing and safety pipeline, not production LLM intent accuracy.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.ai.errors import AIError

DATASET = Path(__file__).with_name("router_eval.json")
ROUTES = {"POLICY", "SQL", "ACTION", "POLICY+SQL", "POLICY+ACTION", "SQL+ACTION", "POLICY+SQL+ACTION", "CLARIFICATION", "REFUSAL"}
REQUIRED_CATEGORIES = {"policy", "sql", "action", "ambiguous", "unsupported", "injection", "multi_intent", "confirmation_required"}


@dataclass
class RouterRun:
    route: str
    status: str  # answered | clarification_required | confirmation_required | refused | partial | error:<CODE>
    source: str | None
    executed: bool  # any part reported an executed (completed) action


@dataclass
class RouterCaseResult:
    id: str
    category: str
    passed: bool
    checks: dict[str, bool]
    route: str
    status: str


@dataclass
class RouterEvalReport:
    results: list[RouterCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed(self) -> list[RouterCaseResult]:
        return [result for result in self.results if not result.passed]

    @property
    def metrics(self) -> dict[str, float]:
        names = sorted({name for result in self.results for name in result.checks})
        return {name: round(sum(r.checks[name] for r in self.results) / max(1, len(self.results)), 3) for name in names}

    @property
    def by_category(self) -> dict[str, tuple[int, int]]:
        summary: dict[str, tuple[int, int]] = {}
        for result in self.results:
            passed, total = summary.get(result.category, (0, 0))
            summary[result.category] = (passed + result.passed, total + 1)
        return summary


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


async def run_copilot(service: Any, ctx: Any, question: str) -> RouterRun:
    """Run one question through a CopilotService and normalise the outcome (including errors)."""
    try:
        response = await service.answer(ctx, question)
    except AIError as error:
        state = service.last_state or {}
        parts = state.get("parts") or []
        return RouterRun(route="+".join(part["route"] for part in parts) or "NONE", status=f"error:{error.code}",
                         source=state.get("classification_source"),
                         executed=any(part.get("status") == "completed" for part in parts))
    return RouterRun(route="+".join(response.routes), status=response.status, source=response.classification_source,
                     executed=any(part.status == "completed" for part in response.parts))


async def evaluate(
    cases: list[dict[str, Any]],
    run: Callable[[dict[str, Any]], Awaitable[RouterRun]],
    business_snapshot: Callable[[], str],
) -> RouterEvalReport:
    report = RouterEvalReport()
    for case in cases:
        before = business_snapshot()
        outcome = await run(case)
        checks = {
            "route": outcome.route == case["expected_route"],
            "status": outcome.status == case["expected_status"],
            "classification_source": case.get("expected_source") is None or outcome.source == case["expected_source"],
            "never_executes": not outcome.executed,
            "business_data_unchanged": business_snapshot() == before,
        }
        report.results.append(RouterCaseResult(case["id"], case["category"], all(checks.values()), checks, outcome.route,
                                               outcome.status))
    return report
