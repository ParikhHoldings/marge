"""baseline schema for current SQLAlchemy models"""

from alembic import op
import sqlalchemy as sa


revision = "20260415_0001"
down_revision = None
branch_labels = None
depends_on = None


care_category_enum = sa.Enum(
    "hospital",
    "crisis",
    "grief",
    "general",
    name="carecategoryenum",
)
care_status_enum = sa.Enum(
    "active",
    "resolved",
    name="carestatusenum",
)
prayer_status_enum = sa.Enum(
    "active",
    "answered",
    "expired",
    name="prayerstatusenum",
)


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rock_id", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("anniversary", sa.Date(), nullable=True),
        sa.Column("last_attendance", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_members_id"), "members", ["id"], unique=False)
    op.create_index(op.f("ix_members_rock_id"), "members", ["rock_id"], unique=True)

    op.create_table(
        "visitors",
        sa.Column("id", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_visitors_id"), "visitors", ["id"], unique=False)

    op.create_table(
        "care_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("category", care_category_enum, nullable=False),
        sa.Column("status", care_status_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_contact", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_care_notes_id"), "care_notes", ["id"], unique=False)

    op.create_table(
        "prayer_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by", sa.String(), nullable=True),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=True),
        sa.Column("status", prayer_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prayer_requests_id"), "prayer_requests", ["id"], unique=False)

    op.create_table(
        "member_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("context_tag", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_member_notes_id"), "member_notes", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_member_notes_id"), table_name="member_notes")
    op.drop_table("member_notes")

    op.drop_index(op.f("ix_prayer_requests_id"), table_name="prayer_requests")
    op.drop_table("prayer_requests")

    op.drop_index(op.f("ix_care_notes_id"), table_name="care_notes")
    op.drop_table("care_notes")

    op.drop_index(op.f("ix_visitors_id"), table_name="visitors")
    op.drop_table("visitors")

    op.drop_index(op.f("ix_members_rock_id"), table_name="members")
    op.drop_index(op.f("ix_members_id"), table_name="members")
    op.drop_table("members")

    prayer_status_enum.drop(op.get_bind(), checkfirst=True)
    care_status_enum.drop(op.get_bind(), checkfirst=True)
    care_category_enum.drop(op.get_bind(), checkfirst=True)
