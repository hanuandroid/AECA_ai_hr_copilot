"""Credentials for the opt-in live evaluation only (tests/ai/rag/test_policy_eval_live.py).

The harness (tests/conftest.py) blanks every provider key and model in the environment, so ordinary test runs can
never reach a real provider, even when a developer's shell or backend/.env holds a key. A live run started by
`python -m evals.run_evals --live` sets AI_LIVE_EVAL=1 and names one provider; only then, and only for that provider,
conftest captures its key and model here BEFORE blanking them. The values stay in memory as SecretStr (never back in
os.environ), the repr hides them, and the other provider's settings stay blank, so a live run cannot fall back to it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import SecretStr

LIVE_FLAG, LIVE_PROVIDER_ENV = "AI_LIVE_EVAL", "AI_LIVE_EVAL_PROVIDER"
# provider: (key variable, key setting, model variable, model setting, model required)
_PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "openai_api_key", "AI_OPENAI_MODEL", "ai_openai_model", True),
    "anthropic": ("ANTHROPIC_API_KEY", "anthropic_api_key", "AI_LLM_MODEL", "ai_llm_model", False),
}


@dataclass(frozen=True, repr=False)
class LiveCredentials:
    provider: str
    settings_update: Mapping[str, Any]  # Settings fields for the selected provider only; the key is a SecretStr

    def __repr__(self) -> str:
        return f"LiveCredentials(provider={self.provider!r})"

    __str__ = __repr__


def capture_live_credentials(environ: Mapping[str, str]) -> LiveCredentials | None:
    """The selected provider's key and model for an explicit live run, or None (every ordinary run)."""
    if environ.get(LIVE_FLAG) != "1":
        return None
    provider = (environ.get(LIVE_PROVIDER_ENV) or "").strip().lower()
    if provider not in _PROVIDERS or (environ.get("AI_LLM_PROVIDER") or "").strip().lower() != provider:
        return None
    key_var, key_field, model_var, model_field, model_required = _PROVIDERS[provider]
    key, model = (environ.get(key_var) or "").strip(), (environ.get(model_var) or "").strip()
    if not key or (model_required and not model):
        return None
    update: dict[str, Any] = {"ai_llm_provider": provider, key_field: SecretStr(key)}
    if model:
        update[model_field] = model
    return LiveCredentials(provider, MappingProxyType(update))
