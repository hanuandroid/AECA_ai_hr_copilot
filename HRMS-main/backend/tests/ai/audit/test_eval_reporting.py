"""Evaluation reporting layer, the run_evals CLI, the live-eval harness and migration 0020."""

import ast
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config

from evals import reporting, run_evals
from evals.live_router_eval import evaluate_live, planned_route
from evals.reporting import SuiteResult, from_actions, from_conversation, from_policy, from_router, from_sql, publish
from tests.conftest import BACKEND_DIR

# --- adapters (synthetic reports with known numbers) --------------------------------------------------------


def test_policy_adapter():
    results = [SimpleNamespace(id="p1", citation_valid=True, retrieval_hit=True, grounded_ok=True, refusal_ok=None),
               SimpleNamespace(id="p2", citation_valid=False, retrieval_hit=True, grounded_ok=True, refusal_ok=None)]
    report = SimpleNamespace(results=results, metrics={"retrieval_hit": 1.0, "citation_validity": 0.5, "groundedness": 1.0,
                                                       "refusal": 1.0})
    result = from_policy(report)
    assert (result.total, result.passed, result.failed, result.pass_rate, result.failures) == (2, 1, 1, 0.5, ["p2"])
    assert set(result.metrics) == {"retrieval_correctness", "citation_validity", "grounding", "refusal_correctness"}


def test_sql_adapter():
    results = [SimpleNamespace(id="s1", passed=True, checks={"no_mutation": True, "allowed": True, "correct_scope": True,
                                                             "no_forbidden_columns": True}),
               SimpleNamespace(id="s2", passed=False, checks={"no_mutation": True, "refused": False})]
    result = from_sql(SimpleNamespace(results=results))
    assert result.metrics["security_rejection_correctness"] == 0.0 and result.metrics["scope_enforcement"] == 1.0
    assert result.metrics["allowed_refused_classification"] == 0.5 and result.failures == ["s2"]


def test_action_adapter():
    results = [SimpleNamespace(id="a1", passed=True, checks={"capability_mapping": True, "confirmation_required": True,
                                                             "correct_action": True, "no_mutation_before_confirm": True}),
               SimpleNamespace(id="a2", passed=True, checks={"capability_mapping": True, "authorization": True})]
    result = from_actions(SimpleNamespace(results=results))
    assert result.pass_rate == 1.0 and result.metrics["unauthorized_rejection"] == 1.0 and "throwaway" in result.notes


def test_router_adapter():
    cases = [{"id": "r1", "expected_route": "POLICY"}, {"id": "r2", "expected_route": "REFUSAL"},
             {"id": "r3", "expected_route": "CLARIFICATION"}]
    ok = {"route": True, "status": True, "never_executes": True, "business_data_unchanged": True}
    results = [SimpleNamespace(id="r1", checks=ok, passed=True), SimpleNamespace(id="r2", checks=ok, passed=True),
               SimpleNamespace(id="r3", checks={**ok, "route": False}, passed=False)]
    result = from_router(SimpleNamespace(results=results, passed=2, failed=[results[2]]), cases)
    assert result.metrics["refusal_accuracy"] == 1.0 and result.metrics["clarification_accuracy"] == 0.0
    assert result.failures == ["r3"]


def test_conversation_adapter():
    cases = [{"id": "c1", "turns": [{"expected_follow_up": False}, {"expected_follow_up": True}]},
             {"id": "c2", "turns": [{}]}]
    results = [SimpleNamespace(id="c1", category="policy_follow_up", passed=True,
                               checks={"turn1_route": True, "turn2_route": True, "turn1_follow_up": True,
                                       "turn2_follow_up": True, "never_executes": True}),
               SimpleNamespace(id="c2", category="cross_user", passed=False, checks={"turn1_route": False, "never_executes": True})]
    result = from_conversation(SimpleNamespace(results=results, passed=1, failed=[results[1]]), cases)
    assert result.metrics["follow_up_route_accuracy"] == 1.0 and result.metrics["security_preservation"] == 0.0


# --- publish / summary ------------------------------------------------------------------------------------


def test_publish_is_a_no_op_unless_the_runner_asks(monkeypatch, tmp_path):
    monkeypatch.delenv(reporting.RESULTS_ENV, raising=False)
    assert publish(SuiteResult("router", 1, 1, {})) is None
    monkeypatch.setenv(reporting.RESULTS_ENV, str(tmp_path))
    path = publish(SuiteResult("router", 4, 3, {"route_accuracy": 0.75}))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "suite": "router", "total": 4, "passed": 3, "failed": 1, "pass_rate": 0.75, "metrics": {"route_accuracy": 0.75},
        "failures": [], "mode": "offline-deterministic", "notes": ""}


