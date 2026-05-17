"""Add church voice and tradition to pastor profiles.

Revision ID: 20260517_0005
Revises: 20260517_0004
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260517_0005"
down_revision = "20260517_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pastor_profiles", sa.Column("faith_tradition", sa.Text(), nullable=True))
    op.add_column("account_pastor_profiles", sa.Column("faith_tradition", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("account_pastor_profiles", "faith_tradition")
    op.drop_column("pastor_profiles", "faith_tradition")
