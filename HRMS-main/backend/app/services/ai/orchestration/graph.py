"""LangGraph copilot router: a finite, controlled dispatcher - not an autonomous agent.

    START -> classify -+-> policy -> [sql] -> [action] -> finalize -> END
                       +-> sql -> [action] ----------^
                       +-> action -------------------^
                       +-> clarification ------------^
                       +-> refusal ------------------^
                       +-> error --------------------^

- Nodes are a fixed set; every edge target is checked against ALLOWED_TRANSITIONS (anything else
  goes to `error`). There are no dynamic node names, tools, URLs, HTTP methods, code or DB writes.
- Capability nodes only call the existing services with the trusted ctx from `deps`:
  PolicyRagService.answer, SQLAgentService.answer, ActionAgentService.propose. The graph never
  confirms or executes an action; confirmation stays on POST /chat/actions/confirm (Phase 5).
- Loop prevention: no node runs twice, plans hold at most one step per capability (POLICY -> SQL ->
  ACTION order, edges never go backwards), MAX_GRAPH_ITERATIONS is checked before every
  non-terminal node, and LangGraph's recursion_limit is a final backstop.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from langchain_core.runnables import RunnableConfig  # LangGraph's config type (langgraph dependency)
from langgraph.graph import END, START, StateGraph

from app.services.ai.audit.events import AuditEventType, AuditStatus, ReasonCode
from app.services.ai.audit.recorder import Timer, reason_code_for, record
from app.services.ai.authorization import AIUserContext
from app.services.ai.errors import AIError, AIMaxIterationsError, AIRoutingError
from app.services.ai.orchestration.models import Intent, RoutingPlan
from app.services.ai.orchestration.state import NODE_WRITES, CopilotState

logger = structlog.get_logger()

MAX_GRAPH_ITERATIONS = 5  # classify + at most 3 capability nodes + finalize
RECURSION_LIMIT = 10  # LangGraph super-step backstop (> longest legal path of 5 nodes)

CAPABILITY_NODES: dict[str, str] = {Intent.POLICY.value: "policy", Intent.SQL.value: "sql", Intent.ACTION.value: "action"}
TERMINAL_NODES = frozenset({"error", "finalize"})

# The only legal transitions. Conditional edges are built from this table, and every routing
# decision is re-checked against it at runtime.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    START: frozenset({"classify"}),
    "classify": frozenset({"policy", "sql", "action", "clarification", "refusal", "error"}),
    "policy": frozenset({"sql", "action", "finalize", "error"}),
    "sql": frozenset({"action", "finalize", "error"}),
    "action": frozenset({"finalize", "error"}),
    "clarification": frozenset({"finalize"}),
    "refusal": frozenset({"finalize"}),
    "error": frozenset({"finalize"}),
    "finalize": frozenset({END}),
}

REFUSAL_MESSAGE = (
    "I can't help with that request. I can answer HR policy questions, look up HR information you're allowed "
    "to see, and prepare HR actions that you confirm yourself."
)


# --- dependencies (trusted; passed via config, never via state) -------------------------------


class RoutingClassifier(Protocol):
    async def plan(self, message: str) -> RoutingPlan: ...


class PolicyAnswerer(Protocol):
    async def answer(self, ctx: AIUserContext, question: object) -> Any: ...


class SQLAnswerer(Protocol):
    async def answer(self, ctx: AIUserContext, question: object) -> Any: ...


class ActionProposer(Protocol):
    """The graph may only PROPOSE actions. Confirmation is not part of this interface."""

    async def propose(self, ctx: AIUserContext, question: object) -> Any: ...


@dataclass(frozen=True, slots=True)
class GraphDeps:
    ctx: AIUserContext
    request_id: str
    classifier: RoutingClassifier
    policy: PolicyAnswerer
    sql: SQLAnswerer
    actions: ActionProposer
    conversation_id: str | None = None


def _deps(config: RunnableConfig | None) -> GraphDeps:
    deps = (config or {}).get("configurable", {}).get("deps")
    if not isinstance(deps, GraphDeps):
        raise AIRoutingError(detail="missing graph deps")
    return deps


# --- nodes ----------------------------------------------------------------------------------

NodeFn = Callable[[CopilotState, GraphDeps], Awaitable[dict[str, Any]]]


async def classify_node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
    context = state.get("conversation_context")
    timer = Timer()
    try:
        if context is None:
            plan = await deps.classifier.plan(state["message"])
        else:
            plan = await deps.classifier.plan(state["message"], context=context)
    except AIError as error:
        record(AuditEventType.CHAT_CLASSIFIED, AuditStatus.FAILED, duration_ms=timer.ms, error_code=error.code)
        return {"error_code": error.code, "error_message": error.message, "error_status": error.http_status}
    record(AuditEventType.CHAT_CLASSIFIED, AuditStatus.SUCCESS, duration_ms=timer.ms,
           intents=[intent.value for intent in plan.intents], source=plan.source, reason=plan.reason,
           confidence=plan.confidence, follow_up=plan.follow_up)
    if plan.outcome == "route":
        record(AuditEventType.CHAT_ROUTED, AuditStatus.SUCCESS, routes=[step.intent.value for step in plan.steps])
    return {
        "outcome": plan.outcome,
        "intents": [intent.value for intent in plan.intents],
        "plan": [{"route": step.intent.value, "question": step.question} for step in plan.steps],
        "classification_source": plan.source,
        "classification_reason": plan.reason,
        "confidence": plan.confidence,
        "clarification_question": plan.clarification_question,
        "follow_up": plan.follow_up,
        "step": 0,
    }


def _capability_node(intent: Intent) -> NodeFn:
    async def node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
        step = state.get("step", 0)
        plan = state.get("plan") or []
        if step >= len(plan) or plan[step].get("route") != intent.value:
            raise AIRoutingError(detail="plan/node mismatch")
        question = plan[step].get("question", "")
        try:
            part = await _call_service(intent, deps, question)
        except AIError as error:
            part = {"route": intent.value, "status": "failed", "message": error.message, "error_code": error.code,
                    "http_status": error.http_status}
        except Exception as exc:  # never leak internals; the request still terminates normally
            logger.error("ai.router.node_failed", node=intent.value, error=type(exc).__name__, request_id=deps.request_id)
            part = {"route": intent.value, "status": "failed", "message": AIRoutingError.message,
                    "error_code": AIRoutingError.code, "http_status": AIRoutingError.http_status}
        return {"parts": [part], "step": step + 1}

    node.__name__ = f"{intent.value.lower()}_node"
    return node


async def _call_service(intent: Intent, deps: GraphDeps, question: str) -> dict[str, Any]:
    if intent == Intent.POLICY:
        policy = await deps.policy.answer(deps.ctx, question)
        return {"route": "POLICY", "status": "answered", "message": policy.answer, "data": policy.model_dump()}
    if intent == Intent.SQL:
        sql = await deps.sql.answer(deps.ctx, question)
        return {"route": "SQL", "status": "answered", "message": sql.answer, "data": sql.model_dump(exclude_none=True)}
    if intent == Intent.ACTION:
        proposal = await deps.actions.propose(deps.ctx, question)
        # Propose never executes. Anything other than these two statuses is a contract violation.
        if proposal.status not in ("clarification_required", "confirmation_required"):
            raise AIRoutingError(detail="unexpected action status")
        return {"route": "ACTION", "status": proposal.status, "message": proposal.message,
                "data": proposal.model_dump(exclude_none=True)}
    raise AIRoutingError(detail="unknown capability")


async def clarification_node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
    question = state.get("clarification_question") or "Could you tell me more about what you need?"
    record(AuditEventType.CLARIFICATION_REQUESTED, AuditStatus.CLARIFICATION_REQUIRED, route="CLARIFICATION",
           reason_code=reason_code_for(state.get("classification_reason"), ReasonCode.AMBIGUOUS_REQUEST))
    return {"parts": [{"route": "CLARIFICATION", "status": "clarification_required", "message": question}]}


async def refusal_node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
    # Safe reason code only: the refused text is never recorded.
    record(AuditEventType.REFUSAL, AuditStatus.REFUSED, route="REFUSAL",
           reason_code=reason_code_for(state.get("classification_reason"), ReasonCode.UNSUPPORTED_REQUEST))
    return {"parts": [{"route": "REFUSAL", "status": "refused", "message": REFUSAL_MESSAGE}]}


def _error_node(max_iterations: int) -> NodeFn:
    async def error_node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
        if state.get("error_code"):
            return {}
        error: AIError = AIMaxIterationsError() if state.get("iterations", 0) >= max_iterations else AIRoutingError()
        return {"error_code": error.code, "error_message": error.message, "error_status": error.http_status}

    return error_node


async def finalize_node(state: CopilotState, deps: GraphDeps) -> dict[str, Any]:
    # Trusted state must be exactly what the backend put in; nothing may have rewritten it.
    if (state.get("request_id") != deps.request_id or state.get("employee_id") != deps.ctx.employee_id
            or state.get("role") != deps.ctx.role.value or state.get("conversation_id") != deps.conversation_id):
        logger.error("ai.router.trusted_state_mismatch", request_id=deps.request_id)
        raise AIRoutingError(detail="trusted state mismatch")
    return {}


def _guarded(name: str, fn: NodeFn, max_iterations: int):
    """Counts iterations, enforces MAX_GRAPH_ITERATIONS and the node's write allowlist."""
    allowed = NODE_WRITES[name]

    async def node(state: CopilotState, config: RunnableConfig) -> dict[str, Any]:
        deps = _deps(config)
        iterations = state.get("iterations", 0) + 1
        bookkeeping = {"visited": [name], "iterations": iterations}
        if name not in TERMINAL_NODES and iterations > max_iterations:
            error = AIMaxIterationsError()
            return {**bookkeeping, "error_code": error.code, "error_message": error.message, "error_status": error.http_status}
        update = await fn(state, deps) or {}
        illegal = set(update) - allowed
        if illegal:
            logger.error("ai.router.illegal_state_write", node=name, keys=sorted(illegal), request_id=deps.request_id)
            raise AIRoutingError(detail="illegal state write")
        return {**update, **bookkeeping}

    node.__name__ = f"{name}_node"
    return node


