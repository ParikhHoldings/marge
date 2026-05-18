"""Add pastor support preferences to ministry profiles.

Revision ID: 20260517_0006
Revises: 20260517_0005
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260517_0006"
down_revision = "20260517_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pastor_profiles", sa.Column("support_preferences", sa.Text(), nullable=True))
    op.add_column("account_pastor_profiles", sa.Column("support_preferences", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("account_pastor_profiles", "support_preferences")
    op.drop_column("pastor_profiles", "support_preferences")
