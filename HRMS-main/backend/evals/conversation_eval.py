"""Deterministic multi-turn conversation evaluation (foundation for Phase 10).

Each case is a sequence of turns in ONE conversation, optionally preceded by seeded (possibly
malicious) history and optionally including a turn sent by another user. Only the model output is
scripted; conversation storage, follow-up resolution, routing, the specialised services and their
security pipelines run for real. Every case also checks that business data never changed (the
copilot only proposes actions) and that no turn reported an executed action.

This measures follow-up resolution and the safety pipeline, not production LLM behaviour.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET = Path(__file__).with_name("conversation_eval.json")
REQUIRED_CATEGORIES = {"policy_follow_up", "sql_follow_up", "action_clarification", "pronouns", "ambiguous_follow_up",
                       "cross_topic", "prompt_injection", "privilege_escalation", "cross_user",
                       "confirmation_continuation"}


@dataclass
class TurnOutcome:
    route: str  # "+".join(routes) | NONE (rejected before routing)
    status: str  # answered | clarification_required | confirmation_required | refused | partial | error:<CODE>
    follow_up: bool | None = None
    source: str | None = None
    scope: str | None = None
    executed: bool = False
    conversation_id: str | None = None


@dataclass
class ConversationCaseResult:
    id: str
    category: str
    passed: bool
    checks: dict[str, bool]
    outcomes: list[TurnOutcome]


@dataclass
class ConversationEvalReport:
    results: list[ConversationCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed(self) -> list[ConversationCaseResult]:
        return [result for result in self.results if not result.passed]

    @property
    def turns(self) -> int:
        return sum(len(result.outcomes) for result in self.results)

    @property
    def by_category(self) -> dict[str, tuple[int, int]]:
        summary: dict[str, tuple[int, int]] = {}
        for result in self.results:
            passed, total = summary.get(result.category, (0, 0))
            summary[result.category] = (passed + result.passed, total + 1)
        return summary


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


TurnRunner = Callable[[dict[str, Any], dict[str, Any], str | None], Awaitable[TurnOutcome]]


async def evaluate(
    cases: list[dict[str, Any]],
    seed: Callable[[dict[str, Any]], Awaitable[str | None]],
    run_turn: TurnRunner,
    business_snapshot: Callable[[], str],
) -> ConversationEvalReport:
    """`seed(case)` stores the case's history and returns its conversation id (or None);
    `run_turn(case, turn, conversation_id)` sends one turn as `turn.get("as_role", case["role"])`."""
    report = ConversationEvalReport()
    for case in cases:
        before = business_snapshot()
        conversation_id = await seed(case)
        checks: dict[str, bool] = {}
        outcomes: list[TurnOutcome] = []
        for index, turn in enumerate(case["turns"], start=1):
            outcome = await run_turn(case, turn, conversation_id)
            outcomes.append(outcome)
            if conversation_id is None and "as_role" not in turn:
                conversation_id = outcome.conversation_id
            checks[f"turn{index}_route"] = outcome.route == turn["expected_route"]
            checks[f"turn{index}_status"] = outcome.status == turn["expected_status"]
            if "expected_follow_up" in turn:
                checks[f"turn{index}_follow_up"] = outcome.follow_up == turn["expected_follow_up"]
            if "expected_source" in turn:
                checks[f"turn{index}_source"] = outcome.source == turn["expected_source"]
            if "expected_scope" in turn:
                checks[f"turn{index}_scope"] = outcome.scope == turn["expected_scope"]
        checks["never_executes"] = not any(outcome.executed for outcome in outcomes)
        checks["business_data_unchanged"] = business_snapshot() == before
        report.results.append(ConversationCaseResult(case["id"], case["category"], all(checks.values()), checks, outcomes))
    return report
