"""Run every deterministic AI evaluation and write a machine-readable + human-readable report.

    cd HRMS-main/backend
    .venv/Scripts/python -m evals.run_evals                 # offline, deterministic (default)
    .venv/Scripts/python -m evals.run_evals --live          # + live router classification, only if ANTHROPIC_API_KEY is set

Safety: the suites run in a pytest SUBPROCESS using the test harness, which builds a throwaway SQLite database
(Alembic + test seed) in a temp directory and never opens storage/hrms.db. This module imports nothing from
`app`, so it cannot bind to a real database by accident. Results go to evals/results/ (git-ignored).

Offline results use scripted model output: they validate pipelines and security behaviour, not model quality.
Live results are reported separately and are NEVER faked: without a key the report says LIVE_MODEL_EVAL = NOT_RUN.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from evals.reporting import RESULTS_ENV, render, summarize

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = BACKEND / "evals" / "results"
EVAL_TESTS = (
    "tests/ai/rag/test_policy_eval.py::test_policy_rag_eval_passes",
    "tests/ai/sql/test_sql_eval.py::test_sql_eval_passes",
    "tests/ai/orchestration/test_router_eval.py::test_router_eval_passes",
    "tests/ai/conversation/test_conversation_eval.py::test_conversation_eval_passes",
    "tests/ai/actions/test_action_eval.py::test_action_eval_passes",
    "tests/ai/hardening/test_assignment_eval.py::test_assignment_eval_passes",
)


def run_offline(results_dir: Path) -> tuple[int, list[dict]]:
    with tempfile.TemporaryDirectory(prefix="ai-evals-") as raw:
        env = {**os.environ, RESULTS_ENV: raw, "AI_LLM_PROVIDER": "fake", "AI_EMBEDDING_PROVIDER": "fake"}
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *EVAL_TESTS],
                                   cwd=BACKEND, env=env, capture_output=True, text=True)
        results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(raw).glob("*.json"))]
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "pytest_output.txt").write_text(completed.stdout[-20_000:], encoding="utf-8")
    for result in results:
        (results_dir / f"{result['suite']}.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return completed.returncode, results


def run_live(requested: bool) -> dict:
    if not requested:
        return {"status": "NOT_RUN", "reason": "offline run (use --live with ANTHROPIC_API_KEY to evaluate the live model)"}
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return {"status": "NOT_RUN", "reason": "ANTHROPIC_API_KEY is not set"}
    completed = subprocess.run([sys.executable, "-m", "evals.live_router_eval"], cwd=BACKEND,
                               env={**os.environ, "AI_LLM_PROVIDER": "anthropic"}, capture_output=True, text=True)
    if completed.returncode != 0:
        return {"status": "FAILED", "reason": "live router evaluation did not complete (see stderr)",
                "stderr_tail": completed.stderr[-2000:]}
    return {"status": "RUN", "result": json.loads(completed.stdout.strip().splitlines()[-1])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--live", action="store_true", help="also run the live-model router evaluation (costs money)")
    args = parser.parse_args(argv)

    exit_code, results = run_offline(args.results_dir)
    summary = summarize(results, live=run_live(args.live))
    (args.results_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    text = render(summary)
    (args.results_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    ok = exit_code == 0 and summary["failed"] == 0 and not summary["missing_suites"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
