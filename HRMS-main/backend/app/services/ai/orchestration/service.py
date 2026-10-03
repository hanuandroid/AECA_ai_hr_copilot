"""CopilotService: entry point of the unified copilot (POST /api/v1/chat).

Order: trusted AIUserContext (INACTIVE rejected) -> message validation -> LangGraph router
(classify -> specialised service(s) -> finalize) -> response. Identity comes only from the ctx built
by `get_ai_user_context`; the request body cannot carry a user id, role or permissions.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Depends
from langgraph.errors import GraphRecursionError

from app.services.ai.actions.service import ActionAgentService, get_action_agent_service
from app.services.ai.audit.recorder import current_request_id
from app.services.ai.authorization import AIUserContext
from app.services.ai.errors import AIError, AIInactiveUserError, AIMaxIterationsError, AIRoutingError
from app.services.ai.orchestration.classifier import RouterClassifier, get_router_classifier
from app.services.ai.orchestration.graph import (
    RECURSION_LIMIT,
    ActionProposer,
    GraphDeps,
    PolicyAnswerer,
    RoutingClassifier,
    SQLAnswerer,
    build_copilot_graph,
)
from app.services.ai.orchestration.followup import ConversationContext
from app.services.ai.orchestration.models import CopilotPart, CopilotResponse
from app.services.ai.orchestration.state import CopilotState
from app.services.ai.rag.service import PolicyRagService, get_policy_rag_service
from app.services.ai.sql.service import SQLAgentService, get_sql_agent_service
from app.services.ai.validation import validate_question

logger = structlog.get_logger()

CLARIFICATION_REQUIRED = "AI_CLARIFICATION_REQUIRED"
UNSUPPORTED_REQUEST = "AI_UNSUPPORTED_REQUEST"
CONFIRMATION_REQUIRED = "AI_CONFIRMATION_REQUIRED"

_COMPILED_GRAPH = build_copilot_graph()  # compiled once; holds no per-request state


class _RoutedError(AIError):
    """Fallback carrier for an error code without a dedicated AIError class."""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message, code=code)
        self.http_status = http_status


def _error_classes(base: type[AIError] = AIError) -> list[type[AIError]]:
    found: list[type[AIError]] = []
    for subclass in base.__subclasses__():
        found.append(subclass)
        found.extend(_error_classes(subclass))
    return found


def _reraise(code: str, message: str, http_status: int) -> AIError:
    """Rebuild a node's error with the same class, code, safe message and HTTP status as the specialised
    service raised (so /chat keeps the error contract of /chat/policy, /chat/sql and /chat/actions)."""
    for cls in _error_classes():
        if cls is not _RoutedError and cls.code == code and cls.http_status == http_status:
            return cls(message)
    return _RoutedError(code, message, http_status)


@dataclass(frozen=True, slots=True)
class TurnResult:
    response: CopilotResponse
    resolved_question: str | None  # the user text actually routed, when it differs from the message (follow-ups)


class CopilotService:
    def __init__(
        self,
        *,
        classifier: RoutingClassifier,
        policy: PolicyAnswerer,
        sql: SQLAnswerer,
        actions: ActionProposer,
        graph: Any = None,
        recursion_limit: int = RECURSION_LIMIT,
    ):
        self._classifier = classifier
        self._policy, self._sql, self._actions = policy, sql, actions
        self._graph = graph or _COMPILED_GRAPH
        self._recursion_limit = recursion_limit
        self.last_state: CopilotState | None = None  # for tests/diagnostics (request-scoped object)

    async def answer(self, ctx: AIUserContext, message: object) -> CopilotResponse:
        """Single stateless request (Phase 6 contract)."""
        return (await self.answer_turn(ctx, message)).response

    async def answer_turn(
        self,
        ctx: AIUserContext,
        message: object,
        *,
        context: ConversationContext | None = None,
        request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> TurnResult:
        """One request, optionally with (untrusted) conversation context. Identity is always `ctx`."""
        request_id = request_id or current_request_id() or uuid.uuid4().hex
        started = time.monotonic()
        self.last_state = None
        record: dict[str, Any] = {"request_id": request_id, "employee_id": ctx.employee_id, "role": ctx.role.value,
                                  "conversation": conversation_id is not None}
        try:
            if not ctx.is_active:
                raise AIInactiveUserError()
            cleaned = validate_question(message)
            final = await self._run_graph(ctx, request_id, cleaned, context, conversation_id)
            self.last_state = final
            record.update(intents=final.get("intents"), routes=_routes(final), source=final.get("classification_source"),
                          reason=final.get("classification_reason"), nodes=final.get("visited"),
                          follow_up=bool(final.get("follow_up")))
            response = _build_response(request_id, final)
            record.update(status=response.status, code=response.code)
            return TurnResult(response=response, resolved_question=_resolved_question(cleaned, final))
        except AIError as error:
            record.update(status="error", error_code=error.code)
            raise
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            logger.info("ai.router", **record)  # never the message text, answers or rows

    async def _run_graph(self, ctx: AIUserContext, request_id: str, message: str,
                         context: ConversationContext | None = None, conversation_id: str | None = None) -> CopilotState:
        deps = GraphDeps(ctx=ctx, request_id=request_id, classifier=self._classifier, policy=self._policy, sql=self._sql,
                         actions=self._actions, conversation_id=conversation_id)
        initial: CopilotState = {"request_id": request_id, "employee_id": ctx.employee_id, "role": ctx.role.value,
                                 "message": message, "conversation_id": conversation_id, "iterations": 0, "visited": [],
                                 "parts": []}
        if context is not None:
            initial["conversation_context"] = context
        try:
            return await self._graph.ainvoke(initial, config={"configurable": {"deps": deps},
                                                              "recursion_limit": self._recursion_limit})
        except GraphRecursionError as exc:
            raise AIMaxIterationsError() from exc
        except AIError:
            raise
        except Exception as exc:
            logger.error("ai.router.graph_failed", error=type(exc).__name__, request_id=request_id)
            raise AIRoutingError() from exc


def _resolved_question(message: str, state: CopilotState) -> str | None:
    if not state.get("follow_up"):
        return None
    plan = state.get("plan") or []
    question = plan[0].get("question") if len(plan) == 1 else None
    return question if question and question != message else None


def _routes(state: CopilotState) -> list[str]:
    return [part["route"] for part in state.get("parts") or []]


def _build_response(request_id: str, state: CopilotState) -> CopilotResponse:
    if state.get("error_code"):
        raise _reraise(state["error_code"], state.get("error_message") or AIRoutingError.message,
                       state.get("error_status") or AIRoutingError.http_status)
    raw_parts = state.get("parts") or []
    if not raw_parts:
        raise AIRoutingError(detail="no parts")

    failed = [part for part in raw_parts if part["status"] == "failed"]
    if len(failed) == len(raw_parts):
        # Single capability (or everything failed): same error contract as the specialised endpoint.
        first = failed[0]
        raise _reraise(first["error_code"], first["message"], first.get("http_status") or AIRoutingError.http_status)

    parts = [CopilotPart(**{key: value for key, value in part.items() if key != "http_status"}) for part in raw_parts]
    statuses = {part.status for part in parts}
    if "refused" in statuses:
        status, code = "refused", UNSUPPORTED_REQUEST
    elif "confirmation_required" in statuses:
        status, code = "confirmation_required", CONFIRMATION_REQUIRED
    elif "clarification_required" in statuses:
        status, code = "clarification_required", CLARIFICATION_REQUIRED
    elif failed:
        status, code = "partial", None
    else:
        status, code = "answered", None

    return CopilotResponse(
        request_id=request_id,
        status=status,
        code=code,
        intents=list(state.get("intents") or []),
        routes=[part.route for part in parts],
        classification_source=state.get("classification_source") or "rules",
        message="\n\n".join(part.message for part in parts),
        parts=parts,
        follow_up=bool(state.get("follow_up")),
    )


def get_copilot_service(
    classifier: RouterClassifier = Depends(get_router_classifier),
    policy: PolicyRagService = Depends(get_policy_rag_service),
    sql: SQLAgentService = Depends(get_sql_agent_service),
    actions: ActionAgentService = Depends(get_action_agent_service),
) -> CopilotService:
    """FastAPI dependency. Reuses the specialised services' own dependencies (and their overrides)."""
    return CopilotService(classifier=classifier, policy=policy, sql=sql, actions=actions)
