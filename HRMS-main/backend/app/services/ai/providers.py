"""Provider factories. The only place that maps configuration to concrete providers."""

import structlog

from app.core.config import Settings, settings
from app.services.ai.embeddings import EmbeddingProvider, FakeEmbeddingProvider, FastEmbedEmbeddingProvider
from app.services.ai.errors import AIUnavailableError
from app.services.ai.llm.base import LLMProvider
from app.services.ai.llm.fake import FakeLLMProvider, ScriptedFakeLLM

logger = structlog.get_logger()

_llm_cache: dict[str, LLMProvider] = {}
_embedding_cache: dict[str, EmbeddingProvider] = {}


def build_llm_provider(config: Settings = settings) -> LLMProvider:
    if not config.ai_enabled:
        raise AIUnavailableError()
    provider = config.ai_llm_provider.strip().lower()
    if provider == "fake":
        # E2E only: a scripted, schema-aware fake (production refuses both the fake provider and the script).
        if config.ai_fake_llm_script:
            try:
                return ScriptedFakeLLM.from_json(config.ai_fake_llm_script)
            except (ValueError, KeyError, TypeError) as exc:  # malformed script: fail closed like a missing provider
                logger.warning("ai.provider.invalid_fake_script", kind="llm")
                raise AIUnavailableError() from exc
        return FakeLLMProvider()
    if provider == "anthropic":
        api_key = config.anthropic_api_key.get_secret_value() if config.anthropic_api_key else ""
        if not api_key.strip():
            logger.warning("ai.provider.missing_key", kind="llm", provider=provider)
            raise AIUnavailableError()
        from app.services.ai.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(
            api_key=api_key,
            model=config.ai_llm_model,
            max_tokens=config.ai_max_tokens,
            timeout_seconds=config.ai_request_timeout_seconds,
            effort=config.ai_llm_effort or None,
            temperature=config.ai_temperature,
            server_fallbacks=config.ai_llm_server_fallbacks,
        )
    if provider == "openai":
        return _openai_provider(config, kind="llm", model=config.ai_openai_model, max_tokens=config.ai_max_tokens)
    logger.error("ai.provider.unknown", kind="llm", provider=provider)
    raise AIUnavailableError()


def _openai_provider(config: Settings, *, kind: str, model: str | None, max_tokens: int) -> LLMProvider:
    """OpenAI needs its own key and an explicitly configured model; anything missing fails closed (never falls back
    to another real provider)."""
    api_key = config.openai_api_key.get_secret_value() if config.openai_api_key else ""
    if not api_key.strip():
        logger.warning("ai.provider.missing_key", kind=kind, provider="openai")
        raise AIUnavailableError()
    if not (model or "").strip():
        logger.warning("ai.provider.missing_model", kind=kind, provider="openai")
        raise AIUnavailableError()
    from app.services.ai.llm.openai_provider import OpenAILLMProvider

    return OpenAILLMProvider(
        api_key=api_key,
        model=model.strip(),
        max_tokens=max_tokens,
        timeout_seconds=config.ai_request_timeout_seconds,
        temperature=config.ai_temperature,
        reasoning_effort=config.ai_openai_reasoning_effort or None,
    )


def fast_model_for(config: Settings) -> str | None:
    """Model id used for routing classification by the configured provider."""
    if config.ai_llm_provider.strip().lower() == "openai":
        return config.ai_openai_fast_model or config.ai_openai_model
    return config.ai_llm_fast_model


def build_fast_llm_provider(config: Settings = settings) -> LLMProvider:
    """Small/fast model for routing classification (AI_LLM_FAST_MODEL, e.g. Claude Haiku 4.5).

    `effort` and the server-side fallback beta are not sent: effort is not supported on Haiku 4.5,
    and routing only needs a short structured classification.
    """
    if not config.ai_enabled:
        raise AIUnavailableError()
    provider = config.ai_llm_provider.strip().lower()
    if provider == "fake":
        return FakeLLMProvider()
    if provider == "anthropic":
        api_key = config.anthropic_api_key.get_secret_value() if config.anthropic_api_key else ""
        if not api_key.strip():
            logger.warning("ai.provider.missing_key", kind="fast_llm", provider=provider)
            raise AIUnavailableError()
        from app.services.ai.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(
            api_key=api_key,
            model=config.ai_llm_fast_model,
            max_tokens=1024,
            timeout_seconds=config.ai_request_timeout_seconds,
            effort=None,
            temperature=config.ai_temperature,
            server_fallbacks=False,
        )
    if provider == "openai":
        return _openai_provider(config, kind="fast_llm", model=fast_model_for(config), max_tokens=1024)
    logger.error("ai.provider.unknown", kind="fast_llm", provider=provider)
    raise AIUnavailableError()


def build_embedding_provider(config: Settings = settings) -> EmbeddingProvider:
    if not config.ai_enabled:
        raise AIUnavailableError()
    provider = config.ai_embedding_provider.strip().lower()
    if provider == "fake":
        return FakeEmbeddingProvider()
    if provider == "fastembed":
        return FastEmbedEmbeddingProvider(config.ai_embedding_model, cache_dir=config.ai_embedding_cache_dir)
    logger.error("ai.provider.unknown", kind="embedding", provider=provider)
    raise AIUnavailableError()


def get_llm_provider() -> LLMProvider:
    """Process-wide provider (one HTTP client). Failures are not cached, so a fixed config recovers."""
    main_model = settings.ai_openai_model if settings.ai_llm_provider.strip().lower() == "openai" else settings.ai_llm_model
    key = f"{settings.ai_llm_provider}:{main_model}"
    if key not in _llm_cache:
        _llm_cache[key] = build_llm_provider(settings)
    return _llm_cache[key]


def get_fast_llm_provider() -> LLMProvider:
    key = f"fast:{settings.ai_llm_provider}:{fast_model_for(settings)}"
    if key not in _llm_cache:
        _llm_cache[key] = build_fast_llm_provider(settings)
    return _llm_cache[key]


def get_embedding_provider() -> EmbeddingProvider:
    """Process-wide provider (the fastembed model is loaded once)."""
    key = f"{settings.ai_embedding_provider}:{settings.ai_embedding_model}"
    if key not in _embedding_cache:
        _embedding_cache[key] = build_embedding_provider(settings)
    return _embedding_cache[key]
