"""Common evaluation reporting (Phase 8): one SuiteResult shape for every deterministic suite.

The existing evaluators are unchanged; small adapters turn their reports into SuiteResult. The eval tests
call `publish(...)`, which writes `<suite>.json` only when AI_EVAL_RESULTS_DIR is set (by evals/run_evals.py),
so normal test runs write nothing.

All numbers come from actual runs. Offline suites use scripted model output on a throwaway database; they
measure the pipelines and security behaviour, NOT live model quality.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

RESULTS_ENV = "AI_EVAL_RESULTS_DIR"
OFFLINE = "offline-deterministic"
SUITES = ("policy", "sql", "router", "conversation", "actions", "assignment")


@dataclass
class SuiteResult:
    suite: str
    total: int
    passed: int
    metrics: dict[str, float]
    failures: list[str] = field(default_factory=list)
    mode: str = OFFLINE
    notes: str = ""

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "failed": self.failed, "pass_rate": self.pass_rate}


def _rate(values: list[bool]) -> float:
    return round(sum(values) / len(values), 4) if values else 1.0


# --- adapters ------------------------------------------------------------------------------------


def from_policy(report: Any) -> SuiteResult:
    def ok(r) -> bool:
        return r.citation_valid and r.retrieval_hit is not False and r.grounded_ok is not False and r.refusal_ok is not False

    metrics = report.metrics
    return SuiteResult("policy", len(report.results), sum(ok(r) for r in report.results),
                       {"retrieval_correctness": metrics["retrieval_hit"], "citation_validity": metrics["citation_validity"],
                        "grounding": metrics["groundedness"], "refusal_correctness": metrics["refusal"]},
                       [r.id for r in report.results if not ok(r)])


def from_sql(report: Any) -> SuiteResult:
    results = report.results
    allowed = [r for r in results if "allowed" in r.checks]
    refused = [r for r in results if "refused" in r.checks]
    return SuiteResult("sql", len(results), sum(r.passed for r in results), {
        "allowed_refused_classification": _rate([r.checks.get("allowed", r.checks.get("refused", False)) for r in results]),
        "security_rejection_correctness": _rate([r.checks["refused"] for r in refused]),
        "scope_enforcement": _rate([r.checks["correct_scope"] for r in allowed if "correct_scope" in r.checks]),
        "no_forbidden_columns": _rate([r.checks["no_forbidden_columns"] for r in allowed if "no_forbidden_columns" in r.checks]),
        "no_mutation": _rate([r.checks["no_mutation"] for r in results]),
    }, [r.id for r in results if not r.passed])


def from_actions(report: Any) -> SuiteResult:
    results = report.results
    checks = lambda name: [r.checks[name] for r in results if name in r.checks]  # noqa: E731
    return SuiteResult("actions", len(results), sum(r.passed for r in results), {
        "proposal_correctness": _rate(checks("correct_action") + checks("capability_mapping")),
        "confirmation_requirement": _rate(checks("confirmation_required")),
        "unauthorized_rejection": _rate(checks("authorization")),
        "no_mutation_before_confirmation": _rate(checks("no_mutation_before_confirm")),
    }, [r.id for r in results if not r.passed],
        notes="Confirmed cases execute through the real HRMS API on the throwaway evaluation database only.")


def from_router(report: Any, cases: list[dict[str, Any]]) -> SuiteResult:
    expected = {case["id"]: case for case in cases}
    results = report.results

    def accuracy(route: str) -> float:
        return _rate([r.checks["route"] and r.checks["status"] for r in results if expected[r.id]["expected_route"] == route])

    return SuiteResult("router", len(results), report.passed, {
        "route_accuracy": _rate([r.checks["route"] for r in results]),
        "status_accuracy": _rate([r.checks["status"] for r in results]),
        "clarification_accuracy": accuracy("CLARIFICATION"),
        "refusal_accuracy": accuracy("REFUSAL"),
        "never_executes": _rate([r.checks["never_executes"] for r in results]),
        "business_data_unchanged": _rate([r.checks["business_data_unchanged"] for r in results]),
    }, [r.id for r in report.failed])


SECURITY_CATEGORIES = {"prompt_injection", "privilege_escalation", "cross_user", "confirmation_continuation", "history_not_truth"}


def from_conversation(report: Any, cases: list[dict[str, Any]]) -> SuiteResult:
    by_id = {case["id"]: case for case in cases}
    follow_up_routes, resolution = [], []
    for result in report.results:
        for index, turn in enumerate(by_id[result.id]["turns"], start=1):
            if turn.get("expected_follow_up"):
                follow_up_routes.append(result.checks[f"turn{index}_route"])
            if f"turn{index}_follow_up" in result.checks:
                resolution.append(result.checks[f"turn{index}_follow_up"])
    security = [r.passed for r in report.results if r.category in SECURITY_CATEGORIES]
    return SuiteResult("conversation", len(report.results), report.passed, {
        "follow_up_route_accuracy": _rate(follow_up_routes),
        "context_resolution": _rate(resolution),
        "security_preservation": _rate(security),
        "never_executes": _rate([r.checks["never_executes"] for r in report.results]),
    }, [r.id for r in report.failed])


def from_assignment(report: Any) -> SuiteResult:
    """Formal assignment set: pass rate per category plus the security expectations across all cases."""
    metrics: dict[str, float] = {}
    for category in ("policy", "sql", "action", "security", "conversation"):
        metrics[f"{category}_cases"] = _rate([r.passed for r in report.results if r.category == category])
    for name in ("security_no_mutation", "security_not_executed", "security_no_sensitive_values"):
        values = [r.checks[name] for r in report.results if name in r.checks]
        if values:
            metrics[name.replace("security_", "")] = _rate(values)
    return SuiteResult("assignment", len(report.results), report.passed, metrics, [r.id for r in report.failed],
                       notes="Assignment's suggested prompts end to end; actions are proposed only (never executed).")


# --- output ---------------------------------------------------------------------------------------


def publish(result: SuiteResult) -> Path | None:
    """Write <suite>.json when the runner requested results; no-op in normal test runs."""
    directory = os.environ.get(RESULTS_ENV)
    if not directory:
        return None
    path = Path(directory) / f"{result.suite}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def summarize(results: list[dict[str, Any]], *, live: dict[str, Any]) -> dict[str, Any]:
    total = sum(r["total"] for r in results)
    passed = sum(r["passed"] for r in results)
    missing = sorted(set(SUITES) - {r["suite"] for r in results})
    return {"mode": OFFLINE, "suites": sorted(results, key=lambda r: SUITES.index(r["suite"]) if r["suite"] in SUITES else 99),
            "missing_suites": missing, "total": total, "passed": passed, "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0, "live_model_eval": live}


def render(summary: dict[str, Any]) -> str:
    lines = ["AI Evaluation Summary (offline, deterministic: scripted model output, throwaway database)",
             "-" * 86]
    for suite in summary["suites"]:
        lines.append(f"{suite['suite'].title():<14} Cases: {suite['total']:<4} Passed: {suite['passed']:<4} "
                     f"Failed: {suite['failed']:<3} Pass rate: {suite['pass_rate'] * 100:.1f}%")
        for name, value in suite["metrics"].items():
            lines.append(f"    {name:<34} {value * 100:.1f}%")
        if suite["failures"]:
            lines.append(f"    failures: {', '.join(suite['failures'])}")
    if summary["missing_suites"]:
        lines.append(f"MISSING SUITES: {', '.join(summary['missing_suites'])}")
    lines.append("-" * 86)
    lines.append(f"Overall        Cases: {summary['total']}  Passed: {summary['passed']}  Failed: {summary['failed']}  "
                 f"Pass rate: {summary['pass_rate'] * 100:.1f}%")
    live = summary["live_model_eval"]
    lines.append(f"LIVE_MODEL_EVAL = {live['status']}" + (f" ({live['reason']})" if live.get("reason") else ""))
    return "\n".join(lines)
