from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HRPolicyEvent(Base):
    """Append-only audit trail of policy publication changes (migration 0021; UPDATE/DELETE blocked by triggers)."""

    __tablename__ = "hr_policy_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("hr_policies.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # SUBMITTED | PUBLISHED | REJECTED | ARCHIVED
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    actor_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
