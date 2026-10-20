"""Run every deterministic AI evaluation and write a machine-readable + human-readable report.

    cd HRMS-main/backend
    .venv/Scripts/python -m evals.run_evals                 # offline, deterministic (default)
    .venv/Scripts/python -m evals.run_evals --live          # + live-model suites (costs money; needs approval)

Safety: the suites run in a pytest SUBPROCESS using the test harness, which builds a throwaway SQLite database
(Alembic + test seed) in a temp directory and never opens storage/hrms.db. This module imports nothing from
`app`, so it cannot bind to a real database by accident. Results go to evals/results/ (git-ignored).

Offline results use scripted model output: they validate pipelines and security behaviour, not model quality.
Live results are reported separately and are NEVER faked. The live provider is taken from the PROCESS environment
only and passed explicitly to every live subprocess, so a live run never switches provider:
- AI_LLM_PROVIDER=anthropic (the application default when unset) needs ANTHROPIC_API_KEY; the model is AI_LLM_MODEL
  or the application default;
- AI_LLM_PROVIDER=openai needs OPENAI_API_KEY and AI_OPENAI_MODEL;
- anything else (including `fake`) is NOT_RUN or CONFIG_ERROR, and no live call is made.
Live suites: router classification (evals/live_router_eval.py) and Policy RAG answer quality
(tests/ai/rag/test_policy_eval_live.py: retrieval, citation validity, grounding/correctness, refusal). Every model call
still passes the provider boundary (provider_policy) and a per-case request budget.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
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


LIVE_PROVIDERS = {
    # provider: (key variable, model variable, model required)
    "anthropic": ("ANTHROPIC_API_KEY", "AI_LLM_MODEL", False),
    "openai": ("OPENAI_API_KEY", "AI_OPENAI_MODEL", True),
}
LIVE_TESTS = ("tests/ai/rag/test_policy_eval_live.py::test_policy_rag_live_eval",)
LIVE_FLAG, LIVE_PROVIDER_ENV = "AI_LIVE_EVAL", "AI_LIVE_EVAL_PROVIDER"


def select_live_provider(env: Mapping[str, str]) -> dict:
    """Which live provider a run would use, or why none can be used. Reads names only; never returns a key."""
    provider = (env.get("AI_LLM_PROVIDER") or "anthropic").strip().lower()
    if provider == "fake":
        return {"status": "NOT_RUN", "reason": "AI_LLM_PROVIDER=fake gives scripted output, not a live model"}
    if provider not in LIVE_PROVIDERS:
        return {"status": "CONFIG_ERROR",
                "reason": f"unsupported AI_LLM_PROVIDER for a live run: {provider[:40]!r} (use anthropic or openai)"}
    key_var, model_var, model_required = LIVE_PROVIDERS[provider]
    if not (env.get(key_var) or "").strip():
        return {"status": "NOT_RUN", "reason": f"{key_var} is not set"}
    model = (env.get(model_var) or "").strip()
    if model_required and not model:
        return {"status": "NOT_RUN", "reason": f"{model_var} is not set"}
    return {"status": "READY", "provider": provider, "model": model or "application default"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_live(requested: bool, env: Mapping[str, str] | None = None) -> dict:
    if not requested:
        return {"status": "NOT_RUN", "reason": "offline run (use --live with a configured provider to evaluate the live model)"}
    env = dict(os.environ if env is None else env)
    selection = select_live_provider(env)
    if selection["status"] != "READY":
        return selection
    provider = selection["provider"]
    # The selected provider is passed explicitly, so neither subprocess can fall back to another one.
    child_env = {**env, "AI_LLM_PROVIDER": provider, LIVE_FLAG: "1", LIVE_PROVIDER_ENV: provider}
    report: dict = {"status": "RUN", "provider": provider, "model": selection["model"], "started_at": _now(),
                    "suites": [], "errors": []}
    if provider == "openai":
        # The router suite reads settings (and therefore backend/.env); pin its model in the environment, which wins
        # over .env, so a live run uses only the models chosen in the process environment.
        child_env["AI_OPENAI_FAST_MODEL"] = (env.get("AI_OPENAI_FAST_MODEL") or "").strip() or selection["model"]
        report["fast_model"] = child_env["AI_OPENAI_FAST_MODEL"]

    router = subprocess.run([sys.executable, "-m", "evals.live_router_eval"], cwd=BACKEND, env=child_env,
                            capture_output=True, text=True)
    if router.returncode == 0 and router.stdout.strip():
        report["suites"].append(json.loads(router.stdout.strip().splitlines()[-1]))
    else:
        report["errors"].append("live router evaluation did not complete")
    with tempfile.TemporaryDirectory(prefix="ai-live-evals-") as raw:
        policy = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *LIVE_TESTS],
                                cwd=BACKEND, env={**child_env, RESULTS_ENV: raw}, capture_output=True, text=True)
        written = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(raw).glob("*.json"))]
    if policy.returncode == 0 and written:
        report["suites"].extend(written)
    else:
        report["errors"].append("live policy answer evaluation did not complete")

    for suite in report["suites"]:  # each suite reports the provider it actually used
        used = (suite.get("details") or {}).get("provider")
        if used != provider:
            report["errors"].append(f"suite {suite.get('suite')} used provider {used!r}, expected {provider!r}")
    report["finished_at"] = _now()
    if report["errors"]:
        report["status"] = "FAILED"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--live", action="store_true",
                        help="also run the live-model suites (external calls that cost money; run only with approval)")
    args = parser.parse_args(argv)

    exit_code, results = run_offline(args.results_dir)
    live = run_live(args.live)
    summary = summarize(results, live=live)
    (args.results_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    text = render(summary)
    (args.results_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    ok = exit_code == 0 and summary["failed"] == 0 and not summary["missing_suites"]
    # A requested live run that could not complete is an error; NOT_RUN (no key) is reported, not an error.
    live_ok = live["status"] not in {"FAILED", "CONFIG_ERROR"}
    return 0 if ok and live_ok else 1


if __name__ == "__main__":
    sys.exit(main())
