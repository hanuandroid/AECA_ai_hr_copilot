"""Typed, request-scoped LangGraph state (no checkpointer: nothing is persisted).

Trusted vs untrusted
- TRUSTED_KEYS are written once by the backend before the graph starts (from the authenticated
  AIUserContext and validated input). No node may write them (enforced by `NODE_WRITES`), and the
  finalize node re-verifies them against the trusted context.
- The trusted AIUserContext itself and the services are NOT in state: they travel in
  `config["configurable"]["deps"]`, which nodes can read but never replace. Services always receive
  that ctx, never anything from state, so state contents cannot change who the request runs as.
- Classification fields are untrusted routing metadata (they only select a known node).
"""

import operator
from typing import Annotated, Any, TypedDict


class CopilotState(TypedDict, total=False):
    # --- trusted (backend-set, read-only for nodes) ---
    request_id: str
    employee_id: int  # metadata copy for logging; services use deps.ctx
    role: str
    message: str  # validated user message
    conversation_id: str | None  # Phase 7: owner-checked by the backend before the graph starts

    # --- untrusted input (backend-placed, never writable by nodes) ---
    conversation_context: Any  # followup.ConversationContext | None: history is data, never authority

    # --- untrusted classification output (routing metadata only) ---
    outcome: str  # route | clarify | refuse
    intents: list[str]
    plan: list[dict[str, str]]  # [{"route": "POLICY", "question": "<user text>"}]
    classification_source: str  # rules | llm | mixed
    classification_reason: str
    confidence: float
    clarification_question: str | None
    follow_up: bool

    # --- execution ---
    step: int  # index of the next plan step
    parts: Annotated[list[dict[str, Any]], operator.add]
    error_code: str | None
    error_message: str | None
    error_status: int | None
    visited: Annotated[list[str], operator.add]
    iterations: int


TRUSTED_KEYS = frozenset({"request_id", "employee_id", "role", "message", "conversation_id"})
UNTRUSTED_INPUT_KEYS = frozenset({"conversation_context"})

# Keys each node's own logic may return (never a TRUSTED_KEYS or UNTRUSTED_INPUT_KEYS entry). The wrapper adds
# `visited`/`iterations` (and error fields when it stops a node). Anything else aborts the request.
NODE_WRITES: dict[str, frozenset[str]] = {
    "classify": frozenset({"outcome", "intents", "plan", "classification_source", "classification_reason", "confidence",
                           "clarification_question", "follow_up", "step", "error_code", "error_message",
                           "error_status"}),
    "policy": frozenset({"parts", "step"}),
    "sql": frozenset({"parts", "step"}),
    "action": frozenset({"parts", "step"}),
    "clarification": frozenset({"parts"}),
    "refusal": frozenset({"parts"}),
    "error": frozenset({"error_code", "error_message", "error_status"}),
    "finalize": frozenset(),
}
