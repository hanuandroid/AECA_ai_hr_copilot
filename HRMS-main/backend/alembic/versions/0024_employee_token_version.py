"""Employee token version (finding B-17, approved 2026-10-17)

Adds `employees.token_version` (INTEGER NOT NULL, server default 0). Every password write increments it (self-service
change, admin reset, re-hire with a new password) and access tokens carry it as `tv`; a token whose `tv` differs from
the stored value is refused, so a password change revokes every earlier session. Existing rows start at 0, and tokens
issued before this migration (no `tv`) count as 0, so nobody is signed out by the upgrade itself.

Downgrade (development only, `deployment-runbook.md` §4.1): drops the column; the older code then keeps earlier tokens
valid after a password change until they expire. Never downgrade production data.

Revision ID: 0024_employee_token_version
Revises: 0023_policy_audience
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_employee_token_version"
down_revision: Union[str, None] = "0023_policy_audience"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.drop_column("token_version")
