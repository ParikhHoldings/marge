"""Scope Rock member IDs by church account.

Revision ID: 20260516_0003
Revises: 20260516_0002
Create Date: 2026-05-16
"""

from alembic import op


revision = "20260516_0003"
down_revision = "20260516_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_members_rock_id", table_name="members")
    op.create_index("ix_members_rock_id", "members", ["rock_id"], unique=False)
    op.create_index("ix_members_account_rock_id", "members", ["account_id", "rock_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_members_account_rock_id", table_name="members")
    op.drop_index("ix_members_rock_id", table_name="members")
    op.create_index("ix_members_rock_id", "members", ["rock_id"], unique=True)
