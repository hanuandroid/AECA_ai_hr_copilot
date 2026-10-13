"""Read-only report of confirmed actions whose outcome needs a human check (production hardening Phase 2, B-11).

- EXECUTING for longer than the threshold: the process may have stopped between claiming the confirmation and recording
  the business API result.
- UNKNOWN: the execution deadline expired (B-10); the business change may or may not have been applied.

The report never changes data and never retries anything. An operator verifies each item in the HR system (e.g. the
leave request's status) and resolves it there. It lists identifiers and timestamps only: no payloads or summaries.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_pending_action import AIPendingAction

DEFAULT_EXECUTING_THRESHOLD_MINUTES = 15


@dataclass(frozen=True)
class ActionNeedingReview:
    confirmation_record_id: int
    action: str
    status: str
    employee_id: int
    target_employee_id: int | None
    resource_id: int | None
    claimed_at: str | None
    result_code: str | None


async def actions_needing_review(db: AsyncSession, *, executing_threshold_minutes: int = DEFAULT_EXECUTING_THRESHOLD_MINUTES,
                                 now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    stale_cutoff = now - timedelta(minutes=executing_threshold_minutes)
    rows = (await db.execute(
        select(AIPendingAction)
        .where(or_(AIPendingAction.status == "UNKNOWN",
                   (AIPendingAction.status == "EXECUTING") & (AIPendingAction.used_at < stale_cutoff)))
        .order_by(AIPendingAction.id.asc())
    )).scalars().all()
    return [asdict(ActionNeedingReview(row.id, row.action, row.status, row.employee_id, row.target_employee_id,
                                       row.resource_id, row.used_at.isoformat() if row.used_at else None, row.result_code))
            for row in rows]
