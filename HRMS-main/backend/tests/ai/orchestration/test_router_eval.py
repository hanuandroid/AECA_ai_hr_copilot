"""Router evaluation dataset: shape/coverage, and a full deterministic run over the real services."""

from evals.reporting import from_router, publish
from evals.router_eval import REQUIRED_CATEGORIES, ROUTES, evaluate, load_cases, run_copilot
from tests.ai.orchestration.conftest import full_llm
from tests.support.db import business_snapshot


def test_dataset_shape_and_coverage():
    cases = load_cases()
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert {"id", "category", "role", "question", "expected_route", "expected_status"} <= set(case)
        assert case["expected_route"] in ROUTES and case["role"] in ("ADMIN", "MANAGER", "EMPLOYEE")
    assert REQUIRED_CATEGORIES <= {case["category"] for case in cases}
    routes = {case["expected_route"] for case in cases}
    assert {"POLICY", "SQL", "ACTION", "POLICY+SQL", "POLICY+ACTION", "CLARIFICATION", "REFUSAL"} <= routes
    assert any(case["expected_status"] == "confirmation_required" for case in cases)
    assert any(case.get("expected_source") == "llm" for case in cases)


async def test_router_eval_passes(real_copilot, ctx_for):
    async def run(case):
        llm = full_llm(router=case.get("classification"), sql=case.get("sql"),
                       action=({"needs_clarification": False, **case["proposal"]} if "proposal" in case else None))
        return await run_copilot(real_copilot(llm), await ctx_for(case["role"].lower()), case["question"])

    report = await evaluate(load_cases(), run, business_snapshot)
    publish(from_router(report, load_cases()))
    failures = [(r.id, r.route, r.status, r.checks) for r in report.failed]
    assert not failures, failures
    assert report.metrics["never_executes"] == 1.0 and report.metrics["business_data_unchanged"] == 1.0
    assert report.passed == len(load_cases())
