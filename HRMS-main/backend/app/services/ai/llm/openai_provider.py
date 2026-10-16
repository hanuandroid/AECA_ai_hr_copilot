"""OpenAI implementation of LLMProvider. The only module importing the OpenAI SDK.

API choice: the Responses API (`responses.create`). Structured output uses a strict JSON-schema text format built from
the requested Pydantic model (see `strict_json_schema`), and the returned JSON is then validated locally against that
model exactly like every other provider's output: model output is untrusted and is never accepted unvalidated.

- Model ids come from AI_OPENAI_MODEL / AI_OPENAI_FAST_MODEL (no built-in default); the key only from OPENAI_API_KEY.
- `store=False` on every request, so responses are not kept for later retrieval through the API. This is NOT
  Zero Data Retention: retention and training settings depend on the OpenAI organisation/project and contract
  (see docs/production-hardening/data-processing.md).
- The API base URL is pinned to the public endpoint, so an OPENAI_BASE_URL environment variable cannot silently
  redirect HR data elsewhere.
- `temperature` and reasoning effort are sent only when explicitly configured (some models reject them).
- Prompts, completions and keys are never logged; only provider, model, status, request ids and error types.
"""

import json
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from app.services.ai.errors import AIInvalidOutputError, AIProviderError, AIUnavailableError
from app.services.ai.llm.base import LLMMessage, LLMResponse, StructuredT, report_usage

logger = structlog.get_logger()

OPENAI_API_BASE_URL = "https://api.openai.com/v1"
# JSON-schema keywords kept when building the strict schema. Value constraints (maxLength, minimum, pattern, ...)
# are dropped from the request because local Pydantic validation enforces them anyway; this keeps requests valid
# even where the server's strict mode does not accept a given constraint keyword.
_STRUCTURAL_KEYWORDS = {"type", "enum", "const", "description", "$ref"}


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> JSON schema in the shape strict structured outputs require: every object closed
    (additionalProperties: false) with all properties required; optional fields stay nullable via anyOf."""
    return _strict_node(schema.model_json_schema())


def _strict_node(node: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "$defs"):
            result[key] = {name: _strict_node(sub) for name, sub in value.items()}
        elif key == "items":
            result[key] = _strict_node(value)
        elif key == "anyOf":
            result["anyOf"] = [_strict_node(sub) for sub in value]
        elif key == "allOf":
            if len(value) != 1:  # Pydantic only emits single-element allOf (a wrapped $ref)
                raise ValueError("unsupported schema construct for strict structured output: allOf")
            result.update(_strict_node(value[0]))
        elif key in _STRUCTURAL_KEYWORDS:
            result[key] = value
    if "$ref" in result:  # a reference must stand alone
        return {"$ref": result["$ref"]}
    if result.get("type") == "object" or "properties" in result:
        result["type"] = "object"
        result.setdefault("properties", {})
        result["required"] = list(result["properties"])
        result["additionalProperties"] = False
    return result


class OpenAILLMProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout_seconds: float,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        client: Any | None = None,
    ):
        if (not api_key and client is None) or not model:
            raise AIUnavailableError()
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        if client is None:
            import openai  # lazy: keep the SDK out of processes that never call the LLM

            client = openai.AsyncOpenAI(api_key=api_key, base_url=OPENAI_API_BASE_URL, timeout=timeout_seconds,
                                        max_retries=2)
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
        response = await self._call(kwargs)
        text = getattr(response, "output_text", "") or ""
        if not text:
            raise AIInvalidOutputError()
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            model=getattr(response, "model", None) or self.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
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
        kwargs["text"] = {"format": {"type": "json_schema", "name": schema.__name__[:64],
                                     "schema": strict_json_schema(schema), "strict": True}}
        response = await self._call(kwargs)
        text = getattr(response, "output_text", "") or ""
        if not text:
            raise AIInvalidOutputError()
        try:
            return schema.model_validate(json.loads(text))
        except (ValidationError, ValueError) as exc:  # malformed JSON or schema violation
            logger.warning("ai.llm.invalid_output", provider=self.name, model=self.model)
            raise AIInvalidOutputError() from exc

    # -- internals ---------------------------------------------------------------------

    def _request_kwargs(
        self, messages: list[LLMMessage], *, system: str | None, max_tokens: int | None, temperature: float | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": [{"role": message.role, "content": message.content} for message in messages],
            "max_output_tokens": max_tokens or self._max_tokens,
            "store": False,
        }
        if system:
            kwargs["instructions"] = system
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        chosen_temperature = temperature if temperature is not None else self._temperature
        if chosen_temperature is not None:
            kwargs["temperature"] = chosen_temperature
        return kwargs

    async def _call(self, kwargs: dict[str, Any]) -> Any:
        import openai

        try:
            response = await self._client.responses.create(**kwargs)
        except openai.APITimeoutError as exc:  # before APIConnectionError (its parent class)
            logger.warning("ai.llm.timeout", provider=self.name, model=self.model)
            raise AIProviderError() from exc
        except openai.RateLimitError as exc:
            logger.warning("ai.llm.rate_limited", provider=self.name, model=self.model)
            raise AIProviderError() from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            logger.error("ai.llm.auth_failed", provider=self.name, model=self.model)
            raise AIUnavailableError() from exc
        except openai.NotFoundError as exc:  # unknown or unavailable model for this project
            logger.error("ai.llm.model_unavailable", provider=self.name, model=self.model)
            raise AIUnavailableError() from exc
        except openai.APIStatusError as exc:
            logger.error("ai.llm.api_error", provider=self.name, model=self.model, status=exc.status_code)
            raise AIProviderError() from exc
        except openai.APIConnectionError as exc:
            logger.error("ai.llm.connection_error", provider=self.name, model=self.model)
            raise AIProviderError() from exc

        usage = getattr(response, "usage", None)
        report_usage(getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        status = getattr(response, "status", None)
        logger.info("ai.llm.completed", provider=self.name, model=getattr(response, "model", None) or self.model,
                    status=status, request_id=getattr(response, "_request_id", None))
        if _refused(response):
            raise AIProviderError("The AI assistant could not answer this request.")
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "content_filter":
                raise AIProviderError("The AI assistant could not answer this request.")
            raise AIInvalidOutputError()  # e.g. max_output_tokens: a truncated answer is never used
        if status not in (None, "completed"):
            raise AIProviderError()
        return response


def _refused(response: Any) -> bool:
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) == "message":
            if any(getattr(part, "type", None) == "refusal" for part in getattr(item, "content", None) or []):
                return True
    return False