# --- routing --------------------------------------------------------------------------------


def _next_capability(state: CopilotState, max_iterations: int) -> str:
    if state.get("error_code") or state.get("iterations", 0) >= max_iterations:
        return "error"
    plan = state.get("plan") or []
    step = state.get("step", 0)
    if step >= len(plan):
        return "finalize"
    node = CAPABILITY_NODES.get(str(plan[step].get("route")))
    if node is None or node in (state.get("visited") or []):
        return "error"  # unknown route, or a cycle back to a node that already ran
    return node


def _router(source: str, max_iterations: int) -> Callable[[CopilotState], str]:
    def route(state: CopilotState) -> str:
        if state.get("error_code"):
            target = "error"
        elif source == "classify":
            outcome = state.get("outcome")
            target = {"refuse": "refusal", "clarify": "clarification"}.get(str(outcome)) or (
                _next_capability(state, max_iterations) if outcome == "route" else "error"
            )
        else:
            target = _next_capability(state, max_iterations)
        return target if target in ALLOWED_TRANSITIONS[source] else "error"

    route.__name__ = f"route_after_{source}"
    return route


def build_copilot_graph(*, max_iterations: int = MAX_GRAPH_ITERATIONS):
    nodes: dict[str, NodeFn] = {
        "classify": classify_node,
        "policy": _capability_node(Intent.POLICY),
        "sql": _capability_node(Intent.SQL),
        "action": _capability_node(Intent.ACTION),
        "clarification": clarification_node,
        "refusal": refusal_node,
        "error": _error_node(max_iterations),
        "finalize": finalize_node,
    }
    graph = StateGraph(CopilotState)
    for name, fn in nodes.items():
        graph.add_node(name, _guarded(name, fn, max_iterations))
    graph.add_edge(START, "classify")
    for source in ("classify", "policy", "sql", "action"):
        graph.add_conditional_edges(source, _router(source, max_iterations),
                                    {target: target for target in sorted(ALLOWED_TRANSITIONS[source])})
    for source in ("clarification", "refusal", "error"):
        graph.add_edge(source, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()  # no checkpointer: state is request-scoped and never persisted
