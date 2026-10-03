import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.services.ai.errors import AIInvalidOutputError
from app.services.ai.llm.base import LLMMessage, LLMResponse, StructuredT

Matcher = str | Callable[[str], bool]


@dataclass
class FakeLLMCall:
    method: str
    system: str | None
    messages: list[LLMMessage]


@dataclass
class FakeLLMProvider:
    """Deterministic, offline LLM for tests and evals.

    Rules are checked in order against the last user message; a `str` matcher is a
    case-insensitive substring. Structured rules return a dict (or JSON string) that
    is validated against the requested schema exactly like a real provider would.
    """

    default_text: str = "FAKE_RESPONSE"
    name: str = "fake"
    model: str = "fake-llm"
    _text_rules: list[tuple[Matcher, str]] = field(default_factory=list)
    _structured_rules: list[tuple[Matcher, dict[str, Any] | str]] = field(default_factory=list)
    calls: list[FakeLLMCall] = field(default_factory=list)

    def add_text_rule(self, matcher: Matcher, text: str) -> "FakeLLMProvider":
        self._text_rules.append((matcher, text))
        return self

    def add_structured_rule(self, matcher: Matcher, payload: dict[str, Any] | str) -> "FakeLLMProvider":
        self._structured_rules.append((matcher, payload))
        return self

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        self.calls.append(FakeLLMCall("generate", system, list(messages)))
        prompt = _last_user_text(messages)
        text = _match(self._text_rules, prompt)
        return LLMResponse(
            text=self.default_text if text is None else text,
            model=self.model,
            input_tokens=len(prompt.split()),
            output_tokens=len((text or self.default_text).split()),
        )

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[StructuredT],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT:
        self.calls.append(FakeLLMCall("generate_structured", system, list(messages)))
        payload = _match(self._structured_rules, _last_user_text(messages))
        if payload is None:
            raise AIInvalidOutputError()
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
            return schema.model_validate(data)
        except (ValueError, ValidationError) as exc:
            raise AIInvalidOutputError() from exc


def _last_user_text(messages: list[LLMMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def _match(rules: list[tuple[Matcher, Any]], prompt: str) -> Any | None:
    lowered = prompt.lower()
    for matcher, value in rules:
        if callable(matcher):
            if matcher(prompt):
                return value
        elif matcher.lower() in lowered:
            return value
    return None
