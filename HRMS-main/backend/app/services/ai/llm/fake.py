import html
import json
import re
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


@dataclass(frozen=True)
class ScriptRule:
    schema: str  # name of the requested output model, e.g. "ActionProposal"
    contains: str  # case-insensitive phrase that must appear in the last user message
    output: dict[str, Any]
    capture: str | None = None  # optional regex; its first group replaces "{capture}" / "{capture:int}" in `output`


class ScriptedFakeLLM:
    """Deterministic fake for the isolated E2E stack only (AI_LLM_PROVIDER=fake + AI_FAKE_LLM_SCRIPT; refused in
    production by core/production_checks). Unlike FakeLLMProvider it is schema-aware: a rule answers only requests for
    its named output model, so a rule for an action proposal can never answer the router or the policy service. A
    request no rule answers fails exactly like the rule-less fake (AIInvalidOutputError). Everything after the model
    output - validation, authorization, confirmation, execution - is the real application flow."""

    name = "fake"
    model = "fake-scripted"

    def __init__(self, rules: list[ScriptRule]):
        self._rules = list(rules)

    @classmethod
    def from_json(cls, script: str) -> "ScriptedFakeLLM":
        data = json.loads(script)
        if not isinstance(data, list):
            raise ValueError("AI_FAKE_LLM_SCRIPT must be a JSON list of rules")
        return cls([ScriptRule(schema=str(rule["schema"]), contains=str(rule["contains"]), output=dict(rule["output"]),
                               capture=rule.get("capture")) for rule in data])

    async def generate(self, messages: list[LLMMessage], *, system: str | None = None, max_tokens: int | None = None,
                       temperature: float | None = None) -> LLMResponse:
        raise AIInvalidOutputError()  # free text is never scripted

    async def generate_structured(self, messages: list[LLMMessage], schema: type[StructuredT], *, system: str | None = None,
                                  max_tokens: int | None = None) -> StructuredT:
        prompt = _request_text(_last_user_text(messages))
        for rule in self._rules:
            if rule.schema != schema.__name__ or rule.contains.lower() not in prompt.lower():
                continue
            output = rule.output
            if rule.capture:
                match = re.search(rule.capture, prompt)
                if match is None:
                    continue
                output = _fill(output, match.group(1))
            try:
                return schema.model_validate(output)
            except ValidationError as exc:
                raise AIInvalidOutputError() from exc
        raise AIInvalidOutputError()


_REQUEST_RE = re.compile(r"<request>\n(.*?)\n</request>", re.DOTALL)


def _request_text(prompt: str) -> str:
    """Only the user's request inside the prompt (never the listed action descriptions); the whole text otherwise."""
    match = _REQUEST_RE.search(prompt)
    return html.unescape(match.group(1)) if match else prompt


def _fill(value: Any, captured: str) -> Any:
    if isinstance(value, dict):
        return {key: _fill(item, captured) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, captured) for item in value]
    if value == "{capture:int}":
        return int(captured)
    if isinstance(value, str):
        return value.replace("{capture}", captured)
    return value
