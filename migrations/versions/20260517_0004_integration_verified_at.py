"""Track connector credential verification time.

Revision ID: 20260517_0004
Revises: 20260516_0003
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260517_0004"
down_revision = "20260516_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_connections", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column("integration_credentials", sa.Column("verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("integration_credentials", "verified_at")
    op.drop_column("integration_connections", "verified_at")
