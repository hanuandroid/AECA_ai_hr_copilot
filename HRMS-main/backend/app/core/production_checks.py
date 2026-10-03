"""Fail-fast production configuration checks (Phase 10).

Only enforced when ENVIRONMENT is "production"/"prod": development and tests keep their defaults. The checks
never read or log secret values; they only report which setting is unsafe.
"""

from app.core.config import Settings

PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})
INSECURE_JWT_SECRETS = frozenset({"", "change_me", "changeme", "secret", "test-only-secret-not-for-production"})


def production_problems(config: Settings) -> list[str]:
    """Unsafe settings for a production deployment (empty list = OK)."""
    problems: list[str] = []
    if config.jwt_secret_key.strip() in INSECURE_JWT_SECRETS or len(config.jwt_secret_key) < 32:
        problems.append("JWT_SECRET_KEY must be set to a strong secret (at least 32 characters, not a default)")
    if config.ai_enabled:
        if config.ai_llm_provider.strip().lower() == "fake":
            problems.append("AI_LLM_PROVIDER=fake is for tests only")
        if config.ai_embedding_provider.strip().lower() == "fake":
            problems.append("AI_EMBEDDING_PROVIDER=fake is for tests only")
        if config.ai_llm_provider.strip().lower() == "anthropic" and not (
            config.anthropic_api_key and config.anthropic_api_key.get_secret_value().strip()
        ):
            problems.append("ANTHROPIC_API_KEY is required when AI_LLM_PROVIDER=anthropic (or set AI_ENABLED=false)")
    return problems


def assert_production_ready(config: Settings) -> None:
    if config.environment.strip().lower() not in PRODUCTION_ENVIRONMENTS:
        return
    problems = production_problems(config)
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
