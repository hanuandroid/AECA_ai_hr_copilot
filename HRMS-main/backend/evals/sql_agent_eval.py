"""Deterministic SQL-agent evaluation (foundation for Phase 10).

Each case scripts the SQL the (untrusted) generator returns and checks the pipeline:
parses / select-only / correct tables / no forbidden columns / correct scope / correct result /
expected refusal code / no data mutation. It does NOT measure how well a real model writes SQL -
that needs real-provider runs in the Evals phase.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlglot import exp

from app.services.ai.errors import AIError
from app.services.ai.sql.parser import parse_select
from app.services.ai.sql.schema import TABLES

DATASET = Path(__file__).with_name("sql_eval.json")


@dataclass
class SQLCaseResult:
    id: str
    passed: bool
    checks: dict[str, bool]
    outcome: str


@dataclass
class SQLEvalReport:
    results: list[SQLCaseResult] = field(default_factory=list)

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


def _referenced_tables(sql: str) -> set[str] | None:
    try:
        return {t.name.lower() for t in parse_select(sql).find_all(exp.Table) if t.name.lower() in TABLES}
    except AIError:
        return None


async def evaluate(
    cases: list[dict[str, Any]],
    ask: Callable[[str, str, str], Awaitable[Any]],
    snapshot: Callable[[], str],
) -> SQLEvalReport:
    """`ask(role_key, question, generated_sql)` runs the real SQL agent with a scripted generator."""
    report = SQLEvalReport()
    for case in cases:
        before = snapshot()
        checks: dict[str, bool] = {}
        try:
            answer = await ask(case["role"].lower(), case["question"], case["generated_sql"])
            outcome = "ALLOW"
        except AIError as error:
            answer, outcome = None, error.code
        checks["no_mutation"] = snapshot() == before
        if case["expected_behavior"] == "ALLOW":
            checks["allowed"] = answer is not None
            tables = _referenced_tables(case["generated_sql"])
            checks["parses_select_only"] = tables is not None
            checks["correct_tables"] = tables == set(case["expected_tables"])
            if answer is not None:
                checks["correct_scope"] = answer.scope == case["expected_scope"]
                checks["no_forbidden_columns"] = not set(case["forbidden_columns"]) & set(answer.columns)
                if "expected_rows" in case:
                    checks["correct_result"] = answer.rows == case["expected_rows"]
                if "expected_row_count" in case:
                    checks["correct_result"] = answer.row_count == case["expected_row_count"]
        else:
            checks["refused"] = outcome == case["expected_error"]
        report.results.append(SQLCaseResult(case["id"], all(checks.values()), checks, outcome))
    return report
