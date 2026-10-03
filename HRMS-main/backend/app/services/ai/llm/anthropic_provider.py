"""Anthropic implementation of LLMProvider (decision D1). The only module importing the Anthropic SDK.

- Model ids, effort and limits come from Settings; the API key only from ANTHROPIC_API_KEY.
- Structured output uses the SDK's `messages.parse(output_format=<Pydantic model>)`.
- Server-side refusal fallback (`fallbacks="default"`) is enabled by default on the Claude API
  (AI_LLM_SERVER_FALLBACKS=false disables it, e.g. behind a proxy that rejects the beta).
- `temperature` is only sent when explicitly configured: current Claude models reject
  non-default sampling parameters.
- Prompts, completions and keys are never logged; only request ids, stop reasons and error types.
"""

from typing import Any

import structlog
from pydantic import ValidationError

from app.services.ai.errors import AIInvalidOutputError, AIProviderError, AIUnavailableError
from app.services.ai.llm.base import LLMMessage, LLMResponse, StructuredT, report_usage

logger = structlog.get_logger()

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLMProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout_seconds: float,
        effort: str | None = None,
        temperature: float | None = None,
        server_fallbacks: bool = True,
        client: Any | None = None,
    ):
        if not api_key and client is None:
            raise AIUnavailableError()
        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._temperature = temperature
        self._server_fallbacks = server_fallbacks
        if client is None:
            import anthropic  # lazy: keep the SDK out of processes that never call the LLM

            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)
        self._client = client

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        kwargs = self._request_kwargs(messages, system=system, max_tokens=max_tokens, temperature=temperature)
        response = await self._call(self._messages_api().create, kwargs)
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if not text:
            raise AIInvalidOutputError()
        return LLMResponse(
            text=text,
            model=getattr(response, "model", self.model),
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
        )

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[StructuredT],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT:
        kwargs = self._request_kwargs(messages, system=system, max_tokens=max_tokens, temperature=None)
        kwargs["output_format"] = schema
        response = await self._call(self._messages_api().parse, kwargs)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise AIInvalidOutputError()
        try:
            return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        except ValidationError as exc:
            raise AIInvalidOutputError() from exc

    # -- internals ---------------------------------------------------------------------

    def _messages_api(self) -> Any:
        return self._client.beta.messages if self._server_fallbacks else self._client.messages

    def _request_kwargs(
        self, messages: list[LLMMessage], *, system: str | None, max_tokens: int | None, temperature: float | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self._max_tokens,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
        }
        if system:
            kwargs["system"] = system
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        chosen_temperature = temperature if temperature is not None else self._temperature
        if chosen_temperature is not None:
            kwargs["temperature"] = chosen_temperature
        if self._server_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def _call(self, method: Any, kwargs: dict[str, Any]) -> Any:
        import anthropic

        try:
            response = await method(**kwargs)
        except anthropic.APITimeoutError as exc:
            logger.warning("ai.llm.timeout", provider=self.name, model=self.model)
            raise AIProviderError() from exc
        except anthropic.RateLimitError as exc:
            logger.warning("ai.llm.rate_limited", provider=self.name, model=self.model)
            raise AIProviderError() from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            logger.error("ai.llm.auth_failed", provider=self.name, model=self.model)
            raise AIUnavailableError() from exc
        except anthropic.APIStatusError as exc:
            logger.error("ai.llm.api_error", provider=self.name, model=self.model, status=exc.status_code)
            raise AIProviderError() from exc
        except anthropic.APIConnectionError as exc:
            logger.error("ai.llm.connection_error", provider=self.name, model=self.model)
            raise AIProviderError() from exc
        except (ValidationError, ValueError) as exc:  # structured output failed to parse
            logger.warning("ai.llm.invalid_output", provider=self.name, model=self.model)
            raise AIInvalidOutputError() from exc

        usage = getattr(response, "usage", None)
        report_usage(getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        stop_reason = getattr(response, "stop_reason", None)
        logger.info(
            "ai.llm.completed",
            provider=self.name,
            model=getattr(response, "model", self.model),
            stop_reason=stop_reason,
            request_id=getattr(response, "_request_id", None),
        )
        if stop_reason == "refusal":
            raise AIProviderError("The AI assistant could not answer this request.")
        if stop_reason == "max_tokens":
            raise AIInvalidOutputError()
        return response
