"""Copilot router data models.

`RouterClassification` is the ONLY thing accepted from the classifier LLM. It is routing metadata:
it names an intent and gives optional hints. It never carries identity, role, permissions, SQL,
URLs, methods or tool names, and unknown fields make it invalid (extra="forbid").
"""

import enum
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Intent(str, enum.Enum):
    POLICY = "POLICY"
    SQL = "SQL"
    ACTION = "ACTION"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


# Capability intents, in the fixed order a multi-intent plan executes them (reads before the action proposal).
CAPABILITY_ORDER: tuple[Intent, ...] = (Intent.POLICY, Intent.SQL, Intent.ACTION)


# --- untrusted model output -----------------------------------------------------------------


class RouterEntities(BaseModel):
    """Optional hints for future logging/UX. Never used for routing decisions or authorization."""

    model_config = ConfigDict(extra="forbid")

    employee_name: str | None = Field(default=None, max_length=100)
    resource: Literal["LEAVE", "TICKET", "PROJECT", "ANNOUNCEMENT", "EMPLOYEE", "POLICY"] | None = None
    date_hint: str | None = Field(default=None, max_length=40)


class RouterClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["POLICY", "SQL", "ACTION", "AMBIGUOUS", "UNSUPPORTED"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=200)
    clarification_question: str | None = Field(default=None, max_length=300)
    entities: RouterEntities = Field(default_factory=RouterEntities)


# --- routing plan (backend-built) -----------------------------------------------------------

Outcome = Literal["route", "clarify", "refuse"]
Source = Literal["rules", "llm", "mixed", "context"]


@dataclass(frozen=True, slots=True)
class PlanStep:
    intent: Intent
    question: str  # always the user's own text (the whole message or a clause of it)


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    outcome: Outcome
    intents: tuple[Intent, ...]
    steps: tuple[PlanStep, ...] = ()
    source: Source = "rules"
    reason: str = ""  # short machine reason, e.g. "rules:policy", "rules:prompt_injection"
    confidence: float = 1.0
    clarification_question: str | None = None
    follow_up: bool = False  # Phase 7: resolved with conversation context


# --- API response ---------------------------------------------------------------------------

PartStatus = Literal["answered", "clarification_required", "confirmation_required", "refused", "failed"]
ResponseStatus = Literal["answered", "clarification_required", "confirmation_required", "refused", "partial"]


class CopilotPart(BaseModel):
    route: Literal["POLICY", "SQL", "ACTION", "CLARIFICATION", "REFUSAL"]
    status: PartStatus
    message: str
    data: dict[str, Any] | None = None
    error_code: str | None = None


class CopilotResponse(BaseModel):
    request_id: str
    status: ResponseStatus
    code: str | None = None  # AI_CLARIFICATION_REQUIRED | AI_UNSUPPORTED_REQUEST | AI_CONFIRMATION_REQUIRED
    intents: list[str]
    routes: list[str]
    classification_source: str
    message: str
    parts: list[CopilotPart]
    conversation_id: str | None = None  # Phase 7
    follow_up: bool = False  # Phase 7: this turn was resolved using the previous turn
