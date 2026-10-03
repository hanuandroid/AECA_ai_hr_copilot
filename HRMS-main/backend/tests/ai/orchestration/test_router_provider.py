"""Routing classifier provider: the fast model from configuration, failing closed."""

import pytest

from app.core.config import Settings
from app.services.ai.errors import AIUnavailableError
from app.services.ai.llm.anthropic_provider import AnthropicLLMProvider
from app.services.ai.llm.fake import FakeLLMProvider
from app.services.ai.orchestration.classifier import RouterClassifier, get_router_classifier
from app.services.ai.providers import build_fast_llm_provider


def test_fast_provider_uses_the_configured_fast_model_without_effort_or_fallback_beta():
    provider = build_fast_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="test-key",
                                                ai_llm_fast_model="claude-haiku-4-5-20251001"))
    assert isinstance(provider, AnthropicLLMProvider)
    assert provider.model == "claude-haiku-4-5-20251001"
    kwargs = provider._request_kwargs([], system="s", max_tokens=None, temperature=None)
    assert "output_config" not in kwargs and "betas" not in kwargs and "fallbacks" not in kwargs
    assert kwargs["max_tokens"] == 1024


def test_fast_provider_fake_for_tests():
    assert isinstance(build_fast_llm_provider(Settings(_env_file=None, ai_llm_provider="fake")), FakeLLMProvider)


@pytest.mark.parametrize("overrides", [
    {"ai_enabled": False},
    {"ai_llm_provider": "anthropic", "anthropic_api_key": None},
    {"ai_llm_provider": "anthropic", "anthropic_api_key": "   "},
    {"ai_llm_provider": "unknown"},
])
def test_fast_provider_fails_closed(overrides):
    with pytest.raises(AIUnavailableError):
        build_fast_llm_provider(Settings(_env_file=None, **overrides))


def test_classifier_dependency_resolves_no_provider_up_front(monkeypatch):
    def explode():
        raise AssertionError("provider must be resolved lazily")

    monkeypatch.setattr("app.services.ai.providers.get_fast_llm_provider", explode)
    assert isinstance(get_router_classifier(), RouterClassifier)
