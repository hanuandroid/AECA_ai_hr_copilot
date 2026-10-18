"""Production providers, exercised offline with injected clients/models (no key, no network, no download)."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.ai.embeddings import FakeEmbeddingProvider, FastEmbedEmbeddingProvider
from app.services.ai.errors import AIEmbeddingError, AIInvalidOutputError, AIProviderError, AIUnavailableError
from app.services.ai.llm.anthropic_provider import ANTHROPIC_API_BASE_URL, FALLBACK_BETA, AnthropicLLMProvider
from app.services.ai.llm.base import LLMMessage
from app.services.ai.providers import build_embedding_provider, build_fast_llm_provider, build_llm_provider


class Answer(BaseModel):
    answer: str
    grounded: bool
    citations: list[str]


class FakeMessagesAPI:
    def __init__(self, response=None, error: Exception | None = None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs):
        return await self._respond("create", kwargs)

    async def parse(self, **kwargs):
        return await self._respond("parse", kwargs)

    async def _respond(self, method, kwargs):
        self.calls.append((method, kwargs))
        if self.error:
            raise self.error
        return self.response


def fake_client(api: FakeMessagesAPI):
    return SimpleNamespace(messages=api, beta=SimpleNamespace(messages=api))


def response(stop_reason="end_turn", parsed=None, text="Hello"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        content=[SimpleNamespace(type="text", text=text)],
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        _request_id="req_test",
    )


def provider(api, **overrides):
    options = dict(api_key="sk-ant-test", model="claude-sonnet-5-5", max_tokens=4096, timeout_seconds=30.0,
                   effort="low", temperature=None, server_fallbacks=True)
    options.update(overrides)
    return AnthropicLLMProvider(client=fake_client(api), **options)


USER = [LLMMessage(role="user", content="question")]


async def test_structured_request_shape():
    parsed = Answer(answer="a", grounded=True, citations=["S1"])
    api = FakeMessagesAPI(response(parsed=parsed))
    result = await provider(api).generate_structured(USER, Answer, system="SYS")
    assert result == parsed
    method, kwargs = api.calls[0]
    assert method == "parse"
    assert kwargs["model"] == "claude-sonnet-5-5" and kwargs["max_tokens"] == 4096
    assert kwargs["output_format"] is Answer
    assert kwargs["system"] == "SYS"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["betas"] == [FALLBACK_BETA] and kwargs["fallbacks"] == "default"
    assert "temperature" not in kwargs  # Claude 5.x rejects non-default sampling params
    assert kwargs["messages"] == [{"role": "user", "content": "question"}]


async def test_fallbacks_can_be_disabled_and_temperature_opt_in():
    api = FakeMessagesAPI(response())
    await provider(api, server_fallbacks=False, temperature=0.2).generate(USER)
    _, kwargs = api.calls[0]
    assert "betas" not in kwargs and "fallbacks" not in kwargs
    assert kwargs["temperature"] == 0.2


async def test_generate_returns_text_and_usage():
    api = FakeMessagesAPI(response(text="Policy answer"))
    result = await provider(api).generate(USER)
    assert (result.text, result.input_tokens, result.output_tokens) == ("Policy answer", 11, 7)


@pytest.mark.parametrize(
    "resp,error",
    [
        (response(stop_reason="refusal"), AIProviderError),
        (response(stop_reason="max_tokens"), AIInvalidOutputError),
        (response(parsed=None), AIInvalidOutputError),
    ],
)
async def test_bad_responses_map_to_ai_errors(resp, error):
    with pytest.raises(error):
        await provider(FakeMessagesAPI(resp)).generate_structured(USER, Answer)


def _sdk_errors():
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(code, cls):
        return cls("boom", response=httpx2.Response(code, request=request), body=None)

    return [
        (anthropic.APITimeoutError(request=request), AIProviderError),
        (anthropic.APIConnectionError(request=request), AIProviderError),
        (status(429, anthropic.RateLimitError), AIProviderError),
        (status(401, anthropic.AuthenticationError), AIUnavailableError),
        (status(500, anthropic.InternalServerError), AIProviderError),
    ]


@pytest.mark.parametrize("index", range(5))
async def test_sdk_exceptions_are_mapped_without_leaking(index):
    sdk_error, expected = _sdk_errors()[index]
    with pytest.raises(expected) as caught:
        await provider(FakeMessagesAPI(error=sdk_error)).generate_structured(USER, Answer)
    assert "boom" not in caught.value.message and "sk-ant" not in caught.value.message


def test_factory_requires_api_key_and_hides_it(monkeypatch):
    with pytest.raises(AIUnavailableError):
        build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key=None))
    with pytest.raises(AIUnavailableError):
        build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="   "))
    llm = build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="sk-ant-secret-value",
                                      ai_llm_model="claude-sonnet-5-5"))
    assert isinstance(llm, AnthropicLLMProvider) and llm.model == "claude-sonnet-5-5"
    assert "sk-ant-secret-value" not in repr(vars(llm))


def test_real_client_is_pinned_to_the_public_endpoint(monkeypatch):
    """AIS-05: an environment override cannot redirect HR data (no request is made here)."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.example")
    for build in (build_llm_provider, build_fast_llm_provider):
        llm = build(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="synthetic-test-key"))
        assert str(llm._client.base_url).rstrip("/") == ANTHROPIC_API_BASE_URL


