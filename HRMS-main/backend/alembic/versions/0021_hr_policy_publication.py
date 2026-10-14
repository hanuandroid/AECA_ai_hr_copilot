"""HR policy publication workflow (production hardening closure, approved 2026-10-14)

ADMIN-only publication of organisation-wide policies: managers submit, admins publish or reject. Adds a status and a
per-title version to hr_policies, review metadata, and an append-only hr_policy_events audit trail. Existing rows
become PUBLISHED version 1, so current behaviour (every existing policy listed and indexed) is unchanged.

Revision ID: 0021_hr_policy_publication
Revises: 0020_ai_audit_events
Create Date: 2026-10-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_hr_policy_publication"
down_revision: Union[str, None] = "0020_ai_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hr_policies") as batch:
        batch.add_column(sa.Column("status", sa.String(length=20), nullable=False, server_default="PUBLISHED"))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("reviewed_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_hr_policies_reviewed_by", "employees", ["reviewed_by"], ["id"])
        batch.create_index("ix_hr_policies_status", ["status"], unique=False)

    op.create_table(
        "hr_policy_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor_employee_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["hr_policies.id"]),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hr_policy_events_policy_id", "hr_policy_events", ["policy_id"], unique=False)
    # Append-only at the database level, like ai_audit_events.
    op.execute(
        "CREATE TRIGGER trg_hr_policy_events_no_update BEFORE UPDATE ON hr_policy_events "
        "BEGIN SELECT RAISE(ABORT, 'hr_policy_events is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_hr_policy_events_no_delete BEFORE DELETE ON hr_policy_events "
        "BEGIN SELECT RAISE(ABORT, 'hr_policy_events is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_hr_policy_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_hr_policy_events_no_update")
    op.drop_index("ix_hr_policy_events_policy_id", table_name="hr_policy_events")
    op.drop_table("hr_policy_events")
    with op.batch_alter_table("hr_policies") as batch:
        batch.drop_index("ix_hr_policies_status")
        batch.drop_constraint("fk_hr_policies_reviewed_by", type_="foreignkey")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by")
        batch.drop_column("version")
        batch.drop_column("status")
