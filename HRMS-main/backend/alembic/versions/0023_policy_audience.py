"""HR policy audiences (RBAC remediation step 7, approved decision 3)

- hr_policies.audience: ALL | MANAGERS | ADMINS (nested: MANAGERS = managers and admins, ADMINS = admins only).
  Existing rows become ALL, so every policy readable today stays readable. A CHECK constraint rejects any other value;
  application code also treats unknown values as unreadable (fail closed).
- ai_conversation_messages.source_policy_ids (AI-owned, nullable JSON): the policies an assistant reply cited, so a
  conversation replay re-checks the reader's current audience. Existing messages have none recorded (NULL).

Downgrade removes both columns and keeps every row. The pre-0023 code serves every PUBLISHED policy to everyone, so
after a rollback MANAGERS and ADMINS policies become readable by all employees: unpublish them before downgrading.

Revision ID: 0023_policy_audience
Revises: 0022_content_audience
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_policy_audience"
down_revision: Union[str, None] = "0022_content_audience"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hr_policies") as batch:
        batch.add_column(sa.Column("audience", sa.String(length=10), nullable=False, server_default="ALL"))
        batch.create_check_constraint("ck_hr_policies_audience", "audience IN ('ALL', 'MANAGERS', 'ADMINS')")
        batch.create_index("ix_hr_policies_audience", ["audience"], unique=False)
    with op.batch_alter_table("ai_conversation_messages") as batch:
        batch.add_column(sa.Column("source_policy_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_conversation_messages") as batch:
        batch.drop_column("source_policy_ids")
    with op.batch_alter_table("hr_policies") as batch:
        batch.drop_index("ix_hr_policies_audience")
        batch.drop_constraint("ck_hr_policies_audience", type_="check")
        batch.drop_column("audience")
