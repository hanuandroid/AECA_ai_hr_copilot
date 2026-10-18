"""Provider boundary policy (AIS-02): the last step before text leaves the backend for an external model provider.

Applied by audit/provider.ObservedLLMProvider to EVERY model call (router classification, Policy RAG, SQL generation
and optional SQL wording, action proposals), so /chat and the direct endpoints share one policy.

- Operation allowlist: an operation that is not registered here fails closed; the provider is never called.
- Credentials (JWTs, bearer tokens, API keys) are removed irreversibly.
- Text is normalised first (NFKC, invisible format characters removed: AIS-03).
- Personal identifiers (email, PAN, IFSC, Aadhaar, phone numbers, 9-18 digit numbers) are replaced by placeholders
  such as "[PII_PAN_1]". Detection quality is measured in tests/ai/test_pii_detection_corpus.py. The placeholder -> value map exists only in
  memory for that one call. It is used to put the caller's own values back into the model's output for that same call
  (so an SQL literal, a ticket description or a clarification keeps what the user typed); it is never logged, stored,
  audited or reused. Only category counts are audited.
- Any failure while preparing the payload blocks the call (AIProviderPolicyError).

Not covered, by design: names and free text (leave reasons, ticket text, policy wording) cannot be recognised by
patterns; which of those may be sent is governance decision G-01/G-02 (see ai-security-remediation-progress.md).
System prompts are fixed constants and are not rewritten.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.services.ai.errors import AIInvalidOutputError, AIProviderPolicyError
from app.services.ai.llm.base import LLMMessage
from app.services.ai.redaction import (
    AADHAAR_SPACED_RE,
    API_KEY_RE,
    BEARER_RE,
    EMAIL_RE,
    IFSC_RE,
    JWT_RE,
    LONG_NUMBER_RE,
    PAN_RE,
    PHONE_CANDIDATE_RE,
    is_phone_number,
)
from app.services.ai.validation import normalize_text

PROVIDER_OPERATIONS = frozenset({"route_classification", "policy_answer", "sql", "action_proposal"})

# Removed for good: the model never needs a credential, and nothing is restored.
_CREDENTIALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (JWT_RE, "[REDACTED_TOKEN]"),
    (BEARER_RE, "Bearer [REDACTED_TOKEN]"),
    (API_KEY_RE, "[REDACTED_KEY]"),
)
# Replaced by placeholders (category, pattern, optional check of the match), most specific first.
_IDENTIFIERS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...] = (
    ("EMAIL", EMAIL_RE, None),
    ("PAN", PAN_RE, None),
    ("IFSC", IFSC_RE, None),
    ("AADHAAR", AADHAAR_SPACED_RE, None),
    ("PHONE", PHONE_CANDIDATE_RE, is_phone_number),
    ("NUMBER", LONG_NUMBER_RE, None),
)
_PLACEHOLDER_RE = re.compile(r"\[PII_[A-Z]+_\d+\]")
_EXISTING_PLACEHOLDER_RE = re.compile(r"\[PII_")  # placeholder-looking input text is neutralised, never restored


@dataclass
class PreparedPayload:
    messages: list[LLMMessage]
    placeholders: dict[str, str] = field(default_factory=dict)  # placeholder -> original (this call only)
    counts: dict[str, int] = field(default_factory=dict)  # category -> number of values removed

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class _Replacer:
    def __init__(self, payload: PreparedPayload):
        self._payload = payload
        self._by_value: dict[tuple[str, str], str] = {}

    def credential(self, text: str) -> str:
        for pattern, replacement in _CREDENTIALS:
            text, removed = pattern.subn(replacement, text)
            if removed:
                self._count("CREDENTIAL", removed)
        return text

    def identifiers(self, text: str) -> str:
        for category, pattern, check in _IDENTIFIERS:
            text = pattern.sub(lambda match, c=category, k=check: (
                self._placeholder(c, match.group(0)) if k is None or k(match.group(0)) else match.group(0)), text)
        return text

    def _placeholder(self, category: str, value: str) -> str:
        self._count(category, 1)
        key = (category, value)
        if key not in self._by_value:  # the same value keeps the same placeholder within the call
            index = sum(1 for c, _ in self._by_value if c == category) + 1
            self._by_value[key] = placeholder = f"[PII_{category}_{index}]"
            self._payload.placeholders[placeholder] = value
        return self._by_value[key]

    def _count(self, category: str, amount: int) -> None:
        self._payload.counts[category] = self._payload.counts.get(category, 0) + amount


def prepare(operation: str, messages: list[LLMMessage]) -> PreparedPayload:
    """The payload that may be sent for `operation`. Raises AIProviderPolicyError instead of sending anything unsafe."""
    if operation not in PROVIDER_OPERATIONS:
        raise AIProviderPolicyError(detail="unregistered provider operation")
    try:
        payload = PreparedPayload(messages=[])
        replacer = _Replacer(payload)
        for message in messages:
            text = _EXISTING_PLACEHOLDER_RE.sub("[PII-", normalize_text(message.content))  # AIS-03
            text = replacer.identifiers(replacer.credential(text))
            payload.messages.append(LLMMessage(role=message.role, content=text))
        return payload
    except Exception as exc:  # noqa: BLE001 - fail closed: nothing is sent if the payload cannot be prepared
        raise AIProviderPolicyError(detail="payload preparation failed") from exc


def restore_text(text: str, payload: PreparedPayload) -> str:
    if not payload.placeholders:
        return text
    return _PLACEHOLDER_RE.sub(lambda match: payload.placeholders.get(match.group(0), match.group(0)), text)


def _restore(value: Any, payload: PreparedPayload) -> Any:
    if isinstance(value, str):
        return restore_text(value, payload)
    if isinstance(value, list):
        return [_restore(item, payload) for item in value]
    if isinstance(value, dict):
        return {key: _restore(item, payload) for key, item in value.items()}
    return value


def restore_structured(result: BaseModel, schema: type[BaseModel], payload: PreparedPayload) -> BaseModel:
    """The model's structured output with this call's placeholders replaced by the caller's own values."""
    if not payload.placeholders:
        return result
    try:
        return schema.model_validate(_restore(result.model_dump(mode="python"), payload))
    except ValidationError as exc:  # e.g. a restored value no longer fits a length limit
        raise AIInvalidOutputError() from exc
