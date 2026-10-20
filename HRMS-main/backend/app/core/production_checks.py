"""Fail-fast production configuration checks (Phase 10).

Only enforced when ENVIRONMENT is "production"/"prod": development and tests keep their defaults. The checks
never read or log secret values; they only report which setting is unsafe.
"""

import os
import sys
from collections.abc import Mapping, Sequence

from app.core.config import Settings, split_csv

PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})
INSECURE_JWT_SECRETS = frozenset({"", "change_me", "changeme", "secret", "test-only-secret-not-for-production"})
# Variables that make LangChain/LangSmith (installed transitively by LangGraph) export traces - which can contain
# prompts and HR data - to an external service (production hardening B-16).
EXTERNAL_TRACING_VARIABLES = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_API_KEY",
                              "LANGCHAIN_API_KEY")


def configured_workers(environ: Mapping[str, str], argv: Sequence[str]) -> int:
    """Worker count from uvicorn's --workers flag or WEB_CONCURRENCY (uvicorn's default for --workers)."""
    counts = [1]
    for index, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--workers=") else (argv[index + 1] if arg == "--workers" and index + 1 < len(argv) else None)
        if value is not None and value.strip().isdigit():
            counts.append(int(value))
    if environ.get("WEB_CONCURRENCY", "").strip().isdigit():
        counts.append(int(environ["WEB_CONCURRENCY"]))
    return max(counts)


def production_problems(config: Settings, environ: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> list[str]:
    """Unsafe settings for a production deployment (empty list = OK)."""
    environ = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    problems: list[str] = []
    workers = configured_workers(environ, argv)
    if workers > 1:
        problems.append(f"In-memory AI rate limits and budgets require a single worker ({workers} configured); run one worker "
                        "per instance and one instance, or introduce shared rate-limit state first")
    hosts = split_csv(config.allowed_hosts)
    if not hosts or any("*" in host for host in hosts):
        problems.append("ALLOWED_HOSTS must list the exact public host names (no wildcards)")
    origins = split_csv(config.cors_allow_origins)
    if any("*" in origin for origin in origins):
        problems.append("CORS_ALLOW_ORIGINS must list exact origins (no wildcards)")
    enabled = [name for name in EXTERNAL_TRACING_VARIABLES if environ.get(name, "").strip()]
    if enabled and not config.allow_external_tracing:
        problems.append("External tracing variables are set (" + ", ".join(enabled) + "); unset them or explicitly "
                        "approve with ALLOW_EXTERNAL_TRACING=true")
    if config.jwt_secret_key.strip() in INSECURE_JWT_SECRETS or len(config.jwt_secret_key) < 32:
        problems.append("JWT_SECRET_KEY must be set to a strong secret (at least 32 characters, not a default)")
    fingerprint_key = config.ai_audit_fingerprint_key.get_secret_value().strip() if config.ai_audit_fingerprint_key else ""
    if config.ai_audit_enabled and (len(fingerprint_key) < 32 or fingerprint_key == config.jwt_secret_key.strip()):
        problems.append("AI_AUDIT_FINGERPRINT_KEY must be set to a dedicated secret (at least 32 characters, not the JWT secret)")
    if (config.ai_fake_llm_script or "").strip():
        problems.append("AI_FAKE_LLM_SCRIPT is for the isolated E2E stack only")
    if config.ai_enabled:
        if config.ai_llm_provider.strip().lower() == "fake":
            problems.append("AI_LLM_PROVIDER=fake is for tests only")
        if config.ai_embedding_provider.strip().lower() == "fake":
            problems.append("AI_EMBEDDING_PROVIDER=fake is for tests only")
        if config.ai_llm_provider.strip().lower() == "anthropic" and not (
            config.anthropic_api_key and config.anthropic_api_key.get_secret_value().strip()
        ):
            problems.append("ANTHROPIC_API_KEY is required when AI_LLM_PROVIDER=anthropic (or set AI_ENABLED=false)")
        if config.ai_llm_provider.strip().lower() == "openai":
            if not (config.openai_api_key and config.openai_api_key.get_secret_value().strip()):
                problems.append("OPENAI_API_KEY is required when AI_LLM_PROVIDER=openai (or set AI_ENABLED=false)")
            if not (config.ai_openai_model or "").strip():
                problems.append("AI_OPENAI_MODEL is required when AI_LLM_PROVIDER=openai")
        if config.ai_llm_provider.strip().lower() not in ("anthropic", "openai", "fake"):
            problems.append("AI_LLM_PROVIDER must be anthropic or openai")
    return problems


def assert_production_ready(config: Settings) -> None:
    if config.environment.strip().lower() not in PRODUCTION_ENVIRONMENTS:
        return
    problems = production_problems(config)
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
