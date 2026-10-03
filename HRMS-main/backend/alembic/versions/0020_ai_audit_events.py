"""AI audit events (Phase 8, AI-owned, append-only)

Revision ID: 0020_ai_audit_events
Revises: 0019_ai_conversations
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_ai_audit_events"
down_revision: Union[str, None] = "0019_ai_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("route", sa.String(length=60), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(op.f("ix_ai_audit_events_request_id"), "ai_audit_events", ["request_id"], unique=False)
    op.create_index(op.f("ix_ai_audit_events_conversation_id"), "ai_audit_events", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_ai_audit_events_created_at"), "ai_audit_events", ["created_at"], unique=False)
    op.create_index("ix_ai_audit_events_employee_created", "ai_audit_events", ["employee_id", "created_at"], unique=False)
    op.create_index("ix_ai_audit_events_type_created", "ai_audit_events", ["event_type", "created_at"], unique=False)
    # Append-only at the database level: the application can never rewrite or delete audit history.
    op.execute(
        "CREATE TRIGGER trg_ai_audit_events_no_update BEFORE UPDATE ON ai_audit_events "
        "BEGIN SELECT RAISE(ABORT, 'ai_audit_events is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_ai_audit_events_no_delete BEFORE DELETE ON ai_audit_events "
        "BEGIN SELECT RAISE(ABORT, 'ai_audit_events is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ai_audit_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_audit_events_no_update")
    op.drop_index("ix_ai_audit_events_type_created", table_name="ai_audit_events")
    op.drop_index("ix_ai_audit_events_employee_created", table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_created_at"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_conversation_id"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_request_id"), table_name="ai_audit_events")
    op.drop_table("ai_audit_events")
