"""Deterministic evaluation of the required policy questions (evals/policy_eval.json)."""

from evals.policy_rag_eval import ExtractiveFakeLLM, evaluate, load_cases
from evals.reporting import from_policy, publish

REQUIRED_QUESTIONS = {
    "What is the leave policy?",
    "How many sick leaves can I take?",
    "Can I work from home?",
    "What happens if I am late?",
    "Can I take a half-day leave?",
}


def test_dataset_covers_required_questions_and_records_evidence():
    cases = load_cases()
    assert REQUIRED_QUESTIONS <= {case["question"] for case in cases}
    for case in cases:
        assert case["expected_behavior"] in {"answer_with_source", "refuse_insufficient"}
        assert case["expected_evidence"]
        if case["expected_behavior"] == "answer_with_source":
            assert case["expected_sources"]


async def test_policy_rag_eval_passes(ctx_for, make_service, retriever, ingested):
    llm = ExtractiveFakeLLM()
    service, _, _ = make_service(llm)
    contexts = {role: await ctx_for(role.lower()) for role in ("EMPLOYEE", "MANAGER", "ADMIN")}
    report = await evaluate(load_cases(), service, retriever, contexts)
    publish(from_policy(report))  # only writes when evals/run_evals.py asks for results

    failures = [r for r in report.results if r.retrieval_hit is False or r.grounded_ok is False or r.refusal_ok is False or not r.citation_valid]
    assert failures == [], failures
    assert report.metrics == {"retrieval_hit": 1.0, "citation_validity": 1.0, "groundedness": 1.0, "refusal": 1.0}


async def test_sick_leave_answer_is_grounded_in_code_backed_fact(ctx_for, make_service, ingested):
    service, _, _ = make_service(ExtractiveFakeLLM())
    answer = await service.answer(await ctx_for("employee"), "How many sick leaves can I take?")
    assert answer.grounded and "10" in answer.answer
    assert answer.sources[0].section in {"Sick Leave", "Leave Types and Annual Entitlement"}
