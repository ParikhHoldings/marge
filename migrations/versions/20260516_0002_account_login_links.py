"""Add passwordless account login links.

Revision ID: 20260516_0002
Revises: 20260516_0001
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260516_0002"
down_revision = "20260516_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_login_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_login_links_account_id", "account_login_links", ["account_id"], unique=False)
    op.create_index("ix_account_login_links_consumed_at", "account_login_links", ["consumed_at"], unique=False)
    op.create_index("ix_account_login_links_expires_at", "account_login_links", ["expires_at"], unique=False)
    op.create_index("ix_account_login_links_id", "account_login_links", ["id"], unique=False)
    op.create_index("ix_account_login_links_token_hash", "account_login_links", ["token_hash"], unique=True)
    op.create_index("ix_account_login_links_user_id", "account_login_links", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_account_login_links_user_id", table_name="account_login_links")
    op.drop_index("ix_account_login_links_token_hash", table_name="account_login_links")
    op.drop_index("ix_account_login_links_id", table_name="account_login_links")
    op.drop_index("ix_account_login_links_expires_at", table_name="account_login_links")
    op.drop_index("ix_account_login_links_consumed_at", table_name="account_login_links")
    op.drop_index("ix_account_login_links_account_id", table_name="account_login_links")
    op.drop_table("account_login_links")
