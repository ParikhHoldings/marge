"""Create the current Marge schema.

Revision ID: 20260516_0001
Revises:
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260516_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pastor_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pastor_name", sa.String(), nullable=True),
        sa.Column("church_name", sa.String(), nullable=True),
        sa.Column("role_title", sa.String(), nullable=True),
        sa.Column("congregation_size", sa.String(), nullable=True),
        sa.Column("church_context", sa.Text(), nullable=True),
        sa.Column("ministry_priorities", sa.Text(), nullable=True),
        sa.Column("followup_pain", sa.Text(), nullable=True),
        sa.Column("weekly_rhythm", sa.Text(), nullable=True),
        sa.Column("communication_style", sa.Text(), nullable=True),
        sa.Column("tools_in_use", sa.Text(), nullable=True),
        sa.Column("guardrails", sa.Text(), nullable=True),
        sa.Column("onboarding_complete", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pastor_profiles_id", "pastor_profiles", ["id"], unique=False)

    op.create_table(
        "church_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("church_name", sa.String(), nullable=False),
        sa.Column("pastor_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_church_accounts_id", "church_accounts", ["id"], unique=False)
    op.create_index("ix_church_accounts_slug", "church_accounts", ["slug"], unique=True)
    op.create_index("ix_church_accounts_token_hash", "church_accounts", ["token_hash"], unique=True)

    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("rock_id", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("anniversary", sa.Date(), nullable=True),
        sa.Column("last_attendance", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_members_account_id", "members", ["account_id"], unique=False)
    op.create_index("ix_members_id", "members", ["id"], unique=False)
    op.create_index("ix_members_rock_id", "members", ["rock_id"], unique=True)

    op.create_table(
        "visitors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("follow_up_day1_sent", sa.Boolean(), nullable=True),
        sa.Column("follow_up_day3_sent", sa.Boolean(), nullable=True),
        sa.Column("follow_up_week2_sent", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_visitors_account_id", "visitors", ["account_id"], unique=False)
    op.create_index("ix_visitors_id", "visitors", ["id"], unique=False)

    op.create_table(
        "account_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_users_account_id", "account_users", ["account_id"], unique=False)
    op.create_index("ix_account_users_email", "account_users", ["email"], unique=False)
    op.create_index("ix_account_users_id", "account_users", ["id"], unique=False)
    op.create_index("ix_account_users_role", "account_users", ["role"], unique=False)
    op.create_index("ix_account_users_token_hash", "account_users", ["token_hash"], unique=True)

    op.create_table(
        "account_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_sessions_account_id", "account_sessions", ["account_id"], unique=False)
    op.create_index("ix_account_sessions_expires_at", "account_sessions", ["expires_at"], unique=False)
    op.create_index("ix_account_sessions_id", "account_sessions", ["id"], unique=False)
    op.create_index("ix_account_sessions_revoked_at", "account_sessions", ["revoked_at"], unique=False)
    op.create_index("ix_account_sessions_token_hash", "account_sessions", ["token_hash"], unique=True)
    op.create_index("ix_account_sessions_user_id", "account_sessions", ["user_id"], unique=False)

    op.create_table(
        "account_pastor_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("pastor_name", sa.String(), nullable=True),
        sa.Column("church_name", sa.String(), nullable=True),
        sa.Column("role_title", sa.String(), nullable=True),
        sa.Column("congregation_size", sa.String(), nullable=True),
        sa.Column("church_context", sa.Text(), nullable=True),
        sa.Column("ministry_priorities", sa.Text(), nullable=True),
        sa.Column("followup_pain", sa.Text(), nullable=True),
        sa.Column("weekly_rhythm", sa.Text(), nullable=True),
        sa.Column("communication_style", sa.Text(), nullable=True),
        sa.Column("tools_in_use", sa.Text(), nullable=True),
        sa.Column("guardrails", sa.Text(), nullable=True),
        sa.Column("onboarding_complete", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_pastor_profiles_account_id", "account_pastor_profiles", ["account_id"], unique=True)
    op.create_index("ix_account_pastor_profiles_id", "account_pastor_profiles", ["id"], unique=False)

    op.create_table(
        "care_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.Enum("hospital", "crisis", "grief", "general", name="carecategoryenum"), nullable=False),
        sa.Column("status", sa.Enum("active", "resolved", name="carestatusenum"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_contact", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_care_notes_account_id", "care_notes", ["account_id"], unique=False)
    op.create_index("ix_care_notes_id", "care_notes", ["id"], unique=False)

    op.create_table(
        "prayer_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by", sa.String(), nullable=True),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=True),
        sa.Column("status", sa.Enum("active", "answered", "expired", name="prayerstatusenum"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prayer_requests_account_id", "prayer_requests", ["account_id"], unique=False)
    op.create_index("ix_prayer_requests_id", "prayer_requests", ["id"], unique=False)

    op.create_table(
        "member_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("context_tag", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_member_notes_account_id", "member_notes", ["account_id"], unique=False)
    op.create_index("ix_member_notes_id", "member_notes", ["id"], unique=False)

    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("auth_type", sa.String(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("config_hint", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_connections_account_id", "integration_connections", ["account_id"], unique=False)
    op.create_index("ix_integration_connections_id", "integration_connections", ["id"], unique=False)
    op.create_index("ix_integration_connections_provider", "integration_connections", ["provider"], unique=False)

    op.create_table(
        "integration_oauth_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_oauth_states_account_id", "integration_oauth_states", ["account_id"], unique=False)
    op.create_index("ix_integration_oauth_states_id", "integration_oauth_states", ["id"], unique=False)
    op.create_index("ix_integration_oauth_states_provider", "integration_oauth_states", ["provider"], unique=False)
    op.create_index("ix_integration_oauth_states_state", "integration_oauth_states", ["state"], unique=True)
    op.create_index("ix_integration_oauth_states_user_id", "integration_oauth_states", ["user_id"], unique=False)

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_type", sa.String(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_token_present", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_credentials_account_id", "integration_credentials", ["account_id"], unique=False)
    op.create_index("ix_integration_credentials_id", "integration_credentials", ["id"], unique=False)
    op.create_index("ix_integration_credentials_provider", "integration_credentials", ["provider"], unique=False)
    op.create_index("ix_integration_credentials_user_id", "integration_credentials", ["user_id"], unique=False)

    op.create_table(
        "integration_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("read_enabled", sa.Boolean(), nullable=True),
        sa.Column("write_enabled", sa.Boolean(), nullable=True),
        sa.Column("require_approval", sa.Boolean(), nullable=True),
        sa.Column("allowed_actions", sa.Text(), nullable=True),
        sa.Column("privacy_mode", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_policies_account_id", "integration_policies", ["account_id"], unique=False)
    op.create_index("ix_integration_policies_id", "integration_policies", ["id"], unique=False)
    op.create_index("ix_integration_policies_provider", "integration_policies", ["provider"], unique=False)

    op.create_table(
        "assistant_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("external_provider", sa.String(), nullable=True),
        sa.Column("related_type", sa.String(), nullable=True),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("privacy_level", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("skipped_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_actions_account_id", "assistant_actions", ["account_id"], unique=False)
    op.create_index("ix_assistant_actions_action_type", "assistant_actions", ["action_type"], unique=False)
    op.create_index("ix_assistant_actions_dedupe_key", "assistant_actions", ["dedupe_key"], unique=True)
    op.create_index("ix_assistant_actions_id", "assistant_actions", ["id"], unique=False)
    op.create_index("ix_assistant_actions_status", "assistant_actions", ["status"], unique=False)

    op.create_table(
        "connected_context_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("item_type", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("subtitle", sa.String(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["action_id"], ["assistant_actions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connected_context_items_account_id", "connected_context_items", ["account_id"], unique=False)
    op.create_index("ix_connected_context_items_external_id", "connected_context_items", ["external_id"], unique=False)
    op.create_index("ix_connected_context_items_id", "connected_context_items", ["id"], unique=False)
    op.create_index("ix_connected_context_items_item_type", "connected_context_items", ["item_type"], unique=False)
    op.create_index("ix_connected_context_items_occurred_at", "connected_context_items", ["occurred_at"], unique=False)
    op.create_index("ix_connected_context_items_provider", "connected_context_items", ["provider"], unique=False)
    op.create_index("ix_connected_context_items_thread_id", "connected_context_items", ["thread_id"], unique=False)

    op.create_table(
        "assistant_chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=True),
        sa.Column("action_count", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_chat_messages_account_id", "assistant_chat_messages", ["account_id"], unique=False)
    op.create_index("ix_assistant_chat_messages_created_at", "assistant_chat_messages", ["created_at"], unique=False)
    op.create_index("ix_assistant_chat_messages_id", "assistant_chat_messages", ["id"], unique=False)
    op.create_index("ix_assistant_chat_messages_intent", "assistant_chat_messages", ["intent"], unique=False)
    op.create_index("ix_assistant_chat_messages_mode", "assistant_chat_messages", ["mode"], unique=False)
    op.create_index("ix_assistant_chat_messages_role", "assistant_chat_messages", ["role"], unique=False)
    op.create_index("ix_assistant_chat_messages_user_id", "assistant_chat_messages", ["user_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("connected_item_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["church_accounts.id"]),
        sa.ForeignKeyConstraint(["action_id"], ["assistant_actions.id"]),
        sa.ForeignKeyConstraint(["connected_item_id"], ["connected_context_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_account_id", "audit_logs", ["account_id"], unique=False)
    op.create_index("ix_audit_logs_action_id", "audit_logs", ["action_id"], unique=False)
    op.create_index("ix_audit_logs_connected_item_id", "audit_logs", ["connected_item_id"], unique=False)
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"], unique=False)
    op.create_index("ix_audit_logs_id", "audit_logs", ["id"], unique=False)
    op.create_index("ix_audit_logs_provider", "audit_logs", ["provider"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_provider", table_name="audit_logs")
    op.drop_index("ix_audit_logs_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_connected_item_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_account_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_assistant_chat_messages_user_id", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_role", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_mode", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_intent", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_id", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_created_at", table_name="assistant_chat_messages")
    op.drop_index("ix_assistant_chat_messages_account_id", table_name="assistant_chat_messages")
    op.drop_table("assistant_chat_messages")

    op.drop_index("ix_connected_context_items_thread_id", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_provider", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_occurred_at", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_item_type", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_id", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_external_id", table_name="connected_context_items")
    op.drop_index("ix_connected_context_items_account_id", table_name="connected_context_items")
    op.drop_table("connected_context_items")

    op.drop_index("ix_assistant_actions_status", table_name="assistant_actions")
    op.drop_index("ix_assistant_actions_id", table_name="assistant_actions")
    op.drop_index("ix_assistant_actions_dedupe_key", table_name="assistant_actions")
    op.drop_index("ix_assistant_actions_action_type", table_name="assistant_actions")
    op.drop_index("ix_assistant_actions_account_id", table_name="assistant_actions")
    op.drop_table("assistant_actions")

    op.drop_index("ix_integration_policies_provider", table_name="integration_policies")
    op.drop_index("ix_integration_policies_id", table_name="integration_policies")
    op.drop_index("ix_integration_policies_account_id", table_name="integration_policies")
    op.drop_table("integration_policies")

    op.drop_index("ix_integration_credentials_user_id", table_name="integration_credentials")
    op.drop_index("ix_integration_credentials_provider", table_name="integration_credentials")
    op.drop_index("ix_integration_credentials_id", table_name="integration_credentials")
    op.drop_index("ix_integration_credentials_account_id", table_name="integration_credentials")
    op.drop_table("integration_credentials")

    op.drop_index("ix_integration_oauth_states_user_id", table_name="integration_oauth_states")
    op.drop_index("ix_integration_oauth_states_state", table_name="integration_oauth_states")
    op.drop_index("ix_integration_oauth_states_provider", table_name="integration_oauth_states")
    op.drop_index("ix_integration_oauth_states_id", table_name="integration_oauth_states")
    op.drop_index("ix_integration_oauth_states_account_id", table_name="integration_oauth_states")
    op.drop_table("integration_oauth_states")

    op.drop_index("ix_integration_connections_provider", table_name="integration_connections")
    op.drop_index("ix_integration_connections_id", table_name="integration_connections")
    op.drop_index("ix_integration_connections_account_id", table_name="integration_connections")
    op.drop_table("integration_connections")

    op.drop_index("ix_member_notes_id", table_name="member_notes")
    op.drop_index("ix_member_notes_account_id", table_name="member_notes")
    op.drop_table("member_notes")

    op.drop_index("ix_prayer_requests_id", table_name="prayer_requests")
    op.drop_index("ix_prayer_requests_account_id", table_name="prayer_requests")
    op.drop_table("prayer_requests")

    op.drop_index("ix_care_notes_id", table_name="care_notes")
    op.drop_index("ix_care_notes_account_id", table_name="care_notes")
    op.drop_table("care_notes")

    op.drop_index("ix_account_pastor_profiles_id", table_name="account_pastor_profiles")
    op.drop_index("ix_account_pastor_profiles_account_id", table_name="account_pastor_profiles")
    op.drop_table("account_pastor_profiles")

    op.drop_index("ix_account_sessions_user_id", table_name="account_sessions")
    op.drop_index("ix_account_sessions_token_hash", table_name="account_sessions")
    op.drop_index("ix_account_sessions_revoked_at", table_name="account_sessions")
    op.drop_index("ix_account_sessions_id", table_name="account_sessions")
    op.drop_index("ix_account_sessions_expires_at", table_name="account_sessions")
    op.drop_index("ix_account_sessions_account_id", table_name="account_sessions")
    op.drop_table("account_sessions")

    op.drop_index("ix_account_users_token_hash", table_name="account_users")
    op.drop_index("ix_account_users_role", table_name="account_users")
    op.drop_index("ix_account_users_id", table_name="account_users")
    op.drop_index("ix_account_users_email", table_name="account_users")
    op.drop_index("ix_account_users_account_id", table_name="account_users")
    op.drop_table("account_users")

    op.drop_index("ix_visitors_id", table_name="visitors")
    op.drop_index("ix_visitors_account_id", table_name="visitors")
    op.drop_table("visitors")

    op.drop_index("ix_members_rock_id", table_name="members")
    op.drop_index("ix_members_id", table_name="members")
    op.drop_index("ix_members_account_id", table_name="members")
    op.drop_table("members")

    op.drop_index("ix_church_accounts_token_hash", table_name="church_accounts")
    op.drop_index("ix_church_accounts_slug", table_name="church_accounts")
    op.drop_index("ix_church_accounts_id", table_name="church_accounts")
    op.drop_table("church_accounts")

    op.drop_index("ix_pastor_profiles_id", table_name="pastor_profiles")
    op.drop_table("pastor_profiles")
