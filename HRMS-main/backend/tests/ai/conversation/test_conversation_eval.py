"""Conversation evaluation dataset: shape/coverage, and a full deterministic multi-turn run."""

from typing import Any

from app.services.ai.errors import AIError
from evals.reporting import from_conversation, publish
from evals.conversation_eval import REQUIRED_CATEGORIES, TurnOutcome, evaluate, load_cases
from tests.ai.conversation.conftest import by_prompt, script, sql
from tests.ai.conversation.test_conversation_flow import seed_history
from tests.support.db import business_snapshot


def test_dataset_shape_and_coverage():
    cases = load_cases()
    assert len(cases) >= 20 and len({case["id"] for case in cases}) == len(cases)
    assert REQUIRED_CATEGORIES <= {case["category"] for case in cases}
    for case in cases:
        assert case["role"] in ("ADMIN", "MANAGER", "EMPLOYEE") and case["turns"]
        assert len(case["turns"]) + len(case.get("history", [])) >= 2  # every case is multi-turn
        for turn in case["turns"]:
            assert {"user", "expected_route", "expected_status"} <= set(turn)
    assert any(turn.get("as_role") for case in cases for turn in case["turns"])
    assert any(turn.get("expected_follow_up") for case in cases for turn in case["turns"])


def _llm(case: dict[str, Any]):
    spec = case.get("llm", {})
    sql_rules = by_prompt(*[(needle, sql(statement)) for needle, statement in spec["sql"]]) if "sql" in spec else None
    action = by_prompt(*[(needle, {"needs_clarification": False, "arguments": {}, **proposal})
                         for needle, proposal in spec["action"]]) if "action" in spec else None
    return script(router=spec.get("router"), sql_rules=sql_rules, action=action)


async def test_conversation_eval_passes(chat_service, ctx_for, db):
    services: dict[str, Any] = {}

    def service_for(case):
        if case["id"] not in services:
            services[case["id"]] = chat_service(_llm(case))
        return services[case["id"]]

    async def seed(case):
        if not case.get("history"):
            return None
        turns = [(h["user"], h["assistant"], h["route"], h["status"]) for h in case["history"]]
        return await seed_history(db, await ctx_for(case["role"].lower()), *turns)

    async def run_turn(case, turn, conversation_id):
        ctx = await ctx_for(turn.get("as_role", case["role"]).lower())
        service = service_for(case)
        service._copilot.last_state = None  # a turn rejected before routing must not report the previous turn
        try:
            result = await service.handle(ctx, turn["user"], conversation_id)
        except AIError as error:
            state = service._copilot.last_state or {}
            routes = "+".join(part["route"] for part in state.get("parts") or []) or "NONE"
            return TurnOutcome(route=routes, status=f"error:{error.code}", follow_up=bool(state.get("follow_up")),
                               source=state.get("classification_source"),
                               conversation_id=getattr(error, "conversation_id", None))
        data = result.parts[0].data or {}
        return TurnOutcome(route="+".join(result.routes), status=result.status, follow_up=result.follow_up,
                           source=result.classification_source, scope=data.get("scope"),
                           executed=any(part.status == "completed" for part in result.parts),
                           conversation_id=result.conversation_id)

    report = await evaluate(load_cases(), seed, run_turn, business_snapshot)
    publish(from_conversation(report, load_cases()))
    failures = [(r.id, {k: v for k, v in r.checks.items() if not v}, [(o.route, o.status, o.follow_up) for o in r.outcomes])
                for r in report.failed]
    assert not failures, failures
    assert report.passed == len(load_cases()) and report.turns >= 40
