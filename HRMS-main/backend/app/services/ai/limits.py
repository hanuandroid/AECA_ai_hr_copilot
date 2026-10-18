"""AI abuse and cost controls (production hardening B-09). All checks run server-side, BEFORE any model call.

- Per-user request rate: keyed by the authenticated employee id from the DB-backed AIUserContext (never by anything the
  client sends), shared by every model-calling AI endpoint, so new conversations or alternate endpoints cannot reset it.
- Per-user daily token budget (and optional cost cap): summed from AI_PROVIDER_RESPONSE audit events of the current UTC
  day. Audit rows are flushed after each response, so concurrent in-flight requests can overshoot slightly (bounded by
  the concurrency limit).
- Process-wide model-call concurrency: at most AI_MAX_CONCURRENT_LLM_CALLS; callers wait up to the queue timeout.

In-memory state: valid for a single-worker, single-instance deployment only (see app/core/rate_limit.py).
"""

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import SlidingWindowLimiter
from app.db.session import get_db
from app.models.ai_audit_event import AIAuditEvent
from app.services.ai.authorization import AIUserContext, get_ai_user_context
from app.services.ai.errors import (
    AIBudgetExceededError,
    AIBusyError,
    AIRateLimitedError,
    AIRequestLimitError,
    AIRequestTimeoutError,
)

logger = structlog.get_logger()

ai_request_limiter = SlidingWindowLimiter()


async def usage_today(db: AsyncSession, employee_id: int) -> tuple[int, float]:
    """(total tokens, estimated cost USD) recorded for the employee since 00:00 UTC today."""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    tokens = func.json_extract(AIAuditEvent.metadata_json, "$.total_tokens")
    cost = func.json_extract(AIAuditEvent.metadata_json, "$.estimated_cost_usd")
    row = (await db.execute(
        select(func.coalesce(func.sum(tokens), 0), func.coalesce(func.sum(cost), 0.0)).where(
            AIAuditEvent.employee_id == employee_id,
            AIAuditEvent.event_type == "AI_PROVIDER_RESPONSE",
            AIAuditEvent.created_at >= start,
        )
    )).one()
    return int(row[0] or 0), float(row[1] or 0.0)


async def enforce_ai_limits(ctx: AIUserContext = Depends(get_ai_user_context), db: AsyncSession = Depends(get_db)) -> AIUserContext:
    """FastAPI dependency for model-calling AI endpoints. Runs after authentication/INACTIVE rejection."""
    if not ai_request_limiter.allow(f"employee:{ctx.employee_id}", settings.ai_rate_limit_per_minute):
        logger.warning("ai.limits.rate_limited", employee_id=ctx.employee_id)
        raise AIRateLimitedError()
    budget, cap = settings.ai_daily_token_budget, settings.ai_daily_cost_cap_usd
    if budget > 0 or cap is not None:
        tokens, cost = await usage_today(db, ctx.employee_id)
        if (budget > 0 and tokens >= budget) or (cap is not None and cost >= cap):
            logger.warning("ai.limits.budget_exceeded", employee_id=ctx.employee_id)
            raise AIBudgetExceededError()
    return ctx


class _LLMSlots:
    """Process-wide semaphore, re-created per event loop (asyncio primitives are loop-bound)."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self.in_flight = 0
        self.peak = 0
        self.acquired = 0  # total model calls admitted (every provider call passes through here)

    def _get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._loop, self._semaphore = loop, asyncio.Semaphore(max(1, settings.ai_max_concurrent_llm_calls))
        return self._semaphore

    @asynccontextmanager
    async def slot(self, deadline: float | None = None):
        """`deadline`: the request's monotonic deadline (AIS-10); waiting for a slot never outlives it."""
        semaphore = self._get()
        queue_timeout = settings.ai_llm_queue_timeout_seconds
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        deadline_first = remaining is not None and remaining < queue_timeout
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=remaining if deadline_first else queue_timeout)
        except TimeoutError as exc:
            if deadline_first:
                logger.warning("ai.limits.request_deadline", stage="queue")
                raise AIRequestTimeoutError() from exc
            logger.warning("ai.limits.llm_busy", limit=settings.ai_max_concurrent_llm_calls)
            raise AIBusyError() from exc
        self.in_flight += 1
        self.acquired += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            yield
        finally:
            self.in_flight -= 1
            semaphore.release()

    def reset(self) -> None:
        self._loop, self._semaphore, self.in_flight, self.peak, self.acquired = None, None, 0, 0, 0


llm_slots = _LLMSlots()


# --- AIS-10: per-request model-call budget and end-to-end deadline ---------------------------------------------------


@dataclass
class RequestBudget:
    """One AI HTTP request: at most `max_calls` model calls (0 = unlimited), all finished before `deadline`
    (monotonic seconds; None = no deadline)."""

    max_calls: int
    deadline: float | None
    calls: int = 0

    def remaining(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()


_request_budget: ContextVar[RequestBudget | None] = ContextVar("ai_request_budget", default=None)


def new_request_budget() -> RequestBudget:
    deadline = settings.ai_request_deadline_seconds
    return RequestBudget(max_calls=max(0, settings.ai_max_model_calls_per_request),
                         deadline=time.monotonic() + deadline if deadline > 0 else None)


def charge_model_call() -> float | None:
    """Called before every model call. Counts the call against the current request's budget and returns its monotonic
    deadline (None: no budget, e.g. operator scripts and evaluations outside an HTTP request)."""
    budget = _request_budget.get()
    if budget is None:
        return None
    remaining = budget.remaining()
    if remaining is not None and remaining <= 0:
        logger.warning("ai.limits.request_deadline", stage="before_call", calls=budget.calls)
        raise AIRequestTimeoutError()
    if budget.max_calls and budget.calls >= budget.max_calls:
        logger.warning("ai.limits.request_call_budget", limit=budget.max_calls)
        raise AIRequestLimitError()
    budget.calls += 1
    return budget.deadline


async def within_deadline(awaitable, deadline: float | None):
    """Await a provider call, cancelling it at the request deadline. SDK retries happen inside the call, so they
    cannot outlive the deadline either. Providers map their own timeouts to AI errors, so a TimeoutError here is ours."""
    if deadline is None:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=max(0.0, deadline - time.monotonic()))
    except TimeoutError as exc:
        logger.warning("ai.limits.request_deadline", stage="provider_call")
        raise AIRequestTimeoutError() from exc


class AIRequestBudgetMiddleware:
    """Pure ASGI: one RequestBudget per AI HTTP request (same paths as the audit scope). It only bounds model calls;
    it never inspects bodies or changes responses itself."""

    PREFIXES = ("/api/v1/chat", "/api/v1/ai")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.PREFIXES):
            await self.app(scope, receive, send)
            return
        token = _request_budget.set(new_request_budget())
        try:
            await self.app(scope, receive, send)
        finally:
            _request_budget.reset(token)
