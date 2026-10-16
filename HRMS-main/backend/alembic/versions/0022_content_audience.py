"""Announcement and poll audience (RBAC remediation step 6, approved decision 5 / X-2)

Adds `audience` (ORG or TEAM) and `team_manager_id` to announcements and polls. ORG items are visible to everyone;
TEAM items to the team manager, that person's direct reports (employees.manager_id) and admins. Existing rows become
ORG with no team, so everything visible today stays visible. A CHECK constraint keeps the pair consistent (ORG has no
team; TEAM always has one) and rejects unknown audience values at the database level.

Downgrade removes both columns and keeps every row. The pre-0022 code shows all announcements and polls to everyone,
so after a rollback TEAM items become organisation-wide: review or remove TEAM items before downgrading in production.

Revision ID: 0022_content_audience
Revises: 0021_hr_policy_publication
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_content_audience"
down_revision: Union[str, None] = "0021_hr_policy_publication"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("announcements", "polls")
AUDIENCE_CHECK = (
    "(audience = 'ORG' AND team_manager_id IS NULL) OR (audience = 'TEAM' AND team_manager_id IS NOT NULL)"
)


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            # server_default backfills every existing row as ORG and keeps omitted inserts deterministic.
            batch.add_column(sa.Column("audience", sa.String(length=10), nullable=False, server_default="ORG"))
            batch.add_column(sa.Column("team_manager_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_team_manager_id", "employees", ["team_manager_id"], ["id"])
            batch.create_check_constraint(f"ck_{table}_audience", AUDIENCE_CHECK)
            batch.create_index(f"ix_{table}_audience", ["audience"], unique=False)
            batch.create_index(f"ix_{table}_team_manager_id", ["team_manager_id"], unique=False)


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_team_manager_id")
            batch.drop_index(f"ix_{table}_audience")
            batch.drop_constraint(f"ck_{table}_audience", type_="check")
            batch.drop_constraint(f"fk_{table}_team_manager_id", type_="foreignkey")
            batch.drop_column("team_manager_id")
            batch.drop_column("audience")
