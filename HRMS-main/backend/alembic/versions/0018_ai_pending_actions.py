"""AI action confirmations (Phase 5, AI-owned table)

Revision ID: 0018_ai_pending_actions
Revises: 0017_policy_chunks
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_ai_pending_actions"
down_revision: Union[str, None] = "0017_policy_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_pending_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("target_employee_id", sa.Integer(), nullable=True),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result_code", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_pending_actions_id"), "ai_pending_actions", ["id"], unique=False)
    op.create_index(op.f("ix_ai_pending_actions_token_hash"), "ai_pending_actions", ["token_hash"], unique=True)
    op.create_index(op.f("ix_ai_pending_actions_employee_id"), "ai_pending_actions", ["employee_id"], unique=False)
    op.create_index(op.f("ix_ai_pending_actions_status"), "ai_pending_actions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_pending_actions_status"), table_name="ai_pending_actions")
    op.drop_index(op.f("ix_ai_pending_actions_employee_id"), table_name="ai_pending_actions")
    op.drop_index(op.f("ix_ai_pending_actions_token_hash"), table_name="ai_pending_actions")
    op.drop_index(op.f("ix_ai_pending_actions_id"), table_name="ai_pending_actions")
    op.drop_table("ai_pending_actions")
