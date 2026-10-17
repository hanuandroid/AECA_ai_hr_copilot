from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIConversation(Base):
    """AI-owned copilot conversation (Phase 7). Private to its owner: every lookup is scoped by employee_id.

    `public_id` (UUID4) is the only identifier exposed by the API, so ids cannot be enumerated.
    `message_count` doubles as the sequence allocator (atomic UPDATE ... RETURNING).
    No role, permission or scope is stored here: authorization is rebuilt on every request.
    Written only by services/ai/conversation/service.py.
    """

    __tablename__ = "ai_conversations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_ai_conversations_status"),
        CheckConstraint("message_count >= 0", name="ck_ai_conversations_message_count"),
        Index("ix_ai_conversations_owner_status_updated", "employee_id", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AIConversationMessage(Base):
    """One persisted message. USER rows hold the (redacted) user text and, for follow-ups, the resolved
    question that was routed. ASSISTANT rows hold the (redacted) reply text plus minimal routing metadata.
    Never stored: prompts, SQL, rows, confirmation tokens, JWTs, roles or permissions.
    `source_policy_ids` (migration 0023) lists the policies an ASSISTANT reply cited, so replay can re-check the
    reader's current policy audience; it is never forwarded to any service."""

    __tablename__ = "ai_conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_ai_conversation_messages_sequence"),
        CheckConstraint("role IN ('USER', 'ASSISTANT')", name="ck_ai_conversation_messages_role"),
        CheckConstraint("sequence >= 1", name="ck_ai_conversation_messages_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    resolved_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str] = mapped_column(String(32))
    route: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_policy_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
