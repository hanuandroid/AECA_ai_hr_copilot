from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PolicyChunk(Base):
    """AI-owned retrieval index over hr_policies (one hr_policies row == one policy document).

    Written only by the policy ingestion service; never by request-time AI code.
    """

    __tablename__ = "policy_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("hr_policies.id", ondelete="CASCADE"), index=True)
    index_key: Mapped[str] = mapped_column(String(64))
    source_filename: Mapped[str] = mapped_column(String(255))
    policy_title: Mapped[str] = mapped_column(String(220))
    category: Mapped[str] = mapped_column(String(60))
    section: Mapped[str] = mapped_column(String(255))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("CURRENT_TIMESTAMP"), nullable=False
    )
