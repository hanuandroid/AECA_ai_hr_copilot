"""AI copilot conversations and messages (Phase 7, AI-owned tables)

Revision ID: 0019_ai_conversations
Revises: 0018_ai_pending_actions
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_ai_conversations"
down_revision: Union[str, None] = "0018_ai_pending_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_ai_conversations_status"),
        sa.CheckConstraint("message_count >= 0", name="ck_ai_conversations_message_count"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_conversations_public_id"), "ai_conversations", ["public_id"], unique=True)
    op.create_index(op.f("ix_ai_conversations_employee_id"), "ai_conversations", ["employee_id"], unique=False)
    op.create_index("ix_ai_conversations_owner_status_updated", "ai_conversations",
                    ["employee_id", "status", "updated_at"], unique=False)

    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resolved_content", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('USER', 'ASSISTANT')", name="ck_ai_conversation_messages_role"),
        sa.CheckConstraint("sequence >= 1", name="ck_ai_conversation_messages_sequence"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_ai_conversation_messages_sequence"),
    )


def downgrade() -> None:
    op.drop_table("ai_conversation_messages")
    op.drop_index("ix_ai_conversations_owner_status_updated", table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_employee_id"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_public_id"), table_name="ai_conversations")
    op.drop_table("ai_conversations")
