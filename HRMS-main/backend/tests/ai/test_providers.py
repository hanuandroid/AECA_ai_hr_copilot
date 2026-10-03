import sys

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.ai.embeddings import EmbeddingProvider, FakeEmbeddingProvider, cosine_similarity
from app.services.ai.errors import AIInvalidOutputError, AIUnavailableError
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.llm.fake import FakeLLMProvider
from app.services.ai.providers import build_embedding_provider, build_llm_provider


class Intent(BaseModel):
    intent: str
    confidence: float


def user(text: str) -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


# --- Fake LLM ----------------------------------------------------------------


async def test_fake_llm():
    llm = FakeLLMProvider().add_text_rule("sick leave", "You get 10 sick leaves.")
    assert isinstance(llm, LLMProvider)

    matched = await llm.generate(user("How many SICK LEAVE days?"), system="policy")
    assert matched.text == "You get 10 sick leaves."
    assert matched.model == "fake-llm"

    fallback = await llm.generate(user("Something else"))
    assert fallback.text == "FAKE_RESPONSE"

    assert [call.method for call in llm.calls] == ["generate", "generate"]
    assert llm.calls[0].system == "policy"


async def test_fake_llm_is_deterministic():
    llm = FakeLLMProvider().add_text_rule(lambda prompt: prompt.startswith("x"), "X")
    results = {(await llm.generate(user("xyz"))).text for _ in range(5)}
    assert results == {"X"}


async def test_fake_llm_structured_output_is_validated():
    llm = (
        FakeLLMProvider()
        .add_structured_rule("leave policy", {"intent": "POLICY_QA", "confidence": 0.9})
        .add_structured_rule("broken", {"intent": "SQL_QUERY"})
        .add_structured_rule("json", '{"intent": "HR_ACTION", "confidence": 0.7}')
    )
    parsed = await llm.generate_structured(user("what is the leave policy"), Intent)
    assert parsed == Intent(intent="POLICY_QA", confidence=0.9)
    assert (await llm.generate_structured(user("json please"), Intent)).intent == "HR_ACTION"

    with pytest.raises(AIInvalidOutputError):
        await llm.generate_structured(user("broken"), Intent)  # missing field
    with pytest.raises(AIInvalidOutputError):
        await llm.generate_structured(user("no rule matches"), Intent)


# --- Fake embeddings ---------------------------------------------------------


async def test_fake_embeddings():
    embedder = FakeEmbeddingProvider(dimension=64)
    assert isinstance(embedder, EmbeddingProvider)
    vectors = await embedder.embed(["Sick leave is 10 days", "Sick leave is 10 days", ""])
    assert len(vectors) == 3
    assert all(len(vector) == 64 for vector in vectors)
    assert vectors[0] == vectors[1]
    assert sum(value * value for value in vectors[0]) == pytest.approx(1.0)
    assert vectors[2] == [0.0] * 64


async def test_fake_embeddings_rank_related_text_higher():
    embedder = FakeEmbeddingProvider()
    query, related, unrelated = await embedder.embed(
        ["how many sick leave days do I get", "Sick leave: 10 days per year", "Travel claims within 15 days of return"]
    )
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_cosine_similarity_rejects_mismatched_dimensions():
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 0.0], [1.0])


# --- Configuration & factories ------------------------------------------------


def test_ai_settings_have_safe_defaults(monkeypatch):
    # The harness forces fake providers via env; remove that to inspect the code defaults.
    for name in ("AI_LLM_PROVIDER", "AI_EMBEDDING_PROVIDER", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)
    assert config.ai_llm_provider == "anthropic"
    assert config.ai_embedding_provider == "fastembed"
    assert config.anthropic_api_key is None
    assert config.ai_sql_max_rows == 100
    assert 0.0 <= config.ai_rag_min_score <= 1.0
    assert config.ai_max_tokens > 0


def test_ai_settings_are_environment_driven(monkeypatch):
    monkeypatch.setenv("AI_LLM_MODEL", "custom-model")
    monkeypatch.setenv("AI_SQL_MAX_ROWS", "25")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-placeholder-value")
    config = Settings(_env_file=None)
    assert config.ai_llm_model == "custom-model"
    assert config.ai_sql_max_rows == 25
    # SecretStr keeps the key out of repr/logs.
    assert "sk-ant" not in repr(config)
    assert config.anthropic_api_key.get_secret_value() == "sk-ant-test-placeholder-value"


def test_factories_return_fakes_in_tests():
    config = Settings(_env_file=None, ai_llm_provider="fake", ai_embedding_provider="fake")
    assert isinstance(build_llm_provider(config), FakeLLMProvider)
    assert isinstance(build_embedding_provider(config), FakeEmbeddingProvider)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ai_enabled": False, "ai_llm_provider": "fake"},
        {"ai_llm_provider": "anthropic"},  # wired in Phase 3
        {"ai_llm_provider": "does-not-exist"},
    ],
)
def test_llm_factory_fails_closed(overrides):
    with pytest.raises(AIUnavailableError):
        build_llm_provider(Settings(_env_file=None, **overrides))


@pytest.mark.parametrize(
    "overrides",
    # Phase 3 wired fastembed, so {"ai_embedding_provider": "fastembed"} moved to test_fastembed_provider_is_lazy.
    [{"ai_enabled": False, "ai_embedding_provider": "fake"}, {"ai_embedding_provider": "x"}],
)
def test_embedding_factory_fails_closed(overrides):
    with pytest.raises(AIUnavailableError):
        build_embedding_provider(Settings(_env_file=None, **overrides))


def test_no_vendor_sdk_loaded_by_app_import():
    """Importing the app (and building fake providers) must not import any vendor SDK.

    Runs in a fresh interpreter: other tests legitimately import anthropic, which would make an
    in-process sys.modules check order-dependent.
    """
    import subprocess
    from pathlib import Path

    code = (
        "import sys, app.main, app.services.ai.providers as p, app.services.ai.rag.service;"
        "from app.core.config import Settings;"
        "p.build_llm_provider(Settings(_env_file=None, ai_llm_provider='fake'));"
        "p.build_embedding_provider(Settings(_env_file=None, ai_embedding_provider='fastembed'));"
        "print(sorted(m for m in ('anthropic','fastembed','onnxruntime','openai') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
