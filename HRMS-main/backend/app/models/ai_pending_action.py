from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIPendingAction(Base):
    """AI-owned confirmation record for a proposed HR action (D6).

    Only the SHA-256 of the confirmation token is stored. The record binds user, action, target,
    resource and the exact request payload (binding_hash); it is single-use and time-limited.
    It is written only by services/ai/actions/confirmation.py, never by business code.
    """

    __tablename__ = "ai_pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(40))
    target_employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    binding_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)  # PENDING|EXECUTING|COMPLETED|FAILED|UNKNOWN|DENIED|EXPIRED|INVALID
    result_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
