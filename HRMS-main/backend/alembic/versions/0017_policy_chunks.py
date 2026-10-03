"""policy chunks for AI policy retrieval (Phase 3, AI-owned table)

Revision ID: 0017_policy_chunks
Revises: 0016_employee_documents_uploaded_by
Create Date: 2026-09-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_policy_chunks"
down_revision: Union[str, None] = "0016_employee_documents_uploaded_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("index_key", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("policy_title", sa.String(length=220), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["hr_policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_policy_chunks_id"), "policy_chunks", ["id"], unique=False)
    op.create_index(op.f("ix_policy_chunks_chunk_id"), "policy_chunks", ["chunk_id"], unique=True)
    op.create_index(op.f("ix_policy_chunks_policy_id"), "policy_chunks", ["policy_id"], unique=False)
    op.create_index(op.f("ix_policy_chunks_embedding_model"), "policy_chunks", ["embedding_model"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_policy_chunks_embedding_model"), table_name="policy_chunks")
    op.drop_index(op.f("ix_policy_chunks_policy_id"), table_name="policy_chunks")
    op.drop_index(op.f("ix_policy_chunks_chunk_id"), table_name="policy_chunks")
    op.drop_index(op.f("ix_policy_chunks_id"), table_name="policy_chunks")
    op.drop_table("policy_chunks")
