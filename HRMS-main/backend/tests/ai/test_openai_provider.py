"""OpenAI provider (AI_LLM_PROVIDER=openai), exercised offline with an injected Responses client: no key, no network.

Covers: strict structured-output schemas for every schema the app requests, request shape (store=False, opt-in
temperature/reasoning), response translation, untrusted-output validation, safe error mapping, provider selection
without cross-provider fallback, usage/cost accounting, production checks and log hygiene."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from structlog.testing import capture_logs

from app.core.config import Settings
from app.core.production_checks import production_problems
from app.services.ai.actions.models import ActionProposal
from app.services.ai.audit.provider import estimate_cost_usd, observe
from app.services.ai.errors import AIInvalidOutputError, AIProviderError, AIUnavailableError
from app.services.ai.llm.anthropic_provider import AnthropicLLMProvider
from app.services.ai.llm.base import LLMMessage
from app.services.ai.llm.fake import FakeLLMProvider
from app.services.ai.llm.openai_provider import OPENAI_API_BASE_URL, OpenAILLMProvider, strict_json_schema
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.providers import build_fast_llm_provider, build_llm_provider
from app.services.ai.rag.models import PolicyLLMOutput
from app.services.ai.sql.models import SQLFormatOutput, SQLGenerationOutput
from tests.ai.audit.conftest import audit_scope  # noqa: F401  (fixture)

KEY ="sk-openai-test-secret-value-0000"
PROMPT = "Employee asks: how many sick days do I have? secret-prompt-marker"
USER = [LLMMessage(role="user", content=PROMPT)]
APP_SCHEMAS = [RouterClassification, PolicyLLMOutput, SQLGenerationOutput, SQLFormatOutput, ActionProposal]


class Answer(BaseModel):
    answer: str
    grounded: bool
    citations: list[str]


class FakeResponses:
    def __init__(self, response=None, error: Exception | None = None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def response(text='{"answer": "10 days", "grounded": true, "citations": ["S1"]}', *, status="completed",
             usage=(11, 7), refusal=False, incomplete_reason=None, model="gpt-test-model"):
    content = [SimpleNamespace(type="refusal", refusal="no")] if refusal else [SimpleNamespace(type="output_text", text=text)]
    return SimpleNamespace(
        output_text="" if refusal else text,
        output=[SimpleNamespace(type="message", content=content)],
        status=status,
        incomplete_details=SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None,
        model=model,
        usage=None if usage is None else SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]),
        _request_id="req_test",
    )


def provider(api: FakeResponses, **overrides) -> OpenAILLMProvider:
    options = dict(api_key=KEY, model="gpt-test-model", max_tokens=4096, timeout_seconds=30.0)
    options.update(overrides)
    return OpenAILLMProvider(client=SimpleNamespace(responses=api), **options)


def openai_settings(**overrides) -> Settings:
    values = dict(_env_file=None, ai_llm_provider="openai", openai_api_key=KEY, ai_openai_model="gpt-test-model")
    values.update(overrides)
    return Settings(**values)


# --- strict schemas -------------------------------------------------------------------------------------------


def _walk(node):
    yield node
    for key, value in node.items():
        if key in ("properties", "$defs"):
            for sub in value.values():
                yield from _walk(sub)
        elif key == "items":
            yield from _walk(value)
        elif key == "anyOf":
            for sub in value:
                yield from _walk(sub)


@pytest.mark.parametrize("schema", APP_SCHEMAS, ids=lambda s: s.__name__)
def test_every_app_schema_converts_to_a_strict_schema(schema):
    strict = strict_json_schema(schema)
    assert strict["type"] == "object"
    for node in _walk(strict):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])  # strict mode: every property required
        if "$ref" in node:
            assert set(node) == {"$ref"}
        assert not set(node) & {"default", "maxLength", "minLength", "minimum", "maximum", "pattern", "allOf", "title"}


def test_optional_fields_stay_nullable_and_constraints_are_enforced_locally():
    strict = strict_json_schema(RouterClassification)
    question = strict["properties"]["clarification_question"]
    assert {"type": "null"} in question["anyOf"]
    # The length/range constraints are not sent, but local validation still rejects violations.
    with pytest.raises(Exception):
        RouterClassification.model_validate({"intent": "SQL", "confidence": 7, "reason": "", "clarification_question": None,
                                             "entities": {"employee_name": None, "resource": None, "date_hint": None}})


# --- request shape and translation ----------------------------------------------------------------------------


async def test_structured_request_uses_responses_api_strict_schema_and_store_false():
    api = FakeResponses(response())
    result = await provider(api).generate_structured(USER, Answer, system="You are HR.")
    assert result == Answer(answer="10 days", grounded=True, citations=["S1"])
    call = api.calls[0]
    assert call["model"] == "gpt-test-model" and call["store"] is False
    assert call["instructions"] == "You are HR." and call["max_output_tokens"] == 4096
    assert call["input"] == [{"role": "user", "content": PROMPT}]
    fmt = call["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "Answer"
    assert fmt["schema"] == strict_json_schema(Answer)
    assert "temperature" not in call and "reasoning" not in call


async def test_temperature_and_reasoning_are_opt_in():
    api = FakeResponses(response())
    await provider(api, temperature=0.2, reasoning_effort="low").generate_structured(USER, Answer)
    assert api.calls[0]["temperature"] == 0.2 and api.calls[0]["reasoning"] == {"effort": "low"}


async def test_generate_returns_text_and_usage():
    api = FakeResponses(response(text="Hello there"))
    result = await provider(api).generate(USER, max_tokens=50)
    assert (result.text, result.model, result.input_tokens, result.output_tokens) == ("Hello there", "gpt-test-model", 11, 7)
    assert api.calls[0]["max_output_tokens"] == 50 and "text" not in api.calls[0]


@pytest.mark.parametrize("resp, error", [
    (response(text=""), AIInvalidOutputError),
    (response(text="not json"), AIInvalidOutputError),
    (response(text='{"answer": "x", "grounded": true}'), AIInvalidOutputError),  # missing field
    (response(text='{"answer": "x", "grounded": "maybe", "citations": "S1"}'), AIInvalidOutputError),  # wrong types
    (response(refusal=True), AIProviderError),
    (response(status="incomplete", incomplete_reason="max_output_tokens"), AIInvalidOutputError),
    (response(status="incomplete", incomplete_reason="content_filter"), AIProviderError),
    (response(status="failed"), AIProviderError),
])
async def test_bad_responses_map_to_ai_errors(resp, error):
    with pytest.raises(error):
        await provider(FakeResponses(resp)).generate_structured(USER, Answer)


async def test_extra_authority_fields_from_the_model_are_rejected():
    """Model output is untrusted: a schema with extra='forbid' rejects injected authority fields."""
    text = json.dumps({"intent": "SQL", "confidence": 0.9, "reason": "", "clarification_question": None,
                       "entities": {"employee_name": None, "resource": None, "date_hint": None}, "role": "ADMIN"})
    with pytest.raises(AIInvalidOutputError):
        await provider(FakeResponses(response(text=text))).generate_structured(USER, RouterClassification)


# --- SDK errors -----------------------------------------------------------------------------------------------


def _sdk_errors():
    import httpx2
    import openai

    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")

    def status(code, cls):
        return cls(f"boom {KEY}", response=httpx2.Response(code, request=request), body=None)

    return [
        (openai.APITimeoutError(request=request), AIProviderError),
        (openai.APIConnectionError(request=request), AIProviderError),
        (status(429, openai.RateLimitError), AIProviderError),
        (status(401, openai.AuthenticationError), AIUnavailableError),
        (status(403, openai.PermissionDeniedError), AIUnavailableError),
        (status(404, openai.NotFoundError), AIUnavailableError),  # unknown/unavailable model
        (status(400, openai.BadRequestError), AIProviderError),
        (status(500, openai.InternalServerError), AIProviderError),
    ]


@pytest.mark.parametrize("index", range(8))
async def test_sdk_exceptions_are_mapped_without_leaking(index):
    sdk_error, expected = _sdk_errors()[index]
    with capture_logs() as logs:
        with pytest.raises(expected) as caught:
            await provider(FakeResponses(error=sdk_error)).generate_structured(USER, Answer)
    assert "boom" not in caught.value.message and KEY not in caught.value.message
    rendered = json.dumps(logs, default=str)
    assert KEY not in rendered and "secret-prompt-marker" not in rendered


async def test_success_logs_contain_no_prompt_completion_or_key():
    with capture_logs() as logs:
        await provider(FakeResponses(response())).generate_structured(USER, Answer, system="system-marker")
    rendered = json.dumps(logs, default=str)
    assert "ai.llm.completed" in rendered
    for secret in (KEY, "secret-prompt-marker", "system-marker", "10 days"):
        assert secret not in rendered


# --- provider selection and configuration ---------------------------------------------------------------------


def test_openai_settings_default_to_unset(monkeypatch):
    for name in ("OPENAI_API_KEY", "AI_OPENAI_MODEL", "AI_OPENAI_FAST_MODEL", "AI_OPENAI_REASONING_EFFORT",
                 "AI_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)
    assert config.ai_llm_provider == "anthropic"  # default provider unchanged
    assert config.openai_api_key is None and config.ai_openai_model is None and config.ai_openai_fast_model is None
    assert config.ai_openai_reasoning_effort is None and config.ai_openai_input_cost_per_mtok is None


def test_factory_builds_openai_and_hides_the_key():
    llm = build_llm_provider(openai_settings())
    assert isinstance(llm, OpenAILLMProvider) and llm.name == "openai" and llm.model == "gpt-test-model"
    assert KEY not in repr(openai_settings()) and KEY not in repr(vars(llm))


def test_real_client_is_pinned_to_the_public_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example/v1")
    llm = build_llm_provider(openai_settings())
    assert str(llm._client.base_url).rstrip("/") == OPENAI_API_BASE_URL


@pytest.mark.parametrize("overrides", [
    {"openai_api_key": None}, {"openai_api_key": "   "}, {"ai_openai_model": None}, {"ai_openai_model": " "},
    {"ai_enabled": False},
])
def test_openai_fails_closed_without_key_or_model(overrides):
    with pytest.raises(AIUnavailableError):
        build_llm_provider(openai_settings(**overrides))
    with pytest.raises(AIUnavailableError):
        build_fast_llm_provider(openai_settings(**overrides))


def test_no_fallback_to_anthropic_when_openai_is_misconfigured():
    config = openai_settings(openai_api_key=None, anthropic_api_key="sk-ant-would-work")
    with pytest.raises(AIUnavailableError):
        build_llm_provider(config)


def test_fast_model_falls_back_to_the_main_openai_model():
    assert build_fast_llm_provider(openai_settings()).model == "gpt-test-model"
    assert build_fast_llm_provider(openai_settings(ai_openai_fast_model="gpt-fast-test")).model == "gpt-fast-test"


def test_other_providers_are_unchanged():
    assert isinstance(build_llm_provider(Settings(_env_file=None, ai_llm_provider="fake")), FakeLLMProvider)
    anthropic = build_llm_provider(Settings(_env_file=None, ai_llm_provider="anthropic", anthropic_api_key="sk-ant-x",
                                            openai_api_key=KEY))
    assert isinstance(anthropic, AnthropicLLMProvider)


def test_production_checks_require_the_selected_providers_key_and_model():
    base = dict(environment="production", jwt_secret_key="j" * 48, ai_audit_fingerprint_key="f" * 48,
                allowed_hosts="api.example.internal", cors_allow_origins="https://hr.example.internal",
                ai_embedding_provider="fastembed")
    problems = production_problems(Settings(_env_file=None, ai_llm_provider="openai", **base), environ={}, argv=[])
    assert any("OPENAI_API_KEY is required" in p for p in problems)
    assert any("AI_OPENAI_MODEL is required" in p for p in problems)
    assert not any("ANTHROPIC_API_KEY" in p for p in problems)
    ok = production_problems(Settings(_env_file=None, ai_llm_provider="openai", openai_api_key=KEY,
                                      ai_openai_model="gpt-test-model", **base), environ={}, argv=[])
    assert not any("OPENAI" in p or "AI_LLM_PROVIDER" in p for p in ok)
    unknown = production_problems(Settings(_env_file=None, ai_llm_provider="other", **base), environ={}, argv=[])
    assert any("AI_LLM_PROVIDER must be" in p for p in unknown)
    assert not any(KEY in p for p in ok + problems)


# --- usage and cost accounting --------------------------------------------------------------------------------


async def test_usage_and_openai_pricing_flow_into_the_audit(audit_scope, monkeypatch):
    from app.services.ai.audit import recorder

    monkeypatch.setattr(recorder.settings, "ai_openai_model", "gpt-test-model")
    monkeypatch.setattr(recorder.settings, "ai_openai_input_cost_per_mtok", 2.0)
    monkeypatch.setattr(recorder.settings, "ai_openai_output_cost_per_mtok", 8.0)
    await observe(provider(FakeResponses(response(usage=(1000, 200)))), "policy_answer").generate_structured(USER, Answer)
    data = audit_scope.events[-1].metadata
    assert (data.provider, data.model) == ("openai", "gpt-test-model")
    assert (data.input_tokens, data.output_tokens, data.total_tokens) == (1000, 200, 1200)
    assert data.estimated_cost_usd == pytest.approx((1000 * 2.0 + 200 * 8.0) / 1_000_000)


async def test_missing_usage_is_reported_as_unknown_not_zero(audit_scope):
    llm = observe(provider(FakeResponses(response(text="Hi", usage=None))), "policy_answer")
    await llm.generate(USER)
    data = audit_scope.events[-1].metadata
    assert data.input_tokens is None and data.output_tokens is None and data.total_tokens is None
    assert data.estimated_cost_usd is None


def test_anthropic_rates_never_price_openai_calls():
    config = Settings(_env_file=None, ai_llm_model="shared-name", ai_llm_input_cost_per_mtok=3.0,
                      ai_llm_output_cost_per_mtok=15.0, ai_openai_model="shared-name")
    assert estimate_cost_usd("shared-name", 1000, 100, config, provider="anthropic") == pytest.approx(0.0045)
    assert estimate_cost_usd("shared-name", 1000, 100, config, provider="openai") is None
    assert estimate_cost_usd("shared-name", 1000, 100, config, provider="fake") is None
