"""Provider observability through the existing LLMProvider abstraction.

`observe(llm, operation)` wraps any provider (real or fake) and records AI_PROVIDER_REQUEST /
AI_PROVIDER_RESPONSE / AI_PROVIDER_ERROR with provider, model, operation, schema, latency and, when
the provider reports them, token counts. Cost is estimated only when real token usage AND configured
pricing for that exact model exist; otherwise it is null (never invented, never billing).
Prompts, completions, keys and headers are never recorded.
"""

from typing import Any

from app.core.config import Settings, settings
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.recorder import Timer, record
from app.services.ai.errors import AIError
from app.services.ai.limits import charge_model_call, llm_slots, within_deadline
from app.services.ai.llm.base import LLMMessage, LLMResponse, LLMUsage, StructuredT, usage_sink
from app.services.ai.provider_policy import (
    PROVIDER_OPERATIONS,
    PreparedPayload,
    prepare,
    restore_structured,
    restore_text,
)


def _pricing_table(provider: str | None, config: Settings) -> dict[str | None, tuple[float | None, float | None]]:
    """Configured USD-per-million-token rates for one provider's models. Rates are never shared across providers."""
    if (provider or "").lower() == "openai":
        fast = config.ai_openai_fast_model or config.ai_openai_model
        table = {config.ai_openai_model: (config.ai_openai_input_cost_per_mtok, config.ai_openai_output_cost_per_mtok)}
        if fast != config.ai_openai_model:
            table[fast] = (config.ai_openai_fast_input_cost_per_mtok, config.ai_openai_fast_output_cost_per_mtok)
        return table
    if (provider or "anthropic").lower() == "anthropic":
        return {
            config.ai_llm_model: (config.ai_llm_input_cost_per_mtok, config.ai_llm_output_cost_per_mtok),
            config.ai_llm_fast_model: (config.ai_llm_fast_input_cost_per_mtok, config.ai_llm_fast_output_cost_per_mtok),
        }
    return {}  # fake or unknown providers have no pricing


def estimate_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None,
                      config: Settings = settings, provider: str | None = None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    pricing = _pricing_table(provider, config).get(model)
    if pricing is None or pricing[0] is None or pricing[1] is None:
        return None
    return round((input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000, 6)


class ObservedLLMProvider:
    observed = True

    def __init__(self, inner: Any, operation: str):
        self._inner = inner
        self._operation = operation
        self.name = getattr(inner, "name", "unknown")
        self.model = getattr(inner, "model", "unknown")

    def _meta(self, schema_name: str | None) -> dict[str, Any]:
        operation = self._operation if self._operation in PROVIDER_OPERATIONS else "unregistered"  # never free text
        return {"provider": _token(self.name), "model": _token(self.model), "operation": operation,
                "schema_name": schema_name}

    # AIS-02: every call passes the provider boundary first (provider_policy.prepare); the inner provider only ever
    # receives the prepared messages, and placeholders are restored in the output of the same call only.

    async def generate(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        payload = self._prepare(messages, None)
        response = await self._call(lambda: self._inner.generate(payload.messages, **kwargs), None, payload)
        return response.model_copy(update={"text": restore_text(response.text, payload)})

    async def generate_structured(self, messages: list[LLMMessage], schema: type[StructuredT], **kwargs: Any) -> StructuredT:
        payload = self._prepare(messages, schema.__name__)
        result = await self._call(lambda: self._inner.generate_structured(payload.messages, schema, **kwargs),
                                  schema.__name__, payload)
        return restore_structured(result, schema, payload)  # type: ignore[return-value]

    def _prepare(self, messages: list[LLMMessage], schema_name: str | None) -> PreparedPayload:
        try:
            return prepare(self._operation, messages)
        except AIError as error:
            record(AuditEventType.AI_PROVIDER_ERROR, AuditStatus.FAILED, error_code=error.code, **self._meta(schema_name))
            raise

    async def _call(self, invoke, schema_name: str | None, payload: PreparedPayload):
        meta = self._meta(schema_name)
        record(AuditEventType.AI_PROVIDER_REQUEST, AuditStatus.STARTED, redacted_values=payload.total,
               redacted_categories=sorted(payload.counts) or None, **meta)
        usage = LLMUsage()
        token = usage_sink.set(usage)
        timer = Timer()
        try:
            deadline = charge_model_call()  # AIS-10: per-request call budget + end-to-end deadline (raises before calling)
            async with llm_slots.slot(deadline):  # B-09: process-wide model-call concurrency bound (AI_BUSY on timeout)
                result = await within_deadline(invoke(), deadline)
        except AIError as error:
            record(AuditEventType.AI_PROVIDER_ERROR, AuditStatus.FAILED, duration_ms=timer.ms, error_code=error.code, **meta)
            raise
        except Exception:
            record(AuditEventType.AI_PROVIDER_ERROR, AuditStatus.FAILED, duration_ms=timer.ms, error_code="UNEXPECTED", **meta)
            raise
        finally:
            usage_sink.reset(token)
        # Fall back to LLMResponse counts only when the provider reported nothing at all; a provider that reported
        # "usage not returned" (None) must not be turned into a fabricated 0.
        if isinstance(result, LLMResponse) and not usage.reported:
            usage.input_tokens, usage.output_tokens = result.input_tokens, result.output_tokens
        total = None if usage.input_tokens is None or usage.output_tokens is None else usage.input_tokens + usage.output_tokens
        record(AuditEventType.AI_PROVIDER_RESPONSE, AuditStatus.SUCCESS, duration_ms=timer.ms,
               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=total,
               estimated_cost_usd=estimate_cost_usd(str(self.model), usage.input_tokens, usage.output_tokens,
                                                    provider=str(self.name)), **meta)
        return result


def _token(value: object) -> str:
    text = str(value)
    return "".join(ch if ch.isalnum() or ch in "._:-/" else "_" for ch in text)[:120]


def observe(llm: Any, operation: str) -> Any:
    """Wrap a provider once; idempotent."""
    if getattr(llm, "observed", False):
        return llm
    return ObservedLLMProvider(llm, operation)
