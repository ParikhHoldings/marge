"""
ORM models for Marge — AI Pastoral Assistant.

Tables:
  - Member         : congregation member records
  - Visitor        : first-time visitor tracking
  - CareNote       : active care / crisis cases
  - PrayerRequest  : prayer requests with lifecycle
  - MemberNote     : pastoral CRM notes per member
  - AssistantChatMessage: persisted assistant conversation history
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime,
    Boolean, ForeignKey, Enum, Index
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class CareCategoryEnum(str, enum.Enum):
    hospital = "hospital"
    crisis = "crisis"
    grief = "grief"
    general = "general"


class CareStatusEnum(str, enum.Enum):
    active = "active"
    resolved = "resolved"


class PrayerStatusEnum(str, enum.Enum):
    active = "active"
    answered = "answered"
    expired = "expired"


class Member(Base):
    """A congregation member. May be synced from Rock RMS via rock_id."""

    __tablename__ = "members"
    __table_args__ = (
        Index("ix_members_account_rock_id", "account_id", "rock_id", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    rock_id = Column(String, nullable=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    birthday = Column(Date, nullable=True)
    anniversary = Column(Date, nullable=True)
    last_attendance = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    care_notes = relationship("CareNote", back_populates="member", cascade="all, delete-orphan")
    prayer_requests = relationship("PrayerRequest", back_populates="member", cascade="all, delete-orphan")
    notes = relationship("MemberNote", back_populates="member", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return " ".join(part for part in [self.first_name, self.last_name] if part).strip()

    def __repr__(self):
        return f"<Member id={self.id} name={self.full_name!r}>"


class Visitor(Base):
    """A first-time (or repeat) visitor. Drives the follow-up sequence."""

    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    visit_date = Column(Date, nullable=False)
    source = Column(String, nullable=True)  # walk-in, web, referral, etc.

    # Follow-up sequence tracking
    follow_up_day1_sent = Column(Boolean, default=False)
    follow_up_day3_sent = Column(Boolean, default=False)
    follow_up_week2_sent = Column(Boolean, default=False)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def full_name(self) -> str:
        return " ".join(part for part in [self.first_name, self.last_name] if part).strip()

    def __repr__(self):
        return f"<Visitor id={self.id} name={self.full_name!r} visit={self.visit_date}>"


class CareNote(Base):
    """
    An active pastoral care case.

    category: hospital | crisis | grief | general
    status:   active | resolved
    """

    __tablename__ = "care_notes"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    category = Column(
        Enum(CareCategoryEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=CareCategoryEnum.general,
    )
    status = Column(
        Enum(CareStatusEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=CareStatusEnum.active,
    )
    description = Column(Text, nullable=True)
    last_contact = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="care_notes")

    def __repr__(self):
        return f"<CareNote id={self.id} member_id={self.member_id} category={self.category} status={self.status}>"


class PrayerRequest(Base):
    """
    A prayer request submitted by or on behalf of a member (or anonymous).

    status: active | answered | expired
    """

    __tablename__ = "prayer_requests"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=True)  # nullable for anonymous requests
    submitted_by = Column(String, nullable=True)  # name/label if member_id is None
    request_text = Column(Text, nullable=False)
    is_private = Column(Boolean, default=False)
    status = Column(
        Enum(PrayerStatusEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=PrayerStatusEnum.active,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="prayer_requests")

    def __repr__(self):
        return f"<PrayerRequest id={self.id} status={self.status} private={self.is_private}>"


class MemberNote(Base):
    """
    A pastoral CRM note attached to a member.

    context_tag: optional label such as 'hospital', 'counseling', 'conversation', 'prayer', etc.
    """

    __tablename__ = "member_notes"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    note_text = Column(Text, nullable=False)
    context_tag = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="notes")

    def __repr__(self):
        return f"<MemberNote id={self.id} member_id={self.member_id} tag={self.context_tag!r}>"


class PastorProfile(Base):
    """
    Pastor and church context Marge uses to personalize first-run onboarding,
    briefings, drafts, and proactive suggestions.
    """

    __tablename__ = "pastor_profiles"

    id = Column(Integer, primary_key=True, index=True)
    pastor_name = Column(String, nullable=True)
    church_name = Column(String, nullable=True)
    role_title = Column(String, nullable=True)
    congregation_size = Column(String, nullable=True)
    church_context = Column(Text, nullable=True)
    faith_tradition = Column(Text, nullable=True)
    ministry_priorities = Column(Text, nullable=True)
    followup_pain = Column(Text, nullable=True)
    support_preferences = Column(Text, nullable=True)
    weekly_rhythm = Column(Text, nullable=True)
    communication_style = Column(Text, nullable=True)
    tools_in_use = Column(Text, nullable=True)
    guardrails = Column(Text, nullable=True)
    onboarding_complete = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<PastorProfile id={self.id} pastor={self.pastor_name!r} church={self.church_name!r}>"


class ChurchAccount(Base):
    """
    A church workspace/account boundary.

    This is intentionally lightweight for the local MVP. The token hash lets
    API clients scope assistant profile data without storing the raw token.
    """

    __tablename__ = "church_accounts"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    church_name = Column(String, nullable=False)
    pastor_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ChurchAccount id={self.id} slug={self.slug!r}>"


class AccountUser(Base):
    """
    A person who can access a church workspace.

    The local MVP stores a per-user token hash so Marge can separate owner/admin
    setup permissions from day-to-day staff access without persisting raw tokens.
    """

    __tablename__ = "account_users"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=False, index=True)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True, index=True)
    role = Column(String, nullable=False, default="pastor", index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AccountUser id={self.id} account_id={self.account_id} role={self.role!r}>"


class AccountSession(Base):
    """
    Revocable, expiring session token for a workspace user.

    Invite/signup user tokens bootstrap local auth; sessions are the shorter-lived
    credential normal clients should use for ongoing API requests.
    """

    __tablename__ = "account_sessions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("account_users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AccountSession id={self.id} account_id={self.account_id} user_id={self.user_id}>"


class AccountLoginLink(Base):
    """
    Short-lived passwordless login link.

    Only the token hash is stored. The raw token is delivered once by email and
    exchanged for a normal revocable AccountSession.
    """

    __tablename__ = "account_login_links"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("account_users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AccountLoginLink id={self.id} account_id={self.account_id} user_id={self.user_id}>"


class AccountPastorProfile(Base):
    """
    Account-scoped pastor/church context.

    Mirrors PastorProfile fields so Marge can personalize each church account
    while preserving the existing singleton profile fallback for local demos.
    """

    __tablename__ = "account_pastor_profiles"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=False, unique=True, index=True)
    pastor_name = Column(String, nullable=True)
    church_name = Column(String, nullable=True)
    role_title = Column(String, nullable=True)
    congregation_size = Column(String, nullable=True)
    church_context = Column(Text, nullable=True)
    faith_tradition = Column(Text, nullable=True)
    ministry_priorities = Column(Text, nullable=True)
    followup_pain = Column(Text, nullable=True)
    support_preferences = Column(Text, nullable=True)
    weekly_rhythm = Column(Text, nullable=True)
    communication_style = Column(Text, nullable=True)
    tools_in_use = Column(Text, nullable=True)
    guardrails = Column(Text, nullable=True)
    onboarding_complete = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AccountPastorProfile account_id={self.account_id} pastor={self.pastor_name!r}>"


class IntegrationConnection(Base):
    """
    Non-secret connector status. Secrets, API keys, and OAuth tokens should
    live in IntegrationCredential or an environment-managed secret store, never
    in this table.
    """

    __tablename__ = "integration_connections"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="planned")
    auth_type = Column(String, nullable=False, default="oauth")
    scopes = Column(Text, nullable=True)
    config_hint = Column(Text, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<IntegrationConnection provider={self.provider!r} status={self.status!r}>"


class IntegrationOAuthState(Base):
    """
    Short-lived OAuth state used to protect provider callback flows.

    The random state is stored server-side before redirecting the pastor to a
    provider. Callback handlers must consume it once and reject expired or
    unknown states.
    """

    __tablename__ = "integration_oauth_states"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("account_users.id"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, unique=True, index=True)
    redirect_uri = Column(Text, nullable=False)
    scopes = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<IntegrationOAuthState provider={self.provider!r} consumed={self.consumed_at is not None}>"


class IntegrationCredential(Base):
    """
    Encrypted connector credential payload.

    OAuth token responses and workspace API-key payloads are encrypted with
    MARGE_ENCRYPTION_KEY before they are persisted. Credential payloads are
    never returned by the API.
    """

    __tablename__ = "integration_credentials"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("account_users.id"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    token_ciphertext = Column(Text, nullable=False)
    token_type = Column(String, nullable=True)
    scopes = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    refresh_token_present = Column(Boolean, default=False)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<IntegrationCredential provider={self.provider!r} expires_at={self.expires_at!r}>"


class IntegrationPolicy(Base):
    """
    Church-level connector policy.

    OAuth consent connects the tool; this policy decides whether Marge is
    allowed to write back through that connector after pastor approval.
    """

    __tablename__ = "integration_policies"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    read_enabled = Column(Boolean, default=True)
    write_enabled = Column(Boolean, default=False)
    require_approval = Column(Boolean, default=True)
    allowed_actions = Column(Text, nullable=True)
    privacy_mode = Column(String, nullable=False, default="pastoral")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<IntegrationPolicy provider={self.provider!r} write_enabled={self.write_enabled!r}>"


class AssistantAction(Base):
    """
    A persisted piece of assistant work that Marge has prepared for pastor review.

    Marge may proactively draft or propose actions, but this queue preserves the
    approval boundary before anything is sent or written to an external system.
    """

    __tablename__ = "assistant_actions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    dedupe_key = Column(String, nullable=True, unique=True, index=True)
    action_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    external_provider = Column(String, nullable=True)
    related_type = Column(String, nullable=True)
    related_id = Column(Integer, nullable=True)
    privacy_level = Column(String, nullable=False, default="pastoral")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    skipped_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<AssistantAction id={self.id} type={self.action_type!r} status={self.status!r}>"


class ConnectedContextItem(Base):
    """
    Normalized read-side cache of external tool context.

    Marge stores only enough provider data to brief the pastor and prepare
    approval-safe actions. Provider payloads are kept compact and should not
    contain secrets.
    """

    __tablename__ = "connected_context_items"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    item_type = Column(String, nullable=False, index=True)
    external_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False)
    subtitle = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=True, index=True)
    action_id = Column(Integer, ForeignKey("assistant_actions.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ConnectedContextItem provider={self.provider!r} type={self.item_type!r} external_id={self.external_id!r}>"


class AssistantChatMessage(Base):
    """
    Persisted assistant conversation turns.

    Chat can contain sensitive pastoral context, so rows are account-scoped and
    should not be mirrored into audit logs or connector payloads.
    """

    __tablename__ = "assistant_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("account_users.id"), nullable=True, index=True)
    role = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    intent = Column(String, nullable=True, index=True)
    mode = Column(String, nullable=False, default="live", index=True)
    saved = Column(Boolean, default=False)
    action_count = Column(Integer, default=0)
    response_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<AssistantChatMessage id={self.id} role={self.role!r} account_id={self.account_id}>"


class AuditLog(Base):
    """
    Security and action audit trail.

    Store event metadata only. Do not put access tokens, refresh tokens, API
    keys, prayer text, or raw provider payloads in audit rows.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("church_accounts.id"), nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    actor = Column(String, nullable=False, default="pastor")
    summary = Column(Text, nullable=False)
    provider = Column(String, nullable=True, index=True)
    action_id = Column(Integer, ForeignKey("assistant_actions.id"), nullable=True, index=True)
    connected_item_id = Column(Integer, ForeignKey("connected_context_items.id"), nullable=True, index=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<AuditLog id={self.id} event={self.event_type!r}>"
