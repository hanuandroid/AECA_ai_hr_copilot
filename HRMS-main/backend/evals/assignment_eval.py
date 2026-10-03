"""Formal assignment evaluation set (Phase 10): the assignment's suggested prompts, end to end.

Each case gives the input, role, expected route, expected behaviour and security expectations (format modelled on
the assignment's "Evaluation Dataset" bonus). A case runs through the real conversation layer, router, Policy RAG,
SQL agent and Action agent on a throwaway database; only the model output is scripted (SQL text, action proposal)
and policy answers are extracted from the retrieved policy text. Actions are only PROPOSED here: business data
must stay unchanged for every case.

This is pipeline/security evaluation, NOT live-model quality evaluation.

Behaviours: answer_with_source | answer_with_rows | confirmation_required | clarification | refuse | error:<CODE>
Security expectations: no_mutation | not_executed | no_sensitive_values
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET = Path(__file__).with_name("assignment_eval.json")
CATEGORIES = ("policy", "sql", "action", "security", "conversation")


@dataclass
class TurnOutcome:
    route: str
    status: str  # answered | clarification_required | confirmation_required | refused | partial | error:<CODE>
    text: str = ""
    sources: int = 0
    rows: list[dict[str, Any]] | None = None
    action: str | None = None
    follow_up: bool | None = None
    executed: bool = False
    conversation_id: str | None = None


@dataclass
class AssignmentCaseResult:
    id: str
    category: str
    passed: bool
    checks: dict[str, bool]


@dataclass
class AssignmentReport:
    results: list[AssignmentCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def failed(self) -> list[AssignmentCaseResult]:
        return [r for r in self.results if not r.passed]


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def turns_of(case: dict[str, Any]) -> list[dict[str, Any]]:
    return case.get("turns") or [{key: case[key] for key in case if key in (
        "input", "expected_route", "expected_behavior", "expected_rows", "expected_action", "expected_answer_contains")}]


def _behaviour_ok(turn: dict[str, Any], outcome: TurnOutcome) -> bool:
    expected = turn["expected_behavior"]
    if expected.startswith("error:"):
        return outcome.status == expected
    if expected == "answer_with_source":
        ok = outcome.status == "answered" and outcome.sources > 0
        return ok and turn.get("expected_answer_contains", "") in outcome.text
    if expected == "answer_with_rows":
        return outcome.status == "answered" and outcome.rows is not None and (
            "expected_rows" not in turn or outcome.rows == turn["expected_rows"])
    if expected == "confirmation_required":
        return outcome.status == "confirmation_required" and outcome.action == turn.get("expected_action", outcome.action)
    if expected == "clarification":
        return outcome.status == "clarification_required"
    if expected == "refuse":
        return outcome.status == "refused"
    return False


async def evaluate(
    cases: list[dict[str, Any]],
    run_turn: Callable[[dict[str, Any], dict[str, Any], str | None], Awaitable[TurnOutcome]],
    business_snapshot: Callable[[], str],
    sensitive_values: list[str],
) -> AssignmentReport:
    report = AssignmentReport()
    for case in cases:
        before = business_snapshot()
        checks: dict[str, bool] = {}
        conversation_id: str | None = None
        outcomes: list[TurnOutcome] = []
        for index, turn in enumerate(turns_of(case), start=1):
            outcome = await run_turn(case, turn, conversation_id)
            outcomes.append(outcome)
            conversation_id = conversation_id or outcome.conversation_id
            checks[f"turn{index}_route"] = outcome.route == turn["expected_route"]
            checks[f"turn{index}_behavior"] = _behaviour_ok(turn, outcome)
            if "expected_follow_up" in turn:
                checks[f"turn{index}_follow_up"] = outcome.follow_up == turn["expected_follow_up"]
        expectations = case.get("security_expectation", [])
        if "no_mutation" in expectations:
            checks["security_no_mutation"] = business_snapshot() == before
        if "not_executed" in expectations:
            checks["security_not_executed"] = not any(o.executed for o in outcomes)
        if "no_sensitive_values" in expectations:
            text = " ".join(o.text + json.dumps(o.rows or []) for o in outcomes)
            checks["security_no_sensitive_values"] = not any(value in text for value in sensitive_values)
        report.results.append(AssignmentCaseResult(case["id"], case["category"], all(checks.values()), checks))
    return report
