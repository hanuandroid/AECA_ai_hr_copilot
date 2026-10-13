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
from contextlib import asynccontextmanager
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
from app.services.ai.errors import AIBudgetExceededError, AIBusyError, AIRateLimitedError

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
    async def slot(self):
        semaphore = self._get()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=settings.ai_llm_queue_timeout_seconds)
        except TimeoutError as exc:
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
