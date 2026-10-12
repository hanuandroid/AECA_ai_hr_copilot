"""Controlled audit vocabulary and the typed, allowlisted metadata document.

Sensitive data is blocked by design:
- event types, statuses and reason codes are closed enums (nothing user- or model-supplied);
- AuditMetadata has a fixed set of fields (extra="forbid"); every text field must be a short
  identifier-like token (no spaces in values beyond a few safe characters, ≤ 120 chars) and must not
  look like a secret/PAN/IFSC/long number; there is no field for prompts, answers, SQL text or rows.
"""

import enum
import re

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.ai.redaction import contains_sensitive_value


class AuditEventType(str, enum.Enum):
    AI_HTTP_REQUEST = "AI_HTTP_REQUEST"  # one per AI HTTP request: endpoint, status, latency

    CHAT_REQUEST = "CHAT_REQUEST"
    CHAT_CLASSIFIED = "CHAT_CLASSIFIED"
    CHAT_ROUTED = "CHAT_ROUTED"
    CHAT_RESPONSE = "CHAT_RESPONSE"

    POLICY_REQUEST = "POLICY_REQUEST"
    POLICY_RESPONSE = "POLICY_RESPONSE"

    SQL_REQUEST = "SQL_REQUEST"
    SQL_RESPONSE = "SQL_RESPONSE"
    SQL_SECURITY_REJECTION = "SQL_SECURITY_REJECTION"

    ACTION_PROPOSED = "ACTION_PROPOSED"
    ACTION_CONFIRMATION_REQUESTED = "ACTION_CONFIRMATION_REQUESTED"
    ACTION_CONFIRMED = "ACTION_CONFIRMED"
    ACTION_REJECTED = "ACTION_REJECTED"
    ACTION_EXPIRED = "ACTION_EXPIRED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_OUTCOME_UNKNOWN = "ACTION_OUTCOME_UNKNOWN"  # execution deadline exceeded; may or may not have applied

    AUTHORIZATION_ALLOWED = "AUTHORIZATION_ALLOWED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"

    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    REFUSAL = "REFUSAL"

    AI_PROVIDER_REQUEST = "AI_PROVIDER_REQUEST"
    AI_PROVIDER_RESPONSE = "AI_PROVIDER_RESPONSE"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"

    AI_ERROR = "AI_ERROR"


class AuditStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DENIED = "DENIED"
    REFUSED = "REFUSED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    PARTIAL = "PARTIAL"


class ReasonCode(str, enum.Enum):
    """Safe, closed reason codes for refusals and denials (never the offending text)."""

    PROMPT_INJECTION = "PROMPT_INJECTION"
    SENSITIVE_DATA_REQUEST = "SENSITIVE_DATA_REQUEST"
    DESTRUCTIVE_REQUEST = "DESTRUCTIVE_REQUEST"
    DIRECT_SYSTEM_ACCESS = "DIRECT_SYSTEM_ACCESS"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    MULTIPLE_ACTIONS = "MULTIPLE_ACTIONS"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    CONFIRMATION_IN_CHAT = "CONFIRMATION_IN_CHAT"
    INACTIVE_USER = "INACTIVE_USER"
    CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_REQUEST = "INVALID_REQUEST"


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+\-/{}]{0,120}$")
_MAX_LIST = 20


def _check_token(value: str) -> str:
    if not _TOKEN_RE.match(value) or contains_sensitive_value(value):
        raise ValueError("audit metadata values must be short identifier-like tokens")
    return value


class AuditMetadata(BaseModel):
    """The ONLY metadata an audit event can carry. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    # request / routing
    endpoint: str | None = None
    actor_role: str | None = None  # role of the authenticated user at request time (from the DB; audit only)
    method: str | None = None
    http_status: int | None = None
    intents: list[str] | None = None
    routes: list[str] | None = None
    source: str | None = None
    reason: str | None = None  # internal routing reason, e.g. "rules:policy"
    reason_code: ReasonCode | None = None
    confidence: float | None = None
    follow_up: bool | None = None
    parts: int | None = None
    message_chars: int | None = None
    message_hash: str | None = None  # keyed hash prefix (correlation only; not reversible to text)
    response_chars: int | None = None
    response_hash: str | None = None
    error_code: str | None = None

    # authorization
    capability: str | None = None
    scope: str | None = None
    target_checked: bool | None = None

    # provider
    provider: str | None = None
    model: str | None = None
    operation: str | None = None
    schema_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None

    # policy
    grounded: bool | None = None
    sources: int | None = None

    # sql (never SQL text or values)
    query_type: str | None = None
    tables: list[str] | None = None
    row_count: int | None = None
    truncated: bool | None = None
    security_result: str | None = None

    # actions
    action: str | None = None
    target_employee_id: int | None = None
    resource_id: int | None = None
    confirmation_record_id: int | None = None
    confirmation_state: str | None = None

    @field_validator("endpoint", "actor_role", "method", "source", "reason", "message_hash", "response_hash", "error_code", "capability",
                     "scope", "provider", "model", "operation", "schema_name", "query_type", "security_result", "action",
                     "confirmation_state")
    @classmethod
    def _tokens(cls, value: str | None) -> str | None:
        return None if value is None else _check_token(value)

    @field_validator("intents", "routes", "tables")
    @classmethod
    def _token_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > _MAX_LIST:
            raise ValueError("too many items")
        return [_check_token(item) for item in value]

    @field_validator("http_status", "parts", "message_chars", "response_chars", "input_tokens", "output_tokens",
                     "total_tokens", "sources", "row_count", "target_employee_id", "resource_id", "confirmation_record_id")
    @classmethod
    def _non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must be >= 0")
        return value
