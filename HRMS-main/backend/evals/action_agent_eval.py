"""Deterministic action-agent evaluation (foundation for Phase 10).

Each case scripts the model's proposal and checks the pipeline: correct action/capability,
authorization decision, confirmation requirement, execution result through the REAL business API,
and business data unchanged for every non-completed outcome. This validates the security and
execution pipeline, not production LLM intent accuracy.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.ai.actions.registry import ACTION_REGISTRY
from app.services.ai.errors import AIError

DATASET = Path(__file__).with_name("action_eval.json")


@dataclass
class ActionCaseResult:
    id: str
    passed: bool
    checks: dict[str, bool]
    outcome: str


@dataclass
class ActionEvalReport:
    results: list[ActionCaseResult] = field(default_factory=list)

    @property
    def metrics(self) -> dict[str, float]:
        names = sorted({name for r in self.results for name in r.checks})
        return {
            name: round(sum(r.checks[name] for r in self.results if name in r.checks)
                        / max(1, sum(1 for r in self.results if name in r.checks)), 3)
            for name in names
        }


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


async def evaluate(
    cases: list[dict[str, Any]],
    propose: Callable[[str, dict[str, Any], str], Awaitable[Any]],
    confirm: Callable[[str, str], Awaitable[Any]],
    business_snapshot: Callable[[], str],
) -> ActionEvalReport:
    report = ActionEvalReport()
    for case in cases:
        checks: dict[str, bool] = {}
        role_key = case["role"].lower()
        before = business_snapshot()
        spec = ACTION_REGISTRY.get(case["expected_action"])  # type: ignore[call-overload]
        checks["capability_mapping"] = spec is not None and spec.capability.value == case["expected_capability"]
        try:
            proposed = await propose(role_key, case["proposal"], case["question"])
            outcome = proposed.status
        except AIError as error:
            proposed, outcome = None, error.code

        if case["expected_result"] in ("denied",):
            checks["authorization"] = outcome == case["expected_error"]
            checks["no_confirmation"] = proposed is None
        elif case["expected_result"] == "clarification":
            checks["clarification"] = outcome == "clarification_required"
            checks["no_confirmation"] = proposed is not None and proposed.confirmation_id is None
        else:
            checks["confirmation_required"] = outcome == "confirmation_required" and bool(proposed.confirmation_id)
            checks["correct_action"] = proposed is not None and proposed.action == case["expected_action"]
            checks["no_mutation_before_confirm"] = business_snapshot() == before
            if checks["confirmation_required"]:
                result = await confirm(role_key, proposed.confirmation_id)
                outcome = result.status if result.status == "completed" else result.error_code
                expected = "completed" if case["expected_result"] == "completed" else case["expected_error"]
                checks["execution_result"] = outcome == expected
        if case["expected_result"] != "completed":
            checks["business_data_unchanged"] = business_snapshot() == before
        report.results.append(ActionCaseResult(case["id"], all(checks.values()), checks, str(outcome)))
    return report
