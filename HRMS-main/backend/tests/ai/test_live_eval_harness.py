"""The opt-in live evaluation harness: ordinary runs stay isolated from real providers, a live run needs an explicit
opt-in for one provider, credentials never leak, and nothing falls back to another provider. All offline: no network."""

import json
import os
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.ai.errors import AIUnavailableError
from app.services.ai.llm.openai_provider import OpenAILLMProvider
from app.services.ai.providers import build_llm_provider
from evals import run_evals
from tests.support.live_credentials import capture_live_credentials

SYNTHETIC_KEY = "synthetic-live-key-not-real"
OPENAI_LIVE = {"AI_LIVE_EVAL": "1", "AI_LIVE_EVAL_PROVIDER": "openai", "AI_LLM_PROVIDER": "openai",
               "OPENAI_API_KEY": SYNTHETIC_KEY, "AI_OPENAI_MODEL": "offline-probe"}


@pytest.mark.skipif(os.environ.get("AI_LIVE_EVAL") == "1", reason="only meaningful in an ordinary (non-live) run")
def test_ordinary_runs_capture_nothing_and_blank_every_provider_setting(live_credentials):
    assert live_credentials is None
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_OPENAI_MODEL", "AI_OPENAI_FAST_MODEL"):
        assert os.environ[name] == ""  # set, not removed: the empty value wins over backend/.env
    assert settings.ai_llm_provider == "fake"
    assert not settings.anthropic_api_key and not settings.openai_api_key and not settings.ai_openai_model


@pytest.mark.parametrize("environ", [
    {},
    {k: v for k, v in OPENAI_LIVE.items() if k != "AI_LIVE_EVAL"},           # no explicit opt-in
    {**OPENAI_LIVE, "AI_LIVE_EVAL": "true"},                                    # only the exact flag counts
    {**OPENAI_LIVE, "AI_LIVE_EVAL_PROVIDER": "fake", "AI_LLM_PROVIDER": "fake"},
    {**OPENAI_LIVE, "AI_LIVE_EVAL_PROVIDER": "other", "AI_LLM_PROVIDER": "other"},
    {**OPENAI_LIVE, "AI_LLM_PROVIDER": "anthropic"},                            # provider mismatch
    {**OPENAI_LIVE, "OPENAI_API_KEY": ""},
    {**OPENAI_LIVE, "OPENAI_API_KEY": "   "},
    {**OPENAI_LIVE, "AI_OPENAI_MODEL": ""},                                     # OpenAI needs an explicit model
    {**OPENAI_LIVE, "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": SYNTHETIC_KEY},  # never falls back to another key
])
def test_no_credentials_without_a_complete_explicit_selection(environ):
    assert capture_live_credentials(environ) is None


def test_openai_selection_carries_only_openai_settings():
    credentials = capture_live_credentials({**OPENAI_LIVE, "ANTHROPIC_API_KEY": "other-provider-key"})
    assert credentials.provider == "openai"
    update = dict(credentials.settings_update)
    assert set(update) == {"ai_llm_provider", "openai_api_key", "ai_openai_model"}
    assert update["ai_llm_provider"] == "openai" and update["ai_openai_model"] == "offline-probe"
    assert update["openai_api_key"].get_secret_value() == SYNTHETIC_KEY


def test_anthropic_selection_carries_only_anthropic_settings():
    credentials = capture_live_credentials({"AI_LIVE_EVAL": "1", "AI_LIVE_EVAL_PROVIDER": "anthropic",
                                            "AI_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": SYNTHETIC_KEY,
                                            "OPENAI_API_KEY": "other-provider-key"})
    assert set(credentials.settings_update) == {"ai_llm_provider", "anthropic_api_key"}


def test_credentials_never_appear_in_text():
    credentials = capture_live_credentials(OPENAI_LIVE)
    for text in (repr(credentials), str(credentials), repr(dict(credentials.settings_update)), f"{credentials}"):
        assert SYNTHETIC_KEY not in text


def test_dummy_key_builds_the_selected_provider_without_a_request():
    credentials = capture_live_credentials(OPENAI_LIVE)
    provider = build_llm_provider(settings.model_copy(update=dict(credentials.settings_update)))
    assert isinstance(provider, OpenAILLMProvider) and provider.model == "offline-probe"


def test_missing_key_fails_closed_before_any_request():
    # The harness's own settings (blank keys) with a live provider selected: refused at construction.
    with pytest.raises(AIUnavailableError):
        build_llm_provider(settings.model_copy(update={"ai_llm_provider": "openai", "ai_openai_model": "offline-probe"}))


@pytest.mark.parametrize(("extra", "fast_model"), [({}, "offline-probe"), ({"AI_OPENAI_FAST_MODEL": "router-probe"}, "router-probe")])
def test_live_runner_pins_the_router_model_and_never_reports_the_key(monkeypatch, extra, fast_model):
    seen = []

    def fake_run(command, *, env, **kwargs):  # no subprocess, no network
        seen.append(env)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(run_evals.subprocess, "run", fake_run)
    report = run_evals.run_live(True, env={**{k: v for k, v in OPENAI_LIVE.items() if not k.startswith("AI_LIVE")},
                                           **extra})
    assert len(seen) == 2  # router suite, then the policy suite
    for env in seen:
        assert env["AI_LLM_PROVIDER"] == "openai" and env["AI_LIVE_EVAL"] == "1"
        assert env["AI_LIVE_EVAL_PROVIDER"] == "openai" and env["AI_OPENAI_FAST_MODEL"] == fast_model
    assert report["status"] == "FAILED" and report["fast_model"] == fast_model
    assert SYNTHETIC_KEY not in json.dumps(report)
