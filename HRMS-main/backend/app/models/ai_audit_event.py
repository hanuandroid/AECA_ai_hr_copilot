from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIAuditEvent(Base):
    """AI-owned, append-only audit event (Phase 8).

    Written only by services/ai/audit/store.py (INSERT only). The database rejects UPDATE and
    DELETE on this table (triggers in migration 0020), so the application cannot rewrite history.
    Identity columns come from the server-side request scope, never from a client or model.
    `metadata_json` holds an allowlisted, typed AuditMetadata document: no prompts, answers, SQL text,
    result rows, tokens or secrets.
    """

    __tablename__ = "ai_audit_events"
    __table_args__ = (
        Index("ix_ai_audit_events_employee_created", "employee_id", "created_at"),
        Index("ix_ai_audit_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True)
    request_id: Mapped[str] = mapped_column(String(32), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    route: Mapped[str | None] = mapped_column(String(60), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
