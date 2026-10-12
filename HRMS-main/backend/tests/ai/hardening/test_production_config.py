"""Production configuration audit (Phase 10): fail fast on unsafe production settings; dev/test unaffected."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.production_checks import assert_production_ready, production_problems

STRONG = "x" * 48
BACKEND = Path(__file__).resolve().parents[3]


def prod(**overrides) -> Settings:
    base = {"environment": "production", "jwt_secret_key": STRONG, "anthropic_api_key": "sk-test-placeholder",
            "ai_llm_provider": "anthropic", "ai_embedding_provider": "fastembed"}
    return Settings(_env_file=None, **{**base, **overrides})


def test_a_safe_production_configuration_passes():
    assert production_problems(prod()) == []
    assert_production_ready(prod())


@pytest.mark.parametrize("overrides,problem", [
    ({"jwt_secret_key": "change_me"}, "JWT_SECRET_KEY"),
    ({"jwt_secret_key": "short-secret"}, "JWT_SECRET_KEY"),
    ({"ai_llm_provider": "fake"}, "AI_LLM_PROVIDER=fake"),
    ({"ai_embedding_provider": "fake"}, "AI_EMBEDDING_PROVIDER=fake"),
    ({"anthropic_api_key": None}, "ANTHROPIC_API_KEY"),
])
def test_unsafe_production_settings_stop_startup(overrides, problem):
    config = prod(**overrides)
    assert any(problem in item for item in production_problems(config))
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        assert_production_ready(config)


def test_ai_disabled_does_not_need_a_key():
    assert production_problems(prod(ai_enabled=False, anthropic_api_key=None)) == []


@pytest.mark.parametrize("environment", ["dev", "test", "staging"])
def test_non_production_environments_keep_their_defaults(environment):
    assert_production_ready(Settings(_env_file=None, environment=environment, jwt_secret_key="change_me", ai_llm_provider="fake"))


def test_error_messages_never_contain_secret_values():
    config = prod(jwt_secret_key="change_me", anthropic_api_key="sk-ant-real-looking-secret-value")
    assert "change_me" not in " ".join(production_problems(config))
    assert "sk-ant" not in " ".join(production_problems(prod(ai_llm_provider="fake", anthropic_api_key="sk-ant-x")))


def test_no_real_secrets_are_committed():
    """Old assertion (until 2026-10-11): `not (BACKEND / ".env").exists()`. That failed for every developer who followed
    the README (docker-compose requires backend/.env) and did not test "committed" at all. Intent now checked directly:
    backend/.env is not tracked and is git-ignored, it is excluded from the Docker build context, the example holds
    no key, and no Anthropic key appears in application code. Requires a git checkout (local runs and CI)."""
    example = (BACKEND / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^ANTHROPIC_API_KEY=\s*$", example, re.MULTILINE)
    git = shutil.which("git")
    assert git, "this test needs git and a git checkout to verify that backend/.env is not committed"
    tracked = subprocess.run([git, "ls-files", "--error-unmatch", ".env"], cwd=BACKEND, capture_output=True, text=True)
    assert tracked.returncode != 0, "backend/.env is tracked by git"
    ignored = subprocess.run([git, "check-ignore", "-q", ".env"], cwd=BACKEND, capture_output=True, text=True)
    assert ignored.returncode == 0, "backend/.env is not git-ignored"
    dockerignore = (BACKEND / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in dockerignore, "backend/.env would be copied into the API image"
    for path in (BACKEND / "app").rglob("*.py"):
        assert not re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", path.read_text(encoding="utf-8")), path


def test_cors_is_an_explicit_allow_list():
    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_origins"] == ["http://localhost:3000"] and "*" not in cors.kwargs["allow_origins"]