def test_server_side_fallback_follows_configuration_only():
    """AIS-06 is gated (G-05): the existing default is preserved; the fast model never sends the beta."""
    assert Settings(_env_file=None).ai_llm_server_fallbacks is True
    main = build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="k"))
    fast = build_fast_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="k"))
    off = build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="k",
                                      ai_llm_server_fallbacks=False))
    assert (main._server_fallbacks, fast._server_fallbacks, off._server_fallbacks) == (True, False, False)


def test_a_misconfigured_provider_never_falls_back_to_another_real_provider():
    for config in (Settings(_env_file=None, ai_llm_provider="openai", anthropic_api_key="k", openai_api_key=None),
                   Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key=None, openai_api_key="k",
                            ai_openai_model="gpt-test")):
        with pytest.raises(AIUnavailableError):
            build_llm_provider(config)


# --- fastembed (injected model; the real one is never loaded in tests) -------------


class FakeFastEmbedModel:
    def __init__(self):
        self.passages, self.queries = [], []

    def passage_embed(self, texts):
        self.passages.extend(texts)
        return iter([[3.0, 4.0] for _ in texts])

    def query_embed(self, text):
        self.queries.append(text)
        return iter([[0.0, 2.0]])


async def test_fastembed_provider_uses_passage_and_query_embeddings():
    model = FakeFastEmbedModel()
    embedder = FastEmbedEmbeddingProvider("BAAI/bge-small-en-v1.5", model=model)
    assert await embedder.embed(["a", "b"]) == [[0.6, 0.8], [0.6, 0.8]]  # normalised
    assert await embedder.embed_query("q") == [0.0, 1.0]
    assert (model.passages, model.queries) == (["a", "b"], ["q"])
    assert embedder.model_id == "BAAI/bge-small-en-v1.5"


async def test_fastembed_failures_are_safe_errors():
    class Broken(FakeFastEmbedModel):
        def query_embed(self, text):
            raise RuntimeError("onnxruntime internal failure at C:\\models")

    with pytest.raises(AIEmbeddingError) as caught:
        await FastEmbedEmbeddingProvider("m", model=Broken()).embed_query("q")
    assert "onnxruntime" not in caught.value.message


async def test_fastembed_load_failure_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)  # simulate a missing/broken native install
    with pytest.raises(AIUnavailableError):
        await FastEmbedEmbeddingProvider("BAAI/bge-small-en-v1.5").embed_query("q")


def test_fastembed_provider_is_lazy():
    embedder = build_embedding_provider(Settings(_env_file=None, ai_embedding_provider="fastembed", ai_embedding_model="custom/model"))
    assert isinstance(embedder, FastEmbedEmbeddingProvider) and embedder.model_id == "custom/model"


async def test_fake_embeddings_are_deterministic_for_queries():
    embedder = FakeEmbeddingProvider(dimension=32)
    assert await embedder.embed_query("sick leave") == (await embedder.embed(["sick leave"]))[0]
    assert len(await embedder.embed_query("sick leave")) == 32