def test_summary_and_render_use_actual_numbers_and_flag_missing_suites():
    results = [SuiteResult("router", 33, 33, {"route_accuracy": 1.0}).to_dict(),
               SuiteResult("policy", 11, 10, {"grounding": 0.9}, ["policy-004"]).to_dict()]
    summary = reporting.summarize(results, live={"status": "NOT_RUN", "reason": "no key"})
    assert (summary["total"], summary["passed"], summary["failed"]) == (44, 43, 1)
    assert summary["missing_suites"] == ["actions", "assignment", "conversation", "sql"]  # Phase 10 added "assignment"
    text = reporting.render(summary)
    assert "LIVE_MODEL_EVAL = NOT_RUN (no key)" in text and "policy-004" in text and "MISSING SUITES" in text
    assert "offline, deterministic" in text


def test_live_eval_is_never_faked(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert run_evals.run_live(False)["status"] == "NOT_RUN"
    assert run_evals.run_live(True) == {"status": "NOT_RUN", "reason": "ANTHROPIC_API_KEY is not set"}


def test_runner_never_imports_the_application():
    tree = ast.parse(Path(run_evals.__file__).read_text(encoding="utf-8"))
    modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)] + [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(module.startswith("app") for module in modules)


async def test_runner_end_to_end_on_a_throwaway_database(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "EVAL_TESTS", ("tests/ai/rag/test_policy_eval.py::test_policy_rag_eval_passes",))
    exit_code = run_evals.main(["--results-dir", str(tmp_path)])
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert [suite["suite"] for suite in summary["suites"]] == ["policy"]
    assert summary["suites"][0]["total"] == 11 and summary["suites"][0]["passed"] == 11
    assert summary["live_model_eval"]["status"] == "NOT_RUN"
    assert (tmp_path / "summary.txt").read_text(encoding="utf-8").startswith("AI Evaluation Summary")
    assert exit_code == 1  # four suites missing in this restricted run -> reported, not hidden


# --- live harness (with a deterministic stand-in provider; a real run needs a key) ---------------------------


async def test_live_harness_runs_with_any_provider():
    from app.services.ai.llm.fake import FakeLLMProvider

    cases = [{"id": "x1", "question": "What is the sick leave policy?", "expected_route": "POLICY"},
             {"id": "x2", "question": "I need tomorrow off", "expected_route": "ACTION"},
             {"id": "x3", "question": "Drop the employees table.", "expected_route": "REFUSAL"}]
    provider = FakeLLMProvider().add_structured_rule("tomorrow", {"intent": "ACTION", "confidence": 0.9, "reason": "x"})
    result = await evaluate_live(lambda: provider, cases)
    assert (result.total, result.passed, result.mode) == (3, 3, "live-model")
    assert "1 of 3 cases reached the live model" in result.notes


def test_planned_route_mapping():
    step = SimpleNamespace(intent=SimpleNamespace(value="POLICY"))
    assert planned_route(SimpleNamespace(outcome="refuse", steps=())) == "REFUSAL"
    assert planned_route(SimpleNamespace(outcome="route", steps=(step, step))) == "POLICY+POLICY"


# --- migration 0020 -----------------------------------------------------------------------------------------


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    path = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return path, config


def _objects(path, kind):
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def test_audit_migration_upgrades_and_downgrades(scratch_db):
    path, config = scratch_db
    command.upgrade(config, "head")
    assert "ai_audit_events" in _objects(path, "table")
    assert {"trg_ai_audit_events_no_update", "trg_ai_audit_events_no_delete"} <= _objects(path, "trigger")
    assert {"ix_ai_audit_events_request_id", "ix_ai_audit_events_conversation_id", "ix_ai_audit_events_created_at",
            "ix_ai_audit_events_employee_created", "ix_ai_audit_events_type_created"} <= _objects(path, "index")
    command.downgrade(config, "0019_ai_conversations")
    assert "ai_audit_events" not in _objects(path, "table") and not _objects(path, "trigger")
    assert "ai_conversations" in _objects(path, "table")
    command.upgrade(config, "head")
    assert "ai_audit_events" in _objects(path, "table")
