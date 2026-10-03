from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

StructuredT = TypeVar("StructuredT", bound=BaseModel)


class LLMMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMUsage:
    """Token usage reported by a provider for the current call (filled via `report_usage`)."""

    input_tokens: int | None = None
    output_tokens: int | None = None


# Per-task sink set by the observability wrapper; providers report usage into it when they know it.
usage_sink: ContextVar[LLMUsage | None] = ContextVar("llm_usage_sink", default=None)


def report_usage(input_tokens: int | None, output_tokens: int | None) -> None:
    sink = usage_sink.get()
    if sink is not None:
        sink.input_tokens, sink.output_tokens = input_tokens, output_tokens


class LLMResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-neutral LLM interface. Business logic depends on this, never on a vendor SDK."""

    name: str

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[StructuredT],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT:
        """Return output validated against `schema`; raise AIInvalidOutputError otherwise."""
        ...
