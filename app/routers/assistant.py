"""
Assistant router — connected secretary desk, onboarding profile, integrations,
and chat responses grounded in Marge's current data.
"""

import base64
import html
import ipaddress
import json
import os
import re
import secrets
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode, urlparse
from datetime import date, datetime, timedelta
from typing import Any, List, Literal, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.integrations import rock as rock_sync
from app.models import (
    AuditLog,
    AccountPastorProfile,
    AccountSession,
    AccountLoginLink,
    AccountUser,
    AssistantChatMessage,
    CareNote,
    AssistantAction,
    ChurchAccount,
    ConnectedContextItem,
    IntegrationCredential,
    IntegrationConnection,
    IntegrationOAuthState,
    IntegrationPolicy,
    Member,
    MemberNote,
    PastorProfile,
    PrayerRequest,
    Visitor,
)
from app.services.demo_data import build_demo_briefing
from app.services.marge import (
    draft_absence_checkin,
    draft_care_message,
    draft_visitor_followup,
    generate_morning_briefing,
    pastor_display_name,
)
from app import marge_voice as voice
from app.services.accounts import (
    ADMIN_ROLES,
    PASTORAL_ROLES,
    AccountAccess,
    account_access_from_token as _shared_account_access_from_token,
    account_from_token as _shared_account_from_token,
    account_id as _account_id,
    account_tokens_required,
    normalize_role,
    require_role,
    require_workspace,
    scoped_query,
    session_cookie_name,
    session_cookie_samesite,
    session_cookie_secure,
    token_hash as _shared_token_hash,
)
from app.services.invitations import send_login_link, send_workspace_invite
from app.services.secure_tokens import (
    ENCRYPTION_KEY_ENV,
    SecureTokenConfigError,
    decrypt_token_payload,
    encrypt_token_payload,
    encryption_key_is_configured,
)
from app.services.setup_actions import retire_data_seed_actions
from app.services.visitor_followup import queue_visitor_welcome_action

router = APIRouter(prefix="/assistant", tags=["assistant"])

OAUTH_STATE_TTL_MINUTES = 15
OAUTH_REQUEST_TIMEOUT_SECONDS = 15
LOGIN_LINK_TTL_MINUTES = 20
LOGIN_LINK_RESEND_COOLDOWN_MINUTES = 5
DEFAULT_GUARDRAILS = "Do not send messages, create calendar events, or write to external systems without approval."
SENSITIVE_IDENTITY_KEY_TERMS = {
    "access_token",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "token",
    "token_ciphertext",
    "api_key",
    "apikey",
}
SENSITIVE_IDENTITY_VALUE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|token[_-]?ciphertext)\s*[:=]\s*\S+",
        r"\bauthorization\s*[:=]\s*bearer\s+\S+",
        r"\bbearer\s+[A-Za-z0-9._~+/\-]{16,}",
        r"\bmarge_sess_[A-Za-z0-9._~+\-/=]{8,}",
        r"\bya29\.[A-Za-z0-9._~+\-/=]{8,}",
        r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}",
    ]
]
SECRET_TEXT_REDACTIONS = [
    (
        re.compile(
            r"\b(access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|token[_-]?ciphertext)\s*[:=]\s*[^\s,;]+",
            re.IGNORECASE,
        ),
        r"\1=<redacted>",
    ),
    (re.compile(r"\b(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"\bmarge_sess_[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-marge-session>"),
    (re.compile(r"\bya29\.[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-google-token>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-jwt>",
    ),
]


def _redact_secret_text(value) -> str:
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value or "")
    for pattern, replacement in SECRET_TEXT_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


ONBOARDING_QUESTIONS = [
    {
        "id": "pastor_name",
        "label": "Your name",
        "question": "What should Marge call you?",
        "placeholder": "Pastor name",
    },
    {
        "id": "church_name",
        "label": "Church",
        "question": "What church are you serving?",
        "placeholder": "Church name",
    },
    {
        "id": "role_title",
        "label": "Role",
        "question": "What is your role there?",
        "placeholder": "Lead pastor, solo pastor, associate pastor...",
    },
    {
        "id": "congregation_size",
        "label": "Weekly size",
        "question": "About how many people attend weekly?",
        "placeholder": "75, 150, 400...",
    },
    {
        "id": "church_context",
        "label": "Church context",
        "question": "What makes your church and community unique?",
        "placeholder": "Small neighborhood church, lots of young families, many new believers...",
    },
    {
        "id": "faith_tradition",
        "label": "Church voice",
        "question": "What church tradition, denomination, or ministry language should Marge respect?",
        "placeholder": "Non-denominational with Baptist roots; avoid insider language with guests...",
    },
    {
        "id": "followup_pain",
        "label": "Follow-up burden",
        "question": "Where does pastoral follow-up break down right now?",
        "placeholder": "Visitors, hospital follow-up, prayer requests, absent members...",
    },
    {
        "id": "ministry_priorities",
        "label": "First priority",
        "question": "What would make Marge genuinely helpful in the first month?",
        "placeholder": "Close loops with first-time guests, protect sermon prep, follow up on private prayer needs...",
    },
    {
        "id": "support_preferences",
        "label": "How to support you",
        "question": "How should Marge support you personally when ministry gets heavy?",
        "placeholder": "Nudge me gently, protect my rest, surface what I am likely to miss, keep me from carrying every loop alone...",
    },
    {
        "id": "tools_in_use",
        "label": "Tools",
        "question": "What tools does the church already use?",
        "placeholder": "Planning Center, Gmail/Google Workspace, Outlook/Microsoft 365, Rock RMS, Breeze...",
    },
    {
        "id": "communication_style",
        "label": "Voice",
        "question": "How should Marge sound when drafting for you?",
        "placeholder": "Warm and brief, direct, more formal, conversational...",
    },
    {
        "id": "weekly_rhythm",
        "label": "Rhythm",
        "question": "What should Marge protect or remember in your weekly rhythm?",
        "placeholder": "Sermon prep Thursdays, hospital visits Tuesdays, staff meeting Monday...",
    },
    {
        "id": "guardrails",
        "label": "Guardrails",
        "question": "What should Marge never do without asking?",
        "placeholder": "Send emails, change Planning Center, share private prayer requests...",
    },
]


class PastorProfilePayload(BaseModel):
    pastor_name: Optional[str] = None
    church_name: Optional[str] = None
    role_title: Optional[str] = None
    congregation_size: Optional[str] = None
    church_context: Optional[str] = None
    faith_tradition: Optional[str] = None
    ministry_priorities: Optional[str] = None
    followup_pain: Optional[str] = None
    support_preferences: Optional[str] = None
    weekly_rhythm: Optional[str] = None
    communication_style: Optional[str] = None
    tools_in_use: Optional[str] = None
    guardrails: Optional[str] = None


class PastorProfileResponse(PastorProfilePayload):
    onboarding_complete: bool
    completion_percent: int
    missing_fields: List[str]
    questions: List[dict]
    account_slug: Optional[str] = None


class AccountSignupRequest(PastorProfilePayload):
    email: Optional[str] = None


class AccountUserResponse(BaseModel):
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    role: str
    active: bool
    last_seen_at: Optional[datetime] = None
    created_at: datetime


class AccountUserInviteRequest(BaseModel):
    name: Optional[str] = None
    email: str
    role: str = "staff"


class AccountUserUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None


class AccountUserInviteResponse(BaseModel):
    user: AccountUserResponse
    token: str
    secure_note: str
    delivery: Optional[dict] = None


class AccountSignupResponse(BaseModel):
    account_id: int
    slug: str
    church_name: str
    pastor_name: Optional[str] = None
    token: str
    current_user: AccountUserResponse
    profile: PastorProfileResponse
    secure_note: str


class AccountResponse(BaseModel):
    id: int
    slug: str
    church_name: str
    pastor_name: Optional[str] = None
    email: Optional[str] = None
    current_role: str
    current_user: Optional[AccountUserResponse] = None
    created_at: datetime


class AccountSessionRequest(BaseModel):
    duration_hours: int = 168


class AccountSessionResponse(BaseModel):
    token: str
    token_type: str
    expires_at: datetime
    current_user: AccountUserResponse
    secure_note: str


class AccountSessionStatusResponse(BaseModel):
    token_type: str
    expires_at: Optional[datetime] = None
    current_user: Optional[AccountUserResponse] = None
    account: Optional[AccountResponse] = None


class AccountLoginLinkRequest(BaseModel):
    email: str
    church_slug: Optional[str] = None


class AccountLoginLinkResponse(BaseModel):
    status: str
    secure_note: str


class AccountLoginLinkExchangeRequest(BaseModel):
    token: str
    duration_hours: int = 168


class AssistantConfigResponse(BaseModel):
    require_account_token: bool
    signup_enabled: bool = True
    secure_note: str


class IntegrationStatus(BaseModel):
    provider: str
    display_name: str
    status: str
    auth_type: str
    scopes: List[str]
    secure_note: str
    config_hint: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None
    write_enabled: bool = False
    require_approval: bool = True
    credential_scope: Optional[str] = None


class IntegrationSetupResponse(BaseModel):
    provider: str
    display_name: str
    status: str
    setup_type: str
    authorization_url: Optional[str] = None
    state_expires_at: Optional[datetime] = None
    instructions: List[str]
    missing_config: List[str] = []
    secure_note: str


class DeskItem(BaseModel):
    id: str
    type: str
    title: str
    subtitle: Optional[str] = None
    detail: Optional[str] = None
    priority: Literal["high", "medium", "low"] = "medium"
    action: Optional[str] = None
    source: Optional[str] = None
    related_id: Optional[int] = None
    form: Optional[str] = None
    provider: Optional[str] = None


class AssistantDeskResponse(BaseModel):
    mode: Literal["demo", "live"]
    greeting: str
    pastor_name: str
    church_name: str
    profile: PastorProfileResponse
    stats: dict
    priorities: List[DeskItem]
    email_drafts: List[DeskItem]
    calendar_blocks: List[DeskItem]
    followups: List[DeskItem]
    approvals: List[DeskItem]
    integrations: List[IntegrationStatus]
    setup_steps: List[DeskItem] = []
    interview_question: Optional[dict] = None
    operating_plan: List[dict] = []
    suggested_prompts: List[str]
    proactive_summary: str


class AssistantChatRequest(BaseModel):
    message: str
    mode: Literal["demo", "live"] = "live"


class AssistantChatResponse(BaseModel):
    reply: str
    intent: str
    mode: Literal["demo", "live"]
    saved: bool = False
    actions: List[DeskItem] = []
    suggested_prompts: List[str] = []
    profile: Optional[PastorProfileResponse] = None


class AssistantChatMessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    intent: Optional[str] = None
    mode: Literal["demo", "live"]
    saved: bool = False
    action_count: int = 0
    actions: List[DeskItem] = []
    suggested_prompts: List[str] = []
    created_at: datetime


class AssistantChatClearResponse(BaseModel):
    messages_deleted: int
    message: str


class AssistantActionCreate(BaseModel):
    action_type: str
    title: str
    description: Optional[str] = None
    payload: dict = {}
    source: Optional[str] = None
    external_provider: Optional[str] = None
    related_type: Optional[str] = None
    related_id: Optional[int] = None
    privacy_level: str = "pastoral"


class AssistantActionResponse(BaseModel):
    id: int
    action_type: str
    status: str
    title: str
    description: Optional[str] = None
    payload: dict = {}
    source: Optional[str] = None
    external_provider: Optional[str] = None
    related_type: Optional[str] = None
    related_id: Optional[int] = None
    privacy_level: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    skipped_at: Optional[datetime] = None


class ConnectedContextItemResponse(BaseModel):
    id: int
    provider: str
    item_type: str
    external_id: str
    thread_id: Optional[str] = None
    title: str
    subtitle: Optional[str] = None
    snippet: Optional[str] = None
    occurred_at: Optional[datetime] = None
    action_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class IntegrationSyncResponse(BaseModel):
    provider: str
    status: str
    synced_at: datetime
    items_seen: int
    items_created: int
    items_updated: int
    actions_prepared: int
    message: str


class IntegrationVerifyResponse(BaseModel):
    provider: str
    status: str
    verified_at: Optional[datetime] = None
    credential_scope: Optional[str] = None
    identity: dict = {}
    message: str


class IntegrationCredentialPayload(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class IntegrationCredentialSetupResponse(BaseModel):
    provider: str
    status: str
    credential_scope: str
    configured_at: datetime
    message: str
    secure_note: str


class IntegrationDisconnectResponse(BaseModel):
    provider: str
    status: str
    disconnected_at: datetime
    credential_scope: Optional[str] = None
    removed_credentials: int
    remaining_credentials: int
    write_enabled: bool
    message: str


class AuditLogResponse(BaseModel):
    id: int
    event_type: str
    actor: str
    summary: str
    provider: Optional[str] = None
    action_id: Optional[int] = None
    connected_item_id: Optional[int] = None
    payload: dict = {}
    created_at: datetime


class IntegrationPolicyPayload(BaseModel):
    read_enabled: Optional[bool] = None
    write_enabled: Optional[bool] = None
    require_approval: Optional[bool] = None
    allowed_actions: Optional[List[str]] = None
    privacy_mode: Optional[str] = None


class IntegrationPolicyResponse(BaseModel):
    provider: str
    display_name: str
    read_enabled: bool
    write_enabled: bool
    require_approval: bool
    allowed_actions: List[str]
    privacy_mode: str
    secure_note: str


@router.get("/config", response_model=AssistantConfigResponse, summary="Get public assistant app configuration")
def get_assistant_config():
    return AssistantConfigResponse(
        require_account_token=account_tokens_required(),
        secure_note="When account tokens are required, create or reconnect a church workspace before loading scoped ministry data.",
    )


@router.post("/signup", response_model=AccountSignupResponse, status_code=201, summary="Create a church account and first pastor profile")
def signup(payload: AccountSignupRequest, db: Session = Depends(get_db)):
    account, token, profile, owner_user = _create_account(db, payload)
    _audit(
        db,
        "account.created",
        f"Created church account: {account.church_name}",
        actor="system",
        account=account,
        payload={"slug": account.slug, "profile_complete": _profile_is_complete(profile)},
    )
    db.commit()
    db.refresh(account)
    db.refresh(profile)
    db.refresh(owner_user)
    return AccountSignupResponse(
        account_id=account.id,
        slug=account.slug,
        church_name=account.church_name,
        pastor_name=account.pastor_name,
        token=token,
        current_user=_account_user_response(owner_user),
        profile=_profile_response(profile, account),
        secure_note="Store this owner user token locally or in a server-side session. Marge stores only a hash of it.",
    )


@router.get("/account", response_model=AccountResponse, summary="Get current church account")
def get_account(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    account = access.account
    if not account:
        raise HTTPException(status_code=401, detail="No valid Marge account token was provided.")
    return AccountResponse(
        id=account.id,
        slug=account.slug,
        church_name=account.church_name,
        pastor_name=account.pastor_name,
        email=account.email,
        current_role=access.role or "owner",
        current_user=_account_user_response(access.user) if access.user else None,
        created_at=account.created_at,
    )


@router.post("/sessions", response_model=AccountSessionResponse, status_code=201, summary="Create an expiring workspace session token")
def create_account_session(
    payload: AccountSessionRequest,
    response: Response,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    if not access.account or not access.user:
        raise HTTPException(status_code=401, detail="A workspace user token is required to create a Marge session.")
    session, token = _create_account_session(db, access.account, access.user, payload.duration_hours)
    _audit(
        db,
        "account_session.created",
        f"Created session for {access.user.email or access.user.name or access.user.id}.",
        account=access.account,
        payload={"user_id": access.user.id, "expires_at": session.expires_at.isoformat()},
    )
    db.commit()
    db.refresh(session)
    _set_session_cookie(response, token, session.expires_at)
    return AccountSessionResponse(
        token=token,
        token_type="session",
        expires_at=session.expires_at,
        current_user=_account_user_response(access.user),
        secure_note="Use this shorter-lived session token for API requests. Marge stores only its hash and it can be revoked.",
    )


@router.post("/login-links/request", response_model=AccountLoginLinkResponse, summary="Request a passwordless workspace login link")
def request_login_link(payload: AccountLoginLinkRequest, db: Session = Depends(get_db)):
    email = _clean(payload.email)
    church_slug = _clean(payload.church_slug)
    user, account = _find_login_link_user(db, email, church_slug)
    if user and account:
        recent_link = _recent_login_link(db, user)
        if recent_link:
            _audit(
                db,
                "account_login_link.request_throttled",
                f"Suppressed duplicate passwordless login link for {user.email or user.id}.",
                account=account,
                payload={
                    "user_id": user.id,
                    "expires_at": recent_link.expires_at.isoformat(),
                    "cooldown_minutes": LOGIN_LINK_RESEND_COOLDOWN_MINUTES,
                },
            )
        else:
            login_link, token = _create_login_link(db, account, user)
            delivery = send_login_link(account, user, token)
            _audit(
                db,
                "account_login_link.requested",
                f"Requested passwordless login link for {user.email or user.id}.",
                account=account,
                payload={
                    "user_id": user.id,
                    "delivery_status": delivery.status,
                    "delivery_channel": delivery.channel,
                    "expires_at": login_link.expires_at.isoformat(),
                },
            )
        db.commit()
    else:
        _audit(
            db,
            "account_login_link.request_ignored",
            "Ignored passwordless login link request with no unique active workspace user.",
            actor="system",
            payload={"email_provided": bool(email), "church_slug_provided": bool(church_slug)},
        )
        db.commit()

    return AccountLoginLinkResponse(
        status="accepted",
        secure_note="If that email belongs to an active Marge workspace user, Marge sent a one-time sign-in link.",
    )


@router.post("/login-links/exchange", response_model=AccountSessionResponse, status_code=201, summary="Exchange a passwordless login link for a workspace session")
def exchange_login_link(
    payload: AccountLoginLinkExchangeRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    login_link = _login_link_from_token(db, payload.token)
    account = db.get(ChurchAccount, login_link.account_id)
    user = db.get(AccountUser, login_link.user_id)
    if not account or not user or not user.active:
        raise HTTPException(status_code=401, detail="This Marge sign-in link is no longer valid.")
    session, session_token = _create_account_session(db, account, user, payload.duration_hours)
    now = datetime.utcnow()
    login_link.consumed_at = now
    user.last_seen_at = now
    _audit(
        db,
        "account_login_link.exchanged",
        f"Exchanged passwordless login link for {user.email or user.id}.",
        account=account,
        payload={"user_id": user.id, "session_expires_at": session.expires_at.isoformat()},
    )
    db.commit()
    db.refresh(session)
    _set_session_cookie(response, session_token, session.expires_at)
    return AccountSessionResponse(
        token=session_token,
        token_type="session",
        expires_at=session.expires_at,
        current_user=_account_user_response(user),
        secure_note="Sign-in link accepted. Marge created a revocable browser session and stores only token hashes.",
    )


@router.get("/sessions/current", response_model=AccountSessionStatusResponse, summary="Inspect current workspace session or token")
def get_current_session(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    account = access.account
    account_response = None
    if account:
        account_response = AccountResponse(
            id=account.id,
            slug=account.slug,
            church_name=account.church_name,
            pastor_name=account.pastor_name,
            email=account.email,
            current_role=access.role or "owner",
            current_user=_account_user_response(access.user) if access.user else None,
            created_at=account.created_at,
        )
    return AccountSessionStatusResponse(
        token_type=access.token_type,
        expires_at=access.session.expires_at if access.session else None,
        current_user=_account_user_response(access.user) if access.user else None,
        account=account_response,
    )


@router.delete("/sessions/current", response_model=AccountSessionStatusResponse, summary="Revoke the current workspace session")
def revoke_current_session(
    response: Response,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    if not access.session:
        raise HTTPException(status_code=409, detail="The current token is not a revocable Marge session.")
    access.session.revoked_at = datetime.utcnow()
    _audit(
        db,
        "account_session.revoked",
        f"Revoked session for {access.user.email or access.user.name or access.user.id if access.user else 'workspace user'}.",
        account=access.account,
        payload={"user_id": access.user.id if access.user else None},
    )
    db.commit()
    _clear_session_cookie(response)
    return AccountSessionStatusResponse(
        token_type="session",
        expires_at=access.session.expires_at,
        current_user=_account_user_response(access.user) if access.user else None,
    )


@router.get("/users", response_model=List[AccountUserResponse], summary="List church workspace users")
def list_account_users(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    if not access.account:
        raise HTTPException(status_code=401, detail="A church workspace token is required to manage users.")
    _require_role(access, ADMIN_ROLES, "manage workspace users")
    users = (
        db.query(AccountUser)
        .filter(AccountUser.account_id == access.account.id)
        .order_by(AccountUser.created_at.asc())
        .all()
    )
    return [_account_user_response(user) for user in users]


@router.post("/users/invite", response_model=AccountUserInviteResponse, status_code=201, summary="Create a role-scoped workspace user token")
def invite_account_user(
    payload: AccountUserInviteRequest,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    if not access.account:
        raise HTTPException(status_code=401, detail="A church workspace token is required to invite users.")
    _require_role(access, ADMIN_ROLES, "invite workspace users")
    user, token = _create_account_user(
        db,
        access.account,
        name=_clean(payload.name),
        email=_clean(payload.email),
        role=normalize_role(payload.role, "staff"),
    )
    delivery = send_workspace_invite(
        access.account,
        user,
        token,
        inviter_name=access.user.name if access.user else access.account.pastor_name,
    )
    _audit(
        db,
        "account_user.invited",
        f"Invited {user.email or user.name or 'workspace user'} as {user.role}.",
        account=access.account,
        payload={"role": user.role, "email": user.email, "delivery_status": delivery.status, "delivery_channel": delivery.channel},
    )
    db.commit()
    db.refresh(user)
    return AccountUserInviteResponse(
        user=_account_user_response(user),
        token=token,
        secure_note=(
            "Invite email sent. Marge stores only the token hash; the raw token is delivered once."
            if delivery.status == "sent"
            else "Share this token through a trusted channel once. Marge stores only its hash and uses the role to protect admin actions."
        ),
        delivery=delivery.as_dict(),
    )


@router.patch("/users/{user_id}", response_model=AccountUserResponse, summary="Update or deactivate a workspace user")
def update_account_user(
    user_id: int,
    payload: AccountUserUpdateRequest,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    if not access.account:
        raise HTTPException(status_code=401, detail="A church workspace token is required to manage users.")
    _require_role(access, ADMIN_ROLES, "manage workspace users")
    user = (
        db.query(AccountUser)
        .filter(AccountUser.account_id == access.account.id, AccountUser.id == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Workspace user not found.")

    updates = payload.model_dump(exclude_unset=True)
    new_role = normalize_role(updates["role"], user.role) if "role" in updates else normalize_role(user.role, "staff")
    new_active = bool(updates["active"]) if "active" in updates else bool(user.active)
    if normalize_role(user.role, "staff") == "owner" and (new_role != "owner" or not new_active):
        active_owner_count = (
            db.query(AccountUser)
            .filter(
                AccountUser.account_id == access.account.id,
                AccountUser.role == "owner",
                AccountUser.active.is_(True),
            )
            .count()
        )
        if active_owner_count <= 1:
            raise HTTPException(status_code=409, detail="Cannot remove or deactivate the last workspace owner.")

    if "name" in updates:
        user.name = _clean(updates["name"])
    if "email" in updates:
        user.email = _clean(updates["email"])
    if "role" in updates:
        user.role = new_role
    if "active" in updates:
        user.active = new_active
    _audit(
        db,
        "account_user.updated",
        f"Updated workspace user {user.email or user.name or user.id}.",
        account=access.account,
        payload={"user_id": user.id, "role": user.role, "active": bool(user.active)},
    )
    db.commit()
    db.refresh(user)
    return _account_user_response(user)


@router.get("/profile", response_model=PastorProfileResponse, summary="Get pastor profile and onboarding questions")
def get_profile(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    account = _account_from_token(db, x_marge_account_token)
    profile = _get_or_create_profile(db, account)
    return _profile_response(profile, account)


@router.patch("/profile", response_model=PastorProfileResponse, summary="Update pastor profile")
def update_profile(
    payload: PastorProfilePayload,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "update the ministry profile")
    account = access.account
    profile = _get_or_create_profile(db, account)
    updated_fields = []
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, _clean(value))
        updated_fields.append(field)
    profile.onboarding_complete = _profile_is_complete(profile)
    _sync_account_identity_from_profile(account, profile)
    _audit(db, "pastor_profile.updated", "Updated pastor ministry profile.", account=account, payload={"fields": updated_fields, "account_slug": account.slug if account else None})
    db.commit()
    db.refresh(profile)
    return _profile_response(profile, account)


@router.get("/integrations", response_model=List[IntegrationStatus], summary="List secure connector statuses")
def list_integrations(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    return _integration_statuses(db, access.account, access.user)


@router.post("/integrations/{provider}/start", response_model=IntegrationSetupResponse, summary="Start secure connector setup")
def start_integration_setup(
    provider: str,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, ADMIN_ROLES, "start connector setup")
    _require_workspace(access, "start connector setup")
    account = access.account
    return _start_integration(provider, db, account, access.user)


@router.post("/integrations/{provider}/credentials", response_model=IntegrationCredentialSetupResponse, summary="Store encrypted workspace API-key connector credentials")
def save_integration_credentials(
    provider: str,
    payload: IntegrationCredentialPayload,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, ADMIN_ROLES, "configure connector credentials")
    _require_workspace(access, "configure connector credentials")
    return _save_api_key_integration(db, provider, payload, access.account)


@router.get("/policies", response_model=List[IntegrationPolicyResponse], summary="List connector writeback policies")
def list_integration_policies(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    account = _account_from_token(db, x_marge_account_token)
    return [_policy_response(db, definition["provider"], account) for definition in _integration_definitions()]


@router.patch("/policies/{provider}", response_model=IntegrationPolicyResponse, summary="Update connector writeback policy")
def update_integration_policy(
    provider: str,
    payload: IntegrationPolicyPayload,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, ADMIN_ROLES, "change connector writeback policy")
    _require_workspace(access, "change connector writeback policy")
    account = access.account
    definitions = {item["provider"]: item for item in _integration_definitions()}
    if provider not in definitions:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    policy = _get_or_create_policy(db, provider, account)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "allowed_actions":
            policy.allowed_actions = ",".join(value or [])
        elif field == "privacy_mode":
            policy.privacy_mode = _clean(value) or "pastoral"
        else:
            setattr(policy, field, bool(value))
    _audit(
        db,
        "integration_policy.updated",
        f"Updated {definitions[provider]['display_name']} connector policy.",
        provider=provider,
        account=account,
        payload=_policy_payload(policy),
    )
    db.commit()
    db.refresh(policy)
    return _policy_response(db, provider, account)


@router.post("/integrations/{provider}/sync", response_model=IntegrationSyncResponse, summary="Sync external context from a connected provider")
def sync_integration(
    provider: str,
    email_limit: int = Query(5, ge=0, le=25),
    people_limit: int = Query(25, ge=0, le=100),
    calendar_days: int = Query(14, ge=1, le=60),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "sync connected ministry tools")
    _require_workspace(access, "sync connected ministry tools")
    account = access.account
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    if provider == "google_workspace":
        return _sync_google_workspace(db, email_limit=email_limit, calendar_days=calendar_days, account=account, user=access.user)
    if provider == "rock":
        return _sync_rock_rms(db, account=account)
    if provider == "planning_center":
        return _sync_planning_center(db, people_limit=people_limit, calendar_days=calendar_days, account=account, user=access.user)
    if provider == "microsoft_365":
        return _sync_microsoft_365(db, email_limit=email_limit, calendar_days=calendar_days, account=account, user=access.user)
    if provider == "breeze":
        return _sync_breeze(db, people_limit=people_limit, calendar_days=calendar_days, account=account)
    raise HTTPException(
        status_code=422,
        detail=(
            f"{definition['display_name']} does not import external ministry data through Marge sync. "
            "Sync is available for Google Workspace, Microsoft 365, Planning Center, Breeze, and Rock RMS."
        ),
    )


@router.post("/integrations/{provider}/verify", response_model=IntegrationVerifyResponse, summary="Verify connector credentials without syncing ministry data")
def verify_integration(
    provider: str,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "verify connected ministry tools")
    _require_workspace(access, "verify connected ministry tools")
    return _verify_integration(db, provider, access.account, access.user)


@router.delete("/integrations/{provider}", response_model=IntegrationDisconnectResponse, summary="Disconnect an OAuth connector for the current Marge user")
def disconnect_integration(
    provider: str,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "disconnect connected ministry tools")
    _require_workspace(access, "disconnect connected ministry tools")
    return _disconnect_integration(db, provider, access.account, access.user)


@router.get("/connected-items", response_model=List[ConnectedContextItemResponse], summary="List synced external context")
def list_connected_items(
    provider: Optional[str] = Query(None),
    item_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "view synced ministry context")
    account = access.account
    query = scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
    if provider:
        query = query.filter(ConnectedContextItem.provider == provider)
    if item_type:
        query = query.filter(ConnectedContextItem.item_type == item_type)
    items = (
        query.order_by(ConnectedContextItem.occurred_at.desc().nullslast(), ConnectedContextItem.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_connected_item_response(item) for item in items]


@router.get("/audit-log", response_model=List[AuditLogResponse], summary="List security and assistant action audit events")
def list_audit_log(
    event_type: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "view the audit log")
    account = access.account
    query = scoped_query(db.query(AuditLog), AuditLog, account)
    if event_type:
        query = query.filter(AuditLog.event_type == event_type)
    if provider:
        query = query.filter(AuditLog.provider == provider)
    rows = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [_audit_response(row) for row in rows]


@router.get("/actions", response_model=List[AssistantActionResponse], summary="List assistant approval queue")
def list_assistant_actions(
    status: Optional[str] = Query("pending"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "view assistant actions")
    account = access.account
    query = scoped_query(db.query(AssistantAction), AssistantAction, account)
    if status and status != "all":
        query = query.filter(AssistantAction.status == status)
    actions = query.order_by(AssistantAction.created_at.desc()).offset(skip).limit(limit).all()
    return [_action_response(action) for action in actions]


@router.get("/actions/{action_id}", response_model=AssistantActionResponse, summary="Get an assistant action")
def get_assistant_action(
    action_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "view assistant actions")
    account = access.account
    return _action_response(_get_action_or_404(db, action_id, account))


@router.post("/actions", response_model=AssistantActionResponse, status_code=201, summary="Create an assistant action")
def create_assistant_action(
    payload: AssistantActionCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "create assistant actions")
    account = access.account
    action = AssistantAction(
        account_id=_account_id(account),
        action_type=_clean(payload.action_type) or "general",
        title=_clean(payload.title) or "Review assistant action",
        description=_clean(payload.description),
        payload_json=_json_dumps(payload.payload),
        source=_clean(payload.source),
        external_provider=_clean(payload.external_provider),
        related_type=_clean(payload.related_type),
        related_id=payload.related_id,
        privacy_level=_clean(payload.privacy_level) or "pastoral",
    )
    db.add(action)
    db.flush()
    _audit(
        db,
        "assistant_action.created",
        f"Created assistant action: {action.title}",
        action_id=action.id,
        provider=action.external_provider,
        account=account,
        payload={"action_type": action.action_type, "privacy_level": action.privacy_level},
    )
    db.commit()
    db.refresh(action)
    return _action_response(action)


@router.post("/actions/prepare", response_model=List[AssistantActionResponse], summary="Prepare today's proactive assistant actions")
def prepare_assistant_actions(
    mode: Literal["auto", "demo", "live"] = Query("auto"),
    email_limit: int = Query(3, ge=1, le=25),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "prepare assistant actions")
    account = access.account
    profile = _get_or_create_profile(db, account)
    effective_mode, briefing = _briefing_for_mode(db, profile, mode, account)
    priorities = _priority_items(briefing)
    email_drafts = _email_drafts(briefing, priorities)
    calendar_blocks = _calendar_blocks(profile, priorities)
    integrations = _integration_statuses(db, account, access.user)
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
    actions = _prepare_actions_from_desk(db, effective_mode, email_drafts, calendar_blocks, priorities, profile, account, email_limit=email_limit)
    actions.extend(_prepare_setup_actions(db, profile, setup_steps, account))
    _audit(db, "assistant_actions.prepared", f"Prepared {len(actions)} assistant action(s) from the desk.", account=account, payload={"mode": effective_mode, "count": len(actions)})
    db.commit()
    return [_action_response(action) for action in actions]


@router.post("/actions/{action_id}/approve", response_model=AssistantActionResponse, summary="Approve an assistant action")
def approve_assistant_action(
    action_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "approve assistant actions")
    account = access.account
    action = _get_action_or_404(db, action_id, account)
    if action.status not in {"pending", "approved"}:
        raise HTTPException(status_code=409, detail=f"Action is {action.status} and cannot be approved.")
    action.status = "approved"
    action.approved_at = action.approved_at or datetime.utcnow()
    _audit(db, "assistant_action.approved", f"Approved assistant action: {action.title}", account=account, action_id=action.id, provider=action.external_provider)
    db.commit()
    db.refresh(action)
    return _action_response(action)


@router.post("/actions/{action_id}/execute", response_model=AssistantActionResponse, summary="Execute an approved assistant action or complete a local reminder")
def execute_assistant_action(
    action_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "execute approved assistant actions")
    account = access.account
    action = _get_action_or_404(db, action_id, account)
    _execute_approved_action(db, action, account, access.user)
    db.commit()
    db.refresh(action)
    return _action_response(action)


@router.post("/actions/{action_id}/skip", response_model=AssistantActionResponse, summary="Skip an assistant action")
def skip_assistant_action(
    action_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "skip assistant actions")
    account = access.account
    action = _get_action_or_404(db, action_id, account)
    if action.status in {"executed", "skipped"}:
        return _action_response(action)
    action.status = "skipped"
    action.skipped_at = datetime.utcnow()
    _audit(db, "assistant_action.skipped", f"Skipped assistant action: {action.title}", account=account, action_id=action.id, provider=action.external_provider)
    db.commit()
    db.refresh(action)
    return _action_response(action)


@router.get(
    "/integrations/{provider}/callback",
    response_class=HTMLResponse,
    summary="Complete an OAuth connector callback",
)
def complete_integration_callback(
    provider: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_page(
            provider=provider,
            display_name=provider.replace("_", " ").title(),
            ok=False,
            message=f"{error}: {error_description or 'The provider did not authorize this connection.'}",
        )
    try:
        result = _complete_oauth_integration(provider, code, state, db)
    except Exception as exc:
        db.rollback()
        return _oauth_callback_page(
            provider=provider,
            display_name=provider.replace("_", " ").title(),
            ok=False,
            message=str(exc),
        )
    return _oauth_callback_page(
        provider=provider,
        display_name=result["display_name"],
        ok=True,
        message=(
            "Marge stored the provider token payload encrypted server-side. "
            "Return to the assistant desk and run Check credentials before syncing ministry data."
        ),
    )


@router.get("/desk", response_model=AssistantDeskResponse, summary="Get connected assistant desk")
def get_assistant_desk(
    mode: Literal["auto", "demo", "live"] = Query("auto"),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    account = access.account
    profile = _get_or_create_profile(db, account)
    effective_mode, briefing = _briefing_for_mode(db, profile, mode, account)
    priorities = _priority_items(briefing)
    email_drafts = _email_drafts(briefing, priorities)
    calendar_blocks = _calendar_blocks(profile, priorities)
    followups = priorities[:5]
    can_view_pastoral_actions = (
        (access.account is None and access.role is None)
        or normalize_role(access.role, "viewer") in PASTORAL_ROLES
    )
    pending_approvals = _pending_approval_items(db, account) if can_view_pastoral_actions else []
    approvals = pending_approvals
    integrations = _integration_statuses(db, account, access.user)
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
    interview_question = _interview_question(profile)
    stats = _desk_stats(briefing, email_drafts, calendar_blocks)
    stats["connectors"] = len([
        item
        for item in integrations
        if item.provider != "mcp" and item.status in {"connected", "configured"} and item.verified_at
    ])
    stats["approvals"] = len(pending_approvals)
    return AssistantDeskResponse(
        mode=effective_mode,
        greeting=_profile_greeting(profile, briefing),
        pastor_name=_profile_pastor_name(profile),
        church_name=_profile_church_name(profile),
        profile=_profile_response(profile, account),
        stats=stats,
        priorities=priorities,
        email_drafts=email_drafts,
        calendar_blocks=calendar_blocks,
        followups=followups,
        approvals=approvals,
        integrations=integrations,
        setup_steps=setup_steps,
        interview_question=interview_question,
        operating_plan=_operating_plan(profile, integrations, priorities, email_drafts, calendar_blocks, setup_steps),
        suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
        proactive_summary=_proactive_summary(profile, priorities, email_drafts, calendar_blocks, setup_steps),
    )


@router.get("/chat/history", response_model=List[AssistantChatMessageResponse], summary="List recent assistant chat turns")
def list_assistant_chat_history(
    limit: int = Query(30, ge=1, le=100),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "read assistant chat history")
    rows = (
        scoped_query(db.query(AssistantChatMessage), AssistantChatMessage, access.account)
        .order_by(AssistantChatMessage.created_at.desc(), AssistantChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [_chat_message_response(row) for row in reversed(rows)]


@router.delete("/chat/history", response_model=AssistantChatClearResponse, summary="Clear assistant chat history")
def clear_assistant_chat_history(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "clear assistant chat history")
    query = scoped_query(db.query(AssistantChatMessage), AssistantChatMessage, access.account)
    count = query.count()
    query.delete(synchronize_session=False)
    _audit(
        db,
        "assistant_chat.cleared",
        "Cleared assistant chat history.",
        account=access.account,
        payload={"messages_deleted": count},
    )
    db.commit()
    return AssistantChatClearResponse(
        messages_deleted=count,
        message="Assistant chat history was cleared. Ministry records and saved profile context were not deleted.",
    )


@router.post("/chat", response_model=AssistantChatResponse, summary="Chat with connected Marge assistant")
def assistant_chat(
    request: AssistantChatRequest,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = _account_access_from_token(db, x_marge_account_token)
    _require_role(access, PASTORAL_ROLES, "chat with Marge")
    account = access.account
    profile = _get_or_create_profile(db, account)
    effective_mode, briefing = _briefing_for_mode(db, profile, request.mode, account)
    priorities = _priority_items(briefing)
    email_drafts = _email_drafts(briefing, priorities)
    calendar_blocks = _calendar_blocks(profile, priorities)
    lower = request.message.lower()

    if _profile_is_complete(profile) and _pastor_pressure_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _pastor_pressure_response(
                profile,
                priorities,
                email_drafts,
                calendar_blocks,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    profile_update = _maybe_save_profile_context(db, profile, account, access.user, request.message, lower)
    if profile_update:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=profile_update["reply"],
            intent="profile_context_saved",
            mode=effective_mode,
            saved=True,
            actions=profile_update.get("actions", []),
            suggested_prompts=profile_update["suggested_prompts"],
            profile=_profile_response(profile, account),
        )

    onboarding_save = _maybe_save_onboarding_answer(db, profile, account, access.user, request.message, lower)
    if onboarding_save:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=onboarding_save["reply"],
            intent="onboarding_answer",
            mode=effective_mode,
            saved=True,
            actions=onboarding_save.get("actions", []),
            suggested_prompts=onboarding_save.get("suggested_prompts", _suggested_prompts(profile, priorities)),
            profile=_profile_response(profile, account),
        )

    if _support_style_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _support_style_chat_response(setup_steps, priorities, effective_mode, profile, account),
        )

    if _context_usage_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _context_usage_chat_response(setup_steps, priorities, effective_mode, profile, account),
        )

    if _secure_connections_explainer_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _secure_connections_chat_response(setup_steps, effective_mode, profile, account),
        )

    if _profile_setting_lookup_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _profile_setting_lookup_response(profile, setup_steps, effective_mode, account, lower),
        )

    if _pastor_pressure_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _pastor_pressure_response(
                profile,
                priorities,
                email_drafts,
                calendar_blocks,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    if _calendar_details_help_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _calendar_details_help_response(db, profile, integrations, account, access.user, effective_mode, lower),
        )

    if _calendar_event_write_requested(lower):
        calendar_action = _prepare_calendar_event_from_chat(db, profile, account, access.user, request.message, lower)
        if calendar_action:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=(
                    f"I queued this calendar event for approval: {calendar_action.title}. "
                    "I will not create it in the external calendar until you approve it and writeback policy allows it."
                ),
                intent="calendar_event_queued",
                mode=effective_mode,
                actions=[_desk_item_from_action(calendar_action)],
                suggested_prompts=["Approve this calendar event.", "Show my approvals."],
            )
        event_payload = _calendar_event_payload_from_message(request.message)
        if event_payload:
            integrations = _integration_statuses(db, account, access.user)
            setup_steps = _calendar_write_setup_steps(integrations)[:2]
            return _chat_turn_response(db, account, access.user, request.message,
                reply=(
                    "I have enough event details to stage this, but I do not see a credential-checked Google Workspace or Microsoft 365 calendar yet. "
                    "I attached the setup or credential-check cards. I will not create an external calendar event until credentials are checked, "
                    "writeback policy allows calendar_block, and you approve that exact event."
                ),
                intent="calendar_event_provider_not_ready",
                mode=effective_mode,
                actions=setup_steps,
                suggested_prompts=_connector_setup_or_check_prompts(setup_steps),
            )
        return _chat_turn_response(db, account, access.user, request.message,
            reply=(
                "I can queue a calendar event once I have a connected Google Workspace or Microsoft 365 calendar, "
                "a title, a date, and a start time. I will keep the actual calendar write behind approval."
            ),
            intent="calendar_event_missing_details",
            mode=effective_mode,
            actions=[],
            suggested_prompts=["Open integrations.", "What calendar details do you need?"],
        )

    member_seed = _maybe_save_member_from_chat(db, profile, account, request.message, lower)
    if member_seed:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=member_seed["reply"],
            intent=member_seed["intent"],
            mode=effective_mode,
            saved=member_seed["saved"],
            actions=member_seed.get("actions", []),
            suggested_prompts=member_seed.get("suggested_prompts", _suggested_prompts(profile, priorities)),
            profile=_profile_response(profile, account),
        )

    if _generic_person_capture_prompt_requested(lower):
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        return _persist_chat_response(db, account, access.user, request.message, _person_capture_guidance_response(seed_step, effective_mode, profile, account))

    if _data_seed_help_requested(lower) and _looks_like_pastoral_update_with_named_person(request.message, lower):
        pastoral_update = _maybe_save_pastoral_update(db, profile, account, access.user, request.message, lower, effective_mode)
        if pastoral_update:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=pastoral_update["reply"],
                intent=pastoral_update["intent"],
                mode=effective_mode,
                saved=pastoral_update["saved"],
                actions=pastoral_update.get("actions", []),
                suggested_prompts=pastoral_update.get("suggested_prompts", _suggested_prompts(profile, priorities)),
                profile=_profile_response(profile, account),
            )

    if _data_seed_help_requested(lower):
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        if seed_step:
            return _persist_chat_response(db, account, access.user, request.message, _data_seed_chat_response(seed_step, effective_mode, profile, account))

    if _setup_steps_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(db, account, access.user, request.message, _setup_steps_chat_response(setup_steps, effective_mode, profile, account))

    if _setup_step_reason_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(db, account, access.user, request.message, _setup_step_reason_chat_response(setup_steps, effective_mode, profile, account))

    if _ministry_update_help_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _ministry_update_help_response(setup_steps, priorities, effective_mode, profile, account),
        )

    if _profile_priority_guidance_requested(lower, profile):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _profile_priority_guidance_response(setup_steps, priorities, effective_mode, profile, account),
        )

    if _first_record_coaching_requested(lower):
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        if seed_step:
            return _persist_chat_response(db, account, access.user, request.message, _first_record_coaching_response(seed_step, effective_mode, profile, account))
        if _mentions(lower, ["private prayer", "prayer request", "prayer"]):
            return _persist_chat_response(db, account, access.user, request.message, _private_prayer_guidance_response(db, profile, account, effective_mode))
        if _mentions(lower, ["care", "hospital", "grief", "crisis"]):
            return _persist_chat_response(db, account, access.user, request.message, _care_case_guidance_response(db, profile, account, effective_mode))

    if _generic_prayer_request_prompt_requested(lower):
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        if seed_step and seed_step.form == "prayer":
            return _persist_chat_response(db, account, access.user, request.message, _data_seed_chat_response(seed_step, effective_mode, profile, account))
        return _persist_chat_response(db, account, access.user, request.message, _private_prayer_guidance_response(db, profile, account, effective_mode))

    if _generic_care_case_prompt_requested(lower):
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        if seed_step and (_data_seed_is_care(seed_step) or seed_step.form == "person"):
            return _persist_chat_response(db, account, access.user, request.message, _data_seed_chat_response(seed_step, effective_mode, profile, account))
        return _persist_chat_response(db, account, access.user, request.message, _care_case_guidance_response(db, profile, account, effective_mode))

    if _pastoral_reminder_lookup_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _pastoral_reminder_lookup_response(db, profile, account, effective_mode),
        )

    if _pastoral_reminder_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _pastoral_reminder_chat_response(db, profile, account, request.message, lower, effective_mode),
        )

    pastoral_update = _maybe_save_pastoral_update(db, profile, account, access.user, request.message, lower, effective_mode)
    if pastoral_update:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=pastoral_update["reply"],
            intent=pastoral_update["intent"],
            mode=effective_mode,
            saved=pastoral_update["saved"],
            actions=pastoral_update.get("actions", []),
            suggested_prompts=pastoral_update.get("suggested_prompts", _suggested_prompts(profile, priorities)),
            profile=_profile_response(profile, account),
        )

    action_command = _maybe_handle_action_command(db, account, access.user, profile, priorities, request.message, lower, effective_mode)
    if action_command:
        return _persist_chat_response(db, account, access.user, request.message, action_command)

    local_draft = _maybe_prepare_local_followup_draft(db, profile, account, request.message, lower, effective_mode)
    if local_draft:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=local_draft["reply"],
            intent=local_draft["intent"],
            mode=effective_mode,
            saved=True,
            actions=local_draft.get("actions", []),
            suggested_prompts=local_draft.get("suggested_prompts", _suggested_prompts(profile, priorities)),
            profile=_profile_response(profile, account),
        )

    connected_import = _maybe_import_connected_person_from_chat(db, profile, account, access.user, request.message, lower, effective_mode)
    if connected_import:
        return _chat_turn_response(db, account, access.user, request.message,
            reply=connected_import["reply"],
            intent=connected_import["intent"],
            mode=effective_mode,
            saved=connected_import["saved"],
            actions=connected_import.get("actions", []),
            suggested_prompts=connected_import.get("suggested_prompts", _suggested_prompts(profile, priorities)),
            profile=_profile_response(profile, account),
        )

    if _church_profile_context_requested(lower, profile):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        plan = _operating_plan(profile, integrations, priorities, email_drafts, calendar_blocks, setup_steps)
        reply = _ministry_operating_plan_reply(profile, plan)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            AssistantChatResponse(
                reply=reply,
                intent="ministry_operating_plan",
                mode=effective_mode,
                actions=setup_steps[:3] if setup_steps else priorities[:3],
                suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
                profile=_profile_response(profile, account),
            ),
        )

    ministry_context = _maybe_answer_ministry_context(db, profile, account, request.message, lower, effective_mode)
    if ministry_context:
        return _persist_chat_response(db, account, access.user, request.message, ministry_context)

    if _absence_draft_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _absence_draft_chat_response(db, profile, account, access.user, email_drafts, priorities, effective_mode),
        )

    if _absence_context_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _absence_context_chat_response(db, profile, priorities, effective_mode, account, access.user),
        )

    if _connector_verification_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        provider = _provider_from_chat(lower) or _next_verification_provider(profile, integrations)
        if not provider:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=(
                    "I do not see a connected connector to check yet. Start secure setup for Google Workspace, "
                    "Microsoft 365, Planning Center, Breeze, or Rock first; then I can verify credentials without syncing ministry data."
                ),
                intent="integration_verify_not_ready",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["Open integrations.", "What should I connect first?"],
            )

        status = next((item for item in integrations if item.provider == provider), None)
        if not status or status.status not in {"connected", "configured", "available"}:
            setup_result = _prepare_integration_setup_from_chat(db, provider, account, access.user)
            action = setup_result.get("action")
            setup = setup_result["setup"]
            return _chat_turn_response(db, account, access.user, request.message,
                reply=(
                    f"I cannot check {setup.display_name} credentials until setup is ready. "
                    f"Current status: {setup.status.replace('_', ' ')}. I queued the setup step without exposing secrets."
                ),
                intent="integration_verify_needs_setup",
                mode=effective_mode,
                actions=[_desk_item_from_action(action)] if action else [],
                suggested_prompts=["Open integrations.", f"Start {setup.display_name} setup."],
            )

        try:
            verification = _verify_integration(db, provider, account, access.user)
        except HTTPException as exc:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I could not verify {status.display_name} yet: {_redact_secret_text(exc.detail)}",
                intent="integration_verify_failed",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["Open integrations.", f"Start {status.display_name} setup."],
            )

        identity_summary = _verification_identity_summary(verification.identity)
        reply = (
            f"I checked {status.display_name}. Credentials verified without syncing people, email, calendar, or attendance data, "
            "and I did not queue any actions."
        )
        if identity_summary:
            reply += f" Non-secret identity check: {identity_summary}."
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="integration_verified",
            mode=effective_mode,
            actions=[],
            suggested_prompts=[f"Sync {status.display_name}.", "Show connected context.", "Explain the approval rules."],
        )

    if _connected_tools_sync_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _connected_tools_sync_response(db, profile, account, access.user, effective_mode),
        )

    if _mentions(lower, ["sync", "refresh", "pull"]) and _mentions(lower, ["rock", "rock rms"]):
        integrations = _integration_statuses(db, account, access.user)
        not_ready = _provider_sync_not_ready_response(profile, integrations, "rock", account, effective_mode)
        if not_ready:
            return _chat_turn_response(db, account, access.user, request.message, **not_ready)
        try:
            sync_result = _sync_rock_rms(db, account=account)
            if sync_result.status == "synced":
                reply = (
                    f"I synced Rock RMS and saw {sync_result.items_seen} people or attendance item(s). "
                    f"I queued {sync_result.actions_prepared} follow-up item(s) from attendance context."
                )
                prompts = ["Who has been absent?", "Show my approval queue."]
            else:
                reply = f"I could not sync Rock RMS yet: {_redact_secret_text(sync_result.message)}"
                prompts = ["Open integrations.", "What can you do before Rock is configured?"]
        except HTTPException as exc:
            verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "rock", exc)
            if verify_first:
                return verify_first
            reply = f"I could not sync Rock RMS yet: {_redact_secret_text(exc.detail)}"
            prompts = ["Open integrations.", "Check Rock credentials."]
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="sync_rock_rms",
            mode=effective_mode,
            actions=_connected_context_desk_items(db, account=account, limit=5),
            suggested_prompts=prompts,
        )

    if _mentions(lower, ["sync", "refresh", "pull"]) and _mentions(lower, ["planning center", "church center", "pco"]):
        integrations = _integration_statuses(db, account, access.user)
        not_ready = _provider_sync_not_ready_response(profile, integrations, "planning_center", account, effective_mode)
        if not_ready:
            return _chat_turn_response(db, account, access.user, request.message, **not_ready)
        try:
            sync_result = _sync_planning_center(db, people_limit=25, calendar_days=14, account=account, user=access.user)
            reply = (
                f"I synced Planning Center and found {sync_result.items_seen} people or event item(s). "
                f"I queued {sync_result.actions_prepared} pastor-review item(s) from that context."
            )
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="sync_planning_center",
                mode=effective_mode,
                actions=_connected_context_desk_items(db, account=account, limit=5),
                suggested_prompts=["Show Planning Center context.", "What events need prep?"],
            )
        except HTTPException as exc:
            verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "planning_center", exc)
            if verify_first:
                return verify_first
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I could not sync Planning Center yet: {_redact_secret_text(exc.detail)}",
                intent="sync_planning_center_unavailable",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["Open integrations.", "What can you do before Planning Center is connected?"],
            )

    if _mentions(lower, ["sync", "refresh", "pull"]) and _mentions(lower, ["breeze", "breeze chms"]):
        integrations = _integration_statuses(db, account, access.user)
        not_ready = _provider_sync_not_ready_response(profile, integrations, "breeze", account, effective_mode)
        if not_ready:
            return _chat_turn_response(db, account, access.user, request.message, **not_ready)
        try:
            sync_result = _sync_breeze(db, people_limit=25, calendar_days=14, account=account)
            if sync_result.status == "synced":
                reply = (
                    f"I synced Breeze and found {sync_result.items_seen} people or event item(s). "
                    f"I queued {sync_result.actions_prepared} review item(s) from that context."
                )
                prompts = ["Show Breeze context.", "What events need prep?"]
            else:
                reply = f"I could not sync Breeze yet: {_redact_secret_text(sync_result.message)}"
                prompts = ["Open integrations.", "What can you do before Breeze is configured?"]
        except HTTPException as exc:
            verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "breeze", exc)
            if verify_first:
                return verify_first
            reply = f"I could not sync Breeze yet: {_redact_secret_text(exc.detail)}"
            prompts = ["Open integrations.", "Check Breeze credentials."]
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="sync_breeze",
            mode=effective_mode,
            actions=_connected_context_desk_items(db, account=account, limit=5),
            suggested_prompts=prompts,
        )

    if _generic_inbox_sync_requested(lower):
        provider = _mail_sync_provider(db, profile, account, access.user)
        if provider == "microsoft_365":
            try:
                sync_result = _sync_microsoft_365(db, email_limit=5, calendar_days=14, account=account, user=access.user)
                reply = (
                    f"I synced Microsoft 365 for the inbox request and found {sync_result.items_seen} Outlook mail or calendar item(s). "
                    f"I queued {sync_result.actions_prepared} item(s) for review."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="sync_microsoft_365",
                    mode=effective_mode,
                    actions=_connected_context_desk_items(db, account=account, limit=5),
                    suggested_prompts=["Show my synced inbox.", "Queue replies for these.", "What meetings need prep?"],
                )
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "microsoft_365", exc)
                if verify_first:
                    return verify_first
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=f"I could not sync Microsoft 365 yet: {_redact_secret_text(exc.detail)}",
                    intent="sync_microsoft_365_unavailable",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=["Open integrations.", "Connect Microsoft 365."],
                )
        if provider == "google_workspace":
            try:
                sync_result = _sync_google_workspace(db, email_limit=5, calendar_days=14, account=account, user=access.user)
                reply = (
                    f"I synced Google Workspace for the inbox request and found {sync_result.items_seen} item(s). "
                    f"I queued {sync_result.actions_prepared} item(s) for review."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="sync_google_workspace",
                    mode=effective_mode,
                    actions=_connected_context_desk_items(db, account=account, limit=5),
                    suggested_prompts=["Show my synced inbox.", "Queue replies for these.", "What meetings need prep?"],
                )
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "google_workspace", exc)
                if verify_first:
                    return verify_first
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=f"I could not sync Google Workspace yet: {_redact_secret_text(exc.detail)}",
                    intent="sync_google_workspace_unavailable",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=["Open integrations.", "Connect Google Workspace."],
                )
        integrations = _integration_statuses(db, account, access.user)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _mailbox_sync_not_connected_response(profile, integrations, account, effective_mode),
        )

    if _generic_calendar_sync_requested(lower):
        provider = _calendar_sync_provider(db, profile, account, access.user)
        if provider == "planning_center":
            try:
                sync_result = _sync_planning_center(db, people_limit=25, calendar_days=14, account=account, user=access.user)
                reply = (
                    f"I synced Planning Center for the calendar request and found {sync_result.items_seen} people or event item(s). "
                    f"I queued {sync_result.actions_prepared} pastor-review item(s)."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="sync_planning_center",
                    mode=effective_mode,
                    actions=_connected_context_desk_items(db, account=account, limit=5),
                    suggested_prompts=["Show Planning Center context.", "What events need prep?"],
                )
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "planning_center", exc)
                if verify_first:
                    return verify_first
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=f"I could not sync Planning Center yet: {_redact_secret_text(exc.detail)}",
                    intent="sync_planning_center_unavailable",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=["Open integrations.", "Connect Planning Center."],
                )
        if provider == "breeze":
            try:
                sync_result = _sync_breeze(db, people_limit=25, calendar_days=14, account=account)
                if sync_result.status == "synced":
                    reply = (
                        f"I synced Breeze for the calendar request and found {sync_result.items_seen} people or event item(s). "
                        f"I queued {sync_result.actions_prepared} review item(s)."
                    )
                    prompts = ["Show Breeze context.", "What events need prep?"]
                else:
                    reply = f"I could not sync Breeze yet: {_redact_secret_text(sync_result.message)}"
                    prompts = ["Open integrations.", "Connect Breeze."]
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "breeze", exc)
                if verify_first:
                    return verify_first
                reply = f"I could not sync Breeze yet: {_redact_secret_text(exc.detail)}"
                prompts = ["Open integrations.", "Check Breeze credentials."]
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="sync_breeze",
                mode=effective_mode,
                actions=_connected_context_desk_items(db, account=account, limit=5),
                suggested_prompts=prompts,
            )
        if provider == "microsoft_365":
            try:
                sync_result = _sync_microsoft_365(db, email_limit=5, calendar_days=14, account=account, user=access.user)
                reply = (
                    f"I synced Microsoft 365 for the calendar request and found {sync_result.items_seen} Outlook mail or calendar item(s). "
                    f"I queued {sync_result.actions_prepared} item(s) for review."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="sync_microsoft_365",
                    mode=effective_mode,
                    actions=_connected_context_desk_items(db, account=account, limit=5),
                    suggested_prompts=["Show my synced calendar.", "What meetings need prep?"],
                )
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "microsoft_365", exc)
                if verify_first:
                    return verify_first
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=f"I could not sync Microsoft 365 yet: {_redact_secret_text(exc.detail)}",
                    intent="sync_microsoft_365_unavailable",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=["Open integrations.", "Connect Microsoft 365."],
                )
        if provider == "google_workspace":
            try:
                sync_result = _sync_google_workspace(db, email_limit=5, calendar_days=14, account=account, user=access.user)
                reply = (
                    f"I synced Google Workspace for the calendar request and found {sync_result.items_seen} item(s). "
                    f"I queued {sync_result.actions_prepared} item(s) for review."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="sync_google_workspace",
                    mode=effective_mode,
                    actions=_connected_context_desk_items(db, account=account, limit=5),
                    suggested_prompts=["Show my synced calendar.", "What meetings need prep?"],
                )
            except HTTPException as exc:
                verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "google_workspace", exc)
                if verify_first:
                    return verify_first
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=f"I could not sync Google Workspace yet: {_redact_secret_text(exc.detail)}",
                    intent="sync_google_workspace_unavailable",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=["Open integrations.", "Connect Google Workspace."],
                )
        integrations = _integration_statuses(db, account, access.user)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _calendar_sync_not_connected_response(profile, integrations, account, effective_mode),
        )

    if _mentions(lower, ["sync", "refresh", "pull"]) and _mentions(lower, ["microsoft", "microsoft 365", "office 365", "outlook"]):
        integrations = _integration_statuses(db, account, access.user)
        not_ready = _provider_sync_not_ready_response(profile, integrations, "microsoft_365", account, effective_mode)
        if not_ready:
            return _chat_turn_response(db, account, access.user, request.message, **not_ready)
        try:
            sync_result = _sync_microsoft_365(db, email_limit=5, calendar_days=14, account=account, user=access.user)
            reply = (
                f"I synced Microsoft 365 and found {sync_result.items_seen} Outlook mail or calendar item(s). "
                f"I queued {sync_result.actions_prepared} item(s) for review. Tokens stayed server-side."
            )
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="sync_microsoft_365",
                mode=effective_mode,
                actions=_connected_context_desk_items(db, account=account, limit=5),
                suggested_prompts=["Show my synced inbox.", "What meetings need prep?"],
            )
        except HTTPException as exc:
            verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "microsoft_365", exc)
            if verify_first:
                return verify_first
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I could not sync Microsoft 365 yet: {_redact_secret_text(exc.detail)}",
                intent="sync_microsoft_365_unavailable",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["Open integrations.", "What can you do before Microsoft 365 is connected?"],
            )

    if _mentions(lower, ["sync", "refresh", "pull"]) and _mentions(lower, ["google", "gmail", "inbox", "email", "calendar"]):
        integrations = _integration_statuses(db, account, access.user)
        not_ready = _provider_sync_not_ready_response(profile, integrations, "google_workspace", account, effective_mode)
        if not_ready:
            return _chat_turn_response(db, account, access.user, request.message, **not_ready)
        try:
            sync_result = _sync_google_workspace(db, email_limit=5, calendar_days=14, account=account, user=access.user)
            reply = (
                f"I synced Google Workspace and found {sync_result.items_seen} item(s). "
                f"I queued {sync_result.actions_prepared} item(s) for review. Tokens stayed server-side."
            )
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="sync_google_workspace",
                mode=effective_mode,
                actions=_connected_context_desk_items(db, account=account, limit=5),
                suggested_prompts=["Show my synced inbox.", "What meetings need prep?"],
            )
        except HTTPException as exc:
            verify_first = _verify_before_sync_chat_response(db, account, access.user, profile, request.message, effective_mode, "google_workspace", exc)
            if verify_first:
                return verify_first
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I could not sync Google Workspace yet: {_redact_secret_text(exc.detail)}",
                intent="sync_google_workspace_unavailable",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["Open integrations.", "What can you do without Google connected?"],
            )

    if _connected_context_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _connected_context_chat_response(db, profile, account, access.user, lower, effective_mode),
        )

    if _pre_connector_help_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        provider = _provider_from_chat(lower)
        provider_phrase = f" before {_provider_display_name(provider)} is connected" if provider else " before the church tools are connected"
        if seed_step:
            next_step = f"I would start by helping you {seed_step.action.lower()}: {seed_step.subtitle or seed_step.detail}"
        elif not _profile_is_complete(profile):
            question = _interview_question(profile)
            next_step = f"I would keep learning your ministry context first: {question['question']}" if question else "I would keep learning your ministry context first."
        else:
            next_step = "I would start with any real visitor, member, care case, or prayer request you want me to remember."
        reply = (
            f"Even{provider_phrase}, I can still be useful. I can learn your ministry context, keep local people, visitor, care, and prayer memory, "
            "draft reviewable follow-up from the details you add, prepare a first-week plan, and keep an approval queue. "
            f"{next_step} Secure connectors come next: setup, credential check, then sync only when you ask."
        )
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="pre_connector_help",
            mode=effective_mode,
            actions=setup_steps[:3],
            suggested_prompts=["Help me log the first real visitor.", "What should I connect first?", "Explain the approval rules."],
            profile=_profile_response(profile, account),
        )

    if _connector_setup_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        provider = _provider_from_chat(lower)
        if not provider:
            provider = _next_setup_provider(profile, integrations)
        if provider:
            status = next((item for item in integrations if item.provider == provider), None)
            recommendation = _connector_setup_recommendation(profile, provider, integrations)
            if status and status.status in {"connected", "configured", "available"} and not status.verified_at:
                check_step = _integration_check_credentials_step(status, profile)
                reply = (
                    f"{recommendation} {status.display_name} is {status.status.replace('_', ' ')}, "
                    "but I still need to check credentials before syncing ministry data."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="integration_setup_started",
                    mode=effective_mode,
                    actions=[check_step],
                    suggested_prompts=[f"Check {status.display_name} credentials.", "Open integrations.", "Explain the approval rules."],
                )
            if status and status.status in {"connected", "configured", "available"} and status.verified_at:
                reply = (
                    f"{recommendation} {status.display_name} is already {status.status.replace('_', ' ')} and checked. "
                    "Ask me to sync it when you want me to pull fresh context."
                )
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=reply,
                    intent="integration_setup_started",
                    mode=effective_mode,
                    actions=[],
                    suggested_prompts=[f"Sync {status.display_name}.", "Open integrations.", "Explain the approval rules."],
                )
            setup_result = _prepare_integration_setup_from_chat(db, provider, account, access.user)
            setup = setup_result["setup"]
            action = setup_result.get("action")
            if setup.status in {"connected", "configured", "available"}:
                reply = (
                    f"{recommendation} {setup.display_name} is already {setup.status.replace('_', ' ')} and checked. "
                    "Ask me to sync it when you want me to pull fresh context."
                )
            elif setup.authorization_url:
                reply = (
                    f"{recommendation} "
                    f"I prepared the secure {setup.display_name} authorization step. "
                    "Open it from the approval queue or Integrations screen; I will not ask you to paste tokens or passwords into chat."
                )
            elif setup.missing_config:
                if _api_key_setup_can_accept_workspace_credentials(setup):
                    reply = (
                        f"{recommendation} "
                        f"I queued encrypted workspace credential setup for {setup.display_name}. "
                        f"Add the church's API key and public HTTPS base URL here, or configure {', '.join(setup.missing_config)} server-side. "
                        "I will still check credentials before syncing ministry data."
                    )
                else:
                    reply = (
                        f"{recommendation} "
                        f"{setup.display_name} needs secure server configuration before a pastor can connect it: {', '.join(setup.missing_config)}. "
                        "I queued the setup note so this stays visible without exposing secrets."
                    )
            else:
                reply = (
                    f"{recommendation} "
                    f"I queued the {setup.display_name} setup instructions. "
                    "This connector stays read-side until a policy and approval allow writes."
                )
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="integration_setup_started",
                mode=effective_mode,
                actions=[_desk_item_from_action(action)] if action else [],
                suggested_prompts=_integration_setup_chat_prompts(setup),
            )

    if _morning_briefing_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _morning_briefing_chat_response(
                profile,
                priorities,
                email_drafts,
                calendar_blocks,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    if _mentions(lower, ["attention", "before noon", "today", "who first", "who needs", "priority"]):
        actions = priorities[:4]
        if actions:
            lines = "; ".join(f"{item.title}: {item.action or item.subtitle or item.detail}" for item in actions[:3])
            reply = f"I would start here: {lines}. I am pulling this from the current care, visitor, prayer, and absence data."
        else:
            seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
            if seed_step:
                return _persist_chat_response(db, account, access.user, request.message, _data_seed_chat_response(seed_step, effective_mode, profile, account))
            integrations = _integration_statuses(db, account, access.user)
            setup_steps = _setup_steps(profile, integrations, needs_seed_context=False)
            if setup_steps:
                first = setup_steps[0]
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=(
                        "I do not see an urgent care, visitor, prayer, or absence follow-up in the current data yet. "
                        f"The next useful step is to {_setup_summary_phrase(first)} so I can work from real ministry context instead of guessing."
                    ),
                    intent="prioritize_day",
                    mode=effective_mode,
                    actions=setup_steps[:4],
                    suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
                )
            rhythm = _clean(profile.weekly_rhythm)
            if rhythm:
                reply = (
                    "I do not see an urgent care, visitor, prayer, or absence follow-up in the current data. "
                    f"Based on the rhythm you saved, I would protect this next: {rhythm}."
                )
            else:
                reply = (
                    "I do not see an urgent care, visitor, prayer, or absence follow-up in the current data yet. "
                    "Give me the next real person, visitor, prayer request, or care update and I will keep that follow-up in view."
                )
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="prioritize_day",
            mode=effective_mode,
            actions=actions,
            suggested_prompts=_suggested_prompts(profile, priorities),
        )

    if _queue_synced_inbox_replies_requested(lower):
        actions = _prepare_connected_email_replies(db, profile, lower, account, limit=3)
        if actions:
            connected_email = _connected_items_filtered(
                db,
                account=account,
                provider=_provider_from_chat(lower),
                item_type="email",
                limit=3,
            )
            integrations = _integration_statuses(db, account, access.user)
            refresh_state = _connected_items_refresh_state(profile, integrations, connected_email)
            titles = "; ".join(action.title for action in actions[:3])
            reply = (
                f"I queued {len(actions)} synced inbox repl{'y' if len(actions) == 1 else 'ies'} for your review: {titles}. "
                "These are drafts only; I will not send or write externally without approval."
            )
            if refresh_state["note"]:
                reply += f" {refresh_state['note']}"
            prompts = ["Show my approvals.", "What else is in my inbox?"]
            if refresh_state["refresh_prompt"]:
                prompts.append(refresh_state["refresh_prompt"])
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="draft_synced_email_replies_queued",
                mode=effective_mode,
                saved=True,
                actions=_dedupe_desk_items(
                    [_desk_item_from_action(action) for action in actions] + refresh_state["actions"][:1]
                ),
                suggested_prompts=prompts,
            )
        return _empty_synced_inbox_chat_response(
            db,
            account,
            access.user,
            request.message,
            profile,
            effective_mode,
            intent="draft_synced_email_replies_empty",
            drafting=True,
        )

    if _mentions(lower, ["draft", "reply", "respond", "replies"]) and _mentions(lower, ["inbox", "message", "gmail", "email", "outlook"]):
        action = _prepare_connected_email_reply(db, profile, lower, account)
        if action:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I drafted a reply from the synced inbox and put it in your approval queue: {action.title}. I will not send or write anything externally without approval and an enabled connector policy.",
                intent="draft_synced_email_reply",
                mode=effective_mode,
                actions=[_desk_item_from_action(action)],
                suggested_prompts=["Show my approvals.", "What else is in my inbox?"],
            )

    if _mentions(lower, ["inbox", "message", "gmail", "outlook"]) or (_mentions(lower, ["email"]) and not _mentions(lower, ["draft"])):
        connected_email = _connected_items(db, "email", account=account, limit=5)
        if connected_email:
            integrations = _integration_statuses(db, account, access.user)
            refresh_state = _connected_items_refresh_state(profile, integrations, connected_email)
            lines = "; ".join(f"{item.title}: {item.snippet or item.subtitle or 'Review'}" for item in connected_email[:3])
            reply = f"From the synced inbox, I would review these first: {lines}. I queued inbox items for review instead of sending anything."
            if refresh_state["note"]:
                reply += f" {refresh_state['note']}"
            prompts = ["Queue replies for these."]
            if refresh_state["refresh_prompt"]:
                prompts.append(refresh_state["refresh_prompt"])
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="synced_inbox",
                mode=effective_mode,
                actions=_dedupe_desk_items(
                    [_desk_item_from_connected_item(item) for item in connected_email] + refresh_state["actions"][:1]
                ),
                suggested_prompts=prompts,
            )
        return _empty_synced_inbox_chat_response(
            db,
            account,
            access.user,
            request.message,
            profile,
            effective_mode,
            intent="synced_inbox_empty",
            drafting=False,
        )

    if _scheduling_reply_requested(lower):
        action = _prepare_scheduling_reply_action(db, profile, account, calendar_blocks, priorities, effective_mode)
        return _chat_turn_response(db, account, access.user, request.message,
            reply=(
                f"I drafted a scheduling reply for review: {action.title}. "
                "It uses your saved rhythm and current ministry priorities, and I will not send it or create a calendar event without approval."
            ),
            intent="scheduling_reply_drafted",
            mode=effective_mode,
            saved=True,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Show my approvals.", "Where can I fit care follow-up?", "What can wait until next week?"],
            profile=_profile_response(profile, account),
        )

    if _mentions(lower, ["draft", "email", "reply", "inbox"]):
        if email_drafts:
            actions = _prepare_email_draft_actions(db, effective_mode, email_drafts, profile, account, limit=4)
            _audit(
                db,
                "assistant_actions.email_drafts_prepared_from_chat",
                f"Prepared {len(actions)} email draft action(s) from chat.",
                account=account,
                payload={"mode": effective_mode, "count": len(actions)},
            )
            db.commit()
            for action in actions:
                db.refresh(action)
            lines = "; ".join(f"{action.title}" for action in actions[:3])
            reply = f"I drafted and queued {len(actions)} ministry repl{'y' if len(actions) == 1 else 'ies'} for review: {lines}. Nothing will be sent until you approve the exact text."
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="draft_replies_queued",
                mode=effective_mode,
                actions=[_desk_item_from_action(action) for action in actions[:5]],
                suggested_prompts=["Show my approvals.", "What should I approve first?"],
            )
        else:
            seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
            if seed_step:
                return _persist_chat_response(db, account, access.user, request.message, _data_seed_chat_response(seed_step, effective_mode, profile, account))
            return _draft_replies_empty_chat_response(
                db,
                account,
                access.user,
                request.message,
                profile,
                effective_mode,
            )

    if _meeting_prep_lookup_requested(lower):
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _meeting_prep_lookup_response(db, profile, account, access.user, effective_mode),
        )

    if _mentions(lower, ["prepare", "prep", "brief"]) and _mentions(lower, ["meeting", "calendar", "event", "visit"]):
        action = _prepare_connected_meeting_prep(db, profile, lower, account)
        if action:
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"I prepared a meeting brief for your approval queue: {action.title}.",
                intent="prepare_synced_meeting",
                mode=effective_mode,
                actions=[_desk_item_from_action(action)],
                suggested_prompts=["Show my approvals.", "What meetings are coming up?"],
            )
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _meeting_prep_lookup_response(db, profile, account, access.user, effective_mode),
        )

    if _care_visit_planning_requested(lower):
        visit_plan = _maybe_prepare_care_visit_plan(db, profile, account, request.message, lower, effective_mode)
        if visit_plan:
            return _persist_chat_response(db, account, access.user, request.message, visit_plan)
        seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _care_visit_needs_context_response(db, profile, account, effective_mode, seed_step),
        )

    if _person_context_requested(lower):
        ministry_context = _maybe_answer_ministry_context(db, profile, account, request.message, lower, effective_mode)
        if ministry_context:
            return _persist_chat_response(db, account, access.user, request.message, ministry_context)

    if _mentions(lower, ["calendar", "schedule", "visit", "block", "meet", "meeting", "time"]):
        visit_plan = _maybe_prepare_care_visit_plan(db, profile, account, request.message, lower, effective_mode)
        if visit_plan:
            return _persist_chat_response(db, account, access.user, request.message, visit_plan)
        if _care_visit_planning_requested(lower):
            seed_step = _seed_context_step(db, account, profile, effective_mode, access.user)
            return _persist_chat_response(
                db,
                account,
                access.user,
                request.message,
                _care_visit_needs_context_response(db, profile, account, effective_mode, seed_step),
            )
        connected_events = _connected_items(db, "calendar_event", account=account, limit=5)
        if connected_events and _mentions(lower, ["calendar", "meeting", "meetings", "events"]):
            lines = "; ".join(f"{item.title}: {item.subtitle or _date_label(item.occurred_at)}" for item in connected_events[:3])
            return _chat_turn_response(db, account, access.user, request.message,
                reply=f"From your synced calendar, these are worth keeping in view: {lines}. I will prepare meeting context but will not change the calendar without approval.",
                intent="synced_calendar",
                mode=effective_mode,
                actions=[_desk_item_from_connected_item(item) for item in connected_events],
                suggested_prompts=["Prepare my next meeting.", "Where can I fit care follow-up?"],
            )
        reply = _calendar_reply(calendar_blocks, profile)
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="calendar_planning",
            mode=effective_mode,
            actions=calendar_blocks,
            suggested_prompts=["Draft a scheduling reply.", "What can wait until next week?"],
        )

    if _defer_until_next_week_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _defer_until_next_week_response(
                profile,
                priorities,
                email_drafts,
                calendar_blocks,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    if _check_on_next_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _check_on_next_response(
                db,
                profile,
                priorities,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    if _next_action_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        pending_actions = _pending_assistant_actions(db, account)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _next_action_response(
                profile,
                priorities,
                email_drafts,
                calendar_blocks,
                setup_steps,
                pending_actions,
                effective_mode,
                account,
            ),
        )

    if _profile_setting_lookup_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _profile_setting_lookup_response(profile, setup_steps, effective_mode, account, lower),
        )

    if _approval_rules_requested(lower):
        pending_actions = _pending_assistant_actions(db, account)
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        reply = (
            "The approval rule is simple: I can draft, summarize, prepare, and queue work, but I do not send email, create calendar events, "
            "or change an external church system until the connector credentials are checked, the church writeback policy allows that action type, "
            "and you approve the exact item. Private prayer and care context stay inside this workspace unless you explicitly put it into an approved draft."
        )
        if pending_actions:
            reply += f" You currently have {len(pending_actions)} item(s) waiting for review."
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="approval_rules",
            mode=effective_mode,
            actions=[_desk_item_from_action(action) for action in pending_actions[:3]],
            suggested_prompts=(
                ["Show my approvals.", "What should I approve first?", "Explain the approval rules."]
                if pending_actions
                else _suggested_prompts(profile, priorities, setup_steps)
            ),
        )

    if _first_week_plan_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        if not _profile_is_complete(profile):
            first_step = setup_steps[0] if setup_steps else None
            next_question = _interview_question(profile)
            reply = (
                "I can prepare a first-week plan after I learn the core ministry context. "
                f"Start here: {next_question['question'] if next_question else 'answer the next ministry-context question.'}"
            )
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="first_week_plan_needs_context",
                mode=effective_mode,
                actions=[first_step] if first_step else [],
                suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
                profile=_profile_response(profile, account),
            )
        actions = _prepare_setup_actions(db, profile, setup_steps, account)
        first_week_action = next((action for action in actions if action.action_type == "first_week_plan"), None)
        plan = _json_loads(first_week_action.payload_json).get("plan") if first_week_action else _first_week_plan(profile, setup_steps)
        summary = _first_week_plan_chat_summary(plan)
        reply = (
            f"I prepared your first-week Marge launch plan for review. {summary} "
            "This stays as a review item; I will not connect tools, send messages, or write externally without approval."
        )
        returned_actions = [_desk_item_from_action(first_week_action)] if first_week_action else []
        returned_actions.extend(setup_steps[:2])
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="first_week_plan_prepared",
            mode=effective_mode,
            saved=True,
            actions=returned_actions[:3],
            suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
            profile=_profile_response(profile, account),
        )

    approval_lookup = _approval_queue_lookup_requested(lower)
    approval_prepare = (
        _mentions(lower, ["prepare the work", "prepare actions", "prepare approvals", "prepare approval", "prepare today"])
        or (_mentions(lower, ["queue", "prepare"]) and _mentions(lower, ["work", "actions", "drafts", "approvals"]))
    )
    if approval_lookup and not approval_prepare:
        pending_actions = _pending_assistant_actions(db, account)
        if pending_actions:
            first = pending_actions[0]
            lines = "; ".join(f"{action.title} ({action.status})" for action in pending_actions[:4])
            reply = (
                f"I would start with this approval item: {first.title}. Current approval queue: {lines}. "
                "I will not send or write anything externally until you approve the exact item."
            )
        else:
            integrations = _integration_statuses(db, account, access.user)
            setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
            setup_actions = setup_steps[:3]
            if setup_actions:
                next_step = setup_actions[0]
                reply = (
                    f"There are no pending approval items right now. The next useful step is {next_step.title}: "
                    f"{next_step.detail or next_step.subtitle or next_step.action}. "
                    "Once there is a real person, prayer, synced email, or care note to work from, I can stage reviewable drafts or calendar blocks."
                )
            else:
                reply = (
                    "There are no pending approval items right now. Give me a real visitor, prayer request, synced email, "
                    "or care note, and I can turn it into a reviewable draft or calendar block without sending anything automatically."
                )
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="approval_queue_lookup",
            mode=effective_mode,
            actions=[_desk_item_from_action(action) for action in pending_actions[:5]] if pending_actions else setup_actions,
            suggested_prompts=_suggested_prompts(profile, priorities, setup_steps if not pending_actions else None),
        )

    if approval_prepare:
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        actions = _prepare_actions_from_desk(db, effective_mode, email_drafts, calendar_blocks, priorities, profile, account)
        actions.extend(_prepare_setup_actions(db, profile, setup_steps, account))
        _audit(db, "assistant_actions.prepared", f"Prepared {len(actions)} assistant action(s) from chat.", account=account, payload={"mode": effective_mode, "count": len(actions)})
        db.commit()
        reply = (
            f"I prepared {len(actions)} item(s) for your review queue. "
            "Nothing will be sent or written to another system until you approve it."
        ) if actions else "I do not see anything that needs an approval queue right now."
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="prepare_approval_queue",
            mode=effective_mode,
            actions=[_desk_item_from_action(action) for action in actions[:5]],
            suggested_prompts=["Show my approvals.", "What should I approve first?"],
        )

    if _open_integrations_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        return _persist_chat_response(
            db,
            account,
            access.user,
            request.message,
            _open_integrations_chat_response(db, profile, integrations, account, effective_mode),
        )

    if _mentions(lower, ["connect", "integration", "tools", "planning center", "rock", "gmail", "outlook", "breeze"]):
        integrations = _integration_statuses(db, account, access.user)
        church_tools = [item for item in integrations if item.provider != "mcp"]
        configured = [item.display_name for item in church_tools if item.status in {"connected", "configured"} and item.verified_at]
        planned = [item.display_name for item in church_tools if item.status not in {"connected", "configured"} or not item.verified_at]
        ready_text = ", ".join(configured) if configured else "no church tools connected yet"
        planned_text = ", ".join(planned) if planned else "none"
        reply = (
            f"I can see the church-tool connector plan. Ready now: {ready_text}. "
            f"Still needing secure setup or a credential check: {planned_text}. I will never ask you to paste secrets into chat."
        )
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="integration_status",
            mode=effective_mode,
            actions=[],
            suggested_prompts=["What should I connect first?", "Explain the approval rules."],
        )

    if _ministry_operating_plan_requested(lower):
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        plan = _operating_plan(profile, integrations, priorities, email_drafts, calendar_blocks, setup_steps)
        reply = _ministry_operating_plan_reply(profile, plan)
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="ministry_operating_plan",
            mode=effective_mode,
            actions=setup_steps[:3] if setup_steps else priorities[:3],
            suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
            profile=_profile_response(profile, account),
        )

    if _ministry_learning_gaps_requested(lower):
        missing = _profile_response(profile, account).missing_fields
        if missing:
            next_question = _interview_question(profile) or next((q for q in ONBOARDING_QUESTIONS if q["id"] == missing[0]), ONBOARDING_QUESTIONS[0])
            why = next_question.get("why") if isinstance(next_question, dict) else None
            reply = f"I still need to learn your ministry context. Start here: {next_question['question']}"
            if why:
                reply = f"{reply} {why}"
            return _chat_turn_response(db, account, access.user, request.message,
                reply=reply,
                intent="onboarding",
                mode=effective_mode,
                actions=[],
                suggested_prompts=["How will you use this context?", "What should I handle next?"],
                profile=_profile_response(profile, account),
            )
        integrations = _integration_statuses(db, account, access.user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        response = _ministry_learning_gaps_response(profile, setup_steps, priorities, effective_mode, account)
        return _chat_turn_response(db, account, access.user, request.message,
            reply=response.reply,
            intent=response.intent,
            mode=effective_mode,
            actions=response.actions,
            suggested_prompts=response.suggested_prompts,
            profile=response.profile,
        )

    if _mentions(lower, ["setup", "profile", "context", "learn", "onboard", "about me", "ministry"]):
        missing = _profile_response(profile, account).missing_fields
        if missing:
            next_question = _interview_question(profile) or next((q for q in ONBOARDING_QUESTIONS if q["id"] == missing[0]), ONBOARDING_QUESTIONS[0])
            why = next_question.get("why") if isinstance(next_question, dict) else None
            reply = f"I still need to learn your ministry context. Start here: {next_question['question']}"
            if why:
                reply = f"{reply} {why}"
            actions = []
            prompts = ["What do you know about my church?", "How will you use this context?", "What should I handle next?"]
        else:
            integrations = _integration_statuses(db, account, access.user)
            setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
            if _ministry_learning_gaps_requested(lower):
                response = _ministry_learning_gaps_response(profile, setup_steps, priorities, effective_mode, account)
                return _chat_turn_response(db, account, access.user, request.message,
                    reply=response.reply,
                    intent=response.intent,
                    mode=effective_mode,
                    actions=response.actions,
                    suggested_prompts=response.suggested_prompts,
                    profile=response.profile,
                )
            plan = _operating_plan(profile, integrations, priorities, email_drafts, calendar_blocks, setup_steps)
            reply = "I have the core ministry profile. " + _ministry_operating_plan_reply(profile, plan)
            actions = []
            prompts = ["What do you know about my church?", "How will you use this context?", "What should I handle next?"]
        return _chat_turn_response(db, account, access.user, request.message,
            reply=reply,
            intent="onboarding",
            mode=effective_mode,
            actions=actions,
            suggested_prompts=prompts,
            profile=_profile_response(profile, account),
        )

    integrations = _integration_statuses(db, account, access.user)
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
    reply = _default_reply(profile, priorities, email_drafts, calendar_blocks, setup_steps)
    fallback_actions = (priorities[:3] + setup_steps[:2])[:3] if priorities else setup_steps[:3]
    return _chat_turn_response(db, account, access.user, request.message,
        reply=reply,
        intent="general_assistant",
        mode=effective_mode,
        actions=fallback_actions,
        suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
        profile=_profile_response(profile, account),
    )


def _create_account(db: Session, payload: AccountSignupRequest) -> tuple[ChurchAccount, str, AccountPastorProfile, AccountUser]:
    church_name = _clean(payload.church_name)
    if not church_name:
        raise HTTPException(status_code=422, detail="Church name is required to create a Marge workspace.")
    email = _clean_email(payload.email)
    if not email:
        raise HTTPException(status_code=422, detail="A valid email is required to create a Marge workspace and recover access later.")
    pastor_name = _clean(payload.pastor_name)
    slug_base = _slugify(church_name)
    slug = _unique_slug(db, slug_base)
    legacy_account_token = f"marge_acct_{secrets.token_urlsafe(32)}"
    account = ChurchAccount(
        slug=slug,
        church_name=church_name,
        pastor_name=pastor_name,
        email=email,
        token_hash=_token_hash(legacy_account_token),
    )
    db.add(account)
    db.flush()
    owner_user, owner_token = _create_account_user(
        db,
        account,
        name=pastor_name,
        email=email,
        role="owner",
    )
    profile = AccountPastorProfile(
        account_id=account.id,
        pastor_name=pastor_name,
        church_name=church_name,
        role_title=_clean(payload.role_title),
        congregation_size=_clean(payload.congregation_size),
        church_context=_clean(payload.church_context),
        faith_tradition=_clean(payload.faith_tradition),
        ministry_priorities=_clean(payload.ministry_priorities),
        followup_pain=_clean(payload.followup_pain),
        support_preferences=_clean(payload.support_preferences),
        weekly_rhythm=_clean(payload.weekly_rhythm),
        communication_style=_clean(payload.communication_style),
        tools_in_use=_clean(payload.tools_in_use),
        guardrails=_clean(payload.guardrails),
    )
    profile.onboarding_complete = _profile_is_complete(profile)
    db.add(profile)
    return account, owner_token, profile, owner_user


def _create_account_user(
    db: Session,
    account: ChurchAccount,
    *,
    name: Optional[str],
    email: Optional[str],
    role: str,
) -> tuple[AccountUser, str]:
    if not account:
        raise HTTPException(status_code=401, detail="A church workspace is required.")
    normalized_role = normalize_role(role, "staff")
    email_value = _clean_email(email)
    if not email_value:
        raise HTTPException(status_code=422, detail="A valid email is required for workspace users.")
    token = f"marge_user_{secrets.token_urlsafe(32)}"
    user = AccountUser(
        account_id=account.id,
        name=_clean(name),
        email=email_value,
        role=normalized_role,
        token_hash=_token_hash(token),
        active=True,
    )
    db.add(user)
    db.flush()
    return user, token


def _create_account_session(
    db: Session,
    account: ChurchAccount,
    user: AccountUser,
    duration_hours: int,
) -> tuple[AccountSession, str]:
    if not account or not user or not user.active:
        raise HTTPException(status_code=401, detail="An active workspace user is required.")
    bounded_hours = max(1, min(int(duration_hours or 168), 720))
    token = f"marge_sess_{secrets.token_urlsafe(32)}"
    now = datetime.utcnow()
    session = AccountSession(
        account_id=account.id,
        user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=now + timedelta(hours=bounded_hours),
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    return session, token


def _create_login_link(db: Session, account: ChurchAccount, user: AccountUser) -> tuple[AccountLoginLink, str]:
    if not account or not user or not user.active:
        raise HTTPException(status_code=401, detail="An active workspace user is required.")
    now = datetime.utcnow()
    token = f"marge_login_{secrets.token_urlsafe(32)}"
    login_link = AccountLoginLink(
        account_id=account.id,
        user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=now + timedelta(minutes=LOGIN_LINK_TTL_MINUTES),
        created_at=now,
    )
    db.add(login_link)
    db.flush()
    return login_link, token


def _recent_login_link(db: Session, user: AccountUser) -> Optional[AccountLoginLink]:
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=LOGIN_LINK_RESEND_COOLDOWN_MINUTES)
    return (
        db.query(AccountLoginLink)
        .filter(
            AccountLoginLink.user_id == user.id,
            AccountLoginLink.consumed_at.is_(None),
            AccountLoginLink.expires_at > now,
            AccountLoginLink.created_at >= cutoff,
        )
        .order_by(AccountLoginLink.created_at.desc())
        .first()
    )


def _find_login_link_user(db: Session, email: Optional[str], church_slug: Optional[str]) -> tuple[Optional[AccountUser], Optional[ChurchAccount]]:
    if not email:
        return None, None
    normalized_email = email.lower()
    candidates = (
        db.query(AccountUser)
        .filter(AccountUser.active.is_(True), func.lower(AccountUser.email) == normalized_email)
        .order_by(AccountUser.created_at.desc())
        .all()
    )
    matches: list[tuple[AccountUser, ChurchAccount]] = []
    for user in candidates:
        account = db.get(ChurchAccount, user.account_id)
        if not account:
            continue
        if church_slug and account.slug != church_slug:
            continue
        matches.append((user, account))
    if len(matches) != 1:
        return None, None
    return matches[0]


def _login_link_from_token(db: Session, token: str) -> AccountLoginLink:
    cleaned = _clean(token)
    if not cleaned:
        raise HTTPException(status_code=401, detail="A Marge sign-in link token is required.")
    login_link = db.query(AccountLoginLink).filter(AccountLoginLink.token_hash == _token_hash(cleaned)).first()
    now = datetime.utcnow()
    if not login_link or login_link.consumed_at:
        raise HTTPException(status_code=401, detail="This Marge sign-in link is no longer valid.")
    if login_link.expires_at <= now:
        login_link.consumed_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="This Marge sign-in link has expired.")
    return login_link


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(1, int((expires_at - datetime.utcnow()).total_seconds()))
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        max_age=max_age,
        expires=max_age,
        path="/",
        httponly=True,
        secure=session_cookie_secure(),
        samesite=session_cookie_samesite(),
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=session_cookie_name(),
        path="/",
        httponly=True,
        secure=session_cookie_secure(),
        samesite=session_cookie_samesite(),
    )


def _account_from_token(db: Session, token: Optional[str]) -> Optional[ChurchAccount]:
    return _shared_account_from_token(db, token)


def _account_access_from_token(db: Session, token: Optional[str]) -> AccountAccess:
    return _shared_account_access_from_token(db, token)


def _require_role(access: AccountAccess, allowed_roles: set[str], action: str) -> None:
    require_role(access, allowed_roles, action)


def _require_workspace(access: AccountAccess, action: str) -> None:
    require_workspace(access, action)


def _chat_message_response(row: AssistantChatMessage) -> AssistantChatMessageResponse:
    response_context = _json_loads(row.response_json)
    return AssistantChatMessageResponse(
        id=row.id,
        role=row.role if row.role in {"user", "assistant"} else "assistant",
        content=row.content,
        intent=row.intent,
        mode=row.mode if row.mode in {"demo", "live"} else "live",
        saved=bool(row.saved),
        action_count=row.action_count or 0,
        actions=_chat_history_actions(response_context),
        suggested_prompts=_chat_history_prompts(response_context),
        created_at=row.created_at,
    )


def _chat_turn_response(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    user_message: str,
    **kwargs,
) -> AssistantChatResponse:
    response = AssistantChatResponse(**kwargs)
    return _persist_chat_response(db, account, user, user_message, response)


def _persist_chat_response(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    user_message: str,
    response: AssistantChatResponse,
) -> AssistantChatResponse:
    _record_chat_turn(db, account, user, user_message, response)
    return response


def _record_chat_turn(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    user_message: str,
    response: AssistantChatResponse,
) -> None:
    user_text = _chat_content(user_message)
    reply_text = _chat_content(response.reply)
    if not user_text and not reply_text:
        return
    account_value = _account_id(account)
    user_value = user.id if user else None
    mode = response.mode if response.mode in {"demo", "live"} else "live"
    if user_text:
        db.add(
            AssistantChatMessage(
                account_id=account_value,
                user_id=user_value,
                role="user",
                content=user_text,
                intent=response.intent,
                mode=mode,
                saved=False,
                action_count=0,
            )
        )
    if reply_text:
        db.add(
            AssistantChatMessage(
                account_id=account_value,
                user_id=user_value,
                role="assistant",
                content=reply_text,
                intent=response.intent,
                mode=mode,
                saved=response.saved,
                action_count=len(response.actions or []),
                response_json=_json_dumps(_chat_response_context(response)),
            )
        )
    db.commit()


def _chat_content(value: Optional[str]) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "")).strip()
    return cleaned[:8000]


def _chat_response_context(response: AssistantChatResponse) -> dict:
    actions = []
    for item in (response.actions or [])[:5]:
        if hasattr(item, "model_dump"):
            actions.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            actions.append(item)
    return {
        "actions": actions,
        "suggested_prompts": (response.suggested_prompts or [])[:5],
    }


def _chat_history_actions(response_context: dict) -> List[DeskItem]:
    actions = []
    for item in (response_context.get("actions") or [])[:5]:
        if not isinstance(item, dict):
            continue
        try:
            actions.append(DeskItem(**item))
        except Exception:
            continue
    return actions


def _chat_history_prompts(response_context: dict) -> List[str]:
    return [
        str(prompt)
        for prompt in (response_context.get("suggested_prompts") or [])[:5]
        if str(prompt).strip()
    ]


def _token_hash(token: str) -> str:
    return _shared_token_hash(token)


def _account_user_response(user: AccountUser) -> AccountUserResponse:
    return AccountUserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=normalize_role(user.role, "staff"),
        active=bool(user.active),
        last_seen_at=user.last_seen_at,
        created_at=user.created_at,
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "church"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    counter = 2
    while db.query(ChurchAccount).filter(ChurchAccount.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _sync_account_identity_from_profile(account: Optional[ChurchAccount], profile) -> None:
    if not account:
        return
    if _clean(profile.church_name):
        account.church_name = _clean(profile.church_name)
    if _clean(profile.pastor_name):
        account.pastor_name = _clean(profile.pastor_name)


def _get_or_create_profile(db: Session, account: Optional[ChurchAccount] = None):
    if account:
        profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
        if profile:
            return profile
        profile = AccountPastorProfile(
            account_id=account.id,
            pastor_name=account.pastor_name,
            church_name=account.church_name,
        )
        profile.onboarding_complete = _profile_is_complete(profile)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    profile = db.query(PastorProfile).order_by(PastorProfile.id.asc()).first()
    if profile:
        return profile
    profile = PastorProfile(
        pastor_name=os.getenv("PASTOR_NAME"),
        church_name=os.getenv("CHURCH_NAME"),
    )
    profile.onboarding_complete = _profile_is_complete(profile)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _profile_response(profile, account: Optional[ChurchAccount] = None) -> PastorProfileResponse:
    missing = _missing_profile_fields(profile)
    complete_count = len(_required_profile_fields()) - len(missing)
    percent = int((complete_count / len(_required_profile_fields())) * 100)
    return PastorProfileResponse(
        pastor_name=profile.pastor_name,
        church_name=profile.church_name,
        role_title=profile.role_title,
        congregation_size=profile.congregation_size,
        church_context=profile.church_context,
        faith_tradition=profile.faith_tradition,
        ministry_priorities=profile.ministry_priorities,
        followup_pain=profile.followup_pain,
        support_preferences=profile.support_preferences,
        weekly_rhythm=profile.weekly_rhythm,
        communication_style=profile.communication_style,
        tools_in_use=profile.tools_in_use,
        guardrails=profile.guardrails,
        onboarding_complete=not missing,
        completion_percent=percent,
        missing_fields=missing,
        questions=ONBOARDING_QUESTIONS,
        account_slug=account.slug if account else None,
    )


def _required_profile_fields() -> List[str]:
    return [
        "pastor_name",
        "church_name",
        "role_title",
        "congregation_size",
        "church_context",
        "faith_tradition",
        "followup_pain",
        "ministry_priorities",
        "support_preferences",
        "tools_in_use",
        "communication_style",
        "weekly_rhythm",
        "guardrails",
    ]


def _missing_profile_fields(profile: PastorProfile) -> List[str]:
    return [field for field in _required_profile_fields() if not _clean(getattr(profile, field, None))]


def _profile_is_complete(profile: PastorProfile) -> bool:
    return not _missing_profile_fields(profile)


def _maybe_save_onboarding_answer(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    message: str,
    lower: str,
) -> Optional[dict]:
    if _connector_verification_requested(lower):
        return None
    if _pastoral_reminder_requested(lower) or _pastoral_reminder_lookup_requested(lower):
        return None
    missing = _missing_profile_fields(profile)
    if not missing:
        return None
    updates = _extract_profile_updates(message, lower)
    current_field = missing[0]
    current_field_update = _current_field_profile_update(current_field, message, lower)
    if current_field_update:
        updates[current_field] = current_field_update
    if _looks_like_assistant_question_or_command(lower):
        if lower.strip().endswith("?"):
            return None
        if current_field not in updates:
            return None
        updates = {current_field: updates[current_field]}
    if _looks_like_pastoral_update_with_named_person(message, lower):
        return None
    changed = _apply_profile_updates(profile, updates)
    if not changed:
        field = missing[0]
        value = _clean(_strip_intro(message))
        if not value:
            return None
        setattr(profile, field, value)
        changed = [field]
    profile.onboarding_complete = _profile_is_complete(profile)
    _sync_account_identity_from_profile(account, profile)
    _audit(
        db,
        "pastor_profile.onboarding_answer_saved",
        "Saved first-run ministry context from chat.",
        account=account,
        payload={"fields": changed},
    )
    db.commit()
    db.refresh(profile)
    next_missing = _missing_profile_fields(profile)
    prepared_actions = []
    if next_missing:
        next_question = _interview_question(profile)
        reply = _ministry_memory_reply(profile, changed, next_question=next_question)
    else:
        prepared_actions = _prepare_profile_ready_actions(db, profile, account, user)
        reply = _ministry_memory_reply(profile, changed, prepared_action_count=len(prepared_actions))
    prepared_items = [_desk_item_from_action(action) for action in prepared_actions[:5]]
    return {
        "fields": changed,
        "reply": reply,
        "actions": prepared_items,
        "suggested_prompts": _suggested_prompts(profile, [], prepared_items),
    }


def _maybe_save_profile_context(
    db: Session,
    profile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    message: str,
    lower: str,
) -> Optional[dict]:
    if _connector_verification_requested(lower):
        return None
    if _pastoral_reminder_requested(lower) or _pastoral_reminder_lookup_requested(lower):
        return None
    if _looks_like_assistant_question_or_command(lower):
        return None
    person_name = _guess_person_name(message)
    if not _looks_like_profile_context(lower) and (
        _looks_like_visitor_update(lower)
        or _looks_like_prayer_update(lower)
        or _looks_like_contact_update(lower)
        or _looks_like_care_case(lower)
        or (person_name and _looks_like_member_note(lower))
    ):
        return None
    updates = _extract_profile_updates(message, lower)
    if not updates:
        return None
    changed = _apply_profile_updates(profile, updates)
    if not changed:
        return None
    profile.onboarding_complete = _profile_is_complete(profile)
    _sync_account_identity_from_profile(account, profile)
    _audit(
        db,
        "pastor_profile.chat_updated",
        "Saved ministry context from chat.",
        account=account,
        payload={"fields": changed},
    )
    db.commit()
    db.refresh(profile)
    missing = _missing_profile_fields(profile)
    prepared_actions = []
    if missing:
        next_question = _interview_question(profile)
        reply = _ministry_memory_reply(profile, changed, next_question=next_question)
    else:
        prepared_actions = _prepare_profile_ready_actions(db, profile, account, user)
        reply = _ministry_memory_reply(profile, changed, prepared_action_count=len(prepared_actions))
    prepared_items = [_desk_item_from_action(action) for action in prepared_actions[:5]]
    return {
        "reply": reply,
        "actions": prepared_items,
        "suggested_prompts": _suggested_prompts(profile, [], prepared_items),
    }


def _apply_profile_updates(profile, updates: dict) -> List[str]:
    changed = []
    for field, value in updates.items():
        cleaned = _clean(value)
        if cleaned and cleaned != _clean(getattr(profile, field, None)):
            setattr(profile, field, cleaned)
            changed.append(field)
    return changed


def _looks_like_assistant_question_or_command(lower: str) -> bool:
    stripped = lower.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    command_prefixes = (
        "show ",
        "draft ",
        "sync ",
        "prepare ",
        "review ",
        "open ",
        "connect ",
        "help me ",
        "queue ",
        "approve ",
    )
    if stripped.startswith(command_prefixes):
        return True
    if stripped.startswith(("what ", "who ", "how ")) and len(stripped.split()) <= 8:
        return True
    return False


def _looks_like_non_logging_assistant_command(lower: str) -> bool:
    stripped = lower.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    return stripped.startswith((
        "show ",
        "draft ",
        "sync ",
        "prepare ",
        "review ",
        "open ",
        "connect ",
        "queue ",
        "approve ",
    ))


def _looks_like_pastoral_update_with_named_person(message: str, lower: str) -> bool:
    if not _guess_person_name(message):
        return False
    return (
        _looks_like_visitor_update(lower)
        or _looks_like_prayer_update(lower)
        or _looks_like_contact_update(lower)
        or _looks_like_care_case(lower)
        or _looks_like_member_note(lower)
    )


def _extract_profile_updates(message: str, lower: str) -> dict:
    updates = {}
    tools = _extract_tools(lower)
    if tools:
        updates["tools_in_use"] = tools
    role = _extract_role(lower)
    if role:
        updates["role_title"] = role
    size = _extract_congregation_size(lower)
    if size:
        updates["congregation_size"] = size
    church = _extract_after_patterns(message, [
        r"(?:i serve at|i pastor at|our church is|my church is|church is)\s+([^.;\n]+)",
        r"(?:i am|i'm)\s+(?:the\s+)?(?:lead|senior|solo|associate|executive|youth)?\s*pastor\s+at\s+([^.;,\n]+)",
        r"(?:i am|i'm)\s+pastor\s+[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2}\s+at\s+([^.;,\n]+)",
    ])
    church = _church_name_candidate(church)
    if church:
        updates["church_name"] = church
    pastor_name = _extract_pastor_name(message)
    if pastor_name and len(pastor_name.split()) <= 4:
        updates["pastor_name"] = pastor_name
    followup = _extract_after_patterns(message, [
        r"(?:follow[- ]?up(?: breaks down| pain)? is|we struggle with|i struggle with|hardest part is)\s+([^.;,\n]+)",
        r"(?:biggest pain is|biggest burden is|follow[- ]?up burden is|we keep missing|i keep missing)\s+([^.;\n]+)",
        r"(?:follow[- ]?up breaks down with)\s+([^.;,\n]+)",
        r"(?:follow[- ]?up breaks with)\s+([^.;,\n]+)",
        r"(?:things slip through the cracks with)\s+([^.;,\n]+)",
        r"([^.;\n]{0,140}follow[- ]?up[^.;\n]{0,100})\s*(?:;|,)?\s*(?:they|it|those)?\s*(?:falls?|fall|slips?|slip)\s+through the cracks",
        r"([^.:\n]{0,120}follow[- ]?up[^.;\n]{0,80})\s+(?:falls?|fall|slips?|slip)\s+through the cracks",
    ])
    if followup:
        updates["followup_pain"] = followup
    priorities = _extract_after_patterns(message, [
        r"(?:my first priority(?:\s+this\s+\w+)? is|our first priority(?:\s+this\s+\w+)? is|my priority is|our priority is|ministry priority is|my goal is|our goal is|what would make this month a win is|a win would be)\s+([^.;\n]+)",
        r"(?:i want marge to help me|we want marge to help us|i need marge to help me|we need marge to help us|marge should help me|marge should help us|help me|help us)\s+([^.;\n]+)",
        r"(?:i need help|we need help)\s+(?:with\s+)?([^.;\n]+)",
    ])
    if priorities:
        updates["ministry_priorities"] = priorities
    support_preferences = _extract_support_preferences(message, lower)
    if support_preferences:
        updates["support_preferences"] = support_preferences
    guardrails = _extract_guardrails(message)
    if guardrails:
        updates["guardrails"] = guardrails
    voice = _extract_voice(lower)
    if voice:
        updates["communication_style"] = voice
    rhythm = _extract_rhythm(message, lower)
    if rhythm:
        updates["weekly_rhythm"] = rhythm
    context = _extract_named_church_context(message) or _extract_after_patterns(message, [
        r"(?:our context is|church context is|our community is|we are a|we're a|we are an|we're an|we are in|we're in|our church serves|we serve|i serve a|i pastor a|pastor at a)\s+([^.;\n]+)",
    ])
    if context:
        updates["church_context"] = context if context.lower().startswith("we are") else f"We are a {context}" if lower.startswith("we are a") else context
    faith_tradition = _extract_faith_tradition(message, lower)
    if faith_tradition:
        updates["faith_tradition"] = faith_tradition
    return updates


def _extract_faith_tradition(message: str, lower: str) -> Optional[str]:
    known = [
        "non-denominational",
        "non denominational",
        "baptist",
        "methodist",
        "presbyterian",
        "wesleyan",
        "pentecostal",
        "lutheran",
        "anglican",
        "reformed",
        "charismatic",
    ]
    if any(term in lower for term in known):
        return _clean(message.strip(" .,:;"))
    explicit = _extract_after_patterns(message, [
        r"(?:church tradition is|faith tradition is|denomination is|we are|we're|our church is)\s+([^.;\n]*(?:baptist|methodist|presbyterian|wesleyan|pentecostal|lutheran|anglican|reformed|charismatic|non[- ]denominational|denominational|tradition|roots|language)[^.;\n]*)",
        r"(?:respect|use|avoid)\s+([^.;\n]*(?:insider language|church language|ministry language|denominational language|theological language)[^.;\n]*)",
    ])
    if explicit:
        avoid = re.search(r"\bavoid\s+([^.\n]+)", message, flags=re.IGNORECASE)
        if avoid and "avoid" not in explicit.lower():
            boundary = _clean(avoid.group(0).strip(" .,:;"))
            if boundary:
                return f"{explicit}; {boundary}"
        return explicit
    return None


def _church_name_candidate(value: Optional[str]) -> Optional[str]:
    candidate = _clean(value)
    if not candidate:
        return None
    candidate = re.split(r",\s+(?:a|an|the)\s+", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
    candidate = _clean(candidate.strip(" .,:;"))
    lowered = candidate.lower()
    if lowered.startswith(("a ", "an ")):
        return None
    if len(candidate.split()) > 8:
        return None
    church_words = ["church", "chapel", "fellowship", "baptist", "methodist", "presbyterian", "assembly"]
    if not any(word in lowered for word in church_words):
        return None
    context_markers = [
        " people",
        " families",
        " volunteers",
        " first-time",
        " guests",
        " neighborhood church of",
        " small church with",
        " rural church with",
        " with many",
        " with a lot",
        " lots of",
    ]
    if any(marker in lowered for marker in context_markers):
        return None
    return candidate


def _extract_named_church_context(message: str) -> Optional[str]:
    match = re.search(
        r"\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z0-9][A-Za-z0-9&.'-]*){0,7}\s+"
        r"(?:Church|Chapel|Fellowship|Baptist|Methodist|Presbyterian|Assembly)"
        r"(?:\s+[A-Z0-9][A-Za-z0-9&.'-]*){0,3})\s+is\s+([^.;\n]+)",
        message,
    )
    if not match:
        return None
    church_name = _clean(match.group(1).strip(" .,:;"))
    context = _clean(match.group(2).strip(" .,:;"))
    if not church_name or not context:
        return None
    return f"{church_name} is {context}"


def _extract_after_patterns(message: str, patterns: List[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return _clean(match.group(1).strip(" .,:;"))
    return None


def _current_field_profile_update(field: str, message: str, lower: str) -> Optional[str]:
    if field in {"pastor_name", "church_name"}:
        return _clean_identity_answer(message)
    if field == "role_title":
        return _extract_role_answer(lower) or _extract_role(lower)
    if field == "congregation_size":
        match = re.search(r"\b(\d{1,5})\b", lower)
        return match.group(1) if match else None
    if field == "tools_in_use":
        return _extract_known_tools(lower)
    if field == "communication_style":
        return _extract_voice_words(lower)
    if field == "weekly_rhythm":
        return _extract_rhythm(message, lower)
    if field == "guardrails":
        return _extract_guardrails(message)
    if field == "ministry_priorities":
        priorities = _extract_profile_updates(message, lower).get("ministry_priorities")
        if priorities:
            return priorities
    if field == "support_preferences":
        return _extract_support_preferences(message, lower) or _clean(_strip_intro(message))
    return None


def _clean_identity_answer(message: str) -> Optional[str]:
    return _clean(_strip_intro(message).strip(" .,:;"))


def _extract_guardrails(message: str) -> Optional[str]:
    match = re.search(r"\b(never|do not|don't)\s+([^.;\n]+)", message, flags=re.IGNORECASE)
    if match:
        prefix = "Never" if match.group(1).lower() == "never" else "Do not"
        action = _clean(match.group(2).strip(" .,:;"))
        return f"{prefix} {action}" if action else None
    ask_first = re.search(r"\b(?:ask|check with)\s+me\s+before\s+([^.;\n]+)", message, flags=re.IGNORECASE)
    if ask_first:
        action = _clean(ask_first.group(1).strip(" .,:;"))
        return f"Ask me before {action}" if action else None
    without_approval = _extract_after_patterns(message, [r"([^.;\n]+without (?:my )?approval)"])
    if not without_approval:
        return None
    without_approval = _clean(re.sub(r"^(?:please\s+)?(?:make sure\s+)?", "", without_approval, flags=re.IGNORECASE))
    lowered = without_approval.lower()
    if lowered.startswith(("do not ", "don't ", "never ", "no ")):
        return without_approval
    if " not " in lowered:
        return without_approval[:1].upper() + without_approval[1:]
    return f"Do not {without_approval}"


def _extract_support_preferences(message: str, lower: str) -> Optional[str]:
    explicit = _extract_after_patterns(message, [
        r"(?:support me by|help me by|i work best when|when ministry gets heavy,?\s*i need|when things get heavy,?\s*i need|what helps me most is)\s+([^.;\n]+)",
        r"(?:marge should support me by|i want marge to support me by|i need marge to support me by)\s+([^.;\n]+)",
    ])
    if explicit:
        return explicit
    if _mentions(lower, [
        "nudge me",
        "remind me",
        "protect rest",
        "protect my rest",
        "surface only",
        "don't overwhelm me",
        "do not overwhelm me",
    ]):
        return _clean(message.strip(" .,:;"))
    return None


def _extract_pastor_name(message: str) -> Optional[str]:
    explicit = _extract_after_patterns(message, [r"(?:my name is|call me)\s+([^.;\n]+)"])
    if explicit:
        return explicit
    match = re.search(
        r"\b(?:I am|I'm|i am|i'm)\s+((?:Pastor\s+)?[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2})(?=,|\.|;|\s+(?:and|at|the|serving|with|from)\b)",
        message,
    )
    if not match:
        return None
    candidate = _clean(match.group(1).strip(" .,:;"))
    if not candidate:
        return None
    lowered = candidate.lower()
    rejected = {"pastor", "lead pastor", "senior pastor", "solo pastor", "associate pastor", "executive pastor", "youth pastor"}
    return None if lowered in rejected else candidate


def _extract_tools(lower: str) -> Optional[str]:
    if not _mentions(lower, ["use", "using", "tools", "software", "system", "systems", "stack", "chms", "platform", "connect", "connected"]):
        return None
    return _extract_known_tools(lower)


def _extract_known_tools(lower: str) -> Optional[str]:
    found = []

    def add(label: str) -> None:
        if label not in found:
            found.append(label)

    if "planning center" in lower:
        add("Planning Center")
    if "church center" in lower:
        add("Church Center")
    if any(term in lower for term in ["gmail", "google workspace", "google calendar"]):
        if "gmail" in lower:
            add("Gmail/Google Workspace")
        elif "google calendar" in lower:
            add("Google Calendar/Google Workspace")
        else:
            add("Google Workspace")
    if any(term in lower for term in ["outlook", "microsoft 365", "office 365"]):
        if "outlook" in lower:
            add("Outlook/Microsoft 365")
        else:
            add("Microsoft 365")
    if "rock rms" in lower or re.search(r"\brock\b", lower):
        add("Rock RMS")
    if "breeze" in lower:
        add("Breeze")
    if "slack" in lower:
        add("Slack")
    return ", ".join(found) if found else None


def _extract_role(lower: str) -> Optional[str]:
    normalized = lower.replace("bi-vocational", "bivocational").replace("co-vocational", "bivocational")
    role_patterns = [
        ("Bivocational Solo Pastor", ["bivocational solo pastor"]),
        ("Bivocational Lead Pastor", ["bivocational lead pastor", "bivocational senior pastor"]),
        ("Bivocational Pastor", ["bivocational pastor"]),
        ("Solo Pastor", ["solo pastor"]),
        ("Lead Pastor", ["lead pastor"]),
        ("Senior Pastor", ["senior pastor"]),
        ("Associate Pastor", ["associate pastor"]),
        ("Executive Pastor", ["executive pastor"]),
        ("Youth Pastor", ["youth pastor"]),
        ("Church Planter", ["church planter"]),
    ]
    for label, needles in role_patterns:
        if any(needle in normalized for needle in needles):
            return label
    return None


def _extract_role_answer(lower: str) -> Optional[str]:
    normalized = _clean(lower.replace("bi-vocational", "bivocational").replace("co-vocational", "bivocational").strip(" .,:;"))
    role_answers = {
        "solo": "Solo Pastor",
        "solo pastor": "Solo Pastor",
        "lead": "Lead Pastor",
        "lead pastor": "Lead Pastor",
        "senior": "Senior Pastor",
        "senior pastor": "Senior Pastor",
        "associate": "Associate Pastor",
        "associate pastor": "Associate Pastor",
        "executive": "Executive Pastor",
        "executive pastor": "Executive Pastor",
        "youth": "Youth Pastor",
        "youth pastor": "Youth Pastor",
        "bivocational": "Bivocational Pastor",
        "bivocational pastor": "Bivocational Pastor",
        "bivocational solo": "Bivocational Solo Pastor",
        "bivocational solo pastor": "Bivocational Solo Pastor",
        "church planter": "Church Planter",
    }
    return role_answers.get(normalized)


def _extract_congregation_size(lower: str) -> Optional[str]:
    match = re.search(r"(?:weekly attendance|attendance|we average|we run|we have|we see|we worship(?: with)?|church of|congregation of|about|around)\D{0,18}(\d{2,5})", lower)
    if not match:
        match = re.search(r"(\d{2,5})\s+(?:people|attend|attendance|members)", lower)
    if not match:
        match = re.search(r"(\d{2,5})\s+(?:on\s+)?sundays?\b", lower)
    return match.group(1) if match else None


def _extract_voice(lower: str) -> Optional[str]:
    if not _mentions(lower, ["sound", "voice", "draft", "write", "tone"]):
        return None
    return _extract_voice_words(lower)


def _extract_voice_words(lower: str) -> Optional[str]:
    words = [word for word in ["warm", "brief", "concise", "direct", "formal", "casual", "conversational", "pastoral", "gentle"] if word in lower]
    return " and ".join(words) if words else None


def _extract_rhythm(message: str, lower: str) -> Optional[str]:
    rhythm_terms = ["sermon prep", "staff meeting", "weekly rhythm", "day off", "sabbath", "office hours"]
    day_terms = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    schedule_terms = ["morning", "afternoon", "evening", "meeting", "meetings", "prep", "rest", "protect", "block", "visits", "off"]
    matching_segments = []
    for segment in _sentence_segments(message):
        segment_lower = segment.lower()
        has_explicit_rhythm = _mentions(segment_lower, rhythm_terms)
        has_day_schedule = _mentions(segment_lower, day_terms) and _mentions(segment_lower, schedule_terms)
        if has_explicit_rhythm or has_day_schedule:
            cleaned = _clean(segment)
            if cleaned:
                cleaned = _clean(re.split(
                    r"\b(?:and\s+)?(?:ask|check with)\s+me\s+before\b",
                    cleaned,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].rstrip(" ,;"))
            if cleaned:
                matching_segments.append(cleaned)
    if matching_segments:
        return " ".join(matching_segments)
    return None


def _sentence_segments(message: str) -> List[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?;])\s+", message) if part.strip()]


def _profile_field_label(field: str) -> str:
    labels = {
        "pastor_name": "your name",
        "church_name": "your church",
        "role_title": "your role",
        "congregation_size": "weekly size",
        "church_context": "church context",
        "faith_tradition": "church voice",
        "ministry_priorities": "ministry priorities",
        "followup_pain": "follow-up burden",
        "support_preferences": "support preferences",
        "tools_in_use": "tools in use",
        "communication_style": "drafting voice",
        "weekly_rhythm": "weekly rhythm",
        "guardrails": "guardrails",
    }
    return labels.get(field, field.replace("_", " "))


def _ministry_memory_reply(
    profile,
    changed: List[str],
    *,
    next_question: Optional[dict] = None,
    prepared_action_count: Optional[int] = None,
) -> str:
    labels = _human_join([_profile_field_label(field) for field in changed]) or "that context"
    summary = _ministry_memory_summary(profile)
    implications = _ministry_memory_implications(profile)
    if next_question:
        next_step = f"Next I need this: {next_question['question']}"
    else:
        count = prepared_action_count or 0
        next_step = (
            f"I prepared {count} first-run setup item(s) so we can connect the right tools, add real people context, "
            "and keep every external action behind your approval."
        )
    return f"I saved {labels}. {summary} {implications} {next_step}"


def _ministry_operating_plan_reply(profile, plan: List[dict]) -> str:
    if not plan:
        return (
            "I do not know enough yet. Start by telling me your church context, tools, follow-up burden, "
            "support preferences, drafting voice, weekly rhythm, and guardrails."
        )
    lead = _ministry_memory_summary(profile)
    steps = []
    for item in plan[:8]:
        title = item.get("title") or "Next step"
        detail = item.get("detail") or item.get("action") or "Keep this in view."
        action = item.get("action")
        if action and action != detail:
            steps.append(f"{title}: {detail} Action: {action}")
        else:
            steps.append(f"{title}: {detail}")
    return f"{lead} Here is how I would start: {'; '.join(steps)}"


def _ministry_memory_summary(profile) -> str:
    identity = _ministry_identity_phrase(profile)
    sentences = []
    if identity:
        sentences.append(f"I am hearing that {identity}.")
    context = _short_context(profile.church_context)
    if context:
        sentences.append(f"The local ministry context I should remember is: {context}.")
    tradition = _short_context(profile.faith_tradition)
    if tradition:
        sentences.append(f"The church voice and tradition I should respect is: {tradition}.")
    followup = _short_context(profile.followup_pain)
    if followup:
        sentences.append(f"The follow-up pressure I will watch first is: {followup}.")
    priorities = _short_context(profile.ministry_priorities)
    if priorities:
        sentences.append(f"The first ministry priority I should help move is: {priorities}.")
    support = _short_context(profile.support_preferences)
    if support:
        sentences.append(f"The way I should support this pastor personally is: {support}.")
    tools = _short_context(profile.tools_in_use)
    if tools:
        sentences.append(f"The systems already in the room are: {tools}.")
    voice = _short_context(profile.communication_style)
    if voice:
        sentences.append(f"The drafting voice I should use is: {voice}.")
    rhythm = _short_context(profile.weekly_rhythm)
    if rhythm:
        sentences.append(f"The weekly rhythm I should protect is: {rhythm}.")
    guardrails = _short_context(profile.guardrails)
    if guardrails:
        sentences.append(f"The approval boundaries I should keep are: {guardrails}.")
    return " ".join(sentences) or "That gives me one more piece of the ministry picture."


def _ministry_memory_implications(profile) -> str:
    uses = []
    if _clean(profile.followup_pain):
        uses.append("keep that follow-up burden visible")
    if _clean(profile.ministry_priorities):
        uses.append("aim setup and drafts at the priority you named")
    if _clean(profile.support_preferences):
        uses.append("support you in the way you named")
    if _clean(profile.faith_tradition):
        uses.append("respect your church's language and tradition")
    if _clean(profile.tools_in_use):
        uses.append("route connector work through secure setup")
    if _clean(profile.communication_style):
        uses.append(f"draft in a {_short_context(profile.communication_style, 80)} voice")
    if _clean(profile.weekly_rhythm):
        uses.append("protect your weekly rhythm")
    if _clean(profile.guardrails):
        uses.append("keep your guardrails in front of every approval")
    if not uses:
        return "I will use this as ministry memory instead of treating your church like a generic account."
    return f"I will use this to {_human_join(uses[:4])}."


def _ministry_identity_phrase(profile) -> Optional[str]:
    role = _short_context(profile.role_title, 70)
    church = _short_context(profile.church_name, 90)
    size = _short_context(profile.congregation_size, 40)
    if role and church:
        base = f"you are serving as {role} at {church}"
    elif church:
        base = f"you are serving {church}"
    elif role:
        base = f"you are serving as {role}"
    else:
        base = None
    if size:
        size_phrase = f"with about {size} people connected weekly"
        return f"{base} {size_phrase}" if base else size_phrase
    return base


def _short_context(value: Optional[str], limit: int = 180) -> Optional[str]:
    cleaned = _clean(value)
    if not cleaned:
        return None
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip(" ,.;:") + "..."


def _human_join(items: List[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _maybe_save_pastoral_update(
    db: Session,
    profile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[dict]:
    if _looks_like_profile_context(lower):
        return None
    if _looks_like_non_logging_assistant_command(lower):
        return None
    person_name = _guess_person_name(message)
    if _looks_like_visitor_update(lower):
        if not person_name:
            return _missing_visitor_name_response(db, account, profile, effective_mode, user)
        return _save_visitor_from_chat(db, account, profile, message, person_name)
    if _looks_like_prayer_update(lower):
        return _save_prayer_from_chat(db, account, profile, message, person_name)
    if _looks_like_contact_update(lower):
        return _save_contact_from_chat(db, account, profile, message, person_name)
    if _looks_like_care_case(lower):
        return _save_care_case_from_chat(db, account, profile, message, lower, person_name)
    if person_name and _looks_like_member_note(lower):
        return _save_member_note_from_chat(db, account, profile, message, lower, person_name)
    return None


def _pastoral_reminder_requested(lower: str) -> bool:
    return (
        bool(re.search(r"\b(?:remind me to|set a reminder to|queue a reminder to|help me remember to|nudge me to)\b", lower))
        or lower.strip().startswith("reminder:")
    )


def _pastoral_reminder_lookup_requested(lower: str) -> bool:
    if _pastoral_reminder_requested(lower):
        return False
    return _mentions(lower, [
        "what reminders do i have",
        "what reminders are pending",
        "show my reminders",
        "show reminders",
        "list reminders",
        "pending reminders",
        "local reminders",
        "pastoral reminders",
    ])


def _pastoral_reminder_lookup_response(
    db: Session,
    profile,
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    reminders = (
        scoped_query(db.query(AssistantAction), AssistantAction, account)
        .filter(AssistantAction.action_type == "pastoral_reminder", AssistantAction.status.in_(["pending", "approved"]))
        .order_by(AssistantAction.updated_at.desc().nullslast(), AssistantAction.created_at.desc())
        .limit(8)
        .all()
    )
    if reminders:
        lines = "; ".join(_pastoral_reminder_line(action) for action in reminders[:4])
        reply = (
            f"You have {len(reminders)} local pastoral reminder{'s' if len(reminders) != 1 else ''}: {lines}. "
            "These are local Marge memory only; nothing has been sent, synced, or written externally."
        )
        actions = [_desk_item_from_action(action) for action in reminders[:5]]
        prompts = ["Who should I check on next?", "Show my approvals.", "What can wait until next week?"]
    else:
        reply = (
            "I do not see any pending local pastoral reminders right now. "
            "Tell me who to check on and when, and I will keep it local until you mark it done."
        )
        actions = []
        prompts = ["Who should I check on next?", "Remind me to check on the next care case next week.", "What should I handle next?"]
    return AssistantChatResponse(
        reply=reply,
        intent="pastoral_reminder_lookup",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _pastoral_reminder_line(action: AssistantAction) -> str:
    payload = _json_loads(action.payload_json)
    reminder = payload.get("reminder") or {}
    task = _clean(reminder.get("task")) or action.title or "Pastoral reminder"
    due = _clean(reminder.get("due"))
    return f"{task}{f' ({due})' if due else ''}"


def _pastoral_reminder_description(
    db: Session,
    account: Optional[ChurchAccount],
    action: AssistantAction,
    reminder: dict,
) -> str:
    task = _clean(reminder.get("task")) or action.title or "Pastoral reminder"
    due_label = _clean(reminder.get("due"))
    person_name = _clean(reminder.get("person_name"))
    member_id = reminder.get("member_id") or (action.related_id if action.related_type == "member" else None)
    member = None
    if member_id:
        member = scoped_query(db.query(Member), Member, account).filter(Member.id == member_id).first()
    description_parts = [task]
    if due_label:
        description_parts.append(f"Timing: {due_label}.")
    if member:
        description_parts.append(f"Linked to {member.full_name} in Marge's people memory.")
    elif person_name:
        description_parts.append(f"Person named: {person_name}. Add them to Marge before drafting or logging sensitive follow-up.")
    description_parts.append("Local reminder only; nothing was sent, synced, or written to an external system.")
    return " ".join(description_parts)


def _pastoral_reminder_chat_response(
    db: Session,
    profile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    person_name = _guess_person_name(message)
    member = _find_member_by_name(db, account, person_name) if person_name else None
    task = _reminder_task_from_message(message) or _short_context(message, 180) or "Follow up on this pastoral reminder"
    due_label = _reminder_due_label(lower)
    title = f"Reminder: {task[:80].rstrip('.')}"
    description_parts = [task]
    if due_label:
        description_parts.append(f"Timing: {due_label}.")
    if member:
        description_parts.append(f"Linked to {member.full_name} in Marge's people memory.")
    elif person_name:
        description_parts.append(f"Person named: {person_name}. Add them to Marge before drafting or logging sensitive follow-up.")
    description_parts.append("Local reminder only; nothing was sent, synced, or written to an external system.")
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"pastoral_reminder:{_slugify(task[:120])}",
        action_type="pastoral_reminder",
        title=title,
        description=" ".join(description_parts),
        payload={
            "reminder": {
                "task": task,
                "person_name": person_name,
                "member_id": member.id if member else None,
                "due": due_label,
                "source_message": message,
            },
            "guardrail": "This is local Marge memory. External messages, calendar writes, and connector syncs still require explicit approval.",
        },
        source="assistant_chat",
        external_provider=None,
        related_type="member" if member else None,
        related_id=member.id if member else None,
        privacy_level="pastoral",
        account=account,
    )
    db.flush()
    _audit(
        db,
        "assistant_action.pastoral_reminder_queued",
        f"Queued pastoral reminder: {task[:120]}",
        account=account,
        action_id=action.id,
        payload={"has_person": bool(person_name), "linked_member": bool(member), "due": due_label},
    )
    db.commit()
    db.refresh(action)
    if member:
        lead = f"I queued that local reminder and linked it to {member.full_name}: {task}."
    elif person_name:
        lead = f"I queued that local reminder for {person_name}: {task}."
    else:
        lead = f"I queued that local pastoral reminder: {task}."
    timing = f" I marked the timing as {due_label}." if due_label else ""
    reply = f"{lead}{timing} Nothing was sent, synced, or written to an external system."
    prompts = ["Show my approvals.", "What should I handle next?"]
    if person_name:
        prompts.insert(0, f"What do you know about {person_name}?")
    return AssistantChatResponse(
        reply=reply,
        intent="pastoral_reminder_queued",
        mode=effective_mode,
        saved=True,
        actions=[_desk_item_from_action(action)],
        suggested_prompts=prompts[:3],
        profile=_profile_response(profile, account),
    )


def _reminder_task_from_message(message: str) -> str:
    task = re.sub(
        r"(?is)^\s*(?:please\s+)?(?:can you\s+)?(?:remind me to|set a reminder to|queue a reminder to|help me remember to|nudge me to|reminder:)\s+",
        "",
        message,
    ).strip(" .")
    return task


def _reminder_due_label(lower: str) -> Optional[str]:
    if "tomorrow" in lower:
        return "tomorrow"
    if "today" in lower:
        return "today"
    if _mentions(lower, ["in two weeks", "two weeks", "2 weeks"]):
        return "in two weeks"
    if "next week" in lower:
        return "next week"
    if "later this week" in lower:
        return "later this week"
    for weekday in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        if weekday in lower:
            return weekday.title()
    return None


def _missing_visitor_name_response(
    db: Session,
    account: Optional[ChurchAccount],
    profile,
    effective_mode: Literal["demo", "live"],
    user: Optional[AccountUser] = None,
) -> dict:
    seed_step = _seed_context_step(db, account, profile, effective_mode, user)
    actions = [seed_step] if seed_step and seed_step.form == "visitor" else []
    prompts = _data_seed_suggested_prompts(seed_step) if seed_step and seed_step.form == "visitor" else [
        "Log the visitor: name, email, and what they asked about.",
        "Show visitors needing follow-up.",
    ]
    return {
        "reply": (
            "I can log that visitor, but I need at least a name so I do not create a made-up person. "
            "Tell me the visitor's real name, when they came, and what follow-up would help."
        ),
        "intent": "visitor_missing_name",
        "saved": False,
        "actions": actions,
        "suggested_prompts": prompts,
    }


def _maybe_save_member_from_chat(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
) -> Optional[dict]:
    if not _looks_like_member_create(lower):
        return None
    person_name = _member_create_person_name(message)
    if not person_name:
        return None
    return _save_member_from_chat(db, account, profile, message, lower, person_name)


def _looks_like_member_create(lower: str) -> bool:
    if _looks_like_visitor_update(lower):
        return False
    has_create_verb = _mentions(lower, ["add ", "create ", "save ", "log "])
    has_people_target = _mentions(lower, [
        " person",
        " people",
        " member",
        " congregant",
        " attender",
        " regular",
        " first real person",
    ])
    return has_create_verb and has_people_target


def _member_create_person_name(message: str) -> Optional[str]:
    patterns = [
        r"(?:first real person|person|member|congregant|attender)[:\s-]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
        r"(?:add|create|save|log)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+(?:as|to|with|,|\.|$)",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+is\s+(?:a\s+)?(?:member|regular|attender|congregant|person)",
    ]
    stop = {"First", "Person", "People", "Member", "Members", "Help", "Marge"}
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        candidate = match.group(1).strip(" ,.;:")
        if candidate.split()[0] not in stop:
            return candidate
    return None


def _save_member_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, lower: str, person_name: str) -> dict:
    first, last = _split_person_name(person_name)
    email = _email_from_text(message)
    phone = _phone_from_text(message)
    member = _find_member_by_name(db, account, person_name)
    created = False
    if member:
        if email and not member.email:
            member.email = email
        if phone and not member.phone:
            member.phone = phone
    else:
        member = Member(
            account_id=_account_id(account),
            first_name=first,
            last_name=last,
            email=email,
            phone=phone,
        )
        db.add(member)
        db.flush()
        created = True

    note = None
    if _member_create_should_save_note(lower):
        note = MemberNote(
            account_id=_account_id(account),
            member_id=member.id,
            note_text=message,
            context_tag=_context_tag_from_text(lower),
        )
        db.add(note)

    care = None
    if _looks_like_care_case(lower):
        care = CareNote(
            account_id=_account_id(account),
            member_id=member.id,
            category=_care_category_from_text(lower),
            description=message,
            last_contact=None,
            status="active",
        )
        db.add(care)

    prayer = None
    if _looks_like_prayer_update(lower):
        prayer = PrayerRequest(
            account_id=_account_id(account),
            member_id=member.id,
            submitted_by=None,
            request_text=message,
            is_private=True,
            status="active",
        )
        db.add(prayer)

    _audit(
        db,
        "member.created_from_chat" if created else "member.updated_from_chat",
        f"{'Created' if created else 'Updated'} local person from assistant chat: {member.full_name}",
        account=account,
        payload={"created": created, "has_email": bool(email), "has_phone": bool(phone), "care": bool(care), "prayer": bool(prayer), "note": bool(note)},
    )
    retire_data_seed_actions(db, account, reason="member_saved_from_chat", related_type="member", related_id=member.id)
    db.commit()
    db.refresh(member)
    if note:
        db.refresh(note)
    if care:
        db.refresh(care)
    if prayer:
        db.refresh(prayer)

    care_cases = _member_active_care_cases(db, account, member)
    prayers = _member_active_prayers(db, account, member)
    notes = _member_recent_notes(db, account, member)
    additions = []
    if email or phone:
        additions.append("contact info")
    if care:
        additions.append(f"{_label(_enum_value(care.category))} care")
    if prayer:
        additions.append("private prayer")
    if note:
        additions.append("a pastoral note")
    detail = f" I also saved {_human_join(additions)}." if additions else ""
    verb = "added" if created else "updated"
    return {
        "intent": "member_logged",
        "saved": True,
        "reply": (
            f"I {verb} {member.full_name} in Marge's people memory.{detail} "
            "I will keep this tied to the right person and will not contact them without your approval."
        ),
        "actions": _member_context_actions(member, care_cases, prayers, notes),
        "suggested_prompts": [f"What do you know about {member.full_name}?", f"Draft a care follow-up for {member.full_name}.", "Who else needs attention?"],
    }


def _member_create_should_save_note(lower: str) -> bool:
    return _looks_like_member_note(lower) or _looks_like_care_case(lower) or _looks_like_prayer_update(lower)


def _email_from_text(message: str) -> Optional[str]:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
    return match.group(0) if match else None


def _phone_from_text(message: str) -> Optional[str]:
    match = re.search(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}", message)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _maybe_answer_ministry_context(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[AssistantChatResponse]:
    if _person_context_requested(lower):
        return _person_context_chat_response(db, profile, account, message, lower, effective_mode)
    if _prayer_context_requested(lower):
        return _prayer_context_chat_response(db, profile, account, lower, effective_mode)
    if _care_context_requested(lower):
        return _care_context_chat_response(db, profile, account, lower, effective_mode)
    if _visitor_context_requested(lower):
        return _visitor_context_chat_response(db, profile, account, lower, effective_mode)
    return None


def _maybe_prepare_local_followup_draft(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[dict]:
    if not _mentions(lower, ["draft", "write", "prepare"]):
        return None
    if _mentions(lower, ["prayer", "pray"]):
        prayer = _target_prayer_for_chat(db, account, message, lower)
        if not prayer:
            return {
                "intent": "draft_prayer_followup_empty",
                "reply": "I do not see an active prayer request to draft from yet. Add the prayer request first, and I will keep the follow-up private and reviewable.",
                "actions": [],
                "suggested_prompts": ["What should I capture for private prayer?", "Who needs prayer follow-up?"],
            }
        item = _prayer_desk_item(prayer)
        action = _prepare_single_followup_draft_action(db, profile, account, item, effective_mode)
        name = _prayer_subject_name(prayer)
        subject = name or _prayer_subject_label(prayer)
        subject_phrase = f"for {name}" if name else f"from {subject}"
        return {
            "intent": "draft_prayer_followup_queued",
            "reply": (
                f"I drafted a private prayer follow-up {subject_phrase} and put it in your approval queue. "
                "I will not send it or expose private prayer context without your review."
            ),
            "actions": [_desk_item_from_action(action)],
            "suggested_prompts": (
                ["Show my approvals.", f"What do you know about {name}?"]
                if name
                else ["Show my approvals.", "Who needs prayer follow-up?", "What should I capture for private prayer?"]
            ),
        }
    if _mentions(lower, ["care", "grief", "hospital", "visit", "follow-up", "follow up"]):
        care = _target_care_for_chat(db, account, message, lower)
        if not care:
            return {
                "intent": "draft_care_followup_empty",
                "reply": "I do not see an active care case to draft from yet. Add the care context first, and I will prepare a reviewable follow-up.",
                "actions": [],
                "suggested_prompts": ["What should I capture for a care case?", "Who needs care follow-up?"],
            }
        item = _care_desk_item(care)
        action = _prepare_single_followup_draft_action(db, profile, account, item, effective_mode)
        name = care.member.full_name if care.member else item.title
        return {
            "intent": "draft_care_followup_queued",
            "reply": (
                f"I drafted a care follow-up for {name} and put it in your approval queue. "
                "You can review or edit the exact words before anything is sent."
            ),
            "actions": [_desk_item_from_action(action)],
            "suggested_prompts": ["Show my approvals.", f"What do you know about {name}?", f"Where can I fit a visit with {name}?"],
        }
    return None


def _scheduling_reply_requested(lower: str) -> bool:
    if _mentions(lower, ["scheduling reply", "schedule reply", "reply about scheduling", "scheduling note"]):
        return _mentions(lower, ["draft", "write", "prepare", "queue"])
    return _mentions(lower, ["draft", "write", "prepare", "queue"]) and _mentions(lower, ["availability", "find a time", "schedule a time", "meeting time"])


def _prepare_scheduling_reply_action(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    calendar_blocks: List[DeskItem],
    priorities: List[DeskItem],
    mode: Literal["demo", "live"],
) -> AssistantAction:
    today_key = datetime.utcnow().date().isoformat()
    block = calendar_blocks[0] if calendar_blocks else None
    priority = priorities[0] if priorities else None
    subject = "Re: Scheduling"
    body = _scheduling_reply_body(profile, block, priority)
    description_parts = []
    if block:
        description_parts.append(block.title)
    if priority:
        description_parts.append(f"Priority: {priority.title}")
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{today_key}:{mode}:scheduling_reply:{block.id if block else 'general'}:{priority.id if priority else 'none'}",
        action_type="email_draft",
        title="Review scheduling reply",
        description="; ".join(description_parts) or "Scheduling reply drafted from saved ministry rhythm.",
        payload={
            "email": {
                "subject": subject,
                "body": body,
            },
            "draft_kind": "scheduling",
            "draft_context": {
                "weekly_rhythm": profile.weekly_rhythm,
                "support_preferences": profile.support_preferences,
                "communication_style": profile.communication_style,
                "guardrail": profile.guardrails or DEFAULT_GUARDRAILS,
                "calendar_block": block.model_dump(mode="json") if block else None,
                "priority": priority.model_dump(mode="json") if priority else None,
            },
            "guardrail": "This is a local review draft. Do not send it or create a calendar event without pastor approval and connector writeback policy.",
        },
        source="calendar",
        external_provider=None,
        related_type=block.type if block else "calendar",
        related_id=block.related_id if block else None,
        privacy_level="pastoral",
        account=account,
    )
    _audit(
        db,
        "assistant_action.scheduling_reply_prepared_from_chat",
        "Prepared scheduling reply from chat.",
        account=account,
        action_id=action.id,
        payload={"has_calendar_block": bool(block), "has_priority": bool(priority)},
    )
    db.commit()
    db.refresh(action)
    return action


def _scheduling_reply_body(
    profile: PastorProfile,
    block: Optional[DeskItem],
    priority: Optional[DeskItem],
) -> str:
    pastor = pastor_display_name(_profile_pastor_name(profile))
    tone = _short_context(profile.communication_style, 80) or "warm and brief"
    rhythm = _short_context(profile.weekly_rhythm, 180)
    block_line = (
        f"I am protecting time for {block.subtitle or block.title}, so I want to schedule this around that ministry priority."
        if block
        else "I want to schedule this around the ministry work already on the calendar."
    )
    priority_line = (
        f"The person or follow-up I am keeping in view first is {priority.title}."
        if priority
        else "I am trying to keep care, prayer, visitor follow-up, and preparation time from getting crowded out."
    )
    rhythm_line = f"My current rhythm is {rhythm}." if rhythm else "I am keeping a few protected ministry blocks open this week."
    return (
        "Hi,\n\n"
        "Thanks for reaching out. I would be glad to find a time.\n\n"
        f"{block_line} {priority_line} {rhythm_line}\n\n"
        "Could you send one or two times that work for you? I will compare them with the protected ministry blocks before confirming.\n\n"
        f"- {pastor}\n\n"
        f"Drafting note for review: keep this {tone}, and do not send or place anything on the calendar until approved."
    )


def _maybe_prepare_care_visit_plan(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[AssistantChatResponse]:
    if not _care_visit_planning_requested(lower):
        return None
    member = _find_mentioned_member(db, account, message, lower)
    care = _target_care_for_chat(db, account, message, lower)
    if care and care.member:
        member = care.member
    if not member:
        return None
    if not care:
        care = (
            scoped_query(db.query(CareNote), CareNote, account)
            .filter(CareNote.member_id == member.id, CareNote.status == "active")
            .order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.asc())
            .first()
        )
    item = _care_visit_calendar_block(member, care, profile)
    action = _prepare_care_visit_block_action(db, profile, account, item, member, care, effective_mode)
    rhythm = f" I would work around your saved rhythm: {profile.weekly_rhythm}." if _clean(profile.weekly_rhythm) else ""
    care_context = ""
    if care:
        care_context = f" I am tying it to the active {_label(_enum_value(care.category)).lower()} care case: {_short_context(care.description, 120) or 'pastoral follow-up'}."
    reply = (
        f"I would protect a visit window for {member.full_name} rather than leave that care follow-up vague."
        f"{care_context}{rhythm} I queued it as a calendar block for review; I will not create or change an external calendar event "
        "without checked credentials, writeback policy, and your approval."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="care_visit_plan_queued",
        mode=effective_mode,
        saved=True,
        actions=[_desk_item_from_action(action)],
        suggested_prompts=["Show my approvals.", f"Draft a care follow-up for {member.full_name}.", f"Log that I visited {member.full_name} today."],
        profile=_profile_response(profile, account),
    )


def _care_visit_planning_requested(lower: str) -> bool:
    if _mentions(lower, ["draft", "email", "reply", "respond"]):
        return False
    if _mentions(lower, ["where can i fit", "where should i fit", "when should i visit", "when can i visit", "find time", "visit window", "care follow-up window", "care follow up window"]):
        return True
    return _mentions(lower, ["visit", "care follow-up", "care follow up"]) and _mentions(lower, ["fit", "time", "block", "schedule", "calendar"])


def _care_visit_calendar_block(
    member: Member,
    care: Optional[CareNote],
    profile: PastorProfile,
) -> DeskItem:
    category = _enum_value(care.category) if care else "pastoral"
    detail_parts = []
    if care:
        detail_parts.append(_short_context(care.description, 160) or f"{_label(category)} care follow-up")
        detail_parts.append(f"Last contact: {_date_label(care.last_contact)}")
    else:
        detail_parts.append("Pastoral visit or check-in")
    if _clean(profile.weekly_rhythm):
        detail_parts.append(f"Respect rhythm: {profile.weekly_rhythm}")
    return DeskItem(
        id=f"calendar-care-visit-{care.id if care else f'member-{member.id}'}",
        type="calendar_block",
        title=f"Visit window for {member.full_name}",
        subtitle=f"{_label(category)} care follow-up" if care else "Pastoral visit",
        detail=". ".join(part for part in detail_parts if part),
        priority="high" if category in {"hospital", "grief", "crisis"} else "medium",
        action="Review protected visit block",
        source="calendar",
        related_id=care.id if care else member.id,
    )


def _prepare_care_visit_block_action(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    item: DeskItem,
    member: Member,
    care: Optional[CareNote],
    mode: Literal["demo", "live"],
) -> AssistantAction:
    today_key = datetime.utcnow().date().isoformat()
    related_type = "care" if care else "member"
    related_id = care.id if care else member.id
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{today_key}:{mode}:care_visit_block:{related_type}:{related_id}",
        action_type="calendar_block",
        title=item.title,
        description=item.detail or item.subtitle or "Proposed pastoral visit block.",
        payload={
            "desk_item": item.model_dump(mode="json"),
            "visit_plan": {
                "member_id": member.id,
                "member_name": member.full_name,
                "care_id": care.id if care else None,
                "weekly_rhythm": profile.weekly_rhythm,
            },
            "guardrail": "Do not create or change an external calendar event until connector credentials are checked, writeback policy allows it, and the pastor approves the exact item.",
        },
        source="calendar",
        external_provider=None,
        related_type=related_type,
        related_id=related_id,
        privacy_level="pastoral",
        account=account,
    )
    _audit(
        db,
        "assistant_action.care_visit_block_prepared_from_chat",
        f"Prepared visit calendar block from chat: {item.title}",
        account=account,
        action_id=action.id,
        payload={"member_id": member.id, "care_id": care.id if care else None},
    )
    db.commit()
    db.refresh(action)
    return action


def _target_prayer_for_chat(db: Session, account: Optional[ChurchAccount], message: str, lower: str) -> Optional[PrayerRequest]:
    member = _find_mentioned_member(db, account, message, lower)
    query = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.status == "active")
    if member:
        query = query.filter(PrayerRequest.member_id == member.id)
    return query.order_by(PrayerRequest.is_private.desc(), PrayerRequest.updated_at.asc()).first()


def _target_care_for_chat(db: Session, account: Optional[ChurchAccount], message: str, lower: str) -> Optional[CareNote]:
    member = _find_mentioned_member(db, account, message, lower)
    query = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.status == "active")
    if member:
        query = query.filter(CareNote.member_id == member.id)
    for category in ["hospital", "grief", "crisis", "general"]:
        if category in lower:
            query = query.filter(CareNote.category == category)
            break
    return query.order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.asc()).first()


def _prepare_single_followup_draft_action(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    item: DeskItem,
    mode: Literal["demo", "live"],
) -> AssistantAction:
    today_key = datetime.utcnow().date().isoformat()
    email_payload = _prepared_email_payload(db, profile, item, account)
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{today_key}:{mode}:chat_email_draft:{item.id}",
        action_type="email_draft",
        title=f"Review {item.title} follow-up",
        description=f"{item.subtitle or 'Pastoral follow-up'}: {item.detail or item.action or 'Draft reply for review.'}",
            payload={
                "desk_item": item.model_dump(mode="json"),
                "email": email_payload,
                "draft_kind": _prepared_email_kind(item),
                "draft_context": _draft_context_for_item(db, profile, account, item),
            },
            source=item.source or "assistant_chat",
            external_provider=None,
            related_type=item.source,
        related_id=item.related_id,
        privacy_level="private" if item.source == "prayer" else "pastoral",
        account=account,
    )
    _audit(
        db,
        "assistant_action.email_draft_prepared_from_chat",
        f"Prepared follow-up draft from chat: {action.title}",
        account=account,
        action_id=action.id,
        payload={"source": item.source, "related_id": item.related_id},
    )
    db.commit()
    db.refresh(action)
    return action


def _person_context_requested(lower: str) -> bool:
    return _mentions(lower, [
        "what do you know about",
        "what should i know about",
        "tell me about",
        "pull context for",
        "pull context on",
        "show context for",
        "show me context for",
        "pastoral context for",
        "member context for",
        "what should i know before",
        "what should i remember before",
        "what do i need to know before",
        "before i meet with",
        "before meeting with",
        "before my meeting with",
        "before i visit",
        "before visiting",
        "when did i last",
        "when was my last",
        "last contact with",
        "last visit with",
        "last call with",
        "last text with",
        "have i followed up with",
        "did i follow up with",
    ])


def _prayer_context_requested(lower: str) -> bool:
    if not _mentions(lower, ["prayer", "pray", "praying"]):
        return False
    return _mentions(lower, ["who", "show", "list", "needs", "follow-up", "follow up", "older", "private", "unanswered"])


def _care_context_requested(lower: str) -> bool:
    if _care_visit_planning_requested(lower):
        return False
    care_terms = ["care", "hospital", "grief", "crisis", "sick"]
    if not _mentions(lower, care_terms):
        return False
    return _mentions(lower, ["who", "show", "list", "needs", "active", "follow-up", "follow up", "older", "this week"])


def _visitor_context_requested(lower: str) -> bool:
    if not _mentions(lower, ["visitor", "visitors", "guest", "guests", "new family", "new families"]):
        return False
    return _mentions(lower, ["who", "show", "list", "needs", "follow-up", "follow up", "recent", "first-time", "first time"])


def _absence_context_requested(lower: str) -> bool:
    if not _mentions(lower, ["absent", "absence", "missed", "missing", "attendance", "not been here", "hasn't been here", "haven't been here"]):
        return False
    return _mentions(lower, ["who", "show", "list", "needs", "follow-up", "follow up", "check", "check-in", "check in", "has been", "have been"])


def _absence_draft_requested(lower: str) -> bool:
    wants_draft = _mentions(lower, ["draft", "write", "prepare", "queue"])
    wants_absence = _mentions(lower, ["absence", "absent", "missed", "missing", "attendance", "hasn't been here", "haven't been here"])
    wants_checkin = _mentions(lower, ["check-in", "check in", "checkins", "check-ins", "reply", "email", "message", "note"])
    return wants_draft and wants_absence and wants_checkin


def _absence_draft_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    email_drafts: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    absence_drafts = [item for item in email_drafts if item.source == "attendance" or item.title.lower().startswith("absence")]
    if not absence_drafts:
        absence_items = [item for item in priorities if item.type == "absence" or item.source == "attendance"]
        absence_drafts = _email_drafts({}, absence_items)
    if not absence_drafts:
        attendance_state = _attendance_context_empty_state(db, profile, account, user)
        return AssistantChatResponse(
            reply=(
                "I do not see an absence follow-up item to draft from yet. "
                f"{attendance_state['reply']} Add a member with last-attendance context if you want to start manually."
            ),
            intent="absence_drafts_empty",
            mode=effective_mode,
            actions=attendance_state["actions"],
            suggested_prompts=attendance_state["prompts"],
            profile=_profile_response(profile, account),
        )

    actions = _prepare_email_draft_actions(db, effective_mode, absence_drafts, profile, account, limit=3)
    _audit(
        db,
        "assistant_actions.absence_drafts_prepared_from_chat",
        f"Prepared {len(actions)} absence check-in draft action(s) from chat.",
        account=account,
        payload={"mode": effective_mode, "count": len(actions)},
    )
    db.commit()
    for action in actions:
        db.refresh(action)
    names = "; ".join(action.title for action in actions[:3])
    return AssistantChatResponse(
        reply=(
            f"I queued {len(actions)} absence check-in draft{'s' if len(actions) != 1 else ''} for review: {names}. "
            "These are gentle drafts only; I will not contact anyone or create a provider draft without your approval."
        ),
        intent="absence_drafts_queued",
        mode=effective_mode,
        saved=True,
        actions=[_desk_item_from_action(action) for action in actions],
        suggested_prompts=["Show my approvals.", "What should I approve first?", "Who else has been absent?"],
        profile=_profile_response(profile, account),
    )


def _absence_context_chat_response(
    db: Session,
    profile: PastorProfile,
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
) -> AssistantChatResponse:
    absence_items = [item for item in priorities if item.type == "absence" or item.source == "attendance"]
    if absence_items:
        lines = "; ".join(
            f"{item.title}: {item.subtitle or item.detail or item.action or 'absence follow-up'}"
            for item in absence_items[:4]
        )
        reply = (
            f"Here are the absence follow-up items I see: {lines}. "
            "I would keep this gentle and reviewable; I can draft check-ins, but I will not contact anyone without your approval."
        )
        prompts = ["Draft absence check-ins.", "Prepare today's queue.", "Show my approvals."]
        actions = absence_items[:5]
    else:
        attendance_state = _attendance_context_empty_state(db, profile, account, user)
        reply = (
            "I do not see an absence follow-up item in the current live desk. "
            f"{attendance_state['reply']} Until then I will not guess who is missing."
        )
        prompts = attendance_state["prompts"]
        actions = attendance_state["actions"]
    return AssistantChatResponse(
        reply=reply,
        intent="absence_context_lookup",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _attendance_context_empty_state(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
) -> dict:
    integrations = _integration_statuses(db, account, user)
    status = next((item for item in integrations if item.provider == "rock"), None)
    if status and status.status in {"connected", "configured", "available"} and status.verified_at:
        return {
            "reply": (
                f"{status.display_name} credentials are checked, so the next safe step is to sync attendance when you want "
                "current absence context imported for review."
            ),
            "actions": [],
            "prompts": [f"Sync {status.display_name}.", "Open integrations.", "Explain the approval rules."],
        }
    if status and status.status in {"connected", "configured", "available"}:
        step = _integration_check_credentials_step(status, profile)
        return {
            "reply": (
                f"{status.display_name} is {status.status.replace('_', ' ')}, but it needs a no-sync credential check before "
                "I sync attendance or prepare absence follow-up."
            ),
            "actions": [step],
            "prompts": [f"Check {status.display_name} credentials.", "Open integrations.", "Explain the approval rules."],
        }
    step = _provider_setup_or_check_step(
        profile,
        integrations,
        "rock",
        subtitle="Use Rock RMS when you want Marge to watch attendance and absence follow-up.",
        detail="Start secure setup first. Marge checks credentials without syncing before any attendance context is imported.",
    )
    actions = [step] if step else []
    return {
        "reply": (
            "No attendance source has completed secure setup and a no-sync credential check yet. "
            "I attached the Rock RMS setup step before any attendance sync."
        ),
        "actions": actions,
        "prompts": _connector_setup_or_check_prompts(actions),
    }


def _connected_context_requested(lower: str) -> bool:
    if _mentions(lower, ["synced people", "synced person", "synced events", "synced calendar", "synced inbox", "connected context", "connected items"]):
        return True
    provider = _provider_from_chat(lower)
    if not provider:
        return False
    if _mentions(lower, ["sync", "refresh", "pull", "connect", "setup", "set up", "authorize"]):
        return False
    return _mentions(lower, ["show", "list", "review", "what did", "what has", "what do", "context", "find", "found", "people", "person", "events", "calendar", "inbox", "emails", "messages"])


def _pre_connector_help_requested(lower: str) -> bool:
    if not _mentions(lower, ["what can you do", "what can marge do", "how can you help", "can you help"]):
        return False
    disconnected_terms = [
        "before tools",
        "before the tools",
        "before connectors",
        "before integrations",
        "without tools",
        "without connectors",
        "without integrations",
        "without google",
        "without gmail",
        "without planning center",
        "without rock",
        "without breeze",
        "without outlook",
        "without microsoft",
    ]
    if _mentions(lower, disconnected_terms):
        return True
    return _mentions(lower, ["before", "without"]) and _mentions(lower, [
        "connected",
        "connection",
        "connector",
        "connectors",
        "integration",
        "integrations",
        "tools",
        "google",
        "gmail",
        "planning center",
        "rock",
        "breeze",
        "outlook",
        "microsoft",
    ])


def _ministry_operating_plan_requested(lower: str) -> bool:
    if _mentions(lower, [
        "what do you know",
        "what have you learned",
        "operating plan",
        "how will you help",
        "how will you serve",
        "do you know my church",
    ]):
        return True
    return _mentions(lower, [
        "how can you help",
        "what can you do",
        "where should we start",
        "where should i start",
        "what should we do next",
        "what should i do next",
        "help me this week",
        "help this week",
        "start this week",
    ])


def _ministry_learning_gaps_requested(lower: str) -> bool:
    return _mentions(lower, [
        "what do you still need to learn",
        "what else do you need to learn",
        "what do you need to learn",
        "what do you not know",
        "what don't you know",
        "what context do you still need",
        "what should i include",
        "what should i say",
        "what else should i tell you",
        "what should i tell you next",
        "what do you still need from me",
        "what else do you need from me",
    ])


def _ministry_learning_gaps_response(
    profile: PastorProfile,
    setup_steps: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    seed_step = next((step for step in setup_steps if step.type == "data_seed"), None)
    integration_steps = [step for step in setup_steps if step.type in {"integration_setup", "integration_check"}]
    learned = _ministry_memory_summary(profile)
    lines = ["I have the core ministry profile now.", learned]
    actions: List[DeskItem] = []
    prompts: List[str] = []

    if seed_step:
        lines.append(
            f"What I still need next is one real ministry record: {seed_step.title}. "
            f"{_data_seed_chat_reply(seed_step)}"
        )
        actions.append(seed_step)
        prompts.extend(_data_seed_suggested_prompts(seed_step))
    elif priorities:
        priority_lines = "; ".join(
            f"{item.title}: {item.action or item.detail or item.subtitle or 'review this next'}"
            for item in priorities[:3]
        )
        lines.append(
            f"From here, I will learn from the real people and review items already in front of us: {priority_lines}. "
            "I will keep those local until you approve an exact draft or writeback."
        )
        actions.extend(priorities[:3])
        prompts.extend(["What should I handle next?", "Who should I check on next?", "Show my reminders."])
    else:
        lines.append(
            "What I do not have yet is live ministry texture: a first real visitor, care case, prayer request, member preference, "
            "or synced inbox/calendar/ChMS context. Give me one real update and I will tie it to people, drafts, reminders, or setup without inventing anything."
        )
        prompts.extend(["Help me add a ministry update.", "What should I record first?", "What should I connect first?"])

    if integration_steps:
        connector_names = _human_join([step.title for step in integration_steps[:3]])
        lines.append(
            f"I also still need credential-checked tool context from {connector_names}. "
            "The safe order is setup, no-sync credential check, then sync only when you ask."
        )
        actions.extend(integration_steps[: max(0, 3 - len(actions))])
        prompts.extend(_connector_setup_or_check_prompts(integration_steps[:2]))

    actions = _dedupe_desk_items(actions)[:3]
    if not prompts:
        prompts = _suggested_prompts(profile, priorities, setup_steps)
    return AssistantChatResponse(
        reply=" ".join(lines),
        intent="ministry_learning_gaps",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_dedupe_strings(prompts)[:3],
        profile=_profile_response(profile, account),
    )


def _profile_setting_lookup_requested(lower: str) -> bool:
    if _mentions(lower, [
        "what are my guardrails",
        "what guardrails",
        "my guardrails",
        "my approval boundaries",
        "what are my approval boundaries",
        "what should you never do",
        "what should you not do without asking",
        "what did i say not to do",
    ]):
        return True
    if _mentions(lower, [
        "what is my rhythm",
        "what rhythm",
        "my weekly rhythm",
        "what should you protect",
        "what should marge protect",
        "what should you remember about my week",
        "what should you remember about my rhythm",
        "what should you protect on my calendar",
    ]):
        return True
    if _mentions(lower, [
        "what tools do you remember",
        "what tools have i told you",
        "what systems do you remember",
        "what systems have i told you",
        "what tools do we use",
        "what systems do we use",
        "what do you know about our tools",
        "what do you know about my tools",
    ]):
        return True
    return _mentions(lower, [
        "drafting voice",
        "communication style",
        "how should you sound",
        "how should you write",
        "how should marge sound",
        "what voice should you use",
        "what tone should you use",
    ])


def _profile_setting_lookup_response(
    profile: PastorProfile,
    setup_steps: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
    lower: str,
) -> AssistantChatResponse:
    actions = setup_steps[:2]
    if _mentions(lower, ["guardrail", "approval boundaries", "never do", "not do without asking", "not to do"]):
        saved = _short_context(profile.guardrails)
        reply = (
            f"You told me this guardrail: {saved}. "
            "I will keep that in front of every draft, calendar block, connector sync, and writeback. "
            "External sends, calendar writes, or church-system changes still require checked credentials, allowed writeback policy, and your approval of the exact item."
        ) if saved else (
            "I do not have a custom guardrail saved yet. Until you give one, I will still keep the default boundary: "
            "I can draft and queue work, but I will not send, schedule, sync, or write externally without checked credentials, allowed policy, and your approval."
        )
        prompts = ["Explain the approval rules.", "What else do you need from me?", "Update my guardrails."]
        intent = "profile_guardrails_lookup"
    elif _mentions(lower, ["rhythm", "protect", "calendar", "week"]):
        rhythm = _short_context(profile.weekly_rhythm)
        reply = (
            f"You told me to protect this weekly rhythm: {rhythm}. "
            "I will use that when suggesting visit blocks, meeting prep, scheduling replies, and what can wait. "
            "I still will not create or change an external calendar event without checked credentials, allowed writeback policy, and your approval."
        ) if rhythm else (
            "I do not have a weekly rhythm saved yet. Tell me what to protect, such as sermon prep, visit days, staff meetings, office hours, or rest."
        )
        prompts = ["Where can I fit care follow-up?", "What can wait until next week?", "Update my weekly rhythm."]
        intent = "profile_weekly_rhythm_lookup"
    elif _mentions(lower, ["tools", "systems"]):
        tools = _short_context(profile.tools_in_use)
        connector_steps = [step for step in setup_steps if step.type in {"integration_setup", "integration_check"}]
        connector_text = ""
        if connector_steps:
            connector_text = (
                " The next safe connector step is "
                + _human_join([step.title for step in connector_steps[:2]])
                + ": setup, no-sync credential check, then sync only when you ask."
            )
            actions = connector_steps[:2]
        reply = (
            f"You told me these church tools are already in the room: {tools}.{connector_text} "
            "I will not ask for passwords in chat, and I will not import ministry data until credentials are checked and you ask me to sync."
        ) if tools else (
            "I do not have your church tools saved yet. Tell me whether you use Planning Center, Rock RMS, Breeze, Google Workspace, Microsoft 365, or another system."
        )
        prompts = (_connector_setup_or_check_prompts(connector_steps[:2]) if connector_steps else ["What should I connect first?", "How do secure connections work?", "Open integrations."])
        intent = "profile_tools_lookup"
    else:
        voice = _short_context(profile.communication_style)
        tradition = _short_context(profile.faith_tradition)
        reply = (
            f"You told me to draft in this voice: {voice}. "
            f"I should also respect this church voice and tradition: {tradition}. "
            "I will keep those as review metadata for drafts and let you approve the exact wording before anything is sent."
        ) if voice else (
            "I do not have a drafting voice saved yet. Tell me whether Marge should sound warm and brief, formal, conversational, or another way."
        )
        prompts = ["Update my drafting voice.", "Draft a care follow-up.", "Explain the approval rules."]
        intent = "profile_drafting_voice_lookup"
    return AssistantChatResponse(
        reply=reply,
        intent=intent,
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _pastor_pressure_requested(lower: str) -> bool:
    if _mentions(lower, [
        "overwhelmed",
        "overloaded",
        "swamped",
        "buried",
        "too much",
        "stressed",
        "exhausted",
        "i'm tired",
        "i am tired",
        "i feel tired",
        "so tired",
        "really tired",
        "burned out",
        "burnt out",
        "discouraged",
        "ministry is heavy",
        "heavy today",
        "hard day",
        "rough day",
        "drowning",
        "take off my plate",
        "off my plate",
        "what can you carry",
        "what can you handle for me",
        "what can you take",
        "help me triage",
        "help me prioritize",
    ]):
        return True
    return _mentions(lower, ["help me"]) and _mentions(lower, ["rest", "carry", "pressure", "heavy"])


def _connected_context_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    provider = _provider_from_chat(lower)
    item_type = _connected_item_type_from_chat(lower)
    items = _connected_items_filtered(db, account=account, provider=provider, item_type=item_type, limit=6)
    label = _connected_context_label(provider, item_type)
    if items:
        lines = "; ".join(_connected_context_line(item) for item in items[:4])
        integrations = _integration_statuses(db, account, user)
        refresh_state = _connected_context_refresh_state(profile, integrations, provider)
        reply = (
            f"Here is the {label} I have synced for review: {lines}. "
            "I am keeping this read-side until you approve a review item or ask me to turn it into local Marge memory."
        )
        if refresh_state["note"]:
            reply += f" {refresh_state['note']}"
        prompts = _connected_context_prompts(items, refresh_state["refresh_prompt"])
        actions = [_desk_item_from_connected_item(item) for item in items[:5]]
        if refresh_state["actions"]:
            actions = actions[:4] + refresh_state["actions"][:1]
    else:
        empty_state = _connected_context_empty_state(db, profile, account, user, provider, item_type)
        reply = empty_state["reply"]
        prompts = empty_state["prompts"]
        actions = empty_state["actions"]
    return AssistantChatResponse(
        reply=reply,
        intent="connected_context_lookup",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _connected_item_type_from_chat(lower: str) -> Optional[str]:
    if _mentions(lower, ["people", "person", "new family", "new families", "visitor", "guest"]):
        return "person"
    if _mentions(lower, ["inbox", "email", "emails", "message", "messages", "gmail", "outlook"]):
        return "email"
    if _mentions(lower, ["calendar", "event", "events", "meeting", "meetings"]):
        return "calendar_event"
    return None


def _connected_items_filtered(
    db: Session,
    *,
    account: Optional[ChurchAccount],
    provider: Optional[str] = None,
    item_type: Optional[str] = None,
    limit: int = 6,
) -> List[ConnectedContextItem]:
    query = scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
    if provider:
        query = query.filter(ConnectedContextItem.provider == provider)
    if item_type:
        query = query.filter(ConnectedContextItem.item_type == item_type)
    return query.order_by(ConnectedContextItem.occurred_at.desc().nullslast(), ConnectedContextItem.created_at.desc()).limit(limit).all()


def _connected_context_label(provider: Optional[str], item_type: Optional[str]) -> str:
    provider_label = _provider_display_name(provider) if provider else "connected tool"
    if item_type == "person":
        return f"{provider_label} people"
    if item_type == "email":
        return f"{provider_label} inbox"
    if item_type == "calendar_event":
        return f"{provider_label} calendar"
    return f"{provider_label} context"


def _provider_display_name(provider: Optional[str]) -> str:
    if not provider:
        return "connected tool"
    definition = next((item for item in _integration_definitions() if item["provider"] == provider), None)
    return definition["display_name"] if definition else provider.replace("_", " ").title()


def _connected_context_line(item: ConnectedContextItem) -> str:
    type_label = {
        "person": "person",
        "email": "email",
        "calendar_event": "event",
    }.get(item.item_type, item.item_type.replace("_", " "))
    review = " queued for review" if item.action_id else ""
    detail = item.snippet or item.subtitle or _date_label(item.occurred_at)
    return f"{item.title} ({_provider_display_name(item.provider)} {type_label}{review}): {_short_context(detail, 100) or 'no preview synced'}"


def _connected_context_prompts(items: List[ConnectedContextItem], refresh_prompt: Optional[str] = None) -> List[str]:
    prompts = ["Show my approvals."]
    if any(item.item_type == "person" for item in items):
        first_person = next((item for item in items if item.item_type == "person"), None)
        prompts.append(f"Add {first_person.title} to Marge." if first_person else "Review synced people.")
    if any(item.item_type == "email" for item in items):
        prompts.append("Draft replies from synced inbox.")
    if any(item.item_type == "calendar_event" for item in items):
        prompts.append("What events need prep?")
    if refresh_prompt:
        prompts.append(refresh_prompt)
    return prompts[:4]


def _connected_context_refresh_state(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    provider: Optional[str],
) -> dict:
    if not provider:
        return {"note": "", "actions": [], "refresh_prompt": None}
    status = next((item for item in integrations if item.provider == provider), None)
    if status and status.status in {"connected", "configured", "available"} and status.verified_at:
        return {"note": "", "actions": [], "refresh_prompt": f"Sync {status.display_name} again."}
    if status and status.status in {"connected", "configured", "available"}:
        step = _integration_check_credentials_step(status, profile)
        return {
            "note": (
                f"{status.display_name} needs a no-sync credential check before I refresh this synced context."
            ),
            "actions": [step],
            "refresh_prompt": f"Check {status.display_name} credentials.",
        }
    step = _provider_setup_or_check_step(
        profile,
        integrations,
        provider,
        subtitle=f"Reconnect {_provider_display_name(provider)} before refreshing synced ministry context.",
        detail="Marge can show existing review context, but secure setup and a no-sync credential check are required before any refresh.",
    )
    actions = [step] if step else []
    fallback_prompts = _connector_setup_or_check_prompts(actions)
    return {
        "note": (
            f"{_provider_display_name(provider)} must complete secure setup and a no-sync credential check before I refresh this synced context."
        ),
        "actions": actions,
        "refresh_prompt": fallback_prompts[0] if fallback_prompts else "Open integrations.",
    }


def _connected_refresh_state_for_providers(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    providers: List[Optional[str]],
) -> dict:
    ordered: List[str] = []
    for provider in providers:
        if provider and provider not in ordered:
            ordered.append(provider)
    if not ordered:
        return {"note": "", "actions": [], "refresh_prompt": None}
    states = [_connected_context_refresh_state(profile, integrations, provider) for provider in ordered]
    blocked_state = next(
        (
            state
            for state in states
            if not (isinstance(state.get("refresh_prompt"), str) and state["refresh_prompt"].startswith("Sync "))
        ),
        None,
    )
    verified_state = next(
        (
            state
            for state in states
            if isinstance(state.get("refresh_prompt"), str) and state["refresh_prompt"].startswith("Sync ")
        ),
        None,
    )
    if verified_state:
        if blocked_state and blocked_state.get("note"):
            return {
                "note": blocked_state.get("note", ""),
                "actions": blocked_state.get("actions", []),
                "refresh_prompt": verified_state.get("refresh_prompt"),
            }
        return verified_state
    return blocked_state or states[0]


def _connected_items_refresh_state(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    items: List[ConnectedContextItem],
) -> dict:
    return _connected_refresh_state_for_providers(profile, integrations, [item.provider for item in items])


def _connected_context_empty_state(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    provider: Optional[str],
    item_type: Optional[str],
) -> dict:
    integrations = _integration_statuses(db, account, user)
    status = next((item for item in integrations if item.provider == provider), None) if provider else None
    actions = _connected_context_setup_actions(profile, integrations, provider)
    provider_hint = f" from {_provider_display_name(provider)}" if provider else ""
    type_hint = f" {item_type.replace('_', ' ')}" if item_type else ""
    ready = status and status.status in {"connected", "configured", "available"}
    if ready and status.verified_at:
        reply = (
            f"I do not have synced{type_hint} context{provider_hint} yet. "
            f"{status.display_name} credentials are checked, so the next safe step is to sync when you want me to import current ministry context for review."
        )
        prompts = [f"Sync {status.display_name}.", "Open integrations.", "Explain the approval rules."]
    elif ready:
        reply = (
            f"I do not have synced{type_hint} context{provider_hint} yet. "
            f"{status.display_name} is {status.status.replace('_', ' ')}, but I need to check credentials before syncing ministry data. "
            "The check confirms access without importing people, email, calendar, or attendance context."
        )
        prompts = [f"Check {status.display_name} credentials.", "Open integrations.", "Explain the approval rules."]
    else:
        if provider:
            subject = _provider_display_name(provider)
            setup_phrase = f"{subject} has not completed secure setup and a no-sync credential check"
        else:
            subject = "the requested church tool"
            setup_phrase = "no church tool has completed secure setup and a no-sync credential check"
        reply = (
            f"I do not have synced{type_hint} context{provider_hint} yet because {setup_phrase}. "
            "I attached the next connector step. After setup and credentials pass, ask me to sync and I will queue sensitive items for pastor review."
        )
        prompts = _connected_context_setup_prompts(actions, subject)
    return {"reply": reply, "actions": actions[:3], "prompts": prompts}


def _connected_context_setup_actions(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    provider: Optional[str],
) -> List[DeskItem]:
    if provider:
        status = next((item for item in integrations if item.provider == provider), None)
        if status and status.status in {"connected", "configured", "available"} and not status.verified_at:
            return [_integration_check_credentials_step(status, profile)]
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=False)
        return [step for step in setup_steps if step.type == "integration_setup" and step.provider == provider][:1]
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=False)
    return [step for step in setup_steps if step.type == "integration_setup"][:3]


def _connected_context_setup_prompts(actions: List[DeskItem], subject: str) -> List[str]:
    if actions:
        return [_setup_prompt(actions[0]), "How do secure connections work?", "Explain the approval rules."]
    return ["Open integrations.", "What should I connect first?", "How do secure connections work?"]


def _maybe_import_connected_person_from_chat(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[dict]:
    if not _connected_person_import_requested(lower):
        return None
    action = _find_connected_person_review_action(db, account, message, lower)
    if not action:
        provider = _provider_from_chat(lower)
        empty_state = _connected_context_empty_state(db, profile, account, user, provider, "person")
        return {
            "intent": "connected_person_import_not_found",
            "saved": False,
            "reply": (
                "I do not see a synced person review item that matches that request. "
                + empty_state["reply"]
            ),
            "actions": empty_state["actions"],
            "suggested_prompts": empty_state["prompts"],
        }
    if action.status == "executed":
        execution = (_json_loads(action.payload_json).get("execution") or {})
        name = execution.get("member_name") or action.title
        return {
            "intent": "connected_person_already_imported",
            "saved": False,
            "reply": f"{name} is already in Marge's local people memory from that synced review.",
            "actions": [_desk_item_from_action(action)],
            "suggested_prompts": [f"What do you know about {name}?", "Review synced people."],
        }
    if action.status not in {"pending", "approved"}:
        return {
            "intent": "connected_person_import_unavailable",
            "saved": False,
            "reply": f"That synced person review is {action.status}, so I cannot add it to local memory from chat.",
            "actions": [_desk_item_from_action(action)],
            "suggested_prompts": ["Review synced people.", "Show my approvals."],
        }

    if action.status == "pending":
        action.status = "approved"
        action.approved_at = action.approved_at or datetime.utcnow()
        _audit(db, "assistant_action.approved_from_chat", f"Approved synced person import from chat: {action.title}", account=account, action_id=action.id, provider=action.source)
    execution = _execute_person_review_action(db, action, account)
    payload = _json_loads(action.payload_json)
    payload["execution"] = execution
    action.payload_json = _json_dumps(payload)
    action.status = "executed"
    action.executed_at = datetime.utcnow()
    _audit(db, "assistant_action.executed_from_chat", f"Added synced person to local Marge memory from chat: {action.title}", account=account, action_id=action.id, provider=action.source)
    db.commit()
    db.refresh(action)

    member = scoped_query(db.query(Member), Member, account).filter(Member.id == execution.get("member_id")).first()
    member_actions = _member_context_actions(member, _member_active_care_cases(db, account, member), _member_active_prayers(db, account, member), _member_recent_notes(db, account, member)) if member else []
    name = execution.get("member_name") or (member.full_name if member else action.title)
    source = (action.source or "connected system").replace("_", " ").title()
    return {
        "intent": "connected_person_imported",
        "saved": True,
        "reply": (
            f"I added {name} to Marge's local people memory from {source} and saved a connector-import note. "
            "This did not write back to the source system."
        ),
        "actions": member_actions or [_desk_item_from_action(action)],
        "suggested_prompts": [f"What do you know about {name}?", "Review synced people.", "Who else needs attention?"],
    }


def _connected_person_import_requested(lower: str) -> bool:
    if not _mentions(lower, ["add", "import", "save", "create", "bring in"]):
        return False
    return _mentions(lower, ["to marge", "local memory", "people memory", "synced person", "synced people", "planning center", "breeze", "from pco", "from breeze"])


def _church_profile_context_requested(lower: str, profile: PastorProfile) -> bool:
    if not _mentions(lower, ["what do you know", "what have you learned", "tell me about", "do you know"]):
        return False
    if _mentions(lower, ["my church", "our church", "this church", "the church", "my ministry", "our ministry", "ministry context"]):
        return True
    if "church" not in lower:
        return False
    church_name = _clean(profile.church_name)
    if not church_name:
        return False
    church_terms = [
        term
        for term in re.split(r"[^a-z0-9]+", church_name.lower())
        if len(term) >= 4 and term not in {"church", "first", "saint", "pastor", "marge"}
    ]
    return bool(church_terms) and any(term in lower for term in church_terms)


def _find_connected_person_review_action(
    db: Session,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
) -> Optional[AssistantAction]:
    connected = _find_mentioned_connected_person(db, account, message, lower)
    if connected and connected.action_id:
        action = scoped_query(db.query(AssistantAction), AssistantAction, account).filter(AssistantAction.id == connected.action_id, AssistantAction.action_type == "person_review").first()
        if action:
            return action

    provider = _provider_from_chat(lower)
    actions = (
        scoped_query(db.query(AssistantAction), AssistantAction, account)
        .filter(AssistantAction.action_type == "person_review")
        .order_by(AssistantAction.created_at.asc())
        .limit(50)
        .all()
    )
    if provider:
        actions = [action for action in actions if action.source == provider]
    name = _lookup_name_from_message(message)
    if name:
        lowered_name = name.lower()
        for action in actions:
            payload = _json_loads(action.payload_json)
            person = payload.get("person") or {}
            haystack = " ".join([
                action.title or "",
                person.get("name") or "",
                person.get("first_name") or "",
                person.get("last_name") or "",
                action.description or "",
            ]).lower()
            if lowered_name in haystack or all(part in haystack for part in lowered_name.split()):
                return action
        return None
    actionable = [action for action in actions if action.status in {"pending", "approved"}]
    return actionable[0] if actionable else (actions[0] if len(actions) == 1 else None)


def _person_context_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    member = _find_mentioned_member(db, account, message, lower)
    visitor = _find_mentioned_visitor(db, account, message, lower)
    connected = _find_mentioned_connected_person(db, account, message, lower)
    name = member.full_name if member else (visitor.full_name if visitor else _lookup_name_from_message(message))

    if member:
        care_cases = _member_active_care_cases(db, account, member)
        prayers = _member_active_prayers(db, account, member)
        notes = _member_recent_notes(db, account, member)
        calendar_events = _member_connected_calendar_events(db, account, member)
        bits = [f"I know {member.full_name} from your Marge people memory."]
        if member.email or member.phone:
            contact = _human_join([part for part in [member.email, member.phone] if part])
            bits.append(f"Contact on file: {contact}.")
        if member.last_attendance:
            bits.append(f"Last attendance is {_date_label(member.last_attendance)}.")
        if care_cases:
            bits.append("Active care: " + "; ".join(_care_case_summary(case) for case in care_cases[:3]) + ".")
        if prayers:
            bits.append("Prayer: " + "; ".join(_prayer_summary(prayer) for prayer in prayers[:3]) + ".")
        if calendar_events:
            bits.append("Synced calendar: " + "; ".join(_connected_calendar_event_summary(event) for event in calendar_events[:3]) + ".")
        preference_notes = [note for note in notes if note.context_tag == "preference"]
        if preference_notes:
            bits.append("Preferences to respect: " + "; ".join(_short_context(note.note_text, 120) for note in preference_notes[:3]) + ".")
        other_notes = [note for note in notes if note.context_tag != "preference"]
        if other_notes:
            bits.append("Recent notes: " + "; ".join(_short_context(note.note_text, 120) for note in other_notes[:3]) + ".")
        if not care_cases and not prayers and not notes and not calendar_events:
            bits.append("I do not see active care, prayer, recent notes, or synced calendar context yet.")
        bits.append("I will use this as pastor-only context and will not contact them without your approval.")
        actions = _dedupe_desk_items(
            _member_context_actions(member, care_cases, prayers, notes)
            + [_desk_item_from_connected_item(event) for event in calendar_events[:2]]
        )
        return AssistantChatResponse(
            reply=" ".join(bits),
            intent="person_context_lookup",
            mode=effective_mode,
            actions=actions,
            suggested_prompts=[
                f"Draft a care follow-up for {member.full_name}.",
                f"Log that I visited {member.full_name} today.",
                f"Where can I fit a visit with {member.full_name}?",
            ],
            profile=_profile_response(profile, account),
        )

    if visitor:
        bits = [f"I know {visitor.full_name} as a visitor from {_date_label(visitor.visit_date)}."]
        if visitor.email or visitor.phone:
            bits.append(f"Contact on file: {_human_join([part for part in [visitor.email, visitor.phone] if part])}.")
        if visitor.source:
            bits.append(f"Source: {visitor.source}.")
        if visitor.notes:
            bits.append(f"Note: {_short_context(visitor.notes, 160)}.")
        sent_bits = []
        if visitor.follow_up_day1_sent:
            sent_bits.append("day-one")
        if visitor.follow_up_day3_sent:
            sent_bits.append("day-three")
        if visitor.follow_up_week2_sent:
            sent_bits.append("week-two")
        bits.append(f"Follow-up logged: {', '.join(sent_bits) if sent_bits else 'none yet'}.")
        bits.append("I can draft the next welcome note, but you approve before anything is sent.")
        return AssistantChatResponse(
            reply=" ".join(bits),
            intent="visitor_context_lookup",
            mode=effective_mode,
            actions=[_visitor_desk_item(visitor)],
            suggested_prompts=["Draft a welcome note.", "Show visitors needing follow-up."],
            profile=_profile_response(profile, account),
        )

    if connected:
        reply = (
            f"I only see {connected.title} in synced {connected.provider.replace('_', ' ')} context so far. "
            f"{connected.snippet or connected.subtitle or 'Review the connected item before writing anything local.'} "
            "If this is someone you shepherd, approve a person-review action or add them to Marge's people memory."
        )
        return AssistantChatResponse(
            reply=reply,
            intent="connected_person_context_lookup",
            mode=effective_mode,
            actions=[_desk_item_from_connected_item(connected)],
            suggested_prompts=["Review synced people.", f"Add {connected.title} to Marge."],
            profile=_profile_response(profile, account),
        )

    if name:
        reply = (
            f"I do not have a confident Marge record for {name} yet. "
            "Add them as a person or visitor first, then I can keep care notes, prayer follow-up, and drafts tied to the right record."
        )
        return AssistantChatResponse(
            reply=reply,
            intent="person_context_not_found",
            mode=effective_mode,
            actions=[],
            suggested_prompts=[f"Help me add {name} as a person.", f"Log {name} as a visitor.", "Show my setup steps."],
            profile=_profile_response(profile, account),
        )
    return None


def _prayer_context_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    query = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.status == "active")
    if _mentions(lower, ["private"]):
        query = query.filter(PrayerRequest.is_private.is_(True))
    if _mentions(lower, ["older", "two weeks", "2 weeks", "14 days"]):
        query = query.filter(PrayerRequest.updated_at <= datetime.utcnow() - timedelta(days=14))
    prayers = query.order_by(PrayerRequest.is_private.desc(), PrayerRequest.updated_at.asc()).limit(5).all()
    if prayers:
        reply = (
            "These prayer requests need pastoral attention: "
            + "; ".join(_prayer_line(prayer) for prayer in prayers)
            + ". I will keep private prayer requests inside this workspace."
        )
    else:
        reply = "I do not see active prayer requests matching that filter. Add the first prayer request, and I will keep it visible for follow-up."
    if prayers:
        first_name = _prayer_subject_name(prayers[0])
        prompts = (
            [f"Draft a prayer follow-up for {first_name}.", "Show active care cases.", "Add a prayer request."]
            if first_name
            else ["Draft a private prayer follow-up from this request.", "What should I capture for private prayer?", "Show active care cases."]
        )
    else:
        prompts = ["What should I capture for private prayer?", "Show active care cases."]
    return AssistantChatResponse(
        reply=reply,
        intent="prayer_context_lookup",
        mode=effective_mode,
        actions=[_prayer_desk_item(prayer) for prayer in prayers],
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _care_context_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    query = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.status == "active")
    for category in ["hospital", "grief", "crisis", "general"]:
        if category in lower:
            query = query.filter(CareNote.category == category)
            break
    if _mentions(lower, ["older", "this week", "follow-up", "follow up", "needs"]):
        query = query.order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.asc())
    else:
        query = query.order_by(CareNote.created_at.desc())
    care_cases = query.limit(5).all()
    if care_cases:
        reply = (
            "I would keep these care cases in front of you: "
            + "; ".join(_care_line(case) for case in care_cases)
            + ". I can draft follow-up, but the pastor reviews before anything is sent."
        )
    else:
        reply = "I do not see active care cases matching that filter. Add one care case when there is someone Marge should keep from slipping through the cracks."
    if care_cases and care_cases[0].member:
        first_name = care_cases[0].member.full_name
        prompts = [f"Draft a care follow-up for {first_name}.", f"Where can I fit a visit with {first_name}?", f"Log that I visited {first_name} today."]
    else:
        prompts = ["What should I capture for a care case?", "Who needs prayer follow-up?", "Help me add a ministry update."]
    return AssistantChatResponse(
        reply=reply,
        intent="care_context_lookup",
        mode=effective_mode,
        actions=[_care_desk_item(case) for case in care_cases],
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _visitor_context_chat_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    query = scoped_query(db.query(Visitor), Visitor, account)
    if _mentions(lower, ["follow-up", "follow up", "needs"]):
        query = query.filter(
            (Visitor.follow_up_day1_sent.is_(False))
            | (Visitor.follow_up_day3_sent.is_(False))
            | (Visitor.follow_up_week2_sent.is_(False))
        )
    visitors = query.order_by(Visitor.visit_date.desc(), Visitor.created_at.desc()).limit(5).all()
    if visitors:
        reply = (
            "These visitors are worth keeping in front of you: "
            + "; ".join(_visitor_line(visitor) for visitor in visitors)
            + ". I can draft the next welcome note for pastor review."
        )
    else:
        reply = "I do not see visitors matching that filter. Log the first visitor, and I will queue a welcome draft for review."
    return AssistantChatResponse(
        reply=reply,
        intent="visitor_context_lookup",
        mode=effective_mode,
        actions=[_visitor_desk_item(visitor) for visitor in visitors],
        suggested_prompts=["Draft visitor welcomes.", "Log a visitor.", "What should I ask a new family?"],
        profile=_profile_response(profile, account),
    )


def _find_mentioned_member(db: Session, account: Optional[ChurchAccount], message: str, lower: str) -> Optional[Member]:
    members = scoped_query(db.query(Member), Member, account).order_by(Member.last_name, Member.first_name).limit(200).all()
    full_matches = [member for member in members if member.full_name.lower() in lower]
    if full_matches:
        return full_matches[0]
    names = _lookup_name_parts(message)
    if names:
        first, last = names
        matches = [
            member for member in members
            if member.first_name.lower() == first and (not last or member.last_name.lower() == last)
        ]
        if len(matches) == 1:
            return matches[0]
    first_name_matches = [member for member in members if re.search(rf"\b{re.escape(member.first_name.lower())}\b", lower)]
    return first_name_matches[0] if len(first_name_matches) == 1 else None


def _find_mentioned_visitor(db: Session, account: Optional[ChurchAccount], message: str, lower: str) -> Optional[Visitor]:
    visitors = scoped_query(db.query(Visitor), Visitor, account).order_by(Visitor.visit_date.desc(), Visitor.created_at.desc()).limit(200).all()
    full_matches = [visitor for visitor in visitors if visitor.full_name.lower() in lower]
    if full_matches:
        return full_matches[0]
    names = _lookup_name_parts(message)
    if names:
        first, last = names
        matches = [
            visitor for visitor in visitors
            if visitor.first_name.lower() == first and (not last or visitor.last_name.lower() == last)
        ]
        if len(matches) == 1:
            return matches[0]
    first_name_matches = [visitor for visitor in visitors if re.search(rf"\b{re.escape(visitor.first_name.lower())}\b", lower)]
    return first_name_matches[0] if len(first_name_matches) == 1 else None


def _find_mentioned_connected_person(db: Session, account: Optional[ChurchAccount], message: str, lower: str) -> Optional[ConnectedContextItem]:
    items = scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account).filter(ConnectedContextItem.item_type == "person").order_by(ConnectedContextItem.updated_at.desc().nullslast(), ConnectedContextItem.created_at.desc()).limit(100).all()
    for item in items:
        haystack = " ".join([item.title or "", item.subtitle or "", item.snippet or ""]).lower()
        if item.title and item.title.lower() in lower:
            return item
        names = _lookup_name_parts(message)
        if names and all(part in haystack for part in names if part):
            return item
    return None


def _lookup_name_parts(message: str) -> Optional[tuple[str, Optional[str]]]:
    name = _lookup_name_from_message(message)
    if not name:
        return None
    parts = [part.lower() for part in re.split(r"\s+", name) if part]
    if not parts:
        return None
    return parts[0], parts[1] if len(parts) > 1 else None


def _lookup_name_from_message(message: str) -> Optional[str]:
    patterns = [
        r"(?:about|for|on|with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:know|remember)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).strip()
    return None


def _member_active_care_cases(db: Session, account: Optional[ChurchAccount], member: Member) -> List[CareNote]:
    return (
        scoped_query(db.query(CareNote), CareNote, account)
        .filter(CareNote.member_id == member.id, CareNote.status == "active")
        .order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.desc())
        .limit(5)
        .all()
    )


def _member_active_prayers(db: Session, account: Optional[ChurchAccount], member: Member) -> List[PrayerRequest]:
    return (
        scoped_query(db.query(PrayerRequest), PrayerRequest, account)
        .filter(PrayerRequest.member_id == member.id, PrayerRequest.status == "active")
        .order_by(PrayerRequest.is_private.desc(), PrayerRequest.updated_at.asc())
        .limit(5)
        .all()
    )


def _member_recent_notes(db: Session, account: Optional[ChurchAccount], member: Member) -> List[MemberNote]:
    return (
        scoped_query(db.query(MemberNote), MemberNote, account)
        .filter(MemberNote.member_id == member.id)
        .order_by(MemberNote.created_at.desc())
        .limit(5)
        .all()
    )


def _member_connected_calendar_events(
    db: Session,
    account: Optional[ChurchAccount],
    member: Member,
    limit: int = 3,
) -> List[ConnectedContextItem]:
    events = _connected_items(db, "calendar_event", account=account, limit=50)
    matched: List[ConnectedContextItem] = []
    member_name = (member.full_name or "").lower()
    member_email = (member.email or "").lower()
    for event in events:
        payload = _json_loads(event.payload_json).get("calendar_event") or {}
        participants = _calendar_event_participants(payload)
        participant_match = any(_member_matches_calendar_participant(member, participant) for participant in participants)
        haystack = " ".join([
            event.title or "",
            event.subtitle or "",
            event.snippet or "",
            payload.get("summary") or "",
            payload.get("subject") or "",
            payload.get("description") or "",
            payload.get("location") or "",
        ]).lower()
        text_match = bool(member_name and member_name in haystack) or bool(member_email and member_email in haystack)
        if participant_match or text_match:
            matched.append(event)
            if len(matched) >= limit:
                break
    return matched


def _member_matches_calendar_participant(member: Member, participant: dict) -> bool:
    email = (participant.get("email") or "").lower()
    if member.email and email and member.email.lower() == email:
        return True
    name = (participant.get("name") or "").lower()
    return bool(member.full_name and name and member.full_name.lower() == name)


def _connected_calendar_event_summary(item: ConnectedContextItem) -> str:
    payload = _json_loads(item.payload_json).get("calendar_event") or {}
    title = item.title or payload.get("summary") or payload.get("subject") or "Calendar event"
    when = item.subtitle or payload.get("when") or _date_label(item.occurred_at)
    return f"{title} ({when or 'time not listed'})"


def _member_context_actions(member: Member, care_cases: List[CareNote], prayers: List[PrayerRequest], notes: List[MemberNote]) -> List[DeskItem]:
    actions = [DeskItem(id=f"member-{member.id}", type="member", title=member.full_name, subtitle="People memory", detail=member.email or member.phone, priority="medium", action="Open person", source="members", related_id=member.id)]
    actions.extend(_care_desk_item(case) for case in care_cases[:2])
    actions.extend(_prayer_desk_item(prayer) for prayer in prayers[:2])
    if notes:
        latest = notes[0]
        actions.append(DeskItem(id=f"member-note-{latest.id}", type="member_note", title=f"Latest note for {member.full_name}", subtitle=latest.context_tag or "Pastoral note", detail=_short_context(latest.note_text, 160), priority="medium", action="Review note", source="member_notes", related_id=latest.id))
    return actions[:5]


def _care_case_summary(case: CareNote) -> str:
    return f"{_label(_enum_value(case.category))} case, last contact {_date_label(case.last_contact)}, {_short_context(case.description, 100) or 'description not attached yet'}"


def _prayer_summary(prayer: PrayerRequest) -> str:
    privacy = "private " if prayer.is_private else ""
    return f"{privacy}request opened {_date_label(prayer.created_at)}: {_short_context(prayer.request_text, 100)}"


def _care_line(case: CareNote) -> str:
    name = case.member.full_name if case.member else "Name not linked"
    return f"{name} ({_label(_enum_value(case.category))}, last contact {_date_label(case.last_contact)}): {_short_context(case.description, 100) or 'description not attached yet'}"


def _prayer_subject_name(prayer: PrayerRequest) -> Optional[str]:
    if prayer.member:
        return prayer.member.full_name
    submitted_by = _clean(prayer.submitted_by)
    if not submitted_by or submitted_by.lower() in {"pastor", "prayer request", "this request"}:
        return None
    return submitted_by


def _prayer_subject_label(prayer: PrayerRequest) -> str:
    name = _prayer_subject_name(prayer)
    if name:
        return name
    return "this private prayer request" if prayer.is_private else "this prayer request"


def _prayer_line(prayer: PrayerRequest) -> str:
    name = _prayer_subject_name(prayer) or ("Private prayer request" if prayer.is_private else "Prayer request")
    privacy = "private, " if prayer.is_private else ""
    return f"{name} ({privacy}updated {_date_label(prayer.updated_at)}): {_short_context(prayer.request_text, 100)}"


def _visitor_line(visitor: Visitor) -> str:
    status = []
    if not visitor.follow_up_day1_sent:
        status.append("day-one")
    if not visitor.follow_up_day3_sent:
        status.append("day-three")
    if not visitor.follow_up_week2_sent:
        status.append("week-two")
    return f"{visitor.full_name} (visited {_date_label(visitor.visit_date)}; next follow-up: {', '.join(status) if status else 'complete'}): {_short_context(visitor.notes, 100) or 'no notes'}"


def _care_desk_item(case: CareNote) -> DeskItem:
    name = case.member.full_name if case.member else "Care case"
    category = _enum_value(case.category)
    return DeskItem(
        id=f"care-{case.id}",
        type="care",
        title=name,
        subtitle=f"{_label(category)} care",
        detail=case.description,
        priority="high" if category in {"hospital", "crisis"} else "medium",
        action="Draft care follow-up",
        source="care",
        related_id=case.id,
    )


def _prayer_desk_item(prayer: PrayerRequest) -> DeskItem:
    name = _prayer_subject_name(prayer) or ("Private prayer request" if prayer.is_private else "Prayer request")
    return DeskItem(
        id=f"prayer-{prayer.id}",
        type="prayer",
        title=name,
        subtitle="Private prayer request" if prayer.is_private else "Prayer request",
        detail=prayer.request_text,
        priority="high" if prayer.is_private else "medium",
        action="Draft prayer follow-up",
        source="prayer",
        related_id=prayer.id,
    )


def _visitor_desk_item(visitor: Visitor) -> DeskItem:
    return DeskItem(
        id=f"visitor-{visitor.id}",
        type="visitor",
        title=visitor.full_name,
        subtitle=f"Visited {_date_label(visitor.visit_date)}",
        detail=visitor.notes,
        priority="high" if not visitor.follow_up_day1_sent else "medium",
        action="Draft welcome note",
        source="visitors",
        related_id=visitor.id,
    )


def _enum_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def _looks_like_profile_context(lower: str) -> bool:
    return _mentions(lower, [
        "my church",
        "our church",
        "i serve at",
        "i pastor at",
        "pastor at",
        "i am pastor",
        "i'm pastor",
        "we are a",
        "we're a",
        "we are an",
        "we're an",
        "we are in",
        "we're in",
        "our church serves",
        "we serve",
        "our community",
        "church context",
        "church tradition",
        "faith tradition",
        "denomination",
        "denominational",
        "baptist",
        "methodist",
        "presbyterian",
        "wesleyan",
        "pentecostal",
        "lutheran",
        "anglican",
        "reformed",
        "charismatic",
        "insider language",
        "ministry language",
        "neighborhood church",
        "tired volunteers",
        "we use",
        "our stack",
        "our systems",
        "we have",
        "we struggle with",
        "biggest pain",
        "biggest burden",
        "first priority",
        "ministry priority",
        "my priority",
        "our priority",
        "my goal",
        "our goal",
        "a win would be",
        "tools",
        "weekly attendance",
        "we average",
        "weekly rhythm",
        "sermon prep",
        "staff meeting",
        "day off",
        "sabbath",
        "office hours",
        "protect ",
        "guardrail",
        "approval",
        "ask me before",
        "bi-vocational",
        "bivocational",
        "my role",
        "solo pastor",
        "lead pastor",
    ])


def _looks_like_visitor_update(lower: str) -> bool:
    if _looks_like_generic_visitor_context(lower):
        return False
    return _mentions(lower, ["visitor", "guest", "first time", "first-time", "new family", "came sunday", "came to church", "visited our church", "visited church"])


def _looks_like_generic_visitor_context(lower: str) -> bool:
    has_group_language = _mentions(lower, ["guests", "visitors", "new families", "first-time guests", "first time guests"])
    if not has_group_language:
        return False
    has_specific_action = _mentions(lower, [
        "log ",
        "add ",
        "save ",
        "new visitor",
        "came sunday",
        "came to church",
        "visited our church",
        "visited church",
        "asked about",
        "email",
        "@",
        "phone",
        "called",
        "texted",
        "followed up",
    ])
    return not has_specific_action


def _looks_like_prayer_update(lower: str) -> bool:
    if _generic_prayer_request_prompt_requested(lower):
        return False
    if "prayed with" in lower and _looks_like_contact_update(lower):
        return False
    return _mentions(lower, ["prayer", "pray for", "praying for", "asked for prayer", "prayer request"])


def _generic_prayer_request_prompt_requested(lower: str) -> bool:
    normalized = re.sub(r"\s+", " ", lower.strip(" .!?")).strip()
    generic_prompts = {
        "add a prayer request",
        "add prayer request",
        "add a private prayer request",
        "add private prayer request",
        "log a prayer request",
        "log prayer request",
        "save a prayer request",
        "save prayer request",
        "new prayer request",
    }
    if normalized in generic_prompts:
        return True
    if not _mentions(normalized, ["add", "log", "save", "new"]) or not _mentions(normalized, ["prayer request"]):
        return False
    has_detail = any(marker in lower for marker in [":", " asked ", " ask ", " for ", " about ", " because ", " diagnosis", " surgery", " grief", " job "])
    return not has_detail


def _generic_care_case_prompt_requested(lower: str) -> bool:
    normalized = re.sub(r"\s+", " ", lower.strip(" .!?")).strip()
    generic_prompts = {
        "add a care case",
        "add care case",
        "open a care case",
        "open care case",
        "log a care case",
        "log care case",
        "save a care case",
        "save care case",
        "new care case",
        "what should i capture for a care case",
        "what should i record for a care case",
        "what do you need for a care case",
        "how do you handle care cases",
        "how should i handle care cases",
    }
    if normalized in generic_prompts:
        return True
    if not _mentions(normalized, ["add", "open", "log", "save", "new"]) or not _mentions(normalized, ["care case"]):
        return False
    has_detail = any(marker in lower for marker in [":", " grieving ", " grief ", " hospital", " surgery", " crisis", " hospice", " diagnosis", " death", " died", " needs "])
    return not has_detail


def _looks_like_care_case(lower: str) -> bool:
    if _generic_care_case_prompt_requested(lower):
        return False
    return _mentions(lower, ["hospital", "surgery", "icu", "hospice", "grief", "grieving", "death", "died", "loss", "funeral", "crisis", "rehab"])


def _looks_like_contact_update(lower: str) -> bool:
    return _mentions(lower, [
        "called",
        "texted",
        "visited",
        "visiting",
        "met with",
        "checked on",
        "checked in on",
        "checked in with",
        "followed up",
        "prayed with",
        "sat with",
        "had coffee with",
        "got back from",
        "dropped by",
        "saw ",
    ])


def _looks_like_member_note(lower: str) -> bool:
    return _mentions(lower, [
        "remember",
        "prefers",
        "preference",
        "likes",
        "does not like",
        "phone calls",
        "texts",
        "job",
        "health",
        "family",
        "marriage",
        "moving",
        "anxious",
        "struggling",
        "lost",
        "diagnosed",
        "needs help",
        "follow up",
    ])


def _save_visitor_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, person_name: str) -> dict:
    first, last = _split_person_name(person_name)
    email = _email_from_text(message)
    phone = _phone_from_text(message)
    visitor = Visitor(
        account_id=_account_id(account),
        first_name=first,
        last_name=last,
        email=email,
        phone=phone,
        visit_date=_infer_pastoral_date(message.lower()),
        source="assistant chat",
        notes=message,
        follow_up_day1_sent=False,
        follow_up_day3_sent=False,
        follow_up_week2_sent=False,
    )
    db.add(visitor)
    db.flush()
    welcome_action = queue_visitor_welcome_action(db, visitor, account)
    retire_data_seed_actions(db, account, reason="visitor_created_from_chat", related_type="visitor", related_id=visitor.id)
    db.commit()
    db.refresh(visitor)
    if welcome_action:
        db.refresh(welcome_action)
    item = DeskItem(id=f"visitor-{visitor.id}", type="visitor", title=visitor.full_name, subtitle="Visitor follow-up", detail=message, priority="high", action="Draft welcome note", source="visitor", related_id=visitor.id)
    actions = [item]
    if welcome_action:
        actions.append(_desk_item_from_action(welcome_action))
    contact_detail = " I saved the contact details too." if email or phone else ""
    return {
        "intent": "visitor_logged",
        "saved": True,
        "reply": f"I logged {visitor.full_name} as a visitor and queued a welcome draft for your review.{contact_detail} I will not send it without approval.",
        "actions": actions,
        "suggested_prompts": ["Show my approvals.", "Who else needs attention?"],
    }


def _save_prayer_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, person_name: Optional[str]) -> dict:
    member = _find_member_by_name(db, account, person_name)
    prayer = PrayerRequest(
        account_id=_account_id(account),
        member_id=member.id if member else None,
        submitted_by=None if member else person_name,
        request_text=message,
        is_private=True,
        status="active",
    )
    db.add(prayer)
    db.flush()
    retire_data_seed_actions(db, account, reason="prayer_created_from_chat", related_type="prayer", related_id=prayer.id)
    db.commit()
    db.refresh(prayer)
    name = member.full_name if member else (person_name or "this request")
    if name in {"this request", "Pastor"}:
        item_title = "Private prayer request"
        prompts = ["Draft a private prayer follow-up from this request.", "What should I capture for private prayer?"]
        reply = (
            "I logged that as a private prayer request. I do not have a person attached yet, so I will keep it as a request-level follow-up "
            "without putting it in a public list."
        )
    else:
        item_title = name
        prompts = [f"Draft a prayer follow-up for {name}.", f"What do you know about {name}?"]
        reply = f"I logged that as a private prayer request for {name}. I will keep it visible for follow-up without putting it in a public list."
    item = DeskItem(id=f"prayer-{prayer.id}", type="prayer", title=item_title, subtitle="Private prayer request", detail=message, priority="medium", action="Check back later", source="prayer", related_id=prayer.id)
    return {
        "intent": "prayer_logged",
        "saved": True,
        "reply": reply,
        "actions": [item],
        "suggested_prompts": prompts,
    }


def _save_care_case_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, lower: str, person_name: Optional[str]) -> dict:
    member = _find_member_by_name(db, account, person_name)
    if not member:
        if not person_name:
            return _queue_unmatched_pastoral_update(db, account, "care_review", "Review care update", person_name, message)
        first, last = _split_person_name(person_name)
        member = Member(
            account_id=_account_id(account),
            first_name=first,
            last_name=last,
            email=_email_from_text(message),
            phone=_phone_from_text(message),
        )
        db.add(member)
        db.flush()
    category = _care_category_from_text(lower)
    care = CareNote(
        account_id=_account_id(account),
        member_id=member.id,
        category=category,
        description=message,
        last_contact=None,
        status="active",
    )
    db.add(care)
    db.flush()
    retire_data_seed_actions(db, account, reason="care_case_created_from_chat", related_type="care", related_id=care.id)
    db.commit()
    db.refresh(care)
    item = DeskItem(id=f"care-{care.id}", type="care", title=member.full_name, subtitle=category.title(), detail=message, priority="high", action="Plan care follow-up", source="care", related_id=care.id)
    return {
        "intent": "care_case_logged",
        "saved": True,
        "reply": f"I opened a {category} care case for {member.full_name}. I will keep it on the care board until there is a clear follow-up.",
        "actions": [item],
        "suggested_prompts": [f"Draft a care follow-up for {member.full_name}.", f"Log that I visited {member.full_name} today.", f"Where can I fit a visit with {member.full_name}?"],
    }


def _save_contact_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, person_name: Optional[str]) -> dict:
    member = _find_member_by_name(db, account, person_name)
    if not member:
        return _queue_unmatched_pastoral_update(db, account, "contact_review", "Review pastoral contact", person_name, message)
    care = (
        scoped_query(db.query(CareNote), CareNote, account)
        .filter(CareNote.member_id == member.id, CareNote.status == "active")
        .order_by(CareNote.created_at.desc())
        .first()
    )
    if care:
        care.last_contact = date.today()
        existing = care.description or ""
        care.description = f"{existing}\n\n[{date.today().isoformat()}] {message}".strip()
        db.commit()
        db.refresh(care)
        item = DeskItem(id=f"care-{care.id}", type="care", title=member.full_name, subtitle="Contact logged", detail=message, priority="medium", action="Follow-up timer reset", source="care", related_id=care.id)
        reply = (
            f"I logged that contact for {member.full_name} and reset the care follow-up timer. "
            "If you want, I can keep the next check-in from slipping by with a local reminder."
        )
        prompts = [
            f"Remind me to check on {member.full_name} next week.",
            f"What do you know about {member.full_name}?",
            f"Draft a care follow-up for {member.full_name}.",
        ]
    else:
        note = MemberNote(account_id=_account_id(account), member_id=member.id, note_text=message, context_tag="followup")
        db.add(note)
        db.commit()
        db.refresh(note)
        item = DeskItem(id=f"note-{note.id}", type="member_note", title=member.full_name, subtitle="Pastoral note", detail=message, priority="low", action="Remember this", source="member_note", related_id=note.id)
        reply = f"I saved that pastoral note for {member.full_name}. There was no active care case, so I kept it in their record."
        prompts = [
            f"Remind me to check on {member.full_name} next week.",
            f"What do you know about {member.full_name}?",
            f"Draft a care follow-up for {member.full_name}.",
        ]
    return {
        "intent": "pastoral_contact_logged",
        "saved": True,
        "reply": reply,
        "actions": [item],
        "suggested_prompts": prompts,
    }


def _save_member_note_from_chat(db: Session, account: Optional[ChurchAccount], profile, message: str, lower: str, person_name: Optional[str]) -> dict:
    member = _find_member_by_name(db, account, person_name)
    if not member:
        return _queue_unmatched_pastoral_update(db, account, "member_note_review", "Review member note", person_name, message)
    tag = _context_tag_from_text(lower)
    note = MemberNote(account_id=_account_id(account), member_id=member.id, note_text=message, context_tag=tag)
    db.add(note)
    db.commit()
    db.refresh(note)
    item = DeskItem(id=f"note-{note.id}", type="member_note", title=member.full_name, subtitle=tag.title(), detail=message, priority="low", action="Remember this", source="member_note", related_id=note.id)
    return {
        "intent": "member_note_logged",
        "saved": True,
        "reply": f"I saved that note for {member.full_name} under {tag}. I will use it for future care nudges.",
        "actions": [item],
        "suggested_prompts": [f"What do you know about {member.full_name}?", f"Draft a care follow-up for {member.full_name}.", "Who else needs attention?"],
    }


def _queue_unmatched_pastoral_update(db: Session, account: Optional[ChurchAccount], action_type: str, title: str, person_name: Optional[str], message: str) -> dict:
    display = person_name or "this person"
    action = AssistantAction(
        account_id=_account_id(account),
        action_type=action_type,
        status="pending",
        title=f"{title}: {display}",
        description=message,
        payload_json=_json_dumps({"person_name": person_name, "message": message}),
        source="assistant_chat",
        privacy_level="pastoral",
    )
    db.add(action)
    db.flush()
    _audit(db, "assistant_action.created_from_chat", f"Queued unmatched chat update: {action.title}", account=account, action_id=action.id, payload={"action_type": action_type})
    db.commit()
    db.refresh(action)
    return {
        "intent": action_type,
        "saved": True,
        "reply": f"I understood this as a pastoral update about {display}, but I could not match the person confidently. I queued it for review instead of writing it onto the wrong record.",
        "actions": [_desk_item_from_action(action)],
        "suggested_prompts": (
            ["Show my approvals.", f"Help me add {person_name} as a person."]
            if person_name
            else ["Show my approvals.", "What should I capture for a person?"]
        ),
    }


def _find_member_by_name(db: Session, account: Optional[ChurchAccount], person_name: Optional[str]) -> Optional[Member]:
    if not person_name:
        return None
    parts = [part for part in re.split(r"\s+", person_name.strip()) if part]
    if not parts:
        return None
    query = scoped_query(db.query(Member), Member, account)
    if len(parts) >= 2:
        member = query.filter(Member.first_name.ilike(parts[0]), Member.last_name.ilike(parts[-1])).first()
        if member:
            return member
    return query.filter(Member.first_name.ilike(parts[0])).first()


def _guess_person_name(message: str) -> Optional[str]:
    stop = {
        "I",
        "A",
        "An",
        "We",
        "Our",
        "The",
        "This",
        "Marge",
        "Pastor",
        "Church",
        "Community",
        "Neighborhood",
        "Apartment",
        "Families",
        "Family",
        "Volunteers",
        "People",
        "Visitor",
        "Visitors",
        "Guest",
        "Guests",
        "Sunday",
        "Sundays",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Google",
        "Planning",
        "Center",
        "Rock",
        "Breeze",
        "Gmail",
        "Outlook",
        "Help",
        "Log",
        "Add",
        "Save",
        "Remind",
        "Reminder",
        "Remember",
        "Call",
        "Text",
        "New",
        "First",
        "Real",
        "First-time",
        "First-Time",
    }
    patterns = [
        r"(?:remind me to|set a reminder to|queue a reminder to|help me remember to|nudge me to)\s+(?:call|text|visit|follow up with|check on|pray for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:log|add|save)\s+(?:the\s+)?(?:first\s+real\s+)?(?:visitor|guest|new family|first[- ]time visitor)\s*[:,-]?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:first\s+(?:real\s+)?visitor|first\s+(?:real\s+)?guest)\s*[:,-]?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:called|texted|visited|visiting|met with|checked on|checked in on|checked in with|followed up with|prayed with|sat with|had coffee with|dropped by to see|saw|pray for|prayer for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:got back from|just got back from|came back from)\s+(?:visiting|seeing|checking on|checking in with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:visitor|guest|new family|first[- ]time visitor)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is|has|had|asked|needs|came|visited)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            candidate = match.group(1).strip()
            if candidate.split()[0] not in stop:
                return candidate
    words = [word.strip(" ,.!?:;()[]") for word in message.split()]
    caps = [word for word in words if len(word) > 1 and word[:1].isupper() and word not in stop]
    if len(caps) >= 2:
        return f"{caps[0]} {caps[1]}"
    if caps:
        return caps[0]
    return None


def _split_person_name(person_name: str) -> tuple[str, str]:
    parts = [part for part in (person_name or "").split() if part]
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def _infer_pastoral_date(lower: str) -> date:
    if "today" in lower:
        return date.today()
    if "yesterday" in lower:
        return date.today() - timedelta(days=1)
    return date.today() - timedelta(days=1)


def _care_category_from_text(lower: str) -> str:
    if _mentions(lower, ["hospital", "surgery", "icu", "rehab"]):
        return "hospital"
    if _mentions(lower, ["grief", "grieving", "death", "died", "loss", "funeral", "hospice"]):
        return "grief"
    if _mentions(lower, ["crisis", "emergency"]):
        return "crisis"
    return "general"


def _context_tag_from_text(lower: str) -> str:
    if _mentions(lower, ["prefers", "preference", "likes", "does not like", "phone calls", "texts"]):
        return "preference"
    if _mentions(lower, ["grief", "grieving", "death", "died", "loss", "funeral"]):
        return "grief"
    for tag in ["job", "health", "family", "grief", "prayer", "hospital", "financial", "marriage"]:
        if tag in lower:
            return tag
    if "follow" in lower:
        return "followup"
    return "general"


def _strip_intro(message: str) -> str:
    cleaned = message.strip()
    for prefix in ["my church is ", "church is ", "i serve at ", "call me ", "you can call me ", "my name is ", "i am ", "i'm "]:
        if cleaned.lower().startswith(prefix):
            return cleaned[len(prefix):].strip()
    return cleaned


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _clean_email(value) -> Optional[str]:
    cleaned = _clean(value)
    if not cleaned:
        return None
    _, address = parseaddr(cleaned)
    address = (address or "").strip().lower()
    if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", address):
        return None
    return address


def _profile_pastor_name(profile: PastorProfile) -> str:
    return profile.pastor_name or os.getenv("PASTOR_NAME", "Pastor")


def _profile_church_name(profile: PastorProfile) -> str:
    return profile.church_name or os.getenv("CHURCH_NAME", "your church")


def _profile_greeting(profile: PastorProfile, briefing: dict) -> str:
    pastor = pastor_display_name(_profile_pastor_name(profile))
    church = _profile_church_name(profile)
    if not _profile_is_complete(profile):
        return f"Good morning, {pastor}. Let's make Marge fit {church}."
    has_people_context = any(
        briefing.get(key)
        for key in [
            "birthdays_this_week",
            "anniversaries_this_week",
            "visitors_needing_followup",
            "active_care_cases",
            "absent_members",
            "unanswered_prayers",
            "nudges",
        ]
    )
    if not has_people_context:
        return f"Good morning, {pastor}. Let's add the first real people Marge should keep in view for {church}."
    return briefing.get("greeting") or f"Good morning, {pastor}."


def _briefing_for_mode(db: Session, profile: PastorProfile, mode: str, account: Optional[ChurchAccount] = None) -> tuple[str, dict]:
    stats = _live_context_counts(db, account)
    has_live_data = any(stats.values())
    if mode == "demo" or (mode == "auto" and not account and not has_live_data):
        return "demo", build_demo_briefing(_briefing_pastor_name(profile), _profile_church_name(profile))
    return "live", generate_morning_briefing(db, _briefing_pastor_name(profile), _profile_church_name(profile), account_id=_account_id(account))


def _live_context_counts(db: Session, account: Optional[ChurchAccount] = None) -> dict:
    return {
        "members": scoped_query(db.query(Member), Member, account).count(),
        "visitors": scoped_query(db.query(Visitor), Visitor, account).count(),
        "care_cases": scoped_query(db.query(CareNote), CareNote, account).count(),
        "prayer_requests": scoped_query(db.query(PrayerRequest), PrayerRequest, account).count(),
    }


def _needs_seed_context(db: Session, account: Optional[ChurchAccount], profile, effective_mode: str) -> bool:
    if effective_mode != "live" or not account or not _profile_is_complete(profile):
        return False
    if any(_live_context_counts(db, account).values()):
        return False
    return bool(_clean(profile.followup_pain) or _clean(profile.church_context))


def _seed_context_step(
    db: Session,
    account: Optional[ChurchAccount],
    profile,
    effective_mode: str,
    user: Optional[AccountUser] = None,
) -> Optional[DeskItem]:
    integrations = _integration_statuses(db, account, user)
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
    return next((step for step in setup_steps if step.type == "data_seed"), None)


def _briefing_pastor_name(profile: PastorProfile) -> str:
    name = _profile_pastor_name(profile).strip()
    return name[7:].strip() if name.lower().startswith("pastor ") else name


def _priority_items(briefing: dict) -> List[DeskItem]:
    items: List[DeskItem] = []
    for care in briefing.get("active_care_cases", []):
        name = _field(care, "member_name") or "Name not linked"
        category = _field(care, "category") or "general"
        last_contact = _field(care, "last_contact")
        action = f"Last contact {_date_label(last_contact)}" if last_contact else "No contact logged"
        items.append(DeskItem(
            id=f"care-{_field(care, 'id')}",
            type="care",
            title=name,
            subtitle=f"{_label(category)} care",
            detail=_field(care, "description"),
            priority="high" if not last_contact else "medium",
            action=action,
            source="care",
            related_id=_field(care, "id"),
        ))
    for visitor in briefing.get("visitors_needing_followup", []):
        items.append(DeskItem(
            id=f"visitor-{_field(visitor, 'id')}",
            type="visitor",
            title=_field(visitor, "full_name") or "Visitor name not linked",
            subtitle=f"Visited {_date_label(_field(visitor, 'visit_date'))}",
            detail=_field(visitor, "notes"),
            priority="high",
            action="Draft welcome note",
            source="visitors",
            related_id=_field(visitor, "id"),
        ))
    for prayer in briefing.get("unanswered_prayers", []):
        items.append(DeskItem(
            id=f"prayer-{_field(prayer, 'id')}",
            type="prayer",
            title=_field(prayer, "submitted_by") or "Prayer request",
            subtitle="Private prayer" if _field(prayer, "is_private") else "Prayer follow-up",
            detail=_field(prayer, "request_text"),
            priority="high" if _field(prayer, "is_private") else "medium",
            action="Check in on this request",
            source="prayer",
            related_id=_field(prayer, "id"),
        ))
    for member in briefing.get("absent_members", []):
        items.append(DeskItem(
            id=f"absence-{_field(member, 'id')}",
            type="absence",
            title=_field(member, "full_name") or "Member name not linked",
            subtitle=f"Last attended {_date_label(_field(member, 'last_attendance'))}",
            detail="Gentle absence check-in",
            priority="medium",
            action="Draft check-in",
            source="attendance",
            related_id=_field(member, "id"),
        ))
    return items


def _email_drafts(briefing: dict, priorities: List[DeskItem]) -> List[DeskItem]:
    drafts: List[DeskItem] = []
    for item in priorities:
        if item.type == "visitor":
            title = "Visitor welcome"
            action = "Review welcome email"
        elif item.type == "care":
            title = "Care update reply"
            action = "Review care note"
        elif item.type == "prayer":
            title = "Prayer follow-up"
            action = "Review prayer reply"
        elif item.type == "absence":
            title = "Absence check-in"
            action = "Review check-in"
        else:
            continue
        drafts.append(DeskItem(
            id=f"draft-{item.id}",
            type="email_draft",
            title=title,
            subtitle=item.title,
            detail=item.detail or item.subtitle,
            priority=item.priority,
            action=action,
            source=item.source,
            related_id=item.related_id,
        ))
        if len(drafts) >= 5:
            break
    return drafts


def _calendar_blocks(profile: PastorProfile, priorities: List[DeskItem]) -> List[DeskItem]:
    blocks = []
    if priorities:
        first = priorities[0]
        blocks.append(_priority_calendar_block(first))
    if _clean(profile.weekly_rhythm):
        blocks.append(DeskItem(
            id="calendar-protected-work",
            type="calendar_block",
            title="Protected ministry work",
            subtitle="Sermon prep, prayer, or quiet follow-up",
            detail=profile.weekly_rhythm,
            priority="medium",
            action="Protect before accepting more meetings",
            source="calendar",
        ))
    return blocks


def _priority_calendar_block(item: DeskItem) -> DeskItem:
    if item.type == "visitor":
        title = "Visitor follow-up window"
        detail = f"Review the welcome draft for {item.title} and make space for a short follow-up while the visit is fresh."
        action = "Protect time for visitor follow-up"
    elif item.type == "prayer":
        title = "Prayer follow-up window"
        detail = f"Make space to check in on {item.title} without exposing private prayer context."
        action = "Protect time for prayer follow-up"
    elif item.type == "absence":
        title = "Absence check-in window"
        detail = f"Make room for a gentle check-in with {item.title}."
        action = "Protect time for an absence check-in"
    else:
        title = "Care follow-up window"
        detail = f"Make room for {item.title}: {item.detail or item.subtitle or 'pastoral care follow-up'}."
        action = "Propose a protected visit/call window"
    return DeskItem(
        id=f"calendar-{item.id}",
        type="calendar_block",
        title=title,
        subtitle=f"Use this for {item.title}",
        detail=detail,
        priority=item.priority,
        action=action,
        source="calendar",
    )


def _approval_items(email_drafts: List[DeskItem], calendar_blocks: List[DeskItem]) -> List[DeskItem]:
    approvals = []
    for item in email_drafts[:3]:
        approvals.append(DeskItem(
            id=f"approval-{item.id}",
            type="approval",
            title=item.title,
            subtitle=item.subtitle,
            detail="Needs pastor review before anything is sent.",
            priority=item.priority,
            action="Review",
            source=item.source,
        ))
    for item in calendar_blocks[:2]:
        approvals.append(DeskItem(
            id=f"approval-{item.id}",
            type="approval",
            title=item.title,
            subtitle=item.subtitle,
            detail="Needs pastor approval before creating or changing a calendar event.",
            priority=item.priority,
            action="Approve time",
            source="calendar",
        ))
    return approvals


def _pending_approval_items(db: Session, account: Optional[ChurchAccount] = None, limit: int = 8) -> List[DeskItem]:
    actions = _pending_assistant_actions(db, account, limit)
    return [
        DeskItem(
            id=f"action-{action.id}",
            type="approval",
            title=action.title,
            subtitle=_label(action.status),
            detail=action.description,
            priority="high" if action.status == "approved" else "medium",
            action="Execute approved" if action.status == "approved" else "Review",
            source=action.source or "assistant_action",
            related_id=action.id,
        )
        for action in actions
    ]


def _pending_assistant_actions(db: Session, account: Optional[ChurchAccount] = None, limit: int = 8) -> List[AssistantAction]:
    actions = (
        scoped_query(db.query(AssistantAction), AssistantAction, account)
        .filter(AssistantAction.status.in_(["pending", "approved"]))
        .order_by(AssistantAction.updated_at.desc().nullslast(), AssistantAction.created_at.desc())
        .limit(max(limit * 4, 25))
        .all()
    )
    return sorted(actions, key=_assistant_action_priority_key)[:limit]


def _assistant_action_priority_key(action: AssistantAction) -> tuple:
    status_priority = 0 if action.status == "approved" else 1
    type_priority = {
        "email_draft": 0,
        "calendar_block": 0,
        "pastoral_followup": 0,
        "email_triage": 1,
        "person_review": 1,
        "meeting_prep": 1,
        "data_seed": 2,
        "integration_setup": 3,
        "profile_question": 4,
        "first_week_plan": 5,
    }.get(action.action_type or "", 3)
    updated = action.updated_at or action.created_at
    timestamp = updated.timestamp() if updated else 0
    return (status_priority, type_priority, -timestamp, action.id or 0)


def _desk_stats(briefing: dict, email_drafts: List[DeskItem], calendar_blocks: List[DeskItem]) -> dict:
    attention = (
        len(briefing.get("active_care_cases", []))
        + len(briefing.get("visitors_needing_followup", []))
        + len(briefing.get("unanswered_prayers", []))
        + len(briefing.get("absent_members", []))
    )
    return {
        "attention": attention,
        "drafts": len(email_drafts),
        "calendar_blocks": len(calendar_blocks),
        "connectors": 0,
        "care_cases": len(briefing.get("active_care_cases", [])),
        "visitors": len(briefing.get("visitors_needing_followup", [])),
        "prayers": len(briefing.get("unanswered_prayers", [])),
    }


def _sync_rock_rms(db: Session, account: Optional[ChurchAccount] = None) -> IntegrationSyncResponse:
    synced_at = datetime.utcnow()
    config = _rock_api_config(db, account)
    connection = _get_or_create_connection(db, "rock", "Rock RMS", "env_api_key", account)
    missing_config = _rock_missing_config(config)
    if missing_config:
        message = "Rock RMS sync is disabled until a Rock API key and API base URL are configured."
        connection.status = "needs_configuration"
        connection.config_hint = message
        connection.error_message = message
        _audit(
            db,
            "integration.sync_skipped",
            "Rock RMS sync skipped because server configuration is missing.",
            provider="rock",
            account=account,
            payload={"reason": "missing_server_config", "missing_config": missing_config},
        )
        db.commit()
        return IntegrationSyncResponse(
            provider="rock",
            status="needs_configuration",
            synced_at=synced_at,
            items_seen=0,
            items_created=0,
            items_updated=0,
            actions_prepared=0,
            message=message,
        )
    _require_verified_for_sync(db, "rock", account, credential=_provider_credential(db, "rock", account, None))
    result = rock_sync.run_full_sync(
        db,
        account_id=_account_id(account),
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
    )

    if not result.get("rock_sync_enabled"):
        message = result.get("message") or "Rock RMS sync is disabled until a Rock API key and API base URL are configured."
        connection.status = "needs_configuration"
        connection.config_hint = message
        connection.error_message = message
        _audit(
            db,
            "integration.sync_skipped",
            "Rock RMS sync skipped because server configuration is missing.",
            provider="rock",
            account=account,
            payload={"reason": "missing_server_config"},
        )
        db.commit()
        return IntegrationSyncResponse(
            provider="rock",
            status="needs_configuration",
            synced_at=synced_at,
            items_seen=0,
            items_created=0,
            items_updated=0,
            actions_prepared=0,
            message=message,
        )

    members = result.get("members") or {}
    attendance = result.get("attendance") or {}
    member_created = _stat_value(members, "created")
    member_updated = _stat_value(members, "updated")
    member_skipped = _stat_value(members, "skipped")
    attendance_updated = _stat_value(attendance, "updated")
    attendance_not_found = _stat_value(attendance, "not_found")
    items_seen = member_created + member_updated + member_skipped + attendance_updated + attendance_not_found
    items_updated = member_updated + attendance_updated

    profile = _get_or_create_profile(db, account)
    briefing = generate_morning_briefing(
        db,
        _briefing_pastor_name(profile),
        _profile_church_name(profile),
        account_id=_account_id(account),
    )
    absence_items = [item for item in _priority_items(briefing) if item.type == "absence"]
    prepared: List[AssistantAction] = []
    today_key = synced_at.date().isoformat()
    for item in absence_items[:5]:
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key=f"rock:absence:{item.related_id}:{today_key}",
            action_type="pastoral_followup",
            title=f"Follow up with {item.title}",
            description=item.detail or item.action or item.subtitle,
            payload={
                "desk_item": item.model_dump(mode="json"),
                "sync_source": "rock",
                "guardrail": "Review with the pastor before contacting the person or writing back to Rock.",
            },
            source="rock",
            external_provider=None,
            related_type="member",
            related_id=item.related_id,
            privacy_level="pastoral",
            account=account,
        ))

    connected_item, _created = _upsert_connected_item(
        db,
        provider="rock",
        item_type="people_attendance_sync",
        external_id=f"rock-sync:{today_key}",
        thread_id=None,
        title="Rock people and attendance synced",
        subtitle=f"{member_created} created, {items_updated} updated",
        snippet=(
            f"Members: {member_created} created, {member_updated} updated, {member_skipped} skipped. "
            f"Attendance: {attendance_updated} updated, {attendance_not_found} unmatched."
        ),
        occurred_at=synced_at,
        payload={"rock": result},
        account=account,
    )
    db.flush()
    if prepared and not connected_item.action_id:
        connected_item.action_id = prepared[0].id

    connection.status = "connected"
    connection.last_synced_at = synced_at
    connection.config_hint = "Synced Rock people and attendance into Marge's pastoral memory."
    connection.error_message = None
    _audit(
        db,
        "integration.synced",
        "Synced Rock RMS people and attendance.",
        provider="rock",
        account=account,
        connected_item_id=connected_item.id,
        payload={
            "items_seen": items_seen,
            "items_created": member_created,
            "items_updated": items_updated,
            "actions_prepared": len(prepared),
        },
    )
    db.commit()
    return IntegrationSyncResponse(
        provider="rock",
        status="synced",
        synced_at=synced_at,
        items_seen=items_seen,
        items_created=member_created,
        items_updated=items_updated,
        actions_prepared=len(prepared),
        message="Rock RMS people and attendance synced into Marge's pastoral memory.",
    )


def _verify_integration(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationVerifyResponse:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    verified_at = datetime.utcnow()
    identity: dict = {}
    credential = _provider_credential(db, provider, account, user)

    if provider == "google_workspace":
        token = _provider_access_token(db, provider, account, user)
        identity = _verify_google_workspace(token)
    elif provider == "microsoft_365":
        token = _provider_access_token(db, provider, account, user)
        identity = _verify_microsoft_365(token)
    elif provider == "planning_center":
        token = _provider_access_token(db, provider, account, user)
        identity = _verify_planning_center(token)
    elif provider == "breeze":
        config_error = _breeze_config_error(db, account)
        if config_error:
            raise HTTPException(status_code=409, detail=config_error)
        identity = _verify_breeze(db, account)
    elif provider == "rock":
        identity = _verify_rock(db, account)
    elif provider == "mcp":
        identity = {"bridge_available": True}
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{definition['display_name']} does not have a credential verification check. "
                "Credential verification is available for Google Workspace, Microsoft 365, Planning Center, Breeze, and Rock RMS. "
                "MCP is only the local agent bridge."
            ),
        )
    sensitive_keys = _sensitive_identity_keys(identity)
    if sensitive_keys:
        raise HTTPException(
            status_code=500,
            detail=f"{definition['display_name']} verification returned unsafe identity metadata: {', '.join(sensitive_keys)}.",
        )
    sensitive_values = _sensitive_identity_value_paths(identity)
    if sensitive_values:
        raise HTTPException(
            status_code=500,
            detail=f"{definition['display_name']} verification returned unsafe identity metadata values at: {', '.join(sensitive_values)}.",
        )
    if not _identity_has_signal(identity):
        raise HTTPException(
            status_code=502,
            detail=f"{definition['display_name']} verification did not confirm usable non-secret identity or permission metadata.",
        )

    if provider == "mcp":
        _audit(
            db,
            "integration.bridge_checked",
            "Checked local MCP agent bridge availability.",
            provider=provider,
            account=account,
            payload={"identity_keys": sorted(identity.keys())[:8]},
        )
        db.commit()
        return IntegrationVerifyResponse(
            provider=provider,
            status="bridge_available",
            verified_at=None,
            credential_scope=None,
            identity=identity,
            message=(
                "MCP bridge checked for local LLM clients. This is not a church-tool credential check, "
                "does not prove an external provider is connected, and did not sync ministry data."
            ),
        )

    connection = _get_or_create_connection(db, provider, definition["display_name"], definition["auth_type"], account)
    connection.status = "connected" if definition["auth_type"] == "oauth" else "configured"
    connection.verified_at = verified_at
    connection.config_hint = f"Verified {definition['display_name']} credentials without syncing ministry data."
    connection.error_message = None
    if credential:
        credential.verified_at = verified_at
    _audit(
        db,
        "integration.verified",
        f"Verified {definition['display_name']} connector credentials.",
        provider=provider,
        account=account,
        payload={"credential_scope": _credential_scope_label(credential), "identity_keys": sorted(identity.keys())[:8]},
    )
    db.commit()
    return IntegrationVerifyResponse(
        provider=provider,
        status="verified",
        verified_at=verified_at,
        credential_scope=_credential_scope_label(credential),
        identity=identity,
        message=f"{definition['display_name']} credentials verified without syncing ministry data.",
    )


def _disconnect_integration(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationDisconnectResponse:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    if definition["auth_type"] != "oauth":
        credential = _provider_credential_for_disconnect(db, provider, account, None)
        if not credential:
            env_var = definition.get("env_var")
            hint = f" Remove {env_var} from server config to disconnect it." if env_var else ""
            raise HTTPException(status_code=409, detail=f"{definition['display_name']} has no stored workspace API-key credential.{hint}")

        disconnected_at = datetime.utcnow()
        credential_scope = _credential_scope_label(credential) or "workspace"
        db.delete(credential)
        connection = _get_or_create_connection(db, provider, definition["display_name"], definition["auth_type"], account)
        if _api_key_provider_has_env(provider):
            status = "configured"
            connection.status = "configured"
            connection.verified_at = None
            connection.config_hint = f"Removed workspace API-key credential. {definition['display_name']} still has server-side config available."
            message = f"Removed the encrypted workspace API key for {definition['display_name']}. Server-side config is still available."
        else:
            status = "disconnected"
            connection.status = "needs_configuration" if provider == "rock" else "planned"
            connection.connected_at = None
            connection.verified_at = None
            connection.config_hint = "Workspace API-key credential removed. Add credentials again before syncing."
            message = f"Removed the encrypted workspace API key for {definition['display_name']}. Add credentials again before syncing."
        connection.error_message = None
        _audit(
            db,
            "integration.api_key_disconnected",
            f"Disconnected workspace API-key credentials for {definition['display_name']}.",
            provider=provider,
            account=account,
            payload={"credential_scope": credential_scope},
        )
        db.commit()
        return IntegrationDisconnectResponse(
            provider=provider,
            status=status,
            disconnected_at=disconnected_at,
            credential_scope=credential_scope,
            removed_credentials=1,
            remaining_credentials=0,
            write_enabled=False,
            message=message,
        )

    credential = _provider_credential_for_disconnect(db, provider, account, user)
    if not credential:
        if user:
            raise HTTPException(status_code=409, detail=f"{provider} is not connected for this Marge user.")
        raise HTTPException(status_code=409, detail=f"{provider} is not connected.")

    disconnected_at = datetime.utcnow()
    credential_scope = _credential_scope_label(credential)
    db.delete(credential)
    pending_state_query = scoped_query(db.query(IntegrationOAuthState), IntegrationOAuthState, account).filter(
        IntegrationOAuthState.provider == provider,
        IntegrationOAuthState.consumed_at.is_(None),
    )
    if user:
        pending_state_query = pending_state_query.filter(IntegrationOAuthState.user_id == user.id)
    else:
        pending_state_query = pending_state_query.filter(IntegrationOAuthState.user_id.is_(None))
    expired_states = pending_state_query.update({"consumed_at": disconnected_at}, synchronize_session=False)
    db.flush()

    remaining_credentials = scoped_query(db.query(IntegrationCredential), IntegrationCredential, account).filter(
        IntegrationCredential.provider == provider
    ).count()
    connection = _get_or_create_connection(db, provider, definition["display_name"], definition["auth_type"], account)
    policy = scoped_query(db.query(IntegrationPolicy), IntegrationPolicy, account).filter(IntegrationPolicy.provider == provider).first()
    if not policy:
        policy = _default_policy(provider, account)
        db.add(policy)

    if remaining_credentials:
        status = "connected"
        connection.status = "connected"
        connection.config_hint = "Disconnected this Marge user's OAuth credential. Other workspace user credentials remain connected."
        message = f"Disconnected {definition['display_name']} for this Marge user. Other workspace user credentials remain connected."
    else:
        status = "disconnected"
        connection.status = "disconnected"
        connection.connected_at = None
        connection.verified_at = None
        connection.config_hint = "Encrypted OAuth credential removed. Start setup again before syncing or writing through this connector."
        policy.write_enabled = False
        message = f"Disconnected {definition['display_name']} and removed the encrypted OAuth credential. Reconnect before syncing or writing through it."
    connection.error_message = None

    _audit(
        db,
        "integration.disconnected",
        f"Disconnected {definition['display_name']} OAuth credential.",
        provider=provider,
        account=account,
        payload={
            "credential_scope": credential_scope,
            "remaining_credentials": remaining_credentials,
            "expired_oauth_states": expired_states,
            "write_enabled": bool(policy.write_enabled),
        },
    )
    db.commit()
    return IntegrationDisconnectResponse(
        provider=provider,
        status=status,
        disconnected_at=disconnected_at,
        credential_scope=credential_scope,
        removed_credentials=1,
        remaining_credentials=remaining_credentials,
        write_enabled=bool(policy.write_enabled),
        message=message,
    )


def _save_api_key_integration(
    db: Session,
    provider: str,
    payload: IntegrationCredentialPayload,
    account: Optional[ChurchAccount] = None,
) -> IntegrationCredentialSetupResponse:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown integration provider.")
    if definition["auth_type"] not in {"api_key", "env_api_key"}:
        raise HTTPException(status_code=409, detail=f"{definition['display_name']} uses OAuth setup, not API-key setup.")

    api_key = _clean(payload.api_key)
    base_url = _clean(payload.base_url)
    if not api_key:
        raise HTTPException(status_code=422, detail="API key is required.")
    if provider in {"breeze", "rock"} and not base_url:
        raise HTTPException(status_code=422, detail=f"{definition['display_name']} base URL is required.")
    if provider in {"breeze", "rock"} and not _valid_https_base_url(base_url):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{definition['display_name']} base URL must be a full public HTTPS URL without username, "
                "password, query, or fragment before Marge stores API-key credentials."
            ),
        )
    if not encryption_key_is_configured():
        raise HTTPException(status_code=409, detail=f"{ENCRYPTION_KEY_ENV} must be set to a valid Fernet key before saving API-key credentials.")

    configured_at = datetime.utcnow()
    token_payload = {
        "kind": "api_key",
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "configured_at": configured_at.isoformat(),
    }
    ciphertext = encrypt_token_payload(token_payload)
    credential = (
        scoped_query(db.query(IntegrationCredential), IntegrationCredential, account)
        .filter(IntegrationCredential.provider == provider, IntegrationCredential.user_id.is_(None))
        .first()
    )
    if not credential:
        credential = IntegrationCredential(
            provider=provider,
            account_id=_account_id(account),
            user_id=None,
        )
        db.add(credential)
    credential.token_ciphertext = ciphertext
    credential.token_type = "api_key"
    credential.scopes = " ".join(definition["scopes"])
    credential.expires_at = None
    credential.refresh_token_present = False
    credential.verified_at = None

    connection = _get_or_create_connection(db, provider, definition["display_name"], definition["auth_type"], account)
    connection.status = "configured"
    connection.auth_type = definition["auth_type"]
    connection.scopes = credential.scopes
    connection.connected_at = configured_at
    connection.verified_at = None
    connection.config_hint = f"Workspace {definition['display_name']} API key is encrypted server-side. Verify credentials before syncing ministry data."
    connection.error_message = None
    _audit(
        db,
        "integration.api_key_configured",
        f"Configured encrypted workspace API-key credentials for {definition['display_name']}.",
        provider=provider,
        account=account,
        payload={"credential_scope": "workspace", "base_url_configured": bool(base_url)},
    )
    db.commit()
    return IntegrationCredentialSetupResponse(
        provider=provider,
        status="configured",
        credential_scope="workspace",
        configured_at=configured_at,
        message=f"{definition['display_name']} credentials were stored encrypted for this workspace. Run Check credentials before syncing.",
        secure_note="Marge encrypted the API key server-side and did not return it to the browser, chat, or audit log.",
    )


def _valid_https_base_url(raw: Optional[str]) -> bool:
    parsed = urlparse((raw or "").strip())
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and _public_connector_hostname(parsed.hostname)
    )


def _public_connector_hostname(hostname: Optional[str]) -> bool:
    cleaned = (hostname or "").strip().strip("[]").lower()
    if not cleaned or cleaned == "localhost" or cleaned.endswith(".localhost") or "." not in cleaned:
        return False
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _verify_google_workspace(token: str) -> dict:
    response = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Google Workspace verification failed with HTTP {response.status_code}.")
    data = response.json()
    return {
        "email": data.get("emailAddress"),
        "messages_total": data.get("messagesTotal"),
        "threads_total": data.get("threadsTotal"),
    }


def _verify_microsoft_365(token: str) -> dict:
    data = _microsoft_graph_get(
        token,
        "/me",
        params={"$select": "id,displayName,userPrincipalName,mail"},
    )
    return {
        "id": data.get("id"),
        "display_name": data.get("displayName"),
        "email": data.get("mail") or data.get("userPrincipalName"),
    }


def _verify_planning_center(token: str) -> dict:
    data = _planning_center_get(token, "/people/v2/me")
    row = data.get("data") if isinstance(data, dict) else None
    attributes = (row or {}).get("attributes") or {}
    return {
        "id": (row or {}).get("id"),
        "name": attributes.get("name") or " ".join(part for part in [attributes.get("first_name"), attributes.get("last_name")] if part),
        "email": attributes.get("primary_email_address"),
    }


def _verify_breeze(db: Session, account: Optional[ChurchAccount] = None) -> dict:
    rows = _as_list(_breeze_get("/people/", params={"limit": 1}, db=db, account=account))
    return {"people_access_confirmed": bool(rows)}


def _verify_rock(db: Session, account: Optional[ChurchAccount] = None) -> dict:
    config = _rock_api_config(db, account)
    missing_config = _rock_missing_config(config)
    if missing_config:
        raise HTTPException(
            status_code=409,
            detail=(
                "Add a workspace Rock API key and API base URL, or set ROCK_API_KEY and ROCK_BASE_URL "
                "server-side before verifying Rock RMS."
            ),
        )
    data = rock_sync._get(
        "People",
        params={"$top": 1, "$select": "Id"},
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
    )
    if data is None:
        raise HTTPException(status_code=502, detail="Rock RMS verification failed.")
    return {"people_access_confirmed": True}


def _sensitive_identity_keys(identity: dict) -> List[str]:
    found: set[str] = set()

    def inspect_key(path: str, value) -> None:
        normalized = path.lower().replace("-", "_")
        if any(term in normalized for term in SENSITIVE_IDENTITY_KEY_TERMS):
            found.add(path)
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                inspect_key(f"{path}.{nested_key}" if path else str(nested_key), nested_value)
        elif isinstance(value, list):
            for index, nested_value in enumerate(value[:20]):
                if isinstance(nested_value, dict):
                    inspect_key(f"{path}[{index}]", nested_value)

    for key, value in (identity or {}).items():
        inspect_key(str(key), value)
    return sorted(found)


def _sensitive_identity_value_paths(identity: dict) -> List[str]:
    found: set[str] = set()

    def inspect_value(path: str, value) -> None:
        if isinstance(value, str) and any(pattern.search(value) for pattern in SENSITIVE_IDENTITY_VALUE_PATTERNS):
            found.add(path)
        elif isinstance(value, dict):
            for nested_key, nested_value in value.items():
                inspect_value(f"{path}.{nested_key}" if path else str(nested_key), nested_value)
        elif isinstance(value, list):
            for index, nested_value in enumerate(value[:20]):
                inspect_value(f"{path}[{index}]", nested_value)

    for key, value in (identity or {}).items():
        inspect_value(str(key), value)
    return sorted(found)


def _identity_has_signal(identity: dict) -> bool:
    def value_has_signal(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (int, float)):
            return value > 0
        if isinstance(value, dict):
            return any(value_has_signal(nested) for nested in value.values())
        if isinstance(value, list):
            return any(value_has_signal(nested) for nested in value[:20])
        return bool(value)

    return isinstance(identity, dict) and any(value_has_signal(value) for value in identity.values())


def _stat_value(stats: dict, key: str) -> int:
    try:
        return int(stats.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _sync_breeze(
    db: Session,
    people_limit: int,
    calendar_days: int,
    account: Optional[ChurchAccount] = None,
) -> IntegrationSyncResponse:
    synced_at = datetime.utcnow()
    config_error = _breeze_config_error(db, account)
    connection = _get_or_create_connection(db, "breeze", "Breeze", "api_key", account)
    if config_error:
        connection.status = "needs_configuration"
        connection.config_hint = config_error
        connection.error_message = config_error
        _audit(
            db,
            "integration.sync_skipped",
            "Breeze sync skipped because server configuration is missing.",
            provider="breeze",
            account=account,
            payload={"reason": "missing_server_config"},
        )
        db.commit()
        return IntegrationSyncResponse(
            provider="breeze",
            status="needs_configuration",
            synced_at=synced_at,
            items_seen=0,
            items_created=0,
            items_updated=0,
            actions_prepared=0,
            message=config_error,
        )

    _require_verified_for_sync(db, "breeze", account, credential=_provider_credential(db, "breeze", account, None))
    stats = {"seen": 0, "created": 0, "updated": 0, "actions": 0}
    for person in _fetch_breeze_people(people_limit, db, account):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="breeze",
            item_type="person",
            external_id=person["id"],
            thread_id=None,
            title=person.get("name") or "Breeze person",
            subtitle=person.get("status") or "Person record",
            snippet=_breeze_person_snippet(person),
            occurred_at=person.get("updated_at") or person.get("created_at"),
            payload={"person": person},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if created and _breeze_person_needs_review(person) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"breeze:person_review:{person['id']}",
                action_type="person_review",
                title=f"Review Breeze person: {person.get('name') or 'Breeze person'}",
                description=_breeze_person_snippet(person),
                payload={
                    "connected_item_id": item.id,
                    "person": person,
                    "guardrail": "Review before creating local notes or follow-up work.",
                },
                source="breeze",
                external_provider=None,
                related_type="breeze_person",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    for event in _fetch_breeze_events(calendar_days, db, account):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="breeze",
            item_type="calendar_event",
            external_id=event["id"],
            thread_id=None,
            title=event.get("name") or "Breeze event",
            subtitle=event.get("when"),
            snippet=event.get("location") or event.get("description"),
            occurred_at=event.get("starts_at"),
            payload={"calendar_event": event},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if _breeze_event_needs_pastoral_prep(event) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"breeze:event_prep:{event['id']}",
                action_type="meeting_prep",
                title=f"Prepare for {event.get('name') or 'Breeze event'}",
                description=event.get("when") or event.get("location") or "Upcoming Breeze event",
                payload={
                    "connected_item_id": item.id,
                    "calendar_event": event,
                    "brief": _breeze_event_prep_text(event),
                },
                source="breeze",
                external_provider=None,
                related_type="calendar_event",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    connection.status = "configured"
    connection.last_synced_at = synced_at
    connection.config_hint = "Synced Breeze people and event context. The API key stays server-side."
    connection.error_message = None
    _audit(
        db,
        "integration.synced",
        "Synced Breeze people and event context.",
        provider="breeze",
        account=account,
        payload={
            "items_seen": stats["seen"],
            "items_created": stats["created"],
            "items_updated": stats["updated"],
            "actions_prepared": stats["actions"],
        },
    )
    db.commit()
    return IntegrationSyncResponse(
        provider="breeze",
        status="synced",
        synced_at=synced_at,
        items_seen=stats["seen"],
        items_created=stats["created"],
        items_updated=stats["updated"],
        actions_prepared=stats["actions"],
        message="Breeze people and event context synced into Marge's review queue.",
    )


def _breeze_config_error(db: Optional[Session] = None, account: Optional[ChurchAccount] = None) -> Optional[str]:
    config = _breeze_api_config(db, account) if db is not None else {"api_key": os.getenv("BREEZE_API_KEY"), "base_url": os.getenv("BREEZE_BASE_URL")}
    missing = []
    if not config.get("api_key"):
        missing.append("BREEZE_API_KEY")
    if not config.get("base_url"):
        missing.append("BREEZE_BASE_URL")
    if missing:
        return f"Add {', '.join(missing)} in secure workspace setup or server-side config to enable Breeze sync."
    if not _valid_https_base_url(config.get("base_url")):
        return "BREEZE_BASE_URL must be a full public HTTPS base URL without username, password, query, or fragment."
    return None


def _fetch_breeze_people(limit: int, db: Optional[Session] = None, account: Optional[ChurchAccount] = None) -> List[dict]:
    if limit <= 0:
        return []
    rows = _breeze_get("/people/", params={"limit": min(limit, 100), "details": 1}, db=db, account=account)
    people = []
    for row in _as_list(rows)[:limit]:
        person = _normalize_breeze_person(row)
        if person["id"]:
            people.append(person)
    return people


def _fetch_breeze_events(days: int, db: Optional[Session] = None, account: Optional[ChurchAccount] = None) -> List[dict]:
    now = datetime.utcnow()
    until = now + timedelta(days=days)
    rows = _breeze_get(
        "/events",
        params={
            "start": now.date().isoformat(),
            "end": until.date().isoformat(),
        },
        db=db,
        account=account,
    )
    events = []
    for row in _as_list(rows):
        event = _normalize_breeze_event(row)
        if event["id"]:
            events.append(event)
    return events


def _breeze_get(
    path: str,
    params: Optional[dict] = None,
    db: Optional[Session] = None,
    account: Optional[ChurchAccount] = None,
):
    config = _breeze_api_config(db, account) if db is not None else {"api_key": os.getenv("BREEZE_API_KEY"), "base_url": os.getenv("BREEZE_BASE_URL")}
    base_url = _normalize_breeze_base_url(config.get("base_url"))
    api_key = config.get("api_key") or ""
    normalized_path = path if path.startswith("/") else f"/{path}"
    response = requests.get(
        f"{base_url}/api{normalized_path}",
        headers={"Api-Key": api_key, "Accept": "application/json", "Content-Type": "application/json"},
        params=params or {},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Breeze request to {normalized_path} failed with HTTP {response.status_code}.")
    data = response.json()
    if isinstance(data, dict) and ("errors" in data or "errorCode" in data):
        raise HTTPException(status_code=502, detail=f"Breeze request to {normalized_path} returned an API error.")
    return data


def _normalize_breeze_base_url(raw: Optional[str]) -> str:
    cleaned = (raw or "").strip().rstrip("/")
    if cleaned.endswith("/api"):
        cleaned = cleaned[:-4]
    return cleaned


def _as_list(value) -> List[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ["people", "events", "data", "results"]:
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def _normalize_breeze_person(row: dict) -> dict:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    first = _clean(row.get("first_name") or details.get("first_name") or details.get("first"))
    last = _clean(row.get("last_name") or details.get("last_name") or details.get("last"))
    name = " ".join(part for part in [first, last] if part) or _clean(row.get("name"))
    status = _breeze_detail_lookup(row, details, ["status", "member status", "membership"])
    return {
        "id": str(row.get("id") or details.get("person_id") or ""),
        "name": name,
        "first_name": first,
        "last_name": last,
        "status": status,
        "email": _breeze_detail_lookup(row, details, ["email", "email address"]),
        "phone": _breeze_detail_lookup(row, details, ["mobile", "phone", "home"]),
        "created_at": _parse_breeze_datetime(row.get("created_on") or details.get("created_on")),
        "updated_at": _parse_breeze_datetime(row.get("updated_on") or row.get("modified_on") or details.get("updated_on")),
        "details": _compact_breeze_details(details),
    }


def _normalize_breeze_event(row: dict) -> dict:
    starts_at = _parse_breeze_datetime(row.get("starts_on") or row.get("start") or row.get("start_datetime"))
    ends_at = _parse_breeze_datetime(row.get("ends_on") or row.get("end") or row.get("end_datetime"))
    return {
        "id": str(row.get("id") or row.get("instance_id") or ""),
        "name": _clean(row.get("name") or row.get("title")),
        "description": _html_to_text(row.get("description") or row.get("details")),
        "location": _clean(row.get("location") or row.get("location_name")),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "when": _planning_center_event_when(starts_at, ends_at),
        "calendar": _clean(row.get("calendar") or row.get("calendar_name") or row.get("category_name")),
    }


def _breeze_detail_lookup(row: dict, details: dict, names: List[str]) -> Optional[str]:
    lower_names = {name.lower() for name in names}
    for source in [row, details]:
        for key, value in source.items():
            if str(key).lower() in lower_names:
                return _breeze_detail_value(value)
            if str(key).lower().replace("_", " ") in lower_names:
                return _breeze_detail_value(value)
    return None


def _breeze_detail_value(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ["name", "value", "response", "address", "number"]:
            if value.get(key):
                return _clean(value.get(key))
        return None
    if isinstance(value, list):
        labels = [_breeze_detail_value(item) for item in value]
        return ", ".join(label for label in labels if label) or None
    return _clean(value)


def _compact_breeze_details(details: dict) -> dict:
    result = {}
    for key, value in details.items():
        if len(result) >= 12:
            break
        label = str(key).strip()
        cleaned = _breeze_detail_value(value)
        if label and cleaned:
            result[label] = cleaned
    return result


def _parse_breeze_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = str(value).strip()
    if cleaned.startswith("0000-00-00"):
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
        try:
            return datetime.strptime(cleaned[:19] if fmt.endswith("%S") else cleaned[:10], fmt)
        except ValueError:
            continue
    return _parse_google_datetime(cleaned)


def _breeze_person_snippet(person: dict) -> str:
    parts = []
    if person.get("status"):
        parts.append(f"Status: {person['status']}")
    if person.get("email"):
        parts.append(f"Email: {person['email']}")
    if person.get("phone"):
        parts.append(f"Phone: {person['phone']}")
    return "; ".join(parts) or "Breeze person record synced for pastoral context."


def _breeze_person_needs_review(person: dict) -> bool:
    text = " ".join([person.get("status") or "", " ".join((person.get("details") or {}).values())]).lower()
    if any(term in text for term in ["visitor", "guest", "new", "prospect", "neighbor", "pending"]):
        return True
    created_at = person.get("created_at")
    return bool(created_at and created_at >= datetime.utcnow() - timedelta(days=14))


def _breeze_event_needs_pastoral_prep(event: dict) -> bool:
    text = " ".join([
        event.get("name") or "",
        event.get("description") or "",
        event.get("location") or "",
        event.get("calendar") or "",
    ]).lower()
    pastoral_terms = ["visit", "care", "hospital", "prayer", "funeral", "wedding", "counsel", "member", "visitor", "meeting", "lunch", "coffee", "small group", "class"]
    return any(term in text for term in pastoral_terms)


def _breeze_event_prep_text(event: dict) -> str:
    return (
        f"Breeze event: {event.get('name') or 'Event'}\n"
        f"When: {event.get('when') or 'Time not listed'}\n"
        f"Location: {event.get('location') or 'Location not listed'}\n"
        f"Calendar: {event.get('calendar') or 'Calendar not listed'}\n"
        f"Notes: {event.get('description') or 'No event notes synced.'}\n\n"
        "Suggested posture: decide whether this needs pastor prep, visitor follow-up, or a care note after the event.\n"
        "Guardrail: Breeze remains the system of record; Marge queues review work only."
    )


def _sync_planning_center(
    db: Session,
    people_limit: int,
    calendar_days: int,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationSyncResponse:
    credential = _provider_credential(db, "planning_center", account, user)
    token = _provider_access_token(db, "planning_center", account, user, credential=credential)
    _require_verified_for_sync(db, "planning_center", account, credential=credential)
    synced_at = datetime.utcnow()
    stats = {"seen": 0, "created": 0, "updated": 0, "actions": 0}

    for person in _fetch_planning_center_people(token, people_limit):
        stats["seen"] += 1
        title = person.get("name") or "Planning Center person"
        subtitle_parts = [part for part in [person.get("membership"), person.get("status")] if part]
        item, created = _upsert_connected_item(
            db,
            provider="planning_center",
            item_type="person",
            external_id=person["id"],
            thread_id=None,
            title=title,
            subtitle=" / ".join(subtitle_parts) or "Person record",
            snippet=_planning_center_person_snippet(person),
            occurred_at=person.get("updated_at") or person.get("created_at"),
            payload={"person": person},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if created and _planning_center_person_needs_review(person) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"planning_center:person_review:{person['id']}",
                action_type="person_review",
                title=f"Review Planning Center person: {title}",
                description=_planning_center_person_snippet(person),
                payload={
                    "connected_item_id": item.id,
                    "person": person,
                    "guardrail": "Review before creating local notes or follow-up work.",
                },
                source="planning_center",
                external_provider=None,
                related_type="planning_center_person",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    for event in _fetch_planning_center_events(token, calendar_days):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="planning_center",
            item_type="calendar_event",
            external_id=event["id"],
            thread_id=None,
            title=event.get("name") or "Planning Center event",
            subtitle=event.get("when"),
            snippet=event.get("location") or event.get("description") or event.get("recurrence_description"),
            occurred_at=event.get("starts_at"),
            payload={"calendar_event": event},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if _planning_center_event_needs_pastoral_prep(event) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"planning_center:event_prep:{event['id']}",
                action_type="meeting_prep",
                title=f"Prepare for {event.get('name') or 'Planning Center event'}",
                description=event.get("when") or event.get("location") or "Upcoming Planning Center event",
                payload={
                    "connected_item_id": item.id,
                    "calendar_event": event,
                    "brief": _planning_center_event_prep_text(event),
                },
                source="planning_center",
                external_provider=None,
                related_type="calendar_event",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    connection = _get_or_create_connection(db, "planning_center", "Planning Center", "oauth", account)
    connection.status = "connected"
    connection.last_synced_at = synced_at
    connection.config_hint = "Synced Planning Center People and Calendar context. Tokens remain encrypted server-side."
    connection.error_message = None
    _audit(
        db,
        "integration.synced",
        "Synced Planning Center people and calendar context.",
        provider="planning_center",
        account=account,
        payload={
            "items_seen": stats["seen"],
            "items_created": stats["created"],
            "items_updated": stats["updated"],
            "actions_prepared": stats["actions"],
        },
    )
    db.commit()
    return IntegrationSyncResponse(
        provider="planning_center",
        status="synced",
        synced_at=synced_at,
        items_seen=stats["seen"],
        items_created=stats["created"],
        items_updated=stats["updated"],
        actions_prepared=stats["actions"],
        message="Planning Center people and calendar context synced into Marge's review queue.",
    )


def _fetch_planning_center_people(token: str, limit: int) -> List[dict]:
    if limit <= 0:
        return []
    data = _planning_center_get(token, "/people/v2/people", params={"per_page": min(limit, 100)})
    people = []
    for row in data.get("data") or []:
        attrs = row.get("attributes") or {}
        person = {
            "id": str(row.get("id") or ""),
            "name": attrs.get("name") or " ".join(part for part in [attrs.get("first_name"), attrs.get("last_name")] if part),
            "first_name": attrs.get("first_name"),
            "last_name": attrs.get("last_name"),
            "membership": attrs.get("membership"),
            "status": attrs.get("status"),
            "birthdate": attrs.get("birthdate"),
            "anniversary": attrs.get("anniversary"),
            "created_at": _parse_google_datetime(attrs.get("created_at")),
            "updated_at": _parse_google_datetime(attrs.get("updated_at")),
        }
        if person["id"]:
            people.append(person)
    return people


def _fetch_planning_center_events(token: str, days: int) -> List[dict]:
    until = datetime.utcnow() + timedelta(days=days)
    data = _planning_center_get(
        token,
        "/calendar/v2/event_instances",
        params={"per_page": 25, "filter": "future", "order": "starts_at"},
    )
    events = []
    for row in data.get("data") or []:
        attrs = row.get("attributes") or {}
        starts_at = _parse_google_datetime(attrs.get("starts_at") or attrs.get("published_starts_at"))
        if starts_at and starts_at > until:
            continue
        ends_at = _parse_google_datetime(attrs.get("ends_at") or attrs.get("published_ends_at"))
        event = {
            "id": str(row.get("id") or ""),
            "name": attrs.get("name"),
            "description": _html_to_text(attrs.get("description")),
            "location": attrs.get("location"),
            "kind": attrs.get("kind"),
            "all_day_event": attrs.get("all_day_event"),
            "starts_at": starts_at,
            "ends_at": ends_at,
            "when": _planning_center_event_when(starts_at, ends_at),
            "recurrence": attrs.get("recurrence"),
            "recurrence_description": attrs.get("recurrence_description") or attrs.get("compact_recurrence_description"),
            "church_center_url": attrs.get("church_center_url"),
            "updated_at": _parse_google_datetime(attrs.get("updated_at")),
        }
        if event["id"]:
            events.append(event)
    return events


def _planning_center_get(token: str, path: str, params: Optional[dict] = None) -> dict:
    response = requests.get(
        f"https://api.planningcenteronline.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or {},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Planning Center request to {path} failed with HTTP {response.status_code}.")
    return response.json()


def _planning_center_person_snippet(person: dict) -> str:
    parts = []
    if person.get("membership"):
        parts.append(f"Membership: {person['membership']}")
    if person.get("status"):
        parts.append(f"Status: {person['status']}")
    if person.get("birthdate"):
        parts.append(f"Birthdate: {person['birthdate']}")
    if person.get("anniversary"):
        parts.append(f"Anniversary: {person['anniversary']}")
    return "; ".join(parts) or "Planning Center person record synced for pastoral context."


def _planning_center_person_needs_review(person: dict) -> bool:
    text = " ".join([person.get("membership") or "", person.get("status") or ""]).lower()
    if any(term in text for term in ["visitor", "guest", "new", "prospect", "pending"]):
        return True
    created_at = person.get("created_at")
    return bool(created_at and created_at >= datetime.utcnow() - timedelta(days=14))


def _planning_center_event_when(starts_at: Optional[datetime], ends_at: Optional[datetime]) -> Optional[str]:
    if not starts_at:
        return None
    if not ends_at:
        return _date_label(starts_at)
    return f"{_date_label(starts_at)} to {_date_label(ends_at)}"


def _planning_center_event_needs_pastoral_prep(event: dict) -> bool:
    text = " ".join([
        event.get("name") or "",
        event.get("description") or "",
        event.get("location") or "",
        event.get("recurrence_description") or "",
    ]).lower()
    pastoral_terms = ["visit", "care", "hospital", "prayer", "funeral", "wedding", "counsel", "member", "visitor", "meeting", "lunch", "coffee", "small group", "class"]
    return any(term in text for term in pastoral_terms)


def _planning_center_event_prep_text(event: dict) -> str:
    return (
        f"Planning Center event: {event.get('name') or 'Event'}\n"
        f"When: {event.get('when') or 'Time not listed'}\n"
        f"Location: {event.get('location') or 'Location not listed'}\n"
        f"Notes: {event.get('description') or event.get('recurrence_description') or 'No event notes synced.'}\n\n"
        "Suggested posture: decide whether this needs pastor prep, visitor follow-up, or a care note after the event.\n"
        "Guardrail: Planning Center remains the system of record; Marge queues review work only."
    )


def _html_to_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip() or None


def _sync_microsoft_365(
    db: Session,
    email_limit: int,
    calendar_days: int,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationSyncResponse:
    credential = _provider_credential(db, "microsoft_365", account, user)
    token = _provider_access_token(db, "microsoft_365", account, user, credential=credential)
    _require_verified_for_sync(db, "microsoft_365", account, credential=credential)
    synced_at = datetime.utcnow()
    stats = {"seen": 0, "created": 0, "updated": 0, "actions": 0}

    for message in _fetch_microsoft_messages(token, email_limit):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="microsoft_365",
            item_type="email",
            external_id=message["id"],
            thread_id=message.get("conversation_id"),
            title=message.get("subject") or "Outlook email needing review",
            subtitle=message.get("from"),
            snippet=message.get("snippet"),
            occurred_at=message.get("received_at"),
            payload={"email": message},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"microsoft_365:email:{message['id']}",
                action_type="email_triage",
                title=f"Review Outlook email: {message.get('subject') or 'No subject'}",
                description=_email_triage_description(message),
                payload={
                    "connected_item_id": item.id,
                    "email": message,
                    "guardrail": "Microsoft 365 mail sync is read-side context. Outlook draft creation requires a separate approved email_draft action and writeback policy; Marge never sends mail.",
                },
                source="microsoft_365",
                external_provider=None,
                related_type="email",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    for event in _fetch_microsoft_calendar_events(token, calendar_days):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="microsoft_365",
            item_type="calendar_event",
            external_id=event["id"],
            thread_id=None,
            title=event.get("subject") or "Outlook calendar event",
            subtitle=event.get("when"),
            snippet=event.get("location") or event.get("description"),
            occurred_at=event.get("start_at"),
            payload={"calendar_event": event},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if _microsoft_event_needs_pastoral_prep(event) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"microsoft_365:calendar:{event['id']}",
                action_type="meeting_prep",
                title=f"Prepare for {event.get('subject') or 'Outlook calendar event'}",
                description=event.get("when") or event.get("location") or "Upcoming Outlook calendar event",
                payload={
                    "connected_item_id": item.id,
                    "calendar_event": event,
                    "brief": _microsoft_event_prep_text(event),
                    "review_context": _meeting_review_context_for_event(db, account, event),
                },
                source="microsoft_365",
                external_provider=None,
                related_type="calendar_event",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1

    connection = _get_or_create_connection(db, "microsoft_365", "Microsoft 365", "oauth", account)
    connection.status = "connected"
    connection.last_synced_at = synced_at
    connection.config_hint = "Synced Outlook mail and calendar context. Tokens remain encrypted server-side."
    connection.error_message = None
    _audit(
        db,
        "integration.synced",
        "Synced Microsoft 365 Outlook mail and calendar context.",
        provider="microsoft_365",
        account=account,
        payload={
            "items_seen": stats["seen"],
            "items_created": stats["created"],
            "items_updated": stats["updated"],
            "actions_prepared": stats["actions"],
        },
    )
    db.commit()
    return IntegrationSyncResponse(
        provider="microsoft_365",
        status="synced",
        synced_at=synced_at,
        items_seen=stats["seen"],
        items_created=stats["created"],
        items_updated=stats["updated"],
        actions_prepared=stats["actions"],
        message="Microsoft 365 Outlook context synced into Marge's review queue.",
    )


def _fetch_microsoft_messages(token: str, limit: int) -> List[dict]:
    if limit <= 0:
        return []
    data = _microsoft_graph_get(
        token,
        "/me/messages",
        params={
            "$top": min(limit, 25),
            "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview,isRead,webLink",
            "$orderby": "receivedDateTime desc",
        },
    )
    messages = []
    for row in data.get("value") or []:
        message = {
            "id": str(row.get("id") or ""),
            "conversation_id": row.get("conversationId"),
            "from": _microsoft_sender(row),
            "subject": row.get("subject"),
            "received_at": _parse_microsoft_datetime(row.get("receivedDateTime")),
            "snippet": _clean(row.get("bodyPreview")),
            "is_read": row.get("isRead"),
            "web_link": row.get("webLink"),
        }
        if message["id"]:
            messages.append(message)
    return messages


def _fetch_microsoft_calendar_events(token: str, days: int) -> List[dict]:
    now = datetime.utcnow()
    until = now + timedelta(days=days)
    data = _microsoft_graph_get(
        token,
        "/me/calendarView",
        params={
            "startDateTime": now.isoformat(timespec="seconds") + "Z",
            "endDateTime": until.isoformat(timespec="seconds") + "Z",
            "$top": 25,
            "$select": "id,subject,bodyPreview,start,end,location,attendees,organizer,webLink,isCancelled",
            "$orderby": "start/dateTime",
        },
        headers={"Prefer": 'outlook.timezone="UTC"'},
    )
    events = []
    for row in data.get("value") or []:
        if row.get("isCancelled"):
            continue
        start = row.get("start") or {}
        end = row.get("end") or {}
        start_at = _parse_microsoft_event_datetime(start)
        end_at = _parse_microsoft_event_datetime(end)
        location = ((row.get("location") or {}).get("displayName")) or None
        event = {
            "id": str(row.get("id") or ""),
            "subject": row.get("subject"),
            "description": _clean(row.get("bodyPreview")),
            "location": location,
            "start": start,
            "end": end,
            "start_at": start_at,
            "end_at": end_at,
            "when": _microsoft_event_when(start_at, end_at),
            "attendees": _microsoft_attendees(row.get("attendees") or []),
            "organizer": _microsoft_participant(row.get("organizer")),
            "web_link": row.get("webLink"),
            "is_cancelled": bool(row.get("isCancelled")),
        }
        if event["id"]:
            events.append(event)
    return events


def _microsoft_graph_get(token: str, path: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    request_headers.update(headers or {})
    normalized_path = path if path.startswith("/") else f"/{path}"
    response = requests.get(
        f"https://graph.microsoft.com/v1.0{normalized_path}",
        headers=request_headers,
        params=params or {},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Microsoft Graph request to {normalized_path} failed with HTTP {response.status_code}.")
    return response.json()


def _microsoft_graph_post(token: str, path: str, json_body: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    request_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    normalized_path = path if path.startswith("/") else f"/{path}"
    response = requests.post(
        f"https://graph.microsoft.com/v1.0{normalized_path}",
        headers=request_headers,
        json=json_body or {},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Microsoft Graph request to {normalized_path} failed with HTTP {response.status_code}.")
    return response.json()


def _microsoft_sender(message: dict) -> Optional[str]:
    return _microsoft_address_label(((message.get("from") or {}).get("emailAddress") or {}))


def _microsoft_participant(value: Optional[dict]) -> Optional[dict]:
    if not value:
        return None
    address = value.get("emailAddress") or {}
    name = _clean(address.get("name"))
    email_address = _clean(address.get("address"))
    if not name and not email_address:
        return None
    return {"name": name, "email": email_address}


def _microsoft_attendees(attendees: List[dict]) -> List[dict]:
    result = []
    for attendee in attendees:
        participant = _microsoft_participant(attendee)
        if participant:
            result.append(participant)
    return result


def _microsoft_address_label(address: dict) -> Optional[str]:
    name = _clean(address.get("name"))
    email_address = _clean(address.get("address") or address.get("email"))
    if name and email_address:
        return f"{name} <{email_address}>"
    return name or email_address


def _parse_microsoft_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return _parse_google_datetime(cleaned)


def _parse_microsoft_event_datetime(value: Optional[dict]) -> Optional[datetime]:
    if isinstance(value, dict):
        return _parse_microsoft_datetime(value.get("dateTime"))
    return _parse_microsoft_datetime(value)


def _microsoft_event_when(starts_at: Optional[datetime], ends_at: Optional[datetime]) -> Optional[str]:
    if not starts_at:
        return None
    if not ends_at:
        return _date_label(starts_at)
    return f"{_date_label(starts_at)} to {_date_label(ends_at)}"


def _microsoft_event_needs_pastoral_prep(event: dict) -> bool:
    text = " ".join([
        event.get("subject") or "",
        event.get("description") or "",
        event.get("location") or "",
    ]).lower()
    pastoral_terms = ["visit", "care", "hospital", "prayer", "funeral", "wedding", "counsel", "member", "visitor", "meeting", "lunch", "coffee", "small group", "class"]
    return any(term in text for term in pastoral_terms)


def _microsoft_event_prep_text(event: dict) -> str:
    attendees = event.get("attendees") or []
    attendee_names = ", ".join(item.get("name") or item.get("email") for item in attendees[:5] if item.get("name") or item.get("email"))
    return (
        f"Outlook event: {event.get('subject') or 'Event'}\n"
        f"When: {event.get('when') or 'Time not listed'}\n"
        f"Location: {event.get('location') or 'Location not listed'}\n"
        f"Organizer: {(_microsoft_address_label(event.get('organizer') or {}) if isinstance(event.get('organizer'), dict) else None) or 'Organizer not listed'}\n"
        f"Attendees: {attendee_names or 'Attendees not listed'}\n"
        f"Notes: {event.get('description') or 'No event notes synced.'}\n\n"
        "Suggested posture: decide whether this needs pastor prep, visitor follow-up, or a care note after the event.\n"
        "Guardrail: Microsoft 365 calendar sync is read-side. Marge may queue prep; any Outlook calendar write requires a separate approved calendar action and church writeback policy."
    )


def _sync_google_workspace(
    db: Session,
    email_limit: int,
    calendar_days: int,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationSyncResponse:
    credential = _provider_credential(db, "google_workspace", account, user)
    token = _provider_access_token(db, "google_workspace", account, user, credential=credential)
    _require_verified_for_sync(db, "google_workspace", account, credential=credential)
    profile = _get_or_create_profile(db, account)
    synced_at = datetime.utcnow()
    stats = {"seen": 0, "created": 0, "updated": 0, "actions": 0}
    for message in _fetch_google_messages(token, email_limit):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="google_workspace",
            item_type="email",
            external_id=message["id"],
            thread_id=message.get("thread_id"),
            title=message.get("subject") or "Email needing review",
            subtitle=message.get("from"),
            snippet=message.get("snippet"),
            occurred_at=message.get("date"),
            payload={"email": message},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"google_workspace:email:{message['id']}",
                action_type="email_triage",
                title=f"Review email: {message.get('subject') or 'No subject'}",
                description=_email_triage_description(message),
                payload={
                    "connected_item_id": item.id,
                    "email": message,
                    "guardrail": "Google Workspace sync is read-side until a draft action is approved and writeback policy is enabled.",
                },
                source="google_workspace",
                external_provider=None,
                related_type="email",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1
    for event in _fetch_google_calendar_events(token, calendar_days):
        stats["seen"] += 1
        item, created = _upsert_connected_item(
            db,
            provider="google_workspace",
            item_type="calendar_event",
            external_id=event["id"],
            thread_id=None,
            title=event.get("summary") or "Calendar event",
            subtitle=event.get("when"),
            snippet=event.get("description"),
            occurred_at=event.get("start_at"),
            payload={"calendar_event": event},
            account=account,
        )
        stats["created" if created else "updated"] += 1
        if _calendar_event_needs_pastoral_prep(event) and not item.action_id:
            action = _upsert_prepared_action(
                db,
                dedupe_key=f"google_workspace:calendar:{event['id']}",
                action_type="meeting_prep",
                title=f"Prepare for {event.get('summary') or 'calendar event'}",
                description=event.get("when") or "Upcoming calendar event",
                payload={
                    "connected_item_id": item.id,
                    "calendar_event": event,
                    "brief": _connected_meeting_prep_text(profile, item, event),
                    "review_context": _meeting_review_context_for_event(db, account, event),
                },
                source="google_workspace",
                external_provider=None,
                related_type="calendar_event",
                related_id=item.id,
                privacy_level="pastoral",
                account=account,
            )
            db.flush()
            item.action_id = action.id
            stats["actions"] += 1
    connection = _get_or_create_connection(db, "google_workspace", "Google Workspace", "oauth", account)
    connection.status = "connected"
    connection.last_synced_at = synced_at
    connection.config_hint = "Synced Gmail and Calendar context. Tokens remain encrypted server-side."
    connection.error_message = None
    _audit(
        db,
        "integration.synced",
        "Synced Google Workspace context.",
        provider="google_workspace",
        account=account,
        payload={
            "items_seen": stats["seen"],
            "items_created": stats["created"],
            "items_updated": stats["updated"],
            "actions_prepared": stats["actions"],
        },
    )
    db.commit()
    return IntegrationSyncResponse(
        provider="google_workspace",
        status="synced",
        synced_at=synced_at,
        items_seen=stats["seen"],
        items_created=stats["created"],
        items_updated=stats["updated"],
        actions_prepared=stats["actions"],
        message="Google Workspace context synced into Marge's review queue.",
    )


def _fetch_google_messages(token: str, limit: int) -> List[dict]:
    if limit <= 0:
        return []
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    list_response = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={"maxResults": limit, "labelIds": "INBOX", "q": "newer_than:14d"},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not list_response.ok:
        raise HTTPException(status_code=502, detail=f"Google Gmail message list failed with HTTP {list_response.status_code}.")
    messages = list_response.json().get("messages") or []
    result = []
    for message in messages[:limit]:
        message_id = message.get("id")
        if not message_id:
            continue
        detail_response = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers=headers,
            params=[
                ("format", "metadata"),
                ("metadataHeaders", "From"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "Date"),
            ],
            timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
        )
        if not detail_response.ok:
            continue
        detail = detail_response.json()
        headers_by_name = _gmail_headers(detail)
        result.append({
            "id": detail.get("id") or message_id,
            "thread_id": detail.get("threadId") or message.get("threadId"),
            "from": headers_by_name.get("from"),
            "subject": headers_by_name.get("subject"),
            "date": _parse_email_date(headers_by_name.get("date")),
            "snippet": detail.get("snippet"),
            "label_ids": detail.get("labelIds") or [],
        })
    return result


def _fetch_google_calendar_events(token: str, days: int) -> List[dict]:
    now = datetime.utcnow()
    until = now + timedelta(days=days)
    response = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={
            "timeMin": now.isoformat(timespec="seconds") + "Z",
            "timeMax": until.isoformat(timespec="seconds") + "Z",
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 25,
        },
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Google Calendar event list failed with HTTP {response.status_code}.")
    events = []
    for event in response.json().get("items") or []:
        start = event.get("start") or {}
        start_value = start.get("dateTime") or start.get("date")
        start_at = _parse_google_datetime(start_value)
        events.append({
            "id": event.get("id"),
            "summary": event.get("summary"),
            "description": event.get("description"),
            "location": event.get("location"),
            "start": start,
            "end": event.get("end") or {},
            "start_at": start_at,
            "when": _date_label(start_at) if start_at else start_value,
            "attendees": event.get("attendees") or [],
            "html_link": event.get("htmlLink"),
        })
    return [event for event in events if event.get("id")]


def _upsert_connected_item(
    db: Session,
    *,
    provider: str,
    item_type: str,
    external_id: str,
    thread_id: Optional[str],
    title: str,
    subtitle: Optional[str],
    snippet: Optional[str],
    occurred_at: Optional[datetime],
    payload: dict,
    account: Optional[ChurchAccount] = None,
) -> tuple[ConnectedContextItem, bool]:
    item = (
        scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
        .filter(ConnectedContextItem.provider == provider, ConnectedContextItem.item_type == item_type, ConnectedContextItem.external_id == external_id)
        .first()
    )
    created = item is None
    if not item:
        item = ConnectedContextItem(provider=provider, item_type=item_type, external_id=external_id, account_id=_account_id(account))
        db.add(item)
    item.thread_id = thread_id
    item.title = title
    item.subtitle = subtitle
    item.snippet = snippet
    item.occurred_at = occurred_at
    item.payload_json = _json_dumps(payload)
    if created:
        db.flush()
    return item, created


def _connected_item_response(item: ConnectedContextItem) -> ConnectedContextItemResponse:
    return ConnectedContextItemResponse(
        id=item.id,
        provider=item.provider,
        item_type=item.item_type,
        external_id=item.external_id,
        thread_id=item.thread_id,
        title=item.title,
        subtitle=item.subtitle,
        snippet=item.snippet,
        occurred_at=item.occurred_at,
        action_id=item.action_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _connected_items(db: Session, item_type: Optional[str] = None, account: Optional[ChurchAccount] = None, limit: int = 5) -> List[ConnectedContextItem]:
    query = scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
    if item_type:
        query = query.filter(ConnectedContextItem.item_type == item_type)
    return query.order_by(ConnectedContextItem.occurred_at.desc().nullslast(), ConnectedContextItem.created_at.desc()).limit(limit).all()


def _connected_context_desk_items(db: Session, account: Optional[ChurchAccount] = None, limit: int = 5) -> List[DeskItem]:
    return [_desk_item_from_connected_item(item) for item in _connected_items(db, account=account, limit=limit)]


def _desk_item_from_connected_item(item: ConnectedContextItem) -> DeskItem:
    item_type = {
        "email": "synced_email",
        "calendar_event": "synced_calendar",
        "person": "synced_person",
    }.get(item.item_type, "synced_context")
    priority = "high" if item.action_id else "medium"
    action = "Review queued action" if item.action_id else "Review context"
    return DeskItem(
        id=f"action-{item.action_id}" if item.action_id else f"connected-{item.id}",
        type=item_type,
        title=item.title,
        subtitle=item.subtitle or _date_label(item.occurred_at),
        detail=item.snippet,
        priority=priority,
        action=action,
        source=item.provider,
        related_id=item.action_id or item.id,
        provider=item.provider,
    )


def _get_or_create_connection(
    db: Session,
    provider: str,
    display_name: str,
    auth_type: str,
    account: Optional[ChurchAccount] = None,
) -> IntegrationConnection:
    connection = scoped_query(db.query(IntegrationConnection), IntegrationConnection, account).filter(IntegrationConnection.provider == provider).first()
    if connection:
        return connection
    connection = IntegrationConnection(provider=provider, display_name=display_name, auth_type=auth_type, account_id=_account_id(account))
    db.add(connection)
    return connection


def _require_verified_for_sync(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    credential: Optional[IntegrationCredential] = None,
) -> None:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider, {"display_name": provider.replace("_", " ").title()})
    if credential and credential.verified_at:
        return
    connection = scoped_query(db.query(IntegrationConnection), IntegrationConnection, account).filter(IntegrationConnection.provider == provider).first()
    if not credential and connection and connection.verified_at:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Check credentials for {definition['display_name']} before syncing ministry data. "
            "Verification confirms access without importing people, email, or calendar context."
        ),
    )


def _default_policy(provider: str, account: Optional[ChurchAccount] = None) -> IntegrationPolicy:
    return IntegrationPolicy(
        account_id=_account_id(account),
        provider=provider,
        read_enabled=True,
        write_enabled=False,
        require_approval=True,
        allowed_actions="email_draft,calendar_block" if provider in {"google_workspace", "microsoft_365"} else "",
        privacy_mode="pastoral",
    )


def _get_or_create_policy(db: Session, provider: str, account: Optional[ChurchAccount] = None) -> IntegrationPolicy:
    policy = scoped_query(db.query(IntegrationPolicy), IntegrationPolicy, account).filter(IntegrationPolicy.provider == provider).first()
    if policy:
        return policy
    policy = _default_policy(provider, account)
    db.add(policy)
    db.flush()
    return policy


def _policy_or_default(db: Session, provider: str, account: Optional[ChurchAccount] = None) -> IntegrationPolicy:
    return scoped_query(db.query(IntegrationPolicy), IntegrationPolicy, account).filter(IntegrationPolicy.provider == provider).first() or _default_policy(provider, account)


def _policy_response(db: Session, provider: str, account: Optional[ChurchAccount] = None) -> IntegrationPolicyResponse:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider, {"display_name": provider.replace("_", " ").title()})
    policy = _policy_or_default(db, provider, account)
    return IntegrationPolicyResponse(
        provider=provider,
        display_name=definition["display_name"],
        read_enabled=bool(policy.read_enabled),
        write_enabled=bool(policy.write_enabled),
        require_approval=bool(policy.require_approval),
        allowed_actions=_scopes_to_list(policy.allowed_actions, []),
        privacy_mode=policy.privacy_mode or "pastoral",
        secure_note="OAuth connection does not grant writeback by itself. Writes require this policy and per-action approval.",
    )


def _policy_payload(policy: IntegrationPolicy) -> dict:
    return {
        "read_enabled": bool(policy.read_enabled),
        "write_enabled": bool(policy.write_enabled),
        "require_approval": bool(policy.require_approval),
        "allowed_actions": _scopes_to_list(policy.allowed_actions, []),
        "privacy_mode": policy.privacy_mode,
    }


def _ensure_external_write_allowed(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> None:
    provider = action.external_provider
    if not provider:
        return
    policy = _get_or_create_policy(db, provider, account)
    if not policy.write_enabled:
        raise HTTPException(status_code=403, detail=f"{provider} writeback is disabled by church policy.")
    if policy.require_approval and action.status != "approved":
        raise HTTPException(status_code=409, detail="This action still needs pastor approval.")
    allowed = set(_scopes_to_list(policy.allowed_actions, []))
    if allowed and action.action_type not in allowed:
        raise HTTPException(status_code=403, detail=f"{action.action_type} is not allowed for {provider} writeback.")
    if action.privacy_level == "private" and policy.privacy_mode != "private_allowed":
        raise HTTPException(status_code=403, detail="Private pastoral actions cannot be written to external systems under the current policy.")
    _require_verified_for_external_write(db, provider, account, user)


def _require_verified_for_external_write(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> None:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider, {"display_name": provider.replace("_", " ").title()})
    display_name = definition["display_name"]
    credential = _provider_credential(db, provider, account, user)
    if credential:
        if credential.verified_at:
            return
        raise HTTPException(
            status_code=409,
            detail=(
                f"Check credentials for {display_name} before writing externally. "
                "Verification confirms access without creating drafts or calendar events."
            ),
        )
    if user:
        raise HTTPException(
            status_code=409,
            detail=f"{display_name} is not connected for this Marge user. Connect and check credentials before writing externally.",
        )
    connection = scoped_query(db.query(IntegrationConnection), IntegrationConnection, account).filter(IntegrationConnection.provider == provider).first()
    if connection and connection.verified_at:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Check credentials for {display_name} before writing externally. "
            "Verification confirms access without creating drafts or calendar events."
        ),
    )


def _gmail_headers(message: dict) -> dict:
    headers = ((message.get("payload") or {}).get("headers") or [])
    return {str(header.get("name", "")).lower(): header.get("value") for header in headers}


def _parse_email_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(value)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _parse_google_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _email_triage_description(message: dict) -> str:
    sender = message.get("from") or "Sender not available"
    snippet = message.get("snippet") or "No preview was included by the provider."
    return f"{sender}: {snippet}"


def _calendar_event_needs_pastoral_prep(event: dict) -> bool:
    text = " ".join([event.get("summary") or "", event.get("description") or "", event.get("location") or ""]).lower()
    pastoral_terms = ["visit", "care", "hospital", "prayer", "funeral", "wedding", "counsel", "member", "visitor", "meeting", "lunch", "coffee"]
    return any(term in text for term in pastoral_terms)


def _prepare_actions_from_desk(
    db: Session,
    mode: str,
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    priorities: List[DeskItem],
    profile: PastorProfile,
    account: Optional[ChurchAccount] = None,
    *,
    email_limit: int = 3,
) -> List[AssistantAction]:
    today_key = datetime.utcnow().date().isoformat()
    prepared: List[AssistantAction] = _prepare_email_draft_actions(db, mode, email_drafts, profile, account, limit=email_limit)
    for item in calendar_blocks[:2]:
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key=f"{today_key}:{mode}:calendar_block:{item.id}",
            action_type="calendar_block",
            title=item.title,
            description=item.detail or item.subtitle or "Proposed protected ministry block.",
            payload={"desk_item": item.model_dump(mode="json")},
            source="calendar",
            external_provider=None,
            related_type=item.type,
            related_id=item.related_id,
            privacy_level="pastoral",
            account=account,
        ))
    for item in priorities[:3]:
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key=f"{today_key}:{mode}:followup:{item.id}",
            action_type="pastoral_followup",
            title=f"Follow up with {item.title}",
            description=item.detail or item.action or item.subtitle,
            payload={"desk_item": item.model_dump(mode="json")},
            source=item.source or "assistant",
            external_provider=None,
            related_type=item.source,
            related_id=item.related_id,
            privacy_level="private" if item.type == "prayer" else "pastoral",
            account=account,
        ))
    db.commit()
    for action in prepared:
        db.refresh(action)
    return prepared


def _prepare_email_draft_actions(
    db: Session,
    mode: str,
    email_drafts: List[DeskItem],
    profile: PastorProfile,
    account: Optional[ChurchAccount] = None,
    *,
    limit: int = 3,
) -> List[AssistantAction]:
    today_key = datetime.utcnow().date().isoformat()
    prepared: List[AssistantAction] = []
    for item in email_drafts[:limit]:
        email_payload = _prepared_email_payload(db, profile, item, account)
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key=f"{today_key}:{mode}:email_draft:{item.id}",
            action_type="email_draft",
            title=f"Review {item.title}",
            description=f"{item.subtitle or 'Pastoral contact'}: {item.detail or item.action or 'Draft reply for review.'}",
            payload={
                "desk_item": item.model_dump(mode="json"),
                "email": email_payload,
                "draft_kind": _prepared_email_kind(item),
                "draft_context": _draft_context_for_item(db, profile, account, item),
            },
            source=item.source or "assistant",
            external_provider=None,
            related_type=item.source,
            related_id=item.related_id,
            privacy_level="private" if item.source == "prayer" else "pastoral",
            account=account,
        ))
    return prepared


def _prepared_email_kind(item: DeskItem) -> str:
    title = item.title.lower()
    if item.source == "visitors" or "visitor" in title:
        return "visitor"
    if item.source == "prayer" or "prayer" in title:
        return "prayer"
    if item.source == "attendance" or "absence" in title:
        return "absence"
    if item.source == "care" or "care" in title:
        return "care"
    return "general"


def _prepared_email_payload(db: Session, profile: PastorProfile, item: DeskItem, account: Optional[ChurchAccount] = None) -> dict:
    recipient_info = _prepared_email_recipient(db, item, account)
    pastor = _profile_pastor_name(profile)
    church = _profile_church_name(profile)
    kind = _prepared_email_kind(item)
    body = _prepared_email_body(db, item, kind, pastor, church, account, profile)
    if not body:
        recipient = recipient_info.get("name") or item.subtitle or "there"
        first_name = recipient.split()[0] if recipient else "there"
        body = (
            f"Hey {first_name}, I have been thinking about you and wanted to check in. "
            f"How are you doing this week?\n\n- {pastor_display_name(pastor)}"
        )
    payload = {
        "subject": item.title,
        "body": body,
    }
    if recipient_info.get("email"):
        payload["to"] = recipient_info["email"]
    if recipient_info.get("name"):
        payload["recipient_name"] = recipient_info["name"]
    return payload


def _prepared_email_body(
    db: Session,
    item: DeskItem,
    kind: str,
    pastor_name: str,
    church_name: str,
    account: Optional[ChurchAccount] = None,
    profile: Optional[PastorProfile] = None,
) -> Optional[str]:
    if kind == "visitor" and item.related_id:
        visitor = scoped_query(db.query(Visitor), Visitor, account).filter(Visitor.id == item.related_id).first()
        if visitor:
            return draft_visitor_followup(
                visitor,
                day=1,
                pastor_name=pastor_name,
                church_name=church_name,
                communication_style=profile.communication_style if profile else None,
                faith_tradition=profile.faith_tradition if profile else None,
            )
    if kind == "absence" and item.related_id:
        member = scoped_query(db.query(Member), Member, account).filter(Member.id == item.related_id).first()
        if member:
            return draft_absence_checkin(member, pastor_name=pastor_name, church_name=church_name)
    if kind == "care" and item.related_id:
        care = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.id == item.related_id).first()
        if care and care.member:
            category = care.category.value if hasattr(care.category, "value") else care.category
            situation = " ".join(part for part in [str(category or ""), care.description or item.detail or ""] if part).strip() or "general"
            return draft_care_message(
                care.member,
                situation=situation,
                pastor_name=pastor_name,
                communication_style=profile.communication_style if profile else None,
                faith_tradition=profile.faith_tradition if profile else None,
            )
    if kind == "prayer" and item.related_id:
        prayer = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.id == item.related_id).first()
        if prayer:
            name = prayer.member.full_name if prayer.member else (prayer.submitted_by or item.subtitle or "Friend")
            first_name = prayer.member.first_name if prayer.member else name.split()[0]
            created_at = prayer.created_at or datetime.utcnow()
            days_ago = max((datetime.utcnow() - created_at).days, 0)
            return voice.PRAYER_FOLLOWUP_TEMPLATE.format(
                first_name=first_name,
                days_ago=days_ago,
                short_summary=_short_context(prayer.request_text or item.detail or "that request", 90),
                pastor_name=pastor_display_name(pastor_name),
            )
    return None


def _profile_draft_context(profile: PastorProfile) -> dict:
    context = {
        "drafting_voice": _clean(profile.communication_style),
        "faith_tradition": _clean(profile.faith_tradition),
        "support_preferences": _clean(profile.support_preferences),
        "guardrail": _clean(profile.guardrails),
    }
    return {key: value for key, value in context.items() if value}


def _draft_context_for_item(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    item: DeskItem,
) -> dict:
    context = _profile_draft_context(profile)
    member = _member_for_prepared_email_item(db, account, item)
    if not member:
        return context
    return _add_member_review_context(db, account, member, context)


def _member_for_prepared_email_item(
    db: Session,
    account: Optional[ChurchAccount],
    item: DeskItem,
) -> Optional[Member]:
    if not item.related_id:
        return None
    if item.source in {"attendance", "member", "members"}:
        return scoped_query(db.query(Member), Member, account).filter(Member.id == item.related_id).first()
    if item.source == "care":
        care = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.id == item.related_id).first()
        return care.member if care and care.member else None
    if item.source == "prayer":
        prayer = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.id == item.related_id).first()
        return prayer.member if prayer and prayer.member else None
    return None


def _member_preference_notes_for_draft(
    db: Session,
    account: Optional[ChurchAccount],
    member: Member,
) -> List[MemberNote]:
    return (
        scoped_query(db.query(MemberNote), MemberNote, account)
        .filter(MemberNote.member_id == member.id, MemberNote.context_tag == "preference")
        .order_by(MemberNote.created_at.desc())
        .limit(3)
        .all()
    )


def _add_member_preference_context(
    db: Session,
    account: Optional[ChurchAccount],
    member: Member,
    context: dict,
    *,
    preference_notes: Optional[List[MemberNote]] = None,
) -> dict:
    notes = preference_notes if preference_notes is not None else _member_preference_notes_for_draft(db, account, member)
    if not notes:
        return context
    context["member_id"] = member.id
    context["member_name"] = member.full_name
    context["member_preferences"] = [
        {"note_id": note.id, "text": _short_context(note.note_text, 180)}
        for note in notes
    ]
    context["member_preference_guardrail"] = (
        "Pastor-only review context. Respect these preferences while drafting, "
        "but do not send sensitive preference details unless the pastor explicitly approves them."
    )
    return context


def _add_member_review_context(
    db: Session,
    account: Optional[ChurchAccount],
    member: Member,
    context: dict,
) -> dict:
    care_cases = _member_active_care_cases(db, account, member)
    prayers = _member_active_prayers(db, account, member)
    if care_cases or prayers:
        context["member_id"] = member.id
        context["member_name"] = member.full_name
        member_context = {"member_id": member.id, "member_name": member.full_name}
        if care_cases:
            member_context["active_care"] = [_care_case_summary(case) for case in care_cases[:2]]
        if prayers:
            member_context["active_prayer"] = [_prayer_summary(prayer) for prayer in prayers[:2]]
        context["member_context"] = [member_context]
    return _add_member_preference_context(db, account, member, context)


def _prepared_email_recipient(db: Session, item: DeskItem, account: Optional[ChurchAccount] = None) -> dict:
    source = item.source
    related_id = item.related_id
    if not related_id:
        return {"name": item.subtitle}
    if source == "visitors":
        visitor = scoped_query(db.query(Visitor), Visitor, account).filter(Visitor.id == related_id).first()
        if visitor:
            return {"name": visitor.full_name, "email": _clean(visitor.email)}
    if source == "attendance":
        member = scoped_query(db.query(Member), Member, account).filter(Member.id == related_id).first()
        if member:
            return {"name": member.full_name, "email": _clean(member.email)}
    if source == "care":
        care = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.id == related_id).first()
        if care and care.member:
            return {"name": care.member.full_name, "email": _clean(care.member.email)}
    if source == "prayer":
        prayer = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.id == related_id).first()
        if prayer:
            if prayer.member:
                return {"name": prayer.member.full_name, "email": _clean(prayer.member.email)}
            return {"name": prayer.submitted_by or item.subtitle}
    return {"name": item.subtitle}


def _prepare_setup_actions(
    db: Session,
    profile: PastorProfile,
    setup_steps: List[DeskItem],
    account: Optional[ChurchAccount] = None,
) -> List[AssistantAction]:
    prepared: List[AssistantAction] = []
    for step in setup_steps[:4]:
        description = step.subtitle if step.type == "data_seed" else (step.detail or step.subtitle)
        if step.type == "profile_setup":
            action_type = "profile_question"
            title = step.title
            privacy_level = "pastoral"
        elif step.type == "integration_setup":
            action_type = "integration_setup"
            title = step.title
            privacy_level = "pastoral"
        elif step.type == "data_seed":
            action_type = "data_seed"
            title = step.title
            privacy_level = "pastoral"
        else:
            action_type = "setup_step"
            title = step.title
            privacy_level = "pastoral"
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key=f"setup:{step.id}",
            action_type=action_type,
            title=title,
            description=description,
            payload={
                "setup_step": step.model_dump(mode="json"),
                "secure_note": "Marge never asks the pastor to paste API keys, OAuth secrets, or passwords into chat.",
                "missing_profile_fields": _missing_profile_fields(profile),
                "tools_in_use": profile.tools_in_use,
                "faith_tradition": profile.faith_tradition,
                "support_preferences": profile.support_preferences,
            },
            source=step.source or "setup",
            external_provider=None,
            related_type=step.type,
            related_id=None,
            privacy_level=privacy_level,
            account=account,
        ))

    if _profile_is_complete(profile):
        prepared.append(_upsert_prepared_action(
            db,
            dedupe_key="setup:first_week_plan:v1",
            action_type="first_week_plan",
            title="First-week Marge launch plan",
            description=_first_week_plan_description(profile, setup_steps),
            payload={
                "plan": _first_week_plan(profile, setup_steps),
                "followup_pain": profile.followup_pain,
                "support_preferences": profile.support_preferences,
                "faith_tradition": profile.faith_tradition,
                "tools_in_use": profile.tools_in_use,
                "weekly_rhythm": profile.weekly_rhythm,
                "communication_style": profile.communication_style,
                "guardrails": profile.guardrails,
            },
            source="assistant_setup",
            external_provider=None,
            related_type="setup",
            related_id=None,
            privacy_level="pastoral",
            account=account,
        ))

    db.commit()
    for action in prepared:
        db.refresh(action)
    return prepared


def _prepare_profile_ready_actions(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> List[AssistantAction]:
    if not _profile_is_complete(profile):
        return []
    integrations = _integration_statuses(db, account, user)
    setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, "live"))
    actions = _prepare_setup_actions(db, profile, setup_steps, account)
    if actions:
        _audit(
            db,
            "assistant_actions.prepared_after_profile_completion",
            f"Prepared {len(actions)} first-run setup action(s) after ministry profile completion.",
            account=account,
            payload={"count": len(actions), "setup_steps": [step.id for step in setup_steps]},
        )
        db.commit()
        for action in actions:
            db.refresh(action)
    return actions


def _prepare_integration_setup_from_chat(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    setup = _start_integration(provider, db, account, user)
    if setup.status in {"connected", "configured", "available"}:
        return {"setup": setup, "action": None}
    setup_step = _integration_setup_step(setup)
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"integration_setup:{provider}",
        action_type="integration_setup",
        title=f"Connect {setup.display_name}",
        description=_integration_setup_description(setup),
        payload={
            "integration_setup": setup.model_dump(mode="json"),
            "setup_step": setup_step.model_dump(mode="json"),
            "secure_note": setup.secure_note,
            "guardrail": "Use provider authorization or server-side configuration; never paste secrets into chat.",
        },
        source="integrations",
        external_provider=None,
        related_type="integration",
        related_id=None,
        privacy_level="pastoral",
        account=account,
    )
    _audit(
        db,
        "assistant_action.integration_setup_from_chat",
        f"Queued {setup.display_name} setup from chat.",
        provider=provider,
        account=account,
        action_id=action.id,
        payload={"status": setup.status, "missing_config": setup.missing_config, "authorization_url_created": bool(setup.authorization_url)},
    )
    db.commit()
    db.refresh(action)
    return {"setup": setup, "action": action}


def _integration_setup_step(setup: IntegrationSetupResponse) -> DeskItem:
    if setup.authorization_url:
        action = "Start secure setup"
    elif _api_key_setup_can_accept_workspace_credentials(setup):
        action = "Add encrypted credentials"
    elif setup.missing_config:
        action = "Review encrypted storage" if ENCRYPTION_KEY_ENV in setup.missing_config else "Review server config"
    else:
        action = "Open setup"
    return DeskItem(
        id=f"setup-integration-{setup.provider}",
        type="integration_setup",
        title=f"Connect {setup.display_name}",
        subtitle=setup.instructions[0] if setup.instructions else setup.secure_note,
        detail=_integration_setup_description(setup),
        priority="high",
        action=action,
        source="integrations",
        provider=setup.provider,
    )


def _integration_setup_description(setup: IntegrationSetupResponse) -> str:
    if setup.authorization_url:
        return "Open the secure provider authorization URL. Tokens stay encrypted server-side after callback."
    if setup.missing_config:
        if setup.setup_type in {"api_key", "env_api_key"}:
            if ENCRYPTION_KEY_ENV in setup.missing_config:
                remaining = [name for name in setup.missing_config if name != ENCRYPTION_KEY_ENV]
                suffix = f" Other server-side config still missing: {', '.join(remaining)}." if remaining else ""
                return f"Encrypted credential storage is not ready. Set {ENCRYPTION_KEY_ENV} before adding workspace credentials here.{suffix}"
            return f"Add encrypted workspace API-key credentials here, or configure {', '.join(setup.missing_config)} server-side."
        return f"Server config needed: {', '.join(setup.missing_config)}."
    return "Review connector setup instructions before syncing external context."


def _integration_setup_chat_prompts(setup: IntegrationSetupResponse) -> List[str]:
    if setup.status in {"connected", "configured", "available"}:
        return [f"Sync {setup.display_name}.", "Show connected context.", "Explain the approval rules."]
    return ["Open integrations.", "How do secure connections work?", "Explain the approval rules."]


def _api_key_setup_can_accept_workspace_credentials(setup: IntegrationSetupResponse) -> bool:
    return setup.setup_type in {"api_key", "env_api_key"} and ENCRYPTION_KEY_ENV not in (setup.missing_config or [])


def _integration_check_credentials_step(integration: IntegrationStatus, profile: PastorProfile) -> DeskItem:
    return DeskItem(
        id=f"setup-integration-verify-{integration.provider}",
        type="integration_setup",
        title=f"Check {integration.display_name} credentials",
        subtitle=f"Marge can see this connector, but has not verified access for {_profile_tools_label(profile)}.",
        detail="Credential checks confirm access without importing people, email, calendar, or attendance context.",
        priority="high" if integration.provider in {"google_workspace", "planning_center", "rock", "microsoft_365"} else "medium",
        action="Check credentials",
        source="integrations",
        provider=integration.provider,
    )


def _provider_setup_or_check_step(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    provider: str,
    *,
    subtitle: Optional[str] = None,
    detail: Optional[str] = None,
) -> Optional[DeskItem]:
    integration = next((item for item in integrations if item.provider == provider), None)
    if not integration:
        return None
    if integration.status in {"connected", "configured", "available"}:
        if integration.verified_at:
            return None
        return _integration_check_credentials_step(integration, profile)
    return DeskItem(
        id=f"setup-integration-{provider}",
        type="integration_setup",
        title=f"Connect {integration.display_name}",
        subtitle=subtitle or f"Use {integration.display_name} when this church is ready to connect real ministry context.",
        detail=detail or integration.config_hint or integration.secure_note,
        priority="high" if provider in {"google_workspace", "planning_center", "rock", "microsoft_365"} else "medium",
        action="Start secure setup",
        source="integrations",
        provider=provider,
    )


def _connector_setup_or_check_prompts(steps: List[DeskItem]) -> List[str]:
    if steps:
        first = steps[0]
        if first.action == "Check credentials" and first.provider:
            return [f"Check {_provider_display_name(first.provider)} credentials.", "Open integrations.", "Explain the approval rules."]
        return [_setup_prompt(first), "How do secure connections work?", "Explain the approval rules."]
    return ["Open integrations.", "How do secure connections work?", "Explain the approval rules."]


def _dedupe_desk_items(items: List[DeskItem]) -> List[DeskItem]:
    deduped: List[DeskItem] = []
    seen = set()
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        deduped.append(item)
    return deduped


def _dedupe_strings(items: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for item in items:
        cleaned = _clean(item)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _first_week_plan_description(profile: PastorProfile, setup_steps: List[DeskItem]) -> str:
    pain = profile.followup_pain.strip().rstrip(".") if profile.followup_pain else "follow-up gaps"
    connector_count = len([step for step in setup_steps if step.type == "integration_setup"])
    connector_phrase = f"connect {connector_count} ministry tool{'s' if connector_count != 1 else ''}" if connector_count else "connect the first ministry system"
    support = _short_context(profile.support_preferences, 120)
    support_phrase = f" Support the pastor the way he asked: {support}." if support else ""
    return f"Start by addressing {pain}, then {connector_phrase}, while keeping writes behind pastor approval.{support_phrase}"


def _seed_context_detail(profile: PastorProfile) -> str:
    pain = _short_context(profile.followup_pain, 120)
    if pain:
        return f"Start with one real person connected to this follow-up pressure: {pain}."
    context = _short_context(profile.church_context, 120)
    if context:
        return f"Start with one real person from this church context: {context}."
    return "Start with one person Marge should keep from slipping through the cracks."


def _seed_context_kind(profile: PastorProfile) -> str:
    text = " ".join([profile.followup_pain or "", profile.church_context or ""]).lower()
    if _mentions(text, ["visitor", "visitors", "guest", "guests", "new family", "new families", "first time", "first-time"]):
        return "visitor"
    if _mentions(text, ["prayer", "pray", "praying"]):
        return "prayer"
    if _mentions(text, ["hospital", "surgery", "icu", "hospice", "grief", "grieving", "death", "died", "loss", "funeral", "crisis", "rehab", "care case", "care follow-up", "care follow up"]):
        return "care"
    return "person"


def _seed_context_form(kind: str) -> str:
    if kind in {"visitor", "prayer"}:
        return kind
    return "person"


def _seed_context_action(kind: str) -> str:
    if kind == "visitor":
        return "Log first visitor"
    if kind == "prayer":
        return "Add first prayer"
    if kind == "care":
        return "Add first care person"
    return "Add first person"


def _seed_context_title(kind: str) -> str:
    if kind == "visitor":
        return "Log the first real visitor"
    if kind == "prayer":
        return "Add the first real prayer request"
    if kind == "care":
        return "Add the first person needing care"
    return "Add the first real person"


def _data_seed_help_requested(lower: str) -> bool:
    return _mentions(lower, ["seed", "first real", "first person", "first visitor", "first prayer", "first care", "care case", "care person", "real people"]) or (
        lower.startswith("help me") and _mentions(lower, ["visitor", "guest", "person", "people", "prayer", "care"])
    )


def _setup_steps_requested(lower: str) -> bool:
    return _mentions(lower, [
        "show my setup steps",
        "show setup steps",
        "what are my setup steps",
        "what setup steps",
        "setup steps",
    ])


def _support_style_requested(lower: str) -> bool:
    return _mentions(lower, [
        "how will you support me",
        "how can you support me",
        "how will you help me personally",
        "how can you help me personally",
        "how will you support this pastor",
        "how should i tell you to support me",
        "what support style do you need",
    ])


def _context_usage_requested(lower: str) -> bool:
    return _mentions(lower, [
        "how will you use this context",
        "how will you use my context",
        "how do you use this context",
        "how do you use my context",
        "what will you do with this context",
        "what will you do with my context",
        "why do you need this context",
        "why are you asking this",
    ])


def _secure_connections_explainer_requested(lower: str) -> bool:
    if re.search(r"\b(?:paste|enter|send)\b", lower) and _mentions(lower, ["password", "api key", "token", "secret", "credentials"]):
        return True
    return _mentions(lower, [
        "how do secure connections work",
        "how does secure connection work",
        "how does secure setup work",
        "how do secure connectors work",
        "how are connections secure",
        "how are connectors secure",
        "how do you keep credentials safe",
        "how do you protect credentials",
        "will you ask for my password",
        "should i paste my password",
        "can i paste my password",
        "paste my password",
        "should i paste my api key",
        "can i paste my api key",
        "paste my api key",
        "should i paste my token",
        "can i paste my token",
        "paste my token",
        "paste credentials",
        "credentials in chat",
        "secrets in chat",
    ])


def _setup_step_reason_requested(lower: str) -> bool:
    return _mentions(lower, [
        "why is this the next step",
        "why is this next",
        "why this step",
        "why this next",
        "why should this be next",
        "why should i do this next",
    ])


def _ministry_update_help_requested(lower: str) -> bool:
    return _mentions(lower, [
        "help me add a ministry update",
        "help me add ministry update",
        "how do i add a ministry update",
        "how should i add a ministry update",
        "what kind of ministry update",
        "what ministry update should i add",
    ])


def _profile_priority_guidance_requested(lower: str, profile: PastorProfile) -> bool:
    priority = _clean(profile.ministry_priorities)
    if not priority:
        return False
    if lower.startswith((
        "what should i do for",
        "what should we do for",
        "how should i start with",
        "how should we start with",
    )):
        return True
    priority_terms = [
        term
        for term in re.split(r"[^a-z0-9]+", priority.lower())
        if len(term) >= 5 and term not in {"first", "month", "priority", "before", "people", "needs"}
    ]
    return (
        bool(priority_terms)
        and _mentions(lower, ["first priority", "ministry priority", "priority i named"])
        and any(term in lower for term in priority_terms[:4])
    )


def _first_record_coaching_requested(lower: str) -> bool:
    return _mentions(lower, [
        "what should i ask a new family",
        "what should i ask a visitor",
        "what should i ask a guest",
        "what should i ask first",
        "what should i record first",
        "what should i save first",
        "what should i capture for private prayer",
        "what should i capture for a care case",
        "what should i record for a care case",
        "what do you need for a care case",
        "how do you handle private prayer",
        "how should i handle private prayer",
        "what do you need for private prayer",
        "how do you handle care cases",
        "how should i handle care cases",
    ])


def _generic_person_capture_prompt_requested(lower: str) -> bool:
    normalized = re.sub(r"\s+", " ", lower.strip(" .!?")).strip()
    return normalized in {
        "add this person first",
        "add a person",
        "add person",
        "add a member",
        "add member",
        "what should i capture for a person",
        "what should i record for a person",
        "what do you need for a person",
        "how should i add this person",
        "how do i add this person",
    }


def _data_seed_chat_reply(step: DeskItem) -> str:
    is_care_seed = _data_seed_is_care(step)
    if step.form == "visitor":
        return (
            f"I would start with a real visitor or new family. {step.subtitle} "
            "Use Log first visitor, add the name, visit date, and one note about what follow-up would help. "
            "After that, I can keep the welcome follow-up in front of you and draft the first note."
        )
    if step.form == "prayer":
        return (
            f"I would start with one prayer request that needs follow-up. {step.subtitle} "
            "Use Add first prayer, mark whether it is private, and write only the context you want Marge to remember. "
            "After that, I can keep it visible without treating it like a public task."
        )
    if is_care_seed:
        return (
            f"I would start with the first person who needs active care. {step.subtitle} "
            "Tell me the person's name, the care situation, and the latest contact. "
            "After that, I can keep the care case visible and help draft a follow-up for review."
        )
    return (
        f"I would start with one real person who cannot slip through the cracks. {step.subtitle} "
        "Use Add first person with the basic contact details, then add a care case, prayer request, or note if there is sensitive context."
    )


def _data_seed_suggested_prompts(step: DeskItem) -> List[str]:
    if step.form == "visitor":
        return ["Log the first visitor.", "What should I ask a new family?", "Show my setup steps."]
    if step.form == "prayer":
        return ["Add the first prayer request.", "How do you handle private prayer?", "Show my setup steps."]
    if _data_seed_is_care(step):
        return ["Help me open the first care case.", "What should I record first?", "Show my setup steps."]
    return ["Add the first person.", "What should I record first?", "Show my setup steps."]


def _data_seed_is_care(step: DeskItem) -> bool:
    text = f"{step.title or ''} {step.action or ''} {step.subtitle or ''}".lower()
    return _mentions(text, ["care", "hospital", "grief", "crisis"])


def _setup_steps_chat_response(
    setup_steps: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    if setup_steps:
        lines = "; ".join(
            f"{step.title}: {step.action or step.subtitle or step.detail or 'Next setup step'}"
            for step in setup_steps[:4]
        )
        reply = f"Here are the setup steps I would keep in front of you: {lines}."
    else:
        reply = "I do not see a setup step waiting right now. I would keep watching real care, visitor, prayer, and connector context."
    return AssistantChatResponse(
        reply=reply,
        intent="setup_steps_lookup",
        mode=effective_mode,
        actions=setup_steps[:4],
        suggested_prompts=_suggested_prompts(profile, [], setup_steps),
        profile=_profile_response(profile, account),
    )


def _secure_connections_chat_response(
    setup_steps: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    integration_steps = [step for step in setup_steps if step.type == "integration_setup"]
    if integration_steps:
        next_step = f"The next connector step I see is {integration_steps[0].title}."
        actions = integration_steps[:3]
    elif setup_steps:
        next_step = f"First finish {setup_steps[0].title}; then I can recommend the right connector."
        actions = setup_steps[:3]
    else:
        next_step = "Once a church tool is ready, start from Integrations and check credentials before syncing."
        actions = []
    reply = (
        "Secure connections work in this order: create a private workspace, use provider authorization or encrypted API-key setup, "
        "store credentials server-side, run Check credentials without importing ministry data, then sync only when you ask. "
        "Do not paste passwords in chat, and do not paste OAuth tokens, refresh tokens, API keys, or client secrets there either; API-key connectors use the encrypted credential form instead. "
        "External sends or system changes still require connector policy plus your approval of the exact item. "
        f"{next_step}"
    )
    return AssistantChatResponse(
        reply=reply,
        intent="secure_connections_explained",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_suggested_prompts(profile, [], setup_steps),
        profile=_profile_response(profile, account),
    )


def _setup_step_reason_chat_response(
    setup_steps: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    if not setup_steps:
        reply = (
            "I do not see a setup step waiting right now. I would keep watching real care, visitor, prayer, connected tools, "
            "and the approval queue before calling the desk clear."
        )
        actions: List[DeskItem] = []
    else:
        step = setup_steps[0]
        reply = (
            f"I would make {step.title} next because {_setup_step_reason(step, profile)} "
            "That keeps Marge useful without guessing, and it keeps any external send or system change behind your approval."
        )
        actions = [step]
    return AssistantChatResponse(
        reply=reply,
        intent="setup_step_reason",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_suggested_prompts(profile, [], setup_steps),
        profile=_profile_response(profile, account),
    )


def _setup_step_reason(step: DeskItem, profile: PastorProfile) -> str:
    if step.type == "profile_setup":
        return (
            f"I still need this ministry-context answer before I can personalize priorities, drafts, and connector setup for "
            f"{_profile_church_name(profile)}."
        )
    if step.type == "data_seed":
        burden = _short_context(profile.followup_pain, 140)
        if step.form == "visitor":
            return f"you named visitor or guest follow-up as a pressure point, and I need one real visitor before I can prepare honest welcome follow-up."
        if step.form == "prayer":
            return f"you named prayer follow-up as a pressure point, and I need one real request before I can keep private prayer visible safely."
        if _data_seed_is_care(step):
            return f"you named hospital, grief, or care follow-up as a pressure point, and I need the first real person before I can keep a care case visible."
        if burden:
            return f"you named this follow-up pressure: {burden}. I need one real person tied to it before I can prioritize the desk."
        return "a brand-new workspace needs one real person, visitor, care case, or prayer request before I can prioritize real ministry work."
    if step.type == "integration_setup":
        text = f"{step.title or ''} {step.action or ''}".lower()
        if "check" in text:
            return "the connector is present but not verified; the credential check proves access without syncing people, email, calendar, or attendance data."
        tools = _short_context(profile.tools_in_use, 140)
        if tools:
            return f"you said the church already uses {tools}, so secure setup is the path from general guidance to real ministry context."
        return "Marge becomes more useful when she can read from the church systems you already trust, starting with secure setup rather than secrets in chat."
    return step.subtitle or step.detail or "it is the highest-leverage setup item I can see right now."


def _profile_priority_guidance_response(
    setup_steps: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    priority = _short_context(profile.ministry_priorities, 180) or "the priority you named"
    followup = _short_context(profile.followup_pain, 160)
    seed_step = next((step for step in setup_steps if step.type == "data_seed"), None)
    integration_steps = [step for step in setup_steps if step.type == "integration_setup"]
    if seed_step:
        reply = (
            f"For {priority}, I would start with the current first real record: {seed_step.title}. "
            f"{_data_seed_chat_reply(seed_step)} "
            "That gives Marge one real person or request to watch before I draft, prioritize, or connect dots from an empty workspace."
        )
        actions = [seed_step] + integration_steps[:2]
    elif priorities:
        lines = "; ".join(
            f"{item.title}: {item.action or item.detail or item.subtitle or 'review this next'}"
            for item in priorities[:3]
        )
        reply = (
            f"For {priority}, I would work this list first: {lines}. "
            "Nothing external happens until you approve the exact action."
        )
        actions = priorities[:3]
    elif integration_steps:
        reply = (
            f"For {priority}, I would connect the system that already holds this context next: {integration_steps[0].title}. "
            "Use secure setup, check credentials without syncing, then ask Marge to pull ministry context when you are ready."
        )
        actions = integration_steps[:3]
    else:
        pain = f" I am also watching this follow-up pressure: {followup}." if followup else ""
        reply = (
            f"For {priority}, I would add the next real visitor, care case, prayer request, or member note tied to that priority, "
            f"then ask me to draft or schedule the follow-up for review.{pain}"
        )
        actions = []
    return AssistantChatResponse(
        reply=reply,
        intent="ministry_priority_guidance",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
        profile=_profile_response(profile, account),
    )


def _ministry_update_help_response(
    setup_steps: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    seed_step = next((step for step in setup_steps if step.type == "data_seed"), None)
    if seed_step:
        reply = (
            f"Start with the first real ministry record I am already asking for: {seed_step.title}. "
            f"{_data_seed_chat_reply(seed_step)}"
        )
        actions = [seed_step]
        prompts = _data_seed_suggested_prompts(seed_step)
    else:
        reply = (
            "Tell me the update in one sentence with a real name and what happened. "
            "For a visitor, include the visit date and follow-up question. For care, include the person, situation, latest contact, and next step. "
            "For prayer, say whether it is private. For a general note, name the person and what I should remember. "
            "If I cannot match the person confidently, I will queue it for review instead of writing it onto the wrong record."
        )
        actions = priorities[:3]
        prompts = [
            "Log a visitor update.",
            "Open a care case.",
            "Add a private prayer request.",
        ]
    return AssistantChatResponse(
        reply=reply,
        intent="ministry_update_guidance",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _context_usage_chat_response(
    setup_steps: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    summary = _ministry_memory_summary(profile)
    implications = _ministry_memory_implications(profile)
    question = _interview_question(profile)
    if question:
        next_step = f"Next I am asking: {question['question']} {question.get('why') or ''}".strip()
    elif setup_steps:
        next_step = f"Next I would keep this setup step in front of you: {setup_steps[0].title}."
    elif priorities:
        next_step = f"Next I would start with {priorities[0].title}: {priorities[0].action or priorities[0].detail or 'review this before the day gets away'}."
    else:
        next_step = "Next I would keep watching people, visitors, care, prayer, and connected tools for anything that needs follow-up."
    support_clause = "support you in the way you named" if _clean(profile.support_preferences) else "learn how to support you personally"
    reply = (
        f"{summary} {implications} "
        f"{next_step} I use this context only inside this workspace to prioritize care, {support_clause}, shape drafts in your voice, recommend secure connectors, "
        "and keep external sends or system changes behind your approval."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="ministry_context_usage",
        mode=effective_mode,
        actions=setup_steps[:3] if setup_steps else priorities[:3],
        suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
        profile=_profile_response(profile, account),
    )


def _support_style_chat_response(
    setup_steps: List[DeskItem],
    priorities: List[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    support = _clean(profile.support_preferences)
    question = _interview_question(profile)
    actions = setup_steps[:3] if setup_steps else priorities[:3]
    if support:
        context_parts = []
        if _clean(profile.followup_pain):
            context_parts.append(f"keep {profile.followup_pain.strip().rstrip('.')} from going quiet")
        if _clean(profile.ministry_priorities):
            context_parts.append(f"move {profile.ministry_priorities.strip().rstrip('.')} first")
        if _clean(profile.weekly_rhythm):
            context_parts.append("protect the weekly rhythm you named")
        context = f" I will use that to {', '.join(context_parts[:3])}." if context_parts else ""
        reply = (
            f"You told me to support you this way: {support.strip().rstrip('.')}. "
            f"I will treat that as pastoral operating context, not as something I share externally.{context} "
            "I will still keep sends, schedule changes, connector syncs, and system writes behind checked credentials and your approval."
        )
    elif question and question.get("field") == "support_preferences":
        reply = (
            "I do not want to guess how to support you personally. "
            f"{question['question']} {question.get('why') or ''}".strip()
        )
        actions = [_profile_question_desk_item(question)]
    elif question:
        reply = (
            "I can support you better after I learn the next piece of ministry context. "
            f"{question['question']} {question.get('why') or ''}".strip()
        )
        actions = [_profile_question_desk_item(question)]
    else:
        reply = (
            "Tell me how you want to be supported when ministry gets heavy: whether to nudge gently, protect rest, surface missed people, "
            "or keep you from carrying every loop alone. I will use that only inside this workspace to shape proactive reminders and review queues."
        )
    return AssistantChatResponse(
        reply=reply,
        intent="support_style_guidance",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
        profile=_profile_response(profile, account),
    )


def _pastor_pressure_response(
    profile: PastorProfile,
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: List[DeskItem],
    pending_actions: List[AssistantAction],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    support = _short_context(profile.support_preferences, 180)
    rhythm = _short_context(profile.weekly_rhythm, 180)
    actions: List[DeskItem] = []
    if setup_steps:
        next_work = setup_steps[0]
        actions.append(next_work)
        next_sentence = (
            f"I would make the next step small: {next_work.title}. "
            f"{next_work.action or next_work.subtitle or next_work.detail or 'That gives me one real place to help without guessing.'}"
        )
    elif priorities:
        next_work = priorities[0]
        actions.extend(priorities[:3])
        next_sentence = (
            f"I would put the first real item in front of you and let the rest wait: {next_work.title}. "
            f"{next_work.action or next_work.detail or next_work.subtitle or 'Review this before anything else.'}"
        )
    elif pending_actions:
        first_action = pending_actions[0]
        actions.extend(_desk_item_from_action(action) for action in pending_actions[:3])
        next_sentence = (
            f"I would start by reviewing {first_action.title}; it is staged, and nothing external happens unless you approve it."
        )
    elif email_drafts or calendar_blocks:
        actions.extend(email_drafts[:2])
        actions.extend(calendar_blocks[:2])
        next_sentence = "I would only touch the reviewable drafts or calendar blocks already on the desk, then leave lower-trust work alone."
    else:
        next_sentence = "I do not see a must-handle item in the current workspace, so I would protect a quieter ministry block instead of inventing work."

    support_sentence = (
        f"You told me to support you this way: {support}. "
        if support
        else "I still need to learn how you want to be supported when ministry feels heavy. "
    )
    rhythm_sentence = f"I will keep this rhythm in view: {rhythm}. " if rhythm else ""
    reply = (
        f"{support_sentence}{rhythm_sentence}"
        f"{next_sentence} I can help triage what can wait, draft only for review, and keep sends, calendar writes, connector syncs, and system changes behind approval."
    )
    if not support:
        question = _interview_question(profile) or next((q for q in ONBOARDING_QUESTIONS if q["id"] == "support_preferences"), None)
        if question:
            actions.insert(0, _profile_question_desk_item(question))
    return AssistantChatResponse(
        reply=reply,
        intent="pastor_support",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=["What can wait until next week?", "What should I handle next?", "Show my approvals."],
        profile=_profile_response(profile, account),
    )


def _first_record_coaching_response(
    step: DeskItem,
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    is_care_seed = _data_seed_is_care(step)
    if step.form == "visitor":
        reply = (
            "For a new family, capture only enough to make follow-up warm and real: their names, contact info if offered, visit date, "
            "how they found the church, who they met, kids or ministry questions they raised, and the next helpful touch. "
            "I will turn that into a welcome draft for review, not an automatic send."
        )
    elif step.form == "prayer":
        reply = (
            "For private prayer, record the person, the request in the pastor's own boundary language, whether it is private, "
            "who may know, and when to check back. I keep private prayer inside this workspace and will not put it in a public list or draft without approval."
        )
    elif is_care_seed:
        reply = (
            "For the first care case, capture the person's name, the care category, what happened, the latest contact, "
            "who should know, and the next pastoral step. Keep medical, grief, and crisis details factual and scoped to this workspace; "
            "I can draft a follow-up for review after the care case exists."
        )
    else:
        reply = (
            "For the first person, capture their name, contact details if appropriate, why they need attention, the latest pastoral touch, "
            "and one next follow-up step. Sensitive care or prayer details should stay scoped to this workspace."
        )
    return AssistantChatResponse(
        reply=reply,
        intent="first_record_coaching",
        mode=effective_mode,
        actions=[step],
        suggested_prompts=_data_seed_suggested_prompts(step),
        profile=_profile_response(profile, account),
    )


def _private_prayer_guidance_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    prayers = (
        scoped_query(db.query(PrayerRequest), PrayerRequest, account)
        .filter(PrayerRequest.status == "active")
        .order_by(PrayerRequest.is_private.desc(), PrayerRequest.updated_at.asc())
        .limit(3)
        .all()
    )
    reply = (
        "For private prayer, capture the person if you can, the request in your own boundary language, who may know, "
        "and when to check back. If you do not know the person yet, I can still keep it as a private request-level follow-up, "
        "but I will not put it in a public list or draft anything outward without approval."
    )
    if prayers:
        reply += " I attached the active prayer request cards I can work from."
    return AssistantChatResponse(
        reply=reply,
        intent="private_prayer_guidance",
        mode=effective_mode,
        actions=[_prayer_desk_item(prayer) for prayer in prayers],
        suggested_prompts=["Who needs prayer follow-up?", "Draft a private prayer follow-up from this request.", "Explain the approval rules."],
        profile=_profile_response(profile, account),
    )


def _care_case_guidance_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    care_cases = (
        scoped_query(db.query(CareNote), CareNote, account)
        .filter(CareNote.status == "active")
        .order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.asc())
        .limit(3)
        .all()
    )
    reply = (
        "For a care case, I need a real person first, the care category such as hospital, grief, crisis, or general, "
        "what happened, the latest contact, who should know, and the next pastoral step. "
        "Use the person's real name and the actual situation; for example, tell me the category, what happened, the latest contact, and the next visit or call to protect. "
        "I keep medical, grief, and crisis details scoped to this workspace and only draft or schedule follow-up for review."
    )
    if care_cases:
        reply += " I attached the active care cases I can work from."
    first_name = care_cases[0].member.full_name if care_cases and care_cases[0].member else None
    prompts = (
        [f"Draft a care follow-up for {first_name}.", f"Where can I fit a visit with {first_name}?", "Who needs care follow-up?"]
        if first_name
        else ["Help me add a ministry update.", "Who needs care follow-up?", "Explain the approval rules."]
    )
    return AssistantChatResponse(
        reply=reply,
        intent="care_case_guidance",
        mode=effective_mode,
        actions=[_care_desk_item(case) for case in care_cases],
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _care_visit_needs_context_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
    seed_step: Optional[DeskItem],
) -> AssistantChatResponse:
    response = _care_case_guidance_response(db, profile, account, effective_mode)
    response.reply = (
        "Before I can protect a care visit window, I need the real person or active care case first. "
        + response.reply
    )
    if seed_step and not response.actions:
        response.actions = [seed_step]
        response.suggested_prompts = [
            "Help me open the first care case.",
            "Help me add a ministry update.",
            "Show my setup steps.",
        ]
    return response


def _person_capture_guidance_response(
    seed_step: Optional[DeskItem],
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    reply = (
        "To add a person safely, give me a real name first, then optional contact details and why they need pastoral attention. "
        "If the update is really about a visitor, care case, or private prayer request, include that context so I can attach it to the right record instead of creating a vague person record. "
        "Use the person's real name and any actual contact details you have; I can keep care, visitor, or prayer context attached to that real record."
    )
    actions = [seed_step] if seed_step and seed_step.type == "data_seed" else []
    return AssistantChatResponse(
        reply=reply,
        intent="person_capture_guidance",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=["Help me add a ministry update.", "Show my setup steps.", "Explain the approval rules."],
        profile=_profile_response(profile, account),
    )


def _data_seed_chat_response(
    step: DeskItem,
    effective_mode: Literal["demo", "live"],
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    return AssistantChatResponse(
        reply=_data_seed_chat_reply(step),
        intent="data_seed_guidance",
        mode=effective_mode,
        actions=[step],
        suggested_prompts=_data_seed_suggested_prompts(step),
        profile=_profile_response(profile, account),
    )


def _first_week_plan(profile: PastorProfile, setup_steps: List[DeskItem]) -> List[dict]:
    plan = []
    seed_step = next((step for step in setup_steps if step.type == "data_seed"), None)
    integration_steps = [step for step in setup_steps if step.type == "integration_setup"]

    if seed_step:
        plan.append({
            "title": seed_step.title,
            "why": seed_step.subtitle or seed_step.detail or profile.followup_pain or "Start with one real person Marge should keep in view.",
            "action": seed_step.action,
            "form": seed_step.form,
            "guardrail": "Use real account-scoped ministry context; keep private care and prayer details inside this workspace.",
        })
    else:
        plan.append({
            "title": "Add real people who cannot slip",
            "why": profile.followup_pain or "Start with current care, visitor, absence, and prayer follow-up pressure.",
            "action": "Add first person",
            "form": "person",
            "guardrail": "Keep private prayer and care details scoped to this church workspace.",
        })

    for step in integration_steps[:3]:
        plan.append({
            "title": step.title,
            "why": step.detail or step.subtitle or "Connect a ministry system the church already uses.",
            "action": step.action,
            "provider": step.provider,
            "guardrail": "Use secure setup; do not paste API keys, OAuth secrets, passwords, or refresh tokens into chat.",
        })

    if _clean(profile.support_preferences):
        plan.append({
            "title": "Support the pastor the way he asked",
            "why": profile.support_preferences,
            "action": "Shape proactive nudges and summaries around this support style.",
            "guardrail": "Do not turn personal support preferences into external writes without approval.",
        })

    if _clean(profile.weekly_rhythm):
        plan.append({
            "title": "Protect the pastor's weekly rhythm",
            "why": profile.weekly_rhythm,
            "action": "Use this rhythm before proposing calendar blocks.",
            "guardrail": "Do not schedule over protected sermon, care, rest, or meeting rhythms without pastor approval.",
        })

    if _clean(profile.communication_style):
        plan.append({
            "title": "Draft the first follow-up in the pastor's voice",
            "why": f"Use a {profile.communication_style} voice for the first reviewable visitor, care, prayer, or absence draft.",
            "action": "Prepare draft for review",
            "guardrail": profile.guardrails or DEFAULT_GUARDRAILS,
        })

    if _clean(profile.faith_tradition):
        plan.append({
            "title": "Keep the church voice in every draft",
            "why": profile.faith_tradition,
            "action": "Review wording for fit before any external send",
            "guardrail": profile.guardrails or DEFAULT_GUARDRAILS,
        })

    plan.append({
        "title": "Run the morning desk daily",
        "why": "The goal is a pastoral secretary rhythm: brief, draft, schedule, and follow up before people fall through cracks.",
        "action": "Review priorities and approvals",
        "guardrail": "Review audit logs and connector policies as integrations are enabled.",
    })
    return _dedupe_plan(plan)[:8]


def _first_week_plan_chat_summary(plan: List[dict]) -> str:
    highlights = []
    for item in plan[:4]:
        title = _clean(item.get("title"))
        why = _clean(item.get("why"))
        if not title:
            continue
        if why:
            highlights.append(f"{title}: {why}")
        else:
            highlights.append(title)
    if not highlights:
        return "The plan starts with real ministry context, secure connector setup, and approval-guarded follow-up."
    return "Start with " + "; then ".join(highlights) + "."


def _prepare_connected_email_reply(db: Session, profile: PastorProfile, lower: str, account: Optional[ChurchAccount] = None) -> Optional[AssistantAction]:
    item = _best_connected_item(db, "email", lower, account)
    if not item:
        return None
    action = _prepare_email_reply_from_connected_item(db, profile, item, account)
    db.commit()
    db.refresh(action)
    return action


def _queue_synced_inbox_replies_requested(lower: str) -> bool:
    wants_queue = _mentions(lower, ["queue", "draft", "prepare"])
    wants_replies = _mentions(lower, ["reply", "replies", "response", "responses"])
    has_inbox_context = _mentions(lower, ["these", "synced", "inbox", "mailbox", "message", "messages", "email", "emails", "gmail", "outlook"])
    return wants_queue and wants_replies and has_inbox_context


def _meeting_prep_lookup_requested(lower: str) -> bool:
    if _mentions(lower, [
        "what meetings need prep",
        "which meetings need prep",
        "what events need prep",
        "which events need prep",
        "what meetings need preparation",
        "which meetings need preparation",
        "what events need preparation",
        "which events need preparation",
        "what meetings are coming up",
        "which meetings are coming up",
        "what events are coming up",
        "which events are coming up",
    ]):
        return True
    return _mentions(lower, ["what", "which", "show", "list"]) and _mentions(lower, ["meeting", "meetings", "event", "events"]) and _mentions(lower, ["prep", "preparation", "coming up", "upcoming"])


def _meeting_prep_lookup_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    connected_events = _connected_items(db, "calendar_event", account=account, limit=5)
    integrations = _integration_statuses(db, account, user)
    calendar_statuses = [
        item
        for item in integrations
        if item.provider in {"planning_center", "microsoft_365", "google_workspace", "breeze"}
    ]
    verified_calendar = [
        item
        for item in calendar_statuses
        if item.status in {"connected", "configured", "available"} and item.verified_at
    ]
    pending_meeting_actions = [
        action
        for action in _pending_assistant_actions(db, account)
        if action.action_type == "meeting_prep"
    ]
    actions = [_desk_item_from_action(action) for action in pending_meeting_actions[:3]]
    if len(actions) < 5:
        seen = {item.id for item in actions}
        for item in [_desk_item_from_connected_item(event) for event in connected_events[:5]]:
            if item.id not in seen:
                actions.append(item)
                seen.add(item.id)
            if len(actions) >= 5:
                break

    if connected_events:
        lines = "; ".join(
            f"{item.title}: {item.subtitle or _date_label(item.occurred_at) or item.snippet or 'upcoming event'}"
            for item in connected_events[:4]
        )
        refresh_state = _connected_items_refresh_state(profile, integrations, connected_events)
        reply = (
            f"These synced meetings or events need prep in view: {lines}. "
            "I can prepare the next meeting brief as a review item, and I will not change the external calendar without approval."
        )
        if refresh_state["note"]:
            reply += f" {refresh_state['note']}"
    elif pending_meeting_actions:
        refresh_state = _connected_refresh_state_for_providers(
            profile,
            integrations,
            [action.source for action in pending_meeting_actions],
        )
        lines = "; ".join(action.title for action in pending_meeting_actions[:4])
        reply = (
            f"These meeting prep items are already waiting in the approval queue: {lines}. "
            "Review them before making calendar or follow-up changes."
        )
        if refresh_state["note"]:
            reply += f" {refresh_state['note']}"
    else:
        refresh_state = {"note": "", "actions": [], "refresh_prompt": None}
        setup_steps = _calendar_context_setup_steps(profile, integrations)
        if verified_calendar:
            display = verified_calendar[0].display_name
            reply = (
                f"I do not have synced calendar meetings to prep yet. {display} credentials are checked, so the next safe step is to "
                "sync calendar context when you want me to import upcoming meetings for review."
            )
            prompts = [f"Sync {display}.", "Open integrations.", "Explain the approval rules."]
        else:
            reply = (
                "I do not have synced calendar meetings to prep yet because no calendar-capable church tool has completed secure setup "
                "and a no-sync credential check. I attached the next setup or credential-check cards before any calendar sync."
            )
            prompts = _connector_setup_or_check_prompts(setup_steps)
            actions.extend(setup_steps)
    if connected_events or pending_meeting_actions:
        if refresh_state["actions"]:
            actions = _dedupe_desk_items(actions + refresh_state["actions"][:1])
        prompts = ["Prepare my next meeting.", "Show my approvals."]
        if refresh_state["refresh_prompt"]:
            prompts.append(refresh_state["refresh_prompt"])
    return AssistantChatResponse(
        reply=reply,
        intent="meeting_prep_lookup",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _calendar_context_setup_steps(profile: PastorProfile, integrations: List[IntegrationStatus]) -> List[DeskItem]:
    providers: List[str] = []
    for provider in _recommended_providers(profile) + ["planning_center", "microsoft_365", "google_workspace", "breeze"]:
        if provider in {"planning_center", "microsoft_365", "google_workspace", "breeze"} and provider not in providers:
            providers.append(provider)
    steps = []
    for provider in providers:
        step = _provider_setup_or_check_step(
            profile,
            integrations,
            provider,
            subtitle=f"Use {_provider_display_name(provider)} when you want Marge to prep meetings from real calendar context.",
            detail="Start secure setup first. Marge checks credentials without syncing before any calendar context is imported.",
        )
        if step:
            steps.append(step)
    return steps


def _calendar_sync_not_connected_response(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    steps = _calendar_context_setup_steps(profile, integrations)
    reply = (
        "I cannot sync calendar context yet because no calendar-capable church tool has completed secure setup "
        "and a no-sync credential check. I attached the next calendar setup or credential-check cards. "
        "After credentials pass, ask me to sync the calendar again and I will import upcoming ministry events as review context."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="sync_calendar_not_connected",
        mode=effective_mode,
        actions=steps[:4],
        suggested_prompts=_connector_setup_or_check_prompts(steps),
        profile=_profile_response(profile, account),
    )


def _generic_inbox_sync_requested(lower: str) -> bool:
    if not _mentions(lower, ["sync", "refresh", "pull"]):
        return False
    if _mentions(lower, ["google", "gmail", "microsoft", "microsoft 365", "office 365", "outlook"]):
        return False
    return _mentions(lower, ["inbox", "mailbox", "email", "emails", "mail", "messages"])


def _generic_calendar_sync_requested(lower: str) -> bool:
    if not _mentions(lower, ["sync", "refresh", "pull"]):
        return False
    if _provider_from_chat(lower):
        return False
    return _mentions(lower, ["calendar", "calendars", "event", "events", "schedule", "schedules", "meeting", "meetings"])


def _connected_tools_sync_requested(lower: str) -> bool:
    if not _mentions(lower, ["sync", "refresh", "pull"]):
        return False
    if _provider_from_chat(lower):
        return False
    return _mentions(lower, [
        "connected tools",
        "connected tool",
        "church tools",
        "ministry tools",
        "integrations",
        "connectors",
        "all tools",
        "the tools",
    ])


def _connected_tools_sync_response(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    integrations = _integration_statuses(db, account, user)
    ordered = _ordered_syncable_integration_statuses(profile, integrations)
    verified = [
        item
        for item in ordered
        if item.status in {"connected", "configured"} and bool(item.verified_at)
    ]
    unchecked_ready = [
        item
        for item in ordered
        if item.status in {"connected", "configured"} and not item.verified_at
    ]

    if verified:
        synced = []
        failed = []
        for item in verified:
            try:
                result = _sync_connected_provider(db, item.provider, account, user)
                synced.append((item, result))
            except HTTPException as exc:
                failed.append((item, _redact_secret_text(exc.detail)))
        if synced:
            lines = "; ".join(
                f"{item.display_name}: {result.items_seen} item(s), {result.actions_prepared} review item(s)"
                for item, result in synced
            )
            reply = (
                f"I synced the connected church tools that have completed credential checks: {lines}. "
                "This was read-side context only; anything sensitive or external stays in the review queue until you approve it."
            )
            if failed:
                failed_text = "; ".join(f"{item.display_name}: {detail}" for item, detail in failed)
                reply += f" I did not sync these tools: {failed_text}."
            return AssistantChatResponse(
                reply=reply,
                intent="sync_connected_tools",
                mode=effective_mode,
                actions=_connected_context_desk_items(db, account=account, limit=5),
                suggested_prompts=["Show connected context.", "What should I approve first?", "Explain the approval rules."],
                profile=_profile_response(profile, account),
            )

    if unchecked_ready:
        item = unchecked_ready[0]
        try:
            verification = _verify_integration(db, item.provider, account, user)
        except HTTPException as exc:
            check_step = _integration_check_credentials_step(item, profile)
            return AssistantChatResponse(
                reply=(
                    f"I stopped before syncing {item.display_name} because credentials need to be checked first. "
                    f"The safe credential check failed: {_redact_secret_text(exc.detail)}. No ministry data was imported and no actions were queued."
                ),
                intent="integration_verify_failed_before_sync",
                mode=effective_mode,
                actions=[check_step],
                suggested_prompts=[f"Check {item.display_name} credentials.", "Open integrations.", f"Start {item.display_name} setup."],
                profile=_profile_response(profile, account),
            )
        identity_summary = _verification_identity_summary(verification.identity)
        others = [other.display_name for other in unchecked_ready[1:]]
        reply = (
            f"I checked {item.display_name} credentials first. They verified without syncing people, email, calendar, or attendance data, "
            "and I did not queue any actions yet. Ask me to sync the connected tools again when you want me to import fresh ministry context."
        )
        if identity_summary:
            reply += f" Non-secret identity check: {identity_summary}."
        if others:
            reply += f" These tools still need credential checks before sync: {', '.join(others)}."
        return AssistantChatResponse(
            reply=reply,
            intent="integration_verified_before_sync",
            mode=effective_mode,
            actions=[],
            suggested_prompts=["Sync the connected tools.", "Show connected context.", "Explain the approval rules."],
            profile=_profile_response(profile, account),
        )

    setup_steps = [
        step
        for step in _setup_steps(profile, integrations, needs_seed_context=False)
        if step.type == "integration_setup"
    ]
    tool_names = [item["display_name"] for item in _recommended_provider_statuses(profile, integrations)]
    saved_tool_text = f" I see {', '.join(tool_names)} in the saved tools for this church." if tool_names else ""
    reply = (
        "I cannot sync connected tools yet because no church tool has completed secure setup and a no-sync credential check. "
        f"No ministry data was imported and no review actions were queued.{saved_tool_text} "
        "Start secure setup, check credentials, then ask me to sync again."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="connected_tools_sync_precheck",
        mode=effective_mode,
        actions=setup_steps[:4],
        suggested_prompts=["Open integrations.", "What should I connect first?", "How do secure connections work?"],
        profile=_profile_response(profile, account),
    )


def _ordered_syncable_integration_statuses(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
) -> List[IntegrationStatus]:
    syncable = {"planning_center", "microsoft_365", "google_workspace", "breeze", "rock"}
    by_provider = {item.provider: item for item in integrations if item.provider in syncable}
    order = []
    for provider in _recommended_providers(profile) + ["planning_center", "microsoft_365", "google_workspace", "breeze", "rock"]:
        if provider in syncable and provider not in order:
            order.append(provider)
    return [by_provider[provider] for provider in order if provider in by_provider]


def _sync_connected_provider(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
) -> IntegrationSyncResponse:
    if provider == "planning_center":
        return _sync_planning_center(db, people_limit=25, calendar_days=14, account=account, user=user)
    if provider == "microsoft_365":
        return _sync_microsoft_365(db, email_limit=5, calendar_days=14, account=account, user=user)
    if provider == "google_workspace":
        return _sync_google_workspace(db, email_limit=5, calendar_days=14, account=account, user=user)
    if provider == "breeze":
        return _sync_breeze(db, people_limit=25, calendar_days=14, account=account)
    if provider == "rock":
        return _sync_rock_rms(db, account=account)
    raise HTTPException(status_code=404, detail=f"Unknown integration provider: {provider}")


def _provider_sync_not_ready_response(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    provider: str,
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> Optional[dict]:
    status = next((item for item in integrations if item.provider == provider), None)
    if status and status.status in {"connected", "configured", "available"}:
        return None
    step = _provider_setup_or_check_step(
        profile,
        integrations,
        provider,
        subtitle=f"Use {_provider_display_name(provider)} when this church is ready for Marge to import real ministry context.",
        detail="Start secure setup first. Marge checks credentials without syncing before any people, email, calendar, or attendance context is imported.",
    )
    actions = [step] if step else []
    display = _provider_display_name(provider)
    return {
        "reply": (
            f"I cannot sync {display} yet because it has not completed secure setup and a no-sync credential check. "
            "I attached the next setup or credential-check card. After credentials pass, ask me to sync again and I will import current ministry context for review."
        ),
        "intent": _provider_sync_not_ready_intent(provider),
        "mode": effective_mode,
        "actions": actions,
        "suggested_prompts": _connector_setup_or_check_prompts(actions),
        "profile": _profile_response(profile, account),
    }


def _provider_sync_not_ready_intent(provider: str) -> str:
    if provider == "rock":
        return "sync_rock_rms_not_connected"
    return f"sync_{provider}_not_connected"


def _mail_sync_provider(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
) -> Optional[str]:
    statuses = {item.provider: item for item in _integration_statuses(db, account, user)}
    ready = [
        provider
        for provider in ["google_workspace", "microsoft_365"]
        if statuses.get(provider) and statuses[provider].status == "connected"
    ]
    if not ready:
        return None
    recommended = _recommended_providers(profile)
    for provider in recommended:
        if provider in ready:
            return provider
    return ready[0]


def _calendar_sync_provider(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
) -> Optional[str]:
    statuses = {item.provider: item for item in _integration_statuses(db, account, user)}
    provider_order = ["planning_center", "microsoft_365", "google_workspace", "breeze"]
    ready = [
        provider
        for provider in provider_order
        if statuses.get(provider) and statuses[provider].status in {"connected", "configured"}
    ]
    if not ready:
        return None
    return ready[0]


def _calendar_write_provider(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    lower: str,
) -> Optional[str]:
    statuses = {item.provider: item for item in _integration_statuses(db, account, user)}
    ready = [
        provider
        for provider in ["microsoft_365", "google_workspace"]
        if statuses.get(provider)
        and statuses[provider].status == "connected"
        and statuses[provider].verified_at
    ]
    if not ready:
        return None
    requested = _provider_from_chat(lower)
    if requested in {"microsoft_365", "google_workspace"}:
        return requested if requested in ready else None
    recommended = _recommended_providers(profile)
    for provider in recommended:
        if provider in ready:
            return provider
    return ready[0]


def _calendar_details_help_requested(lower: str) -> bool:
    if _mentions(lower, [
        "what calendar details do you need",
        "what details do you need for a calendar",
        "what details do you need for calendar",
        "what information do you need for a calendar",
        "what info do you need for a calendar",
        "what do you need to queue a calendar event",
        "what do you need to create a calendar event",
    ]):
        return True
    return _mentions(lower, ["calendar", "event", "meeting"]) and _mentions(lower, [
        "what details",
        "what information",
        "what info",
        "need from me",
        "need to queue",
        "need to create",
    ])


def _calendar_details_help_response(
    db: Session,
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    effective_mode: Literal["demo", "live"],
    lower: str,
) -> AssistantChatResponse:
    write_provider = _calendar_write_provider(db, profile, account, user, lower)
    provider_sentence = ""
    actions: List[DeskItem] = []
    if write_provider:
        provider_sentence = (
            f"Your connected {_provider_display_name(write_provider)} calendar can stage the review item once those details are present. "
        )
    else:
        actions = _calendar_write_setup_steps(integrations)[:2]
        provider_sentence = (
            "I do not see a credential-checked Google Workspace or Microsoft 365 calendar yet, so I attached the setup or credential-check cards for those tools. "
        )

    example_prompt = _calendar_event_example_prompt(db, profile, account)
    reply = (
        "To queue a calendar event I need a title, a date in YYYY-MM-DD format, and a start time. "
        "Optional details are duration, location, and attendee email addresses. "
        f"Example format: {example_prompt} "
        f"{provider_sentence}"
        "I stage the event as a review item first. The external calendar write happens only after credentials are checked, writeback policy allows calendar_block, "
        "and you approve that exact event."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="calendar_event_details_help",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=_calendar_help_suggested_prompts(example_prompt, actions),
        profile=_profile_response(profile, account),
    )


def _calendar_help_suggested_prompts(example_prompt: str, actions: List[DeskItem]) -> List[str]:
    prompts = [example_prompt]
    setup_prompts = _connector_setup_or_check_prompts(actions)
    if setup_prompts:
        prompts.append(setup_prompts[0])
    prompts.append("Explain the approval rules.")
    result = []
    seen = set()
    for prompt in prompts:
        cleaned = (prompt or "").strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def _calendar_event_example_prompt(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> str:
    example_date = (date.today() + timedelta(days=1)).isoformat()
    subject = _calendar_event_example_subject(db, profile, account).strip(" .")
    return f"Queue a calendar event for {subject} on {example_date} at 3pm for 1 hour."


def _calendar_event_example_subject(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
) -> str:
    care = (
        scoped_query(db.query(CareNote), CareNote, account)
        .filter(CareNote.status == "active")
        .order_by(CareNote.created_at.desc())
        .first()
    )
    if care and care.member:
        category = _label(_enum_value(care.category)).lower()
        prefix = "care" if category == "general" else f"{category} care"
        return f"{prefix} follow-up with {care.member.full_name}"

    visitor = (
        scoped_query(db.query(Visitor), Visitor, account)
        .order_by(Visitor.visit_date.desc(), Visitor.created_at.desc())
        .first()
    )
    if visitor:
        return f"visitor follow-up with {visitor.full_name}"

    prayer = (
        scoped_query(db.query(PrayerRequest), PrayerRequest, account)
        .filter(PrayerRequest.status == "active")
        .order_by(PrayerRequest.created_at.desc())
        .first()
    )
    prayer_name = _prayer_subject_name(prayer) if prayer else None
    if prayer_name:
        return f"prayer follow-up with {prayer_name}"

    context = _clean(getattr(profile, "followup_pain", None) or getattr(profile, "ministry_priorities", None))
    if context:
        return f"{_short_context(context, 64).strip(' .')} follow-up"
    return "the follow-up you want protected"


def _calendar_write_setup_steps(integrations: List[IntegrationStatus]) -> List[DeskItem]:
    steps: List[DeskItem] = []
    for provider in ["google_workspace", "microsoft_365"]:
        integration = next((item for item in integrations if item.provider == provider), None)
        if not integration:
            continue
        sync_ready = integration.status in {"connected", "configured", "available"}
        if sync_ready and integration.verified_at:
            action = "Open integrations"
            title = f"Review {_provider_display_name(provider)} writeback policy"
            subtitle = f"{integration.display_name} credentials are checked; calendar writes still need approval and policy permission."
            detail = "Marge stages events as review items and writes externally only after exact approval."
            step_id = f"setup-calendar-policy-{provider}"
        elif sync_ready:
            action = "Check credentials"
            title = f"Check {integration.display_name} credentials"
            subtitle = f"Marge can see {integration.display_name}, but has not verified calendar access."
            detail = "Credential checks confirm access without importing people, email, calendar, or attendance context."
            step_id = f"setup-calendar-verify-{provider}"
        else:
            action = "Start secure setup"
            title = f"Connect {integration.display_name}"
            subtitle = f"Use {integration.display_name} when you want Marge to stage reviewable calendar events."
            detail = integration.config_hint or integration.secure_note
            step_id = f"setup-calendar-connect-{provider}"
        steps.append(DeskItem(
            id=step_id,
            type="integration_setup",
            title=title,
            subtitle=subtitle,
            detail=detail,
            priority="high" if provider == "google_workspace" else "medium",
            action=action,
            source="integrations",
            provider=provider,
        ))
    return steps


def _email_inbox_setup_steps(profile: PastorProfile, integrations: List[IntegrationStatus]) -> List[DeskItem]:
    steps: List[DeskItem] = []
    providers: List[str] = []
    for provider in _recommended_providers(profile) + ["google_workspace", "microsoft_365"]:
        if provider in {"google_workspace", "microsoft_365"} and provider not in providers:
            providers.append(provider)
    for provider in providers:
        integration = next((item for item in integrations if item.provider == provider), None)
        if not integration:
            continue
        sync_ready = integration.status in {"connected", "configured", "available"}
        if sync_ready and integration.verified_at:
            action = "Sync inbox"
            title = f"Sync {integration.display_name} inbox"
            subtitle = f"{integration.display_name} credentials are checked; syncing imports recent messages as review context."
            detail = "Marge can queue inbox-based reply drafts for review after you ask to sync. She will not send externally without exact approval."
            step_id = f"setup-email-sync-{provider}"
        elif sync_ready:
            action = "Check credentials"
            title = f"Check {integration.display_name} credentials"
            subtitle = f"Marge can see {integration.display_name}, but has not verified mailbox access."
            detail = "Credential checks confirm access without importing email, calendar, people, or attendance context."
            step_id = f"setup-email-verify-{provider}"
        else:
            action = "Start secure setup"
            title = f"Connect {integration.display_name}"
            subtitle = f"Use {integration.display_name} when you want Marge to triage ministry email and prepare reviewable replies."
            detail = integration.config_hint or integration.secure_note
            step_id = f"setup-email-connect-{provider}"
        steps.append(DeskItem(
            id=step_id,
            type="integration_setup",
            title=title,
            subtitle=subtitle,
            detail=detail,
            priority="high" if provider in set(_recommended_providers(profile)) else "medium",
            action=action,
            source="integrations",
            provider=provider,
        ))
    return steps


def _verified_email_integrations(integrations: List[IntegrationStatus]) -> List[IntegrationStatus]:
    return [
        item
        for item in integrations
        if item.provider in {"google_workspace", "microsoft_365"}
        and item.status in {"connected", "configured", "available"}
        and item.verified_at
    ]


def _email_setup_prompts(steps: List[DeskItem], *, verified_display: Optional[str] = None) -> List[str]:
    if verified_display:
        return [f"Sync {verified_display}.", "Open integrations.", "Explain the approval rules."]
    first = steps[0] if steps else None
    return [
        _setup_prompt(first) if first else "Open integrations.",
        "How do secure connections work?",
        "Explain the approval rules.",
    ]


def _mailbox_sync_not_connected_response(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    steps = _email_inbox_setup_steps(profile, integrations)
    reply = (
        "I cannot sync the inbox yet because no Google Workspace or Microsoft 365 mailbox has completed secure setup "
        "and a no-sync credential check. I attached the next mail setup or credential-check cards. "
        "After credentials pass, ask me to sync the inbox again and I will import recent ministry email as review context without sending anything."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="sync_mailbox_not_connected",
        mode=effective_mode,
        actions=steps[:3],
        suggested_prompts=_email_setup_prompts(steps),
        profile=_profile_response(profile, account),
    )


def _empty_synced_inbox_chat_response(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    user_message: str,
    profile: PastorProfile,
    effective_mode: Literal["demo", "live"],
    *,
    intent: str,
    drafting: bool,
) -> AssistantChatResponse:
    integrations = _integration_statuses(db, account, user)
    verified = _verified_email_integrations(integrations)
    steps = _email_inbox_setup_steps(profile, integrations)
    if verified:
        display = verified[0].display_name
        reply = (
            f"I do not have synced inbox items{' to draft from' if drafting else ''} yet. "
            f"{display} credentials are checked, so the next safe step is to sync when you want me to import recent ministry email as review context. "
            "I will queue anything sensitive for review and will not send externally without your exact approval."
        )
        actions = steps[:1]
        prompts = _email_setup_prompts(steps, verified_display=display)
    else:
        reply = (
            f"I do not have synced inbox items{' to draft from' if drafting else ''} yet because no Google Workspace or Microsoft 365 mailbox has completed secure setup and a no-sync credential check. "
            "I attached the next mail setup or credential-check cards. After credentials pass, ask me to sync and I will import recent ministry email as review context without sending anything."
        )
        actions = steps[:2]
        prompts = _email_setup_prompts(steps)
    return _chat_turn_response(db, account, user, user_message,
        reply=reply,
        intent=intent,
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _draft_replies_empty_chat_response(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    user_message: str,
    profile: PastorProfile,
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    integrations = _integration_statuses(db, account, user)
    verified = _verified_email_integrations(integrations)
    steps = _email_inbox_setup_steps(profile, integrations)
    if verified:
        display = verified[0].display_name
        reply = (
            "I do not see a visitor, care, or prayer item that needs a local ministry draft right now. "
            f"{display} credentials are checked, but I do not have synced inbox items loaded; ask me to sync when you want me to import recent messages and queue reviewable replies."
        )
        actions = steps[:1]
        prompts = _email_setup_prompts(steps, verified_display=display)
    elif steps:
        reply = (
            "I do not see a visitor, care, or prayer item that needs a local ministry draft right now, "
            "and I cannot draft from the inbox until a Google Workspace or Microsoft 365 mailbox completes secure setup and a no-sync credential check. "
            "I attached the next mail setup cards so inbox drafting starts from real connected context."
        )
        actions = steps[:2]
        prompts = _email_setup_prompts(steps)
    else:
        reply = (
            "I do not see a visitor, care, prayer, or synced inbox item that needs a draft right now. "
            "Give me the real person or ministry update first, and I will prepare the reply for review."
        )
        actions = []
        prompts = ["Help me add a ministry update.", "Open integrations.", "Explain the approval rules."]
    return _chat_turn_response(db, account, user, user_message,
        reply=reply,
        intent="draft_replies_empty",
        mode=effective_mode,
        actions=actions,
        suggested_prompts=prompts,
        profile=_profile_response(profile, account),
    )


def _calendar_event_write_requested(lower: str) -> bool:
    if _mentions(lower, ["sync", "refresh", "what meetings", "what events", "coming up", "prepare my next meeting"]):
        return False
    wants_write = _mentions(lower, ["create", "add", "schedule", "put", "block", "queue"])
    calendar_context = _mentions(lower, ["calendar", "event", "meeting", "visit", "appointment", "block"])
    return wants_write and calendar_context


def _prepare_calendar_event_from_chat(
    db: Session,
    profile: PastorProfile,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    message: str,
    lower: str,
) -> Optional[AssistantAction]:
    provider = _calendar_write_provider(db, profile, account, user, lower)
    event = _calendar_event_payload_from_message(message)
    if not provider or not event:
        return None
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{provider}:chat_calendar:{event['start']}:{event['summary'].lower()}",
        action_type="calendar_block",
        title=event["summary"],
        description=event.get("description") or "Calendar event requested from chat.",
        payload={
            "calendar_event": event,
            "requested_from_chat": True,
            "guardrail": "Create this external calendar event only after pastor approval and connector writeback policy allow it.",
        },
        source="chat",
        external_provider=provider,
        related_type="calendar_event",
        related_id=None,
        privacy_level="pastoral",
        account=account,
    )
    _audit(
        db,
        "assistant_action.calendar_event_prepared_from_chat",
        f"Queued calendar event from chat: {event['summary']}",
        provider=provider,
        account=account,
        action_id=action.id,
        payload={"start": event["start"], "end": event["end"], "has_attendees": bool(event.get("attendees"))},
    )
    db.commit()
    db.refresh(action)
    return action


def _calendar_event_payload_from_message(message: str) -> Optional[dict]:
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
    time_match = re.search(r"\b(?:at|@)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", message, flags=re.IGNORECASE)
    if not date_match or not time_match:
        return None

    date_text = date_match.group(1)
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    meridiem = (time_match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None

    start_dt = datetime.fromisoformat(f"{date_text}T{hour:02d}:{minute:02d}:00")
    duration_minutes = _calendar_duration_minutes(message) or 60
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    summary = _calendar_summary_from_message(message, date_match.start(), time_match.start())
    if not summary:
        return None

    event = {
        "summary": summary,
        "subject": summary,
        "description": "Queued from Marge chat for pastor approval.",
        "start": start_dt.isoformat(timespec="seconds"),
        "end": end_dt.isoformat(timespec="seconds"),
    }
    location = _calendar_location_from_message(message)
    if location:
        event["location"] = location
    attendees = _calendar_attendees_from_message(message)
    if attendees:
        event["attendees"] = attendees
    return event


def _calendar_duration_minutes(message: str) -> Optional[int]:
    match = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b", message, flags=re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        return max(1, int(amount * 60))
    return max(1, int(amount))


def _calendar_summary_from_message(message: str, date_index: int, time_index: int) -> str:
    before_date = message[:date_index]
    match = re.search(r"\bfor\s+(.+?)(?:\s+on\s+)?$", before_date, flags=re.IGNORECASE)
    if match:
        candidate = match.group(1)
    else:
        candidate = before_date
    candidate = re.sub(r"\b(create|add|schedule|put|block|queue|an?|the|outlook|google|calendar|event|meeting)\b", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -:.,")
    if candidate:
        return candidate[:1].upper() + candidate[1:]
    fallback = message[:min(date_index, time_index)].strip(" -:.,")
    return fallback or "Calendar event"


def _calendar_location_from_message(message: str) -> Optional[str]:
    match = re.search(r"\b(?:location|at location)\s+(.+?)(?:\s+with\s+[\w.+-]+@|\s+attendees?\s+[\w.+-]+@|$)", message, flags=re.IGNORECASE)
    if not match:
        return None
    return _clean(match.group(1).strip(" ."))


def _calendar_attendees_from_message(message: str) -> List[str]:
    return sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)))


def _prepare_connected_email_replies(
    db: Session,
    profile: PastorProfile,
    lower: str,
    account: Optional[ChurchAccount] = None,
    *,
    limit: int = 3,
) -> List[AssistantAction]:
    provider = _provider_from_chat(lower)
    items = _connected_items_filtered(db, account=account, provider=provider, item_type="email", limit=max(limit, 1))
    prepared: List[AssistantAction] = []
    for item in items[:limit]:
        prepared.append(_prepare_email_reply_from_connected_item(db, profile, item, account))
    if prepared:
        _audit(
            db,
            "assistant_actions.synced_inbox_replies_prepared_from_chat",
            f"Prepared {len(prepared)} synced inbox reply draft(s) from chat.",
            provider=provider,
            account=account,
            payload={"count": len(prepared), "provider": provider},
        )
        db.commit()
        for action in prepared:
            db.refresh(action)
    return prepared


def _prepare_email_reply_from_connected_item(
    db: Session,
    profile: PastorProfile,
    item: ConnectedContextItem,
    account: Optional[ChurchAccount] = None,
) -> AssistantAction:
    provider = item.provider or "connected_context"
    payload = _json_loads(item.payload_json).get("email") or {}
    raw_from = payload.get("from") or item.subtitle or ""
    display_name, email_address = parseaddr(raw_from)
    if email_address and "@" not in email_address:
        display_name = display_name or email_address
        email_address = ""
    recipient = _recipient_first_name(display_name or email_address or raw_from)
    subject = item.title or payload.get("subject") or "Your note"
    body = _connected_email_reply_body(profile, recipient, subject, item.snippet)
    email_payload = {
        "to": email_address or raw_from,
        "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
        "body": body,
        "source_message_id": item.external_id,
        "source_thread_id": item.thread_id,
    }
    draft_context = {
        "drafting_voice": profile.communication_style or "warm and brief",
        "church_context": profile.church_context,
        "faith_tradition": profile.faith_tradition,
        "support_preferences": profile.support_preferences,
        "guardrail": profile.guardrails or DEFAULT_GUARDRAILS,
    }
    member = _member_for_connected_email_sender(db, account, display_name, email_address, raw_from)
    if member:
        _add_member_review_context(db, account, member, draft_context)
    external_provider = provider if provider in {"google_workspace", "microsoft_365"} and email_address else None
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{provider}:email_reply:{item.external_id}",
        action_type="email_draft",
        title=f"Draft reply to {subject}",
        description=f"Draft reply for {raw_from or 'synced inbox item'}",
        payload={"connected_item_id": item.id, "email": email_payload, "draft_context": draft_context},
        source=provider,
        external_provider=external_provider,
        related_type="email",
        related_id=item.id,
        privacy_level="pastoral",
        account=account,
    )
    db.flush()
    item.action_id = action.id
    _audit(
        db,
        "assistant_action.created_from_connected_email",
        f"Prepared reply draft from synced email: {subject}",
        provider=provider,
        account=account,
        action_id=action.id,
        connected_item_id=item.id,
        payload={"external_provider": external_provider, "has_recipient": bool(email_address)},
    )
    return action


def _member_for_connected_email_sender(
    db: Session,
    account: Optional[ChurchAccount],
    display_name: Optional[str],
    email_address: Optional[str],
    raw_from: Optional[str],
) -> Optional[Member]:
    query = scoped_query(db.query(Member), Member, account)
    email = _clean(email_address)
    if email and "@" in email:
        existing = query.filter(Member.email.ilike(email)).first()
        if existing:
            return existing
    name = _clean(display_name)
    if not name and raw_from and "@" not in raw_from:
        name = _clean(raw_from)
    if not name:
        return None
    first, last = _split_person_name(name)
    if first and last:
        return query.filter(Member.first_name.ilike(first), Member.last_name.ilike(last)).first()
    return None


def _prepare_connected_meeting_prep(db: Session, profile: PastorProfile, lower: str, account: Optional[ChurchAccount] = None) -> Optional[AssistantAction]:
    item = _best_connected_item(db, "calendar_event", lower, account)
    if not item:
        return None
    provider = item.provider or "connected_context"
    payload = _json_loads(item.payload_json).get("calendar_event") or {}
    summary = item.title or payload.get("summary") or payload.get("subject") or payload.get("name") or "calendar event"
    prep = _connected_meeting_prep_text(profile, item, payload)
    action = _upsert_prepared_action(
        db,
        dedupe_key=f"{provider}:meeting_prep:{item.external_id}",
        action_type="meeting_prep",
        title=f"Prepare for {summary}",
        description=item.subtitle or item.snippet or "Upcoming calendar event",
        payload={
            "connected_item_id": item.id,
            "calendar_event": payload,
            "brief": prep,
            "review_context": _meeting_review_context_for_event(db, account, payload),
        },
        source=provider,
        external_provider=None,
        related_type="calendar_event",
        related_id=item.id,
        privacy_level="pastoral",
        account=account,
    )
    db.flush()
    item.action_id = action.id
    _audit(
        db,
        "assistant_action.created_from_connected_calendar",
        f"Prepared meeting brief from synced calendar event: {summary}",
        provider=provider,
        account=account,
        action_id=action.id,
        connected_item_id=item.id,
    )
    db.commit()
    db.refresh(action)
    return action


def _meeting_review_context_for_event(
    db: Session,
    account: Optional[ChurchAccount],
    event: dict,
) -> dict:
    members = _members_for_calendar_event(db, account, event)
    if not members:
        return {}
    context = {
        "matched_members": [
            {"member_id": member.id, "member_name": member.full_name}
            for member in members[:5]
        ],
    }
    member_context = []
    for member in members[:5]:
        care_cases = _member_active_care_cases(db, account, member)
        prayers = _member_active_prayers(db, account, member)
        context_row = {"member_id": member.id, "member_name": member.full_name}
        if care_cases:
            context_row["active_care"] = [_care_case_summary(case) for case in care_cases[:2]]
        if prayers:
            context_row["active_prayer"] = [_prayer_summary(prayer) for prayer in prayers[:2]]
        if context_row.get("active_care") or context_row.get("active_prayer"):
            member_context.append(context_row)
    if member_context:
        context["member_context"] = member_context
    preferences = []
    for member in members[:5]:
        for note in _member_preference_notes_for_draft(db, account, member):
            preferences.append({
                "member_id": member.id,
                "member_name": member.full_name,
                "note_id": note.id,
                "text": _short_context(note.note_text, 180),
            })
    if preferences:
        if len(members) == 1:
            context["member_id"] = members[0].id
            context["member_name"] = members[0].full_name
        context["member_preferences"] = preferences[:8]
        context["member_preference_guardrail"] = (
            "Pastor-only review context. Respect these preferences while preparing for the meeting, "
            "but do not write or send sensitive preference details externally."
        )
    return context


def _members_for_calendar_event(
    db: Session,
    account: Optional[ChurchAccount],
    event: dict,
) -> List[Member]:
    matches: List[Member] = []
    seen: set[int] = set()
    for participant in _calendar_event_participants(event):
        member = _member_for_calendar_participant(db, account, participant)
        if member and member.id not in seen:
            matches.append(member)
            seen.add(member.id)
    return matches


def _calendar_event_participants(event: dict) -> List[dict]:
    participants: List[dict] = []

    def add(value: Any) -> None:
        if not value:
            return
        if isinstance(value, dict):
            name = _clean(value.get("displayName") or value.get("name"))
            email_address = _clean(value.get("email") or value.get("address"))
        else:
            display_name, parsed_email = parseaddr(str(value))
            name = _clean(display_name)
            email_address = _clean(parsed_email)
        if name or email_address:
            participants.append({"name": name, "email": email_address})

    add(event.get("organizer"))
    add(event.get("creator"))
    for attendee in event.get("attendees") or []:
        add(attendee)
    return participants


def _member_for_calendar_participant(
    db: Session,
    account: Optional[ChurchAccount],
    participant: dict,
) -> Optional[Member]:
    query = scoped_query(db.query(Member), Member, account)
    email = _clean(participant.get("email"))
    if email and "@" in email:
        existing = query.filter(Member.email.ilike(email)).first()
        if existing:
            return existing
    name = _clean(participant.get("name"))
    if not name:
        return None
    first, last = _split_person_name(name)
    if first and last:
        return query.filter(Member.first_name.ilike(first), Member.last_name.ilike(last)).first()
    return None


def _best_connected_item(db: Session, item_type: str, lower: str, account: Optional[ChurchAccount] = None) -> Optional[ConnectedContextItem]:
    items = _connected_items(db, item_type, account=account, limit=10)
    if not items:
        return None
    meaningful_terms = [term for term in lower.replace("?", " ").replace(",", " ").split() if len(term) > 3]
    for item in items:
        haystack = " ".join([item.title or "", item.subtitle or "", item.snippet or ""]).lower()
        if any(term in haystack for term in meaningful_terms):
            return item
    return items[0]


def _recipient_first_name(value: str) -> str:
    cleaned = (value or "").replace('"', "").strip()
    if not cleaned:
        return "there"
    if "@" in cleaned and " " not in cleaned:
        cleaned = cleaned.split("@", 1)[0].replace(".", " ").replace("_", " ")
    return cleaned.split()[0].strip(",") or "there"


def _connected_email_reply_body(profile: PastorProfile, recipient: str, subject: str, snippet: Optional[str]) -> str:
    pastor = pastor_display_name(_profile_pastor_name(profile))
    context = f" I saw your note about {snippet.strip()}" if snippet else ""
    return (
        f"Hi {recipient},\n\n"
        f"Thanks for reaching out.{context}. I am grateful you told me, and I would be glad to follow up with you.\n\n"
        "Would you be open to a quick call or a time to talk this week?\n\n"
        f"- {pastor}"
    )


def _connected_meeting_prep_text(profile: PastorProfile, item: ConnectedContextItem, payload: dict) -> str:
    summary = item.title or payload.get("summary") or payload.get("subject") or payload.get("name") or "this meeting"
    when = item.subtitle or payload.get("when") or "Time was not included by the connected calendar."
    description = item.snippet or payload.get("description") or payload.get("bodyPreview") or "No description was included by the connected calendar."
    context = profile.church_context or "Ask the pastor for local ministry context before making assumptions."
    tradition = profile.faith_tradition or "Ask the pastor what church voice or tradition to respect before drafting outward."
    guardrails = profile.guardrails or DEFAULT_GUARDRAILS
    return (
        f"Meeting: {summary}\n"
        f"When: {when}\n"
        f"Synced note: {description}\n\n"
        f"Ministry context to remember: {context}\n"
        f"Church voice to respect: {tradition}\n"
        "Suggested posture: listen first, clarify the next pastoral step, and decide whether this needs a follow-up note.\n"
        f"Guardrail: {guardrails}"
    )


def _upsert_prepared_action(
    db: Session,
    *,
    dedupe_key: str,
    action_type: str,
    title: str,
    description: Optional[str],
    payload: dict,
    source: Optional[str],
    external_provider: Optional[str],
    related_type: Optional[str],
    related_id: Optional[int],
    privacy_level: str,
    account: Optional[ChurchAccount] = None,
) -> AssistantAction:
    scoped_dedupe_key = f"account:{account.id}:{dedupe_key}" if account else dedupe_key
    action = scoped_query(db.query(AssistantAction), AssistantAction, account).filter(AssistantAction.dedupe_key == scoped_dedupe_key).first()
    if not action:
        action = AssistantAction(dedupe_key=scoped_dedupe_key, account_id=_account_id(account))
        db.add(action)
    if action.status in {"executed", "skipped"}:
        return action
    action.action_type = action_type
    action.title = title
    action.description = description
    action.payload_json = _json_dumps(payload)
    action.source = source
    action.external_provider = external_provider
    action.related_type = related_type
    action.related_id = related_id
    action.privacy_level = privacy_level
    action.status = action.status or "pending"
    return action


def _get_action_or_404(db: Session, action_id: int, account: Optional[ChurchAccount] = None) -> AssistantAction:
    action = scoped_query(db.query(AssistantAction), AssistantAction, account).filter(AssistantAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Assistant action not found.")
    return action


def _action_response(action: AssistantAction) -> AssistantActionResponse:
    return AssistantActionResponse(
        id=action.id,
        action_type=action.action_type,
        status=action.status,
        title=action.title,
        description=action.description,
        payload=_json_loads(action.payload_json),
        source=action.source,
        external_provider=action.external_provider,
        related_type=action.related_type,
        related_id=action.related_id,
        privacy_level=action.privacy_level,
        created_at=action.created_at,
        updated_at=action.updated_at,
        approved_at=action.approved_at,
        executed_at=action.executed_at,
        skipped_at=action.skipped_at,
    )


def _desk_item_from_action(action: AssistantAction) -> DeskItem:
    payload = _json_loads(action.payload_json)
    setup_step = payload.get("setup_step") if isinstance(payload.get("setup_step"), dict) else None
    title = action.title
    subtitle = action.status
    detail = action.description
    item_type = action.action_type
    action_label = "Review"
    source = action.source or "assistant_action"
    form = None
    provider = None
    priority = "medium"
    if setup_step:
        title = setup_step.get("title") or title
        detail = detail or setup_step.get("detail") or setup_step.get("subtitle")
        item_type = setup_step.get("type") or item_type
        action_label = setup_step.get("action") or action_label
        source = setup_step.get("source") or source
        form = setup_step.get("form")
        provider = setup_step.get("provider")
        priority = setup_step.get("priority") or priority
    return DeskItem(
        id=f"action-{action.id}",
        type=item_type,
        title=title,
        subtitle=subtitle,
        detail=detail,
        priority=priority,
        action=action_label,
        source=source,
        related_id=action.related_id,
        form=form,
        provider=provider,
    )


def _json_dumps(payload: Optional[dict]) -> str:
    return json.dumps(payload or {}, default=str, separators=(",", ":"), sort_keys=True)


def _json_loads(payload_json: Optional[str]) -> dict:
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except json.JSONDecodeError:
        return {}


def _audit(
    db: Session,
    event_type: str,
    summary: str,
    *,
    actor: str = "pastor",
    provider: Optional[str] = None,
    account: Optional[ChurchAccount] = None,
    action_id: Optional[int] = None,
    connected_item_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> AuditLog:
    row = AuditLog(
        account_id=_account_id(account),
        event_type=event_type,
        actor=actor,
        summary=summary,
        provider=provider,
        action_id=action_id,
        connected_item_id=connected_item_id,
        payload_json=_json_dumps(payload) if payload else None,
    )
    db.add(row)
    return row


def _audit_response(row: AuditLog) -> AuditLogResponse:
    return AuditLogResponse(
        id=row.id,
        event_type=row.event_type,
        actor=row.actor,
        summary=row.summary,
        provider=row.provider,
        action_id=row.action_id,
        connected_item_id=row.connected_item_id,
        payload=_json_loads(row.payload_json),
        created_at=row.created_at,
    )


def _maybe_handle_action_command(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    profile: PastorProfile,
    priorities: List[DeskItem],
    message: str,
    lower: str,
    effective_mode: Literal["demo", "live"],
) -> Optional[AssistantChatResponse]:
    operation = _action_command_operation(lower)
    if not operation:
        return None

    action = _select_action_for_chat_command(db, account, lower, operation)
    if not action:
        integrations = _integration_statuses(db, account, user)
        setup_steps = _setup_steps(profile, integrations, needs_seed_context=_needs_seed_context(db, account, profile, effective_mode))
        if setup_steps:
            first_step = setup_steps[0]
            reply = (
                f"I do not see a matching approval item yet. The next useful step is {first_step.title}: "
                f"{first_step.detail or first_step.subtitle or first_step.action}. "
                "Once there is a real person, prayer, synced email, or care note to work from, I can help you review the exact staged item."
            )
            return AssistantChatResponse(
                reply=reply,
                intent="assistant_action_not_found",
                mode=effective_mode,
                actions=setup_steps[:3],
                suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
            )
        return AssistantChatResponse(
            reply=(
                "I do not see a matching approval item yet. Show me the approval queue, or give me a real visitor, "
                "prayer request, synced email, or care note and I can stage a reviewable item from it."
            ),
            intent="assistant_action_not_found",
            mode=effective_mode,
            actions=[],
            suggested_prompts=_suggested_prompts(profile, priorities),
        )

    if operation == "skip":
        if action.status in {"executed", "skipped"}:
            reply = f"That item is already {action.status}: {action.title}."
        else:
            action.status = "skipped"
            action.skipped_at = datetime.utcnow()
            _audit(db, "assistant_action.skipped_from_chat", f"Skipped assistant action from chat: {action.title}", account=account, action_id=action.id, provider=action.external_provider)
            db.commit()
            db.refresh(action)
            reply = f"Skipped: {action.title}."
        return AssistantChatResponse(
            reply=reply,
            intent="assistant_action_skipped",
            mode=effective_mode,
            saved=True,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Show my approvals.", "What should I handle next?"],
        )

    if operation == "approve":
        if action.status == "executed":
            reply = f"That item is already done: {action.title}."
        elif action.status == "skipped":
            reply = f"That item was skipped. Open the approval queue if you want to prepare it again: {action.title}."
        else:
            action.status = "approved"
            action.approved_at = action.approved_at or datetime.utcnow()
            _audit(db, "assistant_action.approved_from_chat", f"Approved assistant action from chat: {action.title}", account=account, action_id=action.id, provider=action.external_provider)
            db.commit()
            db.refresh(action)
            reply = f"Approved: {action.title}. I still will not write to another system until you ask me to execute it and connector policy allows it."
        return AssistantChatResponse(
            reply=reply,
            intent="assistant_action_approved",
            mode=effective_mode,
            saved=True,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Execute the approved item.", "Show my approvals."],
        )

    if operation == "send_refusal":
        return AssistantChatResponse(
            reply="I cannot send email from chat. I can create a provider draft after you approve the exact recipient and wording, then you can send it from Gmail or Outlook.",
            intent="email_send_refused",
            mode=effective_mode,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Approve this draft.", "Create the approved draft."],
        )

    if operation == "reschedule":
        if action.action_type != "pastoral_reminder":
            return None
        due_label = _reminder_due_label(lower)
        if not due_label:
            return AssistantChatResponse(
                reply=f"I can move that local reminder, but I need the new timing first: {action.title}.",
                intent="pastoral_reminder_reschedule_needs_time",
                mode=effective_mode,
                actions=[_desk_item_from_action(action)],
                suggested_prompts=["Move this reminder to Friday.", "Show my reminders."],
            )
        payload = _json_loads(action.payload_json)
        reminder = payload.get("reminder") if isinstance(payload.get("reminder"), dict) else {}
        reminder["due"] = due_label
        payload["reminder"] = reminder
        action.payload_json = _json_dumps(payload)
        action.description = _pastoral_reminder_description(db, account, action, reminder)
        _audit(
            db,
            "assistant_action.pastoral_reminder_rescheduled_from_chat",
            f"Rescheduled local pastoral reminder from chat: {action.title}",
            account=account,
            action_id=action.id,
            payload={"due": due_label},
        )
        db.commit()
        db.refresh(action)
        return AssistantChatResponse(
            reply=f"Moved that local reminder to {due_label}: {action.title}. Nothing was sent, synced, or written externally.",
            intent="pastoral_reminder_rescheduled",
            mode=effective_mode,
            saved=True,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Show my reminders.", "What should I handle next?"],
        )

    if action.status != "approved" and not _action_can_execute_without_approval(action):
        return AssistantChatResponse(
            reply=f"I need you to approve this before I execute it: {action.title}. Say \"approve this\" if the exact item looks right.",
            intent="assistant_action_needs_approval",
            mode=effective_mode,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Approve this.", "Show my approvals."],
        )

    try:
        execution = _execute_approved_action(db, action, account, user)
    except HTTPException as exc:
        db.rollback()
        return AssistantChatResponse(
            reply=f"I could not execute that yet: {_redact_secret_text(exc.detail)}",
            intent="assistant_action_execution_blocked",
            mode=effective_mode,
            actions=[_desk_item_from_action(action)],
            suggested_prompts=["Open integrations.", "Explain the approval rules."],
        )
    db.commit()
    db.refresh(action)
    return AssistantChatResponse(
        reply=_action_execution_reply(action, execution),
        intent="assistant_action_executed",
        mode=effective_mode,
        saved=True,
        actions=[_desk_item_from_action(action)],
        suggested_prompts=["Show my approvals.", "What should I handle next?"],
    )


def _action_command_operation(lower: str) -> Optional[str]:
    if _approval_lookup_question(lower):
        return None
    context_terms = [
        "approval", "approvals", "queue", "item", "draft", "reply", "email", "message",
        "outlook", "microsoft", "gmail", "google", "person", "review", "action", "calendar",
        "event", "reminder", "pastoral reminder", "first", "this", "that", "it",
    ]
    has_action_reference = _action_id_from_chat(lower) is not None or _action_text_has(lower, context_terms)
    if not has_action_reference:
        return None
    if _action_text_has(lower, ["send", "send it", "send the email", "send this", "send the draft", "send the message"]):
        return "send_refusal"
    if _action_text_has(lower, ["reschedule", "move", "change", "push", "snooze"]) and (
        _action_text_has(lower, ["reminder", "pastoral reminder"])
        or (_action_text_has(lower, ["it", "this", "that"]) and _reminder_due_label(lower))
    ):
        return "reschedule"
    if _action_text_has(lower, ["skip", "dismiss", "ignore this", "cancel", "cancel this", "cancel that", "cancel reminder", "cancel the reminder"]):
        return "skip"
    if _action_text_has(lower, [
        "execute", "mark done", "done", "complete", "completed", "create the", "create a", "create an", "create it",
        "create draft", "create outlook", "create gmail", "create event", "prepare reply",
        "add to marge", "import this",
    ]):
        return "execute"
    if _action_text_has(lower, ["approve", "approved", "looks good", "go ahead"]):
        return "approve"
    return None


def _approval_lookup_question(lower: str) -> bool:
    patterns = [
        r"\bwhat\s+(?:item\s+)?should\s+(?:i|we)\s+approve\b",
        r"\bwhich\s+(?:item\s+)?should\s+(?:i|we)\s+approve\b",
        r"\bwhat\s+(?:item\s+)?should\s+(?:i|we)\s+review\b",
        r"\bwhich\s+(?:item\s+)?should\s+(?:i|we)\s+review\b",
        r"\bwhat\s+(?:needs|requires)\s+(?:my|our)\s+approval\b",
        r"\bwhat\s+(?:needs|requires)\s+(?:my|our)\s+review\b",
        r"\bshould\s+(?:i|we)\s+approve\b",
    ]
    return any(re.search(pattern, lower) for pattern in patterns)


def _approval_queue_lookup_requested(lower: str) -> bool:
    if _mentions(lower, ["approval", "approvals", "approve", "review queue"]):
        return True
    return _approval_lookup_question(lower) or _mentions(lower, [
        "what should i review first",
        "what should we review first",
        "what should i review next",
        "what should we review next",
        "what is waiting for review",
        "what's waiting for review",
        "what needs my review",
        "show my review items",
    ])


def _action_text_has(lower: str, terms: List[str]) -> bool:
    for term in terms:
        pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"
        if re.search(pattern, lower):
            return True
    return False


def _select_action_for_chat_command(
    db: Session,
    account: Optional[ChurchAccount],
    lower: str,
    operation: str,
) -> Optional[AssistantAction]:
    explicit_id = _action_id_from_chat(lower)
    statuses = ["pending", "approved"] if operation in {"skip", "send_refusal"} else (["pending"] if operation == "approve" else ["approved", "pending"])
    query = scoped_query(db.query(AssistantAction), AssistantAction, account).filter(AssistantAction.status.in_(statuses))
    if explicit_id:
        action = query.filter(AssistantAction.id == explicit_id).first()
        if action:
            return action
    actions = query.order_by(AssistantAction.updated_at.desc().nullslast(), AssistantAction.created_at.desc()).limit(20).all()
    if not actions:
        return None
    scored = sorted(((_action_command_score(action, lower, operation), action) for action in actions), key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    best_score, best_action = scored[0]
    generic_reference = _action_text_has(lower, ["first", "top", "next", "this", "that", "it", "item", "approval", "action", "reminder"])
    specific_terms = _specific_action_reference_terms(lower)
    if specific_terms and not _action_text_matches_specific_reference(best_action, specific_terms):
        return None
    if specific_terms:
        return best_action
    if generic_reference or best_score > 9:
        return best_action
    return None


def _action_id_from_chat(lower: str) -> Optional[int]:
    match = re.search(r"(?:action|item|approval|#)\s*#?(\d+)", lower)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _action_command_score(action: AssistantAction, lower: str, operation: str) -> int:
    text = " ".join([action.title or "", action.description or "", action.action_type or "", action.source or "", action.external_provider or ""]).lower()
    score = 1
    if operation == "execute" and action.status == "approved":
        score += 8
    if operation == "approve" and action.status == "pending":
        score += 8
    if operation == "reschedule" and action.action_type == "pastoral_reminder":
        score += 8
    if _action_text_has(lower, ["first", "top", "next", "this", "that", "it"]):
        score += 2
    keyword_groups = [
        (["outlook", "microsoft"], ["microsoft_365", "outlook"]),
        (["gmail", "google"], ["google_workspace", "gmail", "google"]),
        (["draft", "reply", "email", "message"], ["email_draft", "email_triage", "email", "reply"]),
        (["inbox"], ["email_triage", "inbox"]),
        (["person", "add to marge", "import"], ["person_review", "person"]),
        (["meeting", "calendar", "prep", "event"], ["meeting_prep", "calendar_block", "calendar", "event"]),
        (["reminder", "pastoral reminder"], ["pastoral_reminder", "reminder"]),
        (["reschedule", "move", "change", "push"], ["pastoral_reminder", "reminder"]),
    ]
    for triggers, targets in keyword_groups:
        if _action_text_has(lower, triggers) and any(target in text for target in targets):
            score += 5
    return score


def _specific_action_reference_terms(lower: str) -> List[str]:
    stop_words = {
        "add", "ahead", "approve", "approved", "approval", "approvals", "can", "cancel", "cancelled", "canceled", "could", "create",
        "change", "complete", "completed", "dismiss", "done", "draft", "email", "execute", "first", "for", "from", "gmail", "good",
        "ignore", "import", "item", "looks", "marge", "mark", "message", "next", "outlook", "please",
        "push", "queue", "reply", "reschedule", "review", "send", "skip", "snooze", "that", "the", "this", "to", "top", "move", "what", "which",
        "one", "two", "three", "four", "today", "tomorrow", "week", "weeks", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    }
    terms: List[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9'-]*", lower):
        normalized = token.strip("'")
        if len(normalized) < 3 or normalized in stop_words:
            continue
        terms.append(normalized)
    return sorted(set(terms))


def _action_text_matches_specific_reference(action: AssistantAction, terms: List[str]) -> bool:
    text = " ".join([
        action.title or "",
        action.description or "",
        action.action_type or "",
        action.source or "",
        action.external_provider or "",
        json.dumps(_json_loads(action.payload_json), default=str),
    ]).lower()
    return all(_action_text_has(text, [term]) for term in terms)


def _action_execution_reply(action: AssistantAction, execution: Optional[dict]) -> str:
    execution = execution or {}
    kind = execution.get("kind")
    if kind == "gmail_draft":
        return f"Created the Gmail draft for review: {action.title}. It was not sent."
    if kind == "outlook_draft":
        return f"Created the Outlook draft for review: {action.title}. It was not sent."
    if kind == "calendar_event":
        return f"Created the Google Calendar event: {action.title}."
    if kind == "outlook_calendar_event":
        return f"Created the Outlook calendar event: {action.title}."
    if kind == "email_reply_drafted":
        return f"Prepared a reply draft from that inbox review: {execution.get('draft_title') or action.title}. It is still in the approval queue."
    if kind == "local_member_upsert":
        verb = "Created" if execution.get("created") else "Updated"
        return f"{verb} local Marge people memory for {execution.get('member_name') or action.title}. I did not write back to the source system."
    if kind == "pastoral_reminder_completed":
        return f"Marked done: {action.title}."
    return f"Marked done: {action.title}."


def _execute_local_action(db: Session, action: AssistantAction, account: Optional[ChurchAccount] = None) -> Optional[dict]:
    if action.action_type == "person_review":
        return _execute_person_review_action(db, action, account)
    if action.action_type == "email_triage":
        return _execute_email_triage_action(db, action, account)
    if action.action_type == "pastoral_reminder":
        return {
            "kind": "pastoral_reminder_completed",
            "completed_at": datetime.utcnow().isoformat(),
        }
    return None


def _action_can_execute_without_approval(action: AssistantAction) -> bool:
    return action.action_type == "pastoral_reminder" and not action.external_provider and action.status == "pending"


def _execute_approved_action(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> Optional[dict]:
    if action.status != "approved" and not _action_can_execute_without_approval(action):
        raise HTTPException(status_code=409, detail="Approve this action before execution.")
    if action.external_provider:
        _ensure_external_write_allowed(db, action, account, user)
        execution = _execute_external_action(db, action, account, user)
    else:
        execution = _execute_local_action(db, action, account)
    if execution:
        payload = _json_loads(action.payload_json)
        payload["execution"] = execution
        action.payload_json = _json_dumps(payload)
    action.status = "executed"
    action.executed_at = datetime.utcnow()
    _audit(db, "assistant_action.executed", f"Executed assistant action: {action.title}", account=account, action_id=action.id, provider=action.external_provider)
    return execution


def _execute_email_triage_action(db: Session, action: AssistantAction, account: Optional[ChurchAccount] = None) -> dict:
    payload = _json_loads(action.payload_json)
    connected_item_id = payload.get("connected_item_id") or action.related_id
    connected_item = None
    if connected_item_id:
        connected_item = (
            scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
            .filter(ConnectedContextItem.id == connected_item_id, ConnectedContextItem.item_type == "email")
            .first()
        )
    if not connected_item:
        email_payload = payload.get("email") or {}
        external_id = email_payload.get("id") or email_payload.get("source_message_id")
        provider = action.source
        if external_id and provider:
            connected_item = (
                scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account)
                .filter(
                    ConnectedContextItem.provider == provider,
                    ConnectedContextItem.item_type == "email",
                    ConnectedContextItem.external_id == external_id,
                )
                .first()
            )
    if not connected_item:
        raise HTTPException(status_code=422, detail="Email triage actions require a synced email item.")

    profile = _get_or_create_profile(db, account)
    draft_action = _prepare_email_reply_from_connected_item(db, profile, connected_item, account)
    _audit(
        db,
        "assistant_action.email_triage_drafted",
        f"Prepared reply draft from email triage: {connected_item.title}",
        provider=connected_item.provider,
        account=account,
        action_id=action.id,
        connected_item_id=connected_item.id,
        payload={"draft_action_id": draft_action.id, "draft_external_provider": draft_action.external_provider},
    )
    return {
        "kind": "email_reply_drafted",
        "draft_action_id": draft_action.id,
        "draft_title": draft_action.title,
        "source": connected_item.provider,
        "connected_item_id": connected_item.id,
        "external_provider": draft_action.external_provider,
    }


def _execute_person_review_action(db: Session, action: AssistantAction, account: Optional[ChurchAccount] = None) -> dict:
    payload = _json_loads(action.payload_json)
    person = payload.get("person") or {}
    if not person:
        connected_item_id = payload.get("connected_item_id") or action.related_id
        connected_item = None
        if connected_item_id:
            connected_item = scoped_query(db.query(ConnectedContextItem), ConnectedContextItem, account).filter(ConnectedContextItem.id == connected_item_id).first()
        if connected_item:
            person = _json_loads(connected_item.payload_json).get("person") or {}
    if not person:
        raise HTTPException(status_code=422, detail="Person review actions require a synced person payload.")

    first_name, last_name = _person_names_from_payload(person)
    if not first_name:
        raise HTTPException(status_code=422, detail="Synced person payload does not include a usable name.")
    email = _clean(person.get("email"))
    phone = _clean(person.get("phone"))
    member = _find_existing_member_for_person(db, account, first_name, last_name, email)
    created = member is None
    if not member:
        member = Member(
            account_id=_account_id(account),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
        )
        db.add(member)
        db.flush()
    else:
        if email and not member.email:
            member.email = email
        if phone and not member.phone:
            member.phone = phone

    note_text = _synced_person_note_text(action, person, created)
    existing_note = (
        scoped_query(db.query(MemberNote), MemberNote, account)
        .filter(MemberNote.member_id == member.id, MemberNote.context_tag == "connector_import", MemberNote.note_text == note_text)
        .first()
    )
    if not existing_note:
        db.add(MemberNote(
            account_id=_account_id(account),
            member_id=member.id,
            context_tag="connector_import",
            note_text=note_text,
        ))
    action.related_type = "member"
    action.related_id = member.id
    _audit(
        db,
        "member.upserted_from_connected_person",
        f"{'Created' if created else 'Updated'} local person from {action.source or 'connected'} review: {member.full_name}",
        provider=action.source,
        account=account,
        action_id=action.id,
        payload={
            "member_id": member.id,
            "created": created,
            "source": action.source,
            "external_id": person.get("id"),
        },
    )
    return {
        "kind": "local_member_upsert",
        "member_id": member.id,
        "member_name": member.full_name,
        "created": created,
        "source": action.source,
        "external_id": person.get("id"),
    }


def _person_names_from_payload(person: dict) -> tuple[Optional[str], Optional[str]]:
    first = _clean(person.get("first_name"))
    last = _clean(person.get("last_name"))
    if first:
        return first, last or ""
    name = _clean(person.get("name"))
    if not name:
        return None, None
    first, last = _split_person_name(name)
    return first, last


def _find_existing_member_for_person(
    db: Session,
    account: Optional[ChurchAccount],
    first_name: str,
    last_name: Optional[str],
    email: Optional[str],
) -> Optional[Member]:
    query = scoped_query(db.query(Member), Member, account)
    if email:
        existing = query.filter(Member.email.ilike(email)).first()
        if existing:
            return existing
    if first_name and last_name:
        existing = query.filter(Member.first_name.ilike(first_name), Member.last_name.ilike(last_name)).first()
        if existing:
            return existing
    return query.filter(Member.first_name.ilike(first_name)).first() if first_name else None


def _synced_person_note_text(action: AssistantAction, person: dict, created: bool) -> str:
    source = (action.source or "connected system").replace("_", " ").title()
    parts = [
        f"{'Created' if created else 'Reviewed'} from {source} synced person review.",
    ]
    if person.get("id"):
        parts.append(f"External id: {person['id']}.")
    if person.get("status"):
        parts.append(f"Status: {person['status']}.")
    if person.get("membership"):
        parts.append(f"Membership: {person['membership']}.")
    if person.get("email"):
        parts.append(f"Email: {person['email']}.")
    return " ".join(parts)


def _execute_external_action(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    if action.external_provider == "microsoft_365":
        if action.action_type == "email_draft":
            return _execute_microsoft_email_draft(db, action, account, user)
        if action.action_type == "calendar_block":
            return _execute_microsoft_calendar_event(db, action, account, user)
        raise HTTPException(status_code=422, detail="This Microsoft 365 action type does not have an external executor.")
    if action.external_provider != "google_workspace":
        provider_name = _provider_display_name(action.external_provider)
        raise HTTPException(
            status_code=422,
            detail=(
                f"{provider_name} does not have an external writeback executor for this action. "
                "Approved external writes are available for Google Workspace and Microsoft 365 email/calendar actions."
            ),
        )
    if action.action_type == "email_draft":
        return _execute_google_email_draft(db, action, account, user)
    if action.action_type == "calendar_block":
        return _execute_google_calendar_event(db, action, account, user)
    raise HTTPException(status_code=422, detail="This action type does not have an external executor.")


def _execute_google_email_draft(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    payload = _json_loads(action.payload_json)
    email_payload = payload.get("email") or payload
    to_address = _clean(email_payload.get("to"))
    subject = _clean(email_payload.get("subject"))
    body = _clean(email_payload.get("body"))
    if not to_address or not subject or not body:
        raise HTTPException(status_code=422, detail="Google email draft actions require payload.email.to, subject, and body.")

    message = EmailMessage()
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    token = _provider_access_token(db, "google_workspace", account, user)
    response = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json={"message": {"raw": raw_message}},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Google Gmail draft creation failed with HTTP {response.status_code}.")
    data = response.json()
    return {"provider": "google_workspace", "kind": "gmail_draft", "provider_id": data.get("id")}


def _execute_microsoft_email_draft(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    payload = _json_loads(action.payload_json)
    email_payload = payload.get("email") or payload
    to_address = _clean(email_payload.get("to"))
    subject = _clean(email_payload.get("subject"))
    body = _clean(email_payload.get("body"))
    if not to_address or not subject or not body:
        raise HTTPException(status_code=422, detail="Microsoft 365 email draft actions require payload.email.to, subject, and body.")

    token = _provider_access_token(db, "microsoft_365", account, user)
    message = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": body,
        },
        "toRecipients": [
            {"emailAddress": {"address": to_address}},
        ],
    }
    data = _microsoft_graph_post(token, "/me/messages", json_body=message)
    return {
        "provider": "microsoft_365",
        "kind": "outlook_draft",
        "provider_id": data.get("id"),
        "web_link": data.get("webLink"),
    }


def _execute_google_calendar_event(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    payload = _json_loads(action.payload_json)
    event_payload = payload.get("calendar_event") or payload
    summary = _clean(event_payload.get("summary") or event_payload.get("title"))
    start = event_payload.get("start")
    end = event_payload.get("end")
    if not summary or not start or not end:
        raise HTTPException(status_code=422, detail="Google calendar actions require payload.calendar_event.summary, start, and end.")

    event = {
        "summary": summary,
        "description": _clean(event_payload.get("description")),
        "start": _google_event_time(start),
        "end": _google_event_time(end),
    }
    attendees = event_payload.get("attendees") or []
    if attendees:
        event["attendees"] = [{"email": item} if isinstance(item, str) else item for item in attendees]
    token = _provider_access_token(db, "google_workspace", account, user)
    response = requests.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=event,
        params={"sendUpdates": "none"},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Google Calendar event creation failed with HTTP {response.status_code}.")
    data = response.json()
    return {"provider": "google_workspace", "kind": "calendar_event", "provider_id": data.get("id"), "html_link": data.get("htmlLink")}


def _execute_microsoft_calendar_event(
    db: Session,
    action: AssistantAction,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> dict:
    payload = _json_loads(action.payload_json)
    event_payload = payload.get("calendar_event") or payload
    subject = _clean(event_payload.get("subject") or event_payload.get("summary") or event_payload.get("title"))
    start = event_payload.get("start")
    end = event_payload.get("end")
    if not subject or not start or not end:
        raise HTTPException(status_code=422, detail="Microsoft 365 calendar actions require payload.calendar_event.subject, start, and end.")

    event = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": _clean(event_payload.get("description") or event_payload.get("body") or ""),
        },
        "start": _microsoft_event_time(start),
        "end": _microsoft_event_time(end),
    }
    location = _clean(event_payload.get("location"))
    if location:
        event["location"] = {"displayName": location}
    attendees = _microsoft_event_attendees(event_payload.get("attendees") or [])
    if attendees:
        event["attendees"] = attendees

    token = _provider_access_token(db, "microsoft_365", account, user)
    data = _microsoft_graph_post(token, "/me/events", json_body=event)
    return {
        "provider": "microsoft_365",
        "kind": "outlook_calendar_event",
        "provider_id": data.get("id"),
        "web_link": data.get("webLink"),
    }


def _google_event_time(value):
    if isinstance(value, dict):
        return value
    cleaned = _clean(value)
    if not cleaned:
        return value
    if len(cleaned) == 10 and cleaned.count("-") == 2:
        return {"date": cleaned}
    return {"dateTime": cleaned}


def _microsoft_event_time(value):
    if isinstance(value, dict):
        if value.get("dateTime") and value.get("timeZone"):
            return value
        if value.get("dateTime"):
            return {"dateTime": value.get("dateTime"), "timeZone": value.get("timeZone") or "UTC"}
        if value.get("date"):
            return {"dateTime": f"{value.get('date')}T00:00:00", "timeZone": value.get("timeZone") or "UTC"}
    cleaned = _clean(value)
    if not cleaned:
        return value
    if len(cleaned) == 10 and cleaned.count("-") == 2:
        return {"dateTime": f"{cleaned}T00:00:00", "timeZone": "UTC"}
    return {"dateTime": cleaned, "timeZone": "UTC"}


def _microsoft_event_attendees(attendees: List[Any]) -> List[dict]:
    result = []
    for attendee in attendees:
        if isinstance(attendee, str):
            address = _clean(attendee)
            name = None
        elif isinstance(attendee, dict):
            email_address = attendee.get("emailAddress") if isinstance(attendee.get("emailAddress"), dict) else {}
            address = _clean(attendee.get("email") or attendee.get("address") or email_address.get("address"))
            name = _clean(attendee.get("name") or email_address.get("name"))
        else:
            continue
        if not address:
            continue
        result.append({
            "emailAddress": {
                "address": address,
                **({"name": name} if name else {}),
            },
            "type": "required",
        })
    return result


def _provider_access_token(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
    credential: Optional[IntegrationCredential] = None,
) -> str:
    credential = credential or _provider_credential(db, provider, account, user)
    if not credential:
        if user:
            raise HTTPException(status_code=409, detail=f"{provider} is not connected for this Marge user.")
        raise HTTPException(status_code=409, detail=f"{provider} is not connected.")
    try:
        payload = decrypt_token_payload(credential.token_ciphertext)
    except SecureTokenConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if credential.expires_at and credential.expires_at <= datetime.utcnow() + timedelta(minutes=2):
        payload = _refresh_oauth_token(db, provider, credential, payload)
    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=409, detail=f"{provider} token payload does not include an access token.")
    return access_token


def _provider_credential(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> Optional[IntegrationCredential]:
    query = scoped_query(db.query(IntegrationCredential), IntegrationCredential, account).filter(IntegrationCredential.provider == provider)
    if user:
        credential = query.filter(IntegrationCredential.user_id == user.id).first()
        if credential:
            return credential
        return query.filter(IntegrationCredential.user_id.is_(None)).first()
    return query.order_by(IntegrationCredential.user_id.isnot(None), IntegrationCredential.updated_at.desc()).first()


def _provider_credential_for_disconnect(
    db: Session,
    provider: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> Optional[IntegrationCredential]:
    query = scoped_query(db.query(IntegrationCredential), IntegrationCredential, account).filter(IntegrationCredential.provider == provider)
    if user:
        credential = query.filter(IntegrationCredential.user_id == user.id).first()
        if credential:
            return credential
        return query.filter(IntegrationCredential.user_id.is_(None)).first()
    return query.filter(IntegrationCredential.user_id.is_(None)).first() or query.order_by(IntegrationCredential.updated_at.desc()).first()


def _api_key_payload(
    db: Optional[Session],
    provider: str,
    account: Optional[ChurchAccount] = None,
) -> dict:
    if db is None:
        return {}
    credential = _provider_credential(db, provider, account, None)
    if not credential or credential.token_type != "api_key":
        return {}
    try:
        payload = decrypt_token_payload(credential.token_ciphertext)
    except SecureTokenConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return payload if isinstance(payload, dict) else {}


def _breeze_api_config(db: Optional[Session], account: Optional[ChurchAccount] = None) -> dict:
    payload = _api_key_payload(db, "breeze", account)
    return {
        "api_key": payload.get("api_key") or os.getenv("BREEZE_API_KEY"),
        "base_url": payload.get("base_url") or os.getenv("BREEZE_BASE_URL"),
    }


def _rock_server_api_key() -> Optional[str]:
    return os.getenv("ROCK_API_KEY") or os.getenv("ROCK_HALLMARK_API_KEY")


def _rock_api_config(db: Optional[Session], account: Optional[ChurchAccount] = None) -> dict:
    payload = _api_key_payload(db, "rock", account)
    return {
        "api_key": payload.get("api_key") or _rock_server_api_key(),
        "base_url": payload.get("base_url") or os.getenv("ROCK_BASE_URL"),
    }


def _rock_missing_config(config: dict) -> List[str]:
    missing = []
    if not config.get("api_key"):
        missing.append("ROCK_API_KEY")
    if not config.get("base_url"):
        missing.append("ROCK_BASE_URL")
    elif not _valid_https_base_url(config.get("base_url")):
        missing.append("ROCK_BASE_URL")
    return missing


def _api_key_provider_has_env(provider: str) -> bool:
    if provider == "breeze":
        return bool(os.getenv("BREEZE_API_KEY") and _valid_https_base_url(os.getenv("BREEZE_BASE_URL")))
    if provider == "rock":
        return bool(_rock_server_api_key() and _valid_https_base_url(os.getenv("ROCK_BASE_URL")))
    return False


def _refresh_oauth_token(db: Session, provider: str, credential: IntegrationCredential, payload: dict) -> dict:
    definition = next((item for item in _integration_definitions() if item["provider"] == provider), None)
    refresh_token = payload.get("refresh_token")
    if not definition or definition["auth_type"] != "oauth" or not refresh_token:
        raise HTTPException(status_code=409, detail=f"{provider} needs to be reconnected.")
    missing = _missing_oauth_config(definition)
    if missing:
        raise HTTPException(status_code=500, detail=f"Server config is missing: {', '.join(missing)}.")

    client_id = os.getenv(definition["client_id_env"], "")
    client_secret = os.getenv(definition["client_secret_env"], "")
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    auth = None
    if definition.get("token_auth") == "basic":
        auth = (client_id, client_secret)
    else:
        data["client_id"] = client_id
        data["client_secret"] = client_secret
    response = requests.post(
        definition["token_url"],
        data=data,
        auth=auth,
        headers={"Accept": "application/json"},
        timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"{definition['display_name']} token refresh failed with HTTP {response.status_code}.")
    refreshed = response.json()
    refreshed["refresh_token"] = refreshed.get("refresh_token") or refresh_token
    next_payload = {**payload, **refreshed}
    credential.token_ciphertext = encrypt_token_payload(next_payload)
    credential.token_type = next_payload.get("token_type")
    credential.scopes = next_payload.get("scope") or credential.scopes
    credential.expires_at = _token_expires_at(next_payload, datetime.utcnow())
    credential.refresh_token_present = True
    db.commit()
    db.refresh(credential)
    return next_payload


def _integration_statuses(
    db: Session,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> List[IntegrationStatus]:
    stored = {i.provider: i for i in scoped_query(db.query(IntegrationConnection), IntegrationConnection, account).all()}
    policies = {p.provider: p for p in scoped_query(db.query(IntegrationPolicy), IntegrationPolicy, account).all()}
    defaults = _integration_definitions()
    result = []
    for definition in defaults:
        provider = definition["provider"]
        display = definition["display_name"]
        auth_type = definition["auth_type"]
        scopes = definition["scopes"]
        env_var = definition.get("env_var")
        record = stored.get(provider)
        credential = _provider_credential(db, provider, account, user)
        policy = policies.get(provider) or _default_policy(provider, account)
        if credential:
            status = "configured" if auth_type in {"api_key", "env_api_key"} else "connected"
        elif provider == "mcp":
            status = "available"
        elif provider in {"breeze", "rock"} and _api_key_provider_has_env(provider):
            status = "configured"
        elif user and auth_type == "oauth":
            status = "needs_authorization"
        elif record and record.status in {"connected", "synced"}:
            status = "connected"
        elif provider not in {"breeze", "rock"} and env_var and os.getenv(env_var):
            status = "configured"
        elif record:
            status = record.status
        else:
            status = "planned" if provider not in {"rock"} else "needs_configuration"
        scope_list = _scopes_to_list(credential.scopes if credential else (record.scopes if record else None), scopes)
        if provider == "mcp":
            verified_at = None
        else:
            verified_at = credential.verified_at if credential else (record.verified_at if record and status in {"connected", "configured", "available"} else None)
        result.append(IntegrationStatus(
            provider=provider,
            display_name=record.display_name if record else display,
            status=status,
            auth_type=record.auth_type if record else auth_type,
            scopes=scope_list,
            secure_note="Secrets, API keys, and OAuth tokens are never returned to the browser or chat.",
            config_hint=_integration_config_hint(definition, record, credential, user),
            last_synced_at=record.last_synced_at if record else None,
            connected_at=record.connected_at if record else None,
            verified_at=verified_at,
            token_expires_at=credential.expires_at if credential else None,
            write_enabled=bool(policy.write_enabled),
            require_approval=bool(policy.require_approval),
            credential_scope=_credential_scope_label(credential),
        ))
    return result


def _credential_scope_label(credential: Optional[IntegrationCredential]) -> Optional[str]:
    if not credential:
        return None
    return "user" if credential.user_id else "workspace"


def _integration_definitions() -> List[dict]:
    return [
        {
            "provider": "rock",
            "display_name": "Rock RMS",
            "auth_type": "env_api_key",
            "scopes": ["members", "attendance", "notes"],
            "env_var": "ROCK_API_KEY",
            "instructions": [
                "Create a Rock API key with the narrowest read permissions Marge needs.",
                "Paste it only into secure connector setup or set ROCK_API_KEY and ROCK_BASE_URL on the server; never put secrets in chat.",
                "Marge stores workspace API keys encrypted server-side and never returns them to the browser.",
                "Run sync only after confirming the church account and permissions.",
            ],
        },
        {
            "provider": "planning_center",
            "display_name": "Planning Center",
            "auth_type": "oauth",
            "scopes": ["people", "calendar", "groups", "services"],
            "client_id_env": "PLANNING_CENTER_CLIENT_ID",
            "client_secret_env": "PLANNING_CENTER_CLIENT_SECRET",
            "redirect_uri_env": "PLANNING_CENTER_REDIRECT_URI",
            "authorize_url": "https://api.planningcenteronline.com/oauth/authorize",
            "token_url": "https://api.planningcenteronline.com/oauth/token",
            "token_auth": "basic",
            "scope_separator": " ",
            "instructions": [
                "Use Planning Center OAuth so the pastor can grant limited access.",
                "Keep the client secret server-side and exchange the code on the backend.",
                "Start with read-only People and Calendar access before writeback.",
            ],
        },
        {
            "provider": "breeze",
            "display_name": "Breeze",
            "auth_type": "api_key",
            "scopes": ["people", "events", "attendance"],
            "env_var": "BREEZE_API_KEY",
            "instructions": [
                "Paste the Breeze API key and base URL only into secure connector setup, or configure them server-side.",
                "Marge stores workspace API keys encrypted server-side and never returns them to the browser.",
                "Use read-only sync first.",
                "Do not expose the key to the browser, frontend logs, or chat transcripts.",
            ],
        },
        {
            "provider": "google_workspace",
            "display_name": "Google Workspace",
            "auth_type": "oauth",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/calendar.events",
            ],
            "client_id_env": "GOOGLE_CLIENT_ID",
            "client_secret_env": "GOOGLE_CLIENT_SECRET",
            "redirect_uri_env": "GOOGLE_REDIRECT_URI",
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "scope_separator": " ",
            "extra_params": {"access_type": "offline", "prompt": "consent"},
            "instructions": [
                "Use Google OAuth with Gmail read/draft and Calendar event scopes.",
                "Store refresh tokens encrypted server-side after the callback.",
                "Keep send/create actions behind Marge's approval queue.",
            ],
        },
        {
            "provider": "microsoft_365",
            "display_name": "Microsoft 365",
            "auth_type": "oauth",
            "scopes": ["Mail.Read", "Mail.ReadWrite", "Calendars.Read", "Calendars.ReadWrite", "offline_access"],
            "client_id_env": "MICROSOFT_CLIENT_ID",
            "client_secret_env": "MICROSOFT_CLIENT_SECRET",
            "redirect_uri_env": "MICROSOFT_REDIRECT_URI",
            "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "scope_separator": " ",
            "extra_params": {"response_mode": "query"},
            "instructions": [
                "Use Microsoft OAuth for Outlook mail/calendar read access, draft creation, and approved calendar events.",
                "Store tokens encrypted server-side after the callback.",
                "Keep Outlook draft and calendar writes behind Marge's approval queue and church writeback policy.",
            ],
        },
        {
            "provider": "mcp",
            "display_name": "MCP",
            "auth_type": "local",
            "scopes": ["marge.tools"],
            "instructions": [
                "Run MARGE_API_URL=http://localhost:8000 python mcp_server/server.py.",
                "Connect Claude Desktop or another MCP client to the local stdio server.",
                "MCP tools call Marge's backend; no external secrets are exposed.",
            ],
        },
    ]


def _start_integration(
    provider: str,
    db: Session,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> IntegrationSetupResponse:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition:
        return IntegrationSetupResponse(
            provider=provider,
            display_name=provider.replace("_", " ").title(),
            status="unknown_provider",
            setup_type="unknown",
            instructions=["Choose one of the listed integration providers."],
            missing_config=[],
            secure_note="Unknown providers are not configured.",
        )

    status = next((item for item in _integration_statuses(db, account, user) if item.provider == provider), None)
    auth_type = definition["auth_type"]
    missing = []
    authorization_url = None
    state_expires_at = None

    if auth_type == "oauth":
        missing = _missing_oauth_config(definition)
        client_id = os.getenv(definition["client_id_env"])
        redirect_uri = os.getenv(definition["redirect_uri_env"])
        if not missing:
            state_value, state_expires_at = _create_oauth_state(db, definition, redirect_uri, account, user)
            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": definition.get("scope_separator", " ").join(definition["scopes"]),
                "state": state_value,
            }
            params.update(definition.get("extra_params", {}))
            authorization_url = f"{definition['authorize_url']}?{urlencode(params)}"
    elif auth_type in {"api_key", "env_api_key"}:
        if not encryption_key_is_configured():
            missing.append(ENCRYPTION_KEY_ENV)
        if provider == "breeze":
            config = _breeze_api_config(db, account)
            if not config.get("api_key"):
                missing.append("BREEZE_API_KEY")
            if not config.get("base_url"):
                missing.append("BREEZE_BASE_URL")
            elif not _valid_https_base_url(config.get("base_url")):
                missing.append("BREEZE_BASE_URL")
        elif provider == "rock":
            config = _rock_api_config(db, account)
            missing.extend(_rock_missing_config(config))
        else:
            env_var = definition.get("env_var")
            if env_var and not os.getenv(env_var):
                missing.append(env_var)

    setup_status = "ready_to_authorize" if authorization_url else (status.status if status else "planned")
    if missing:
        setup_status = "missing_server_config"

    response = IntegrationSetupResponse(
        provider=provider,
        display_name=definition["display_name"],
        status=setup_status,
        setup_type=auth_type,
        authorization_url=authorization_url,
        state_expires_at=state_expires_at,
        instructions=definition["instructions"],
        missing_config=missing,
        secure_note="Marge returns setup state and instructions only. Client secrets, API keys, and OAuth tokens must stay server-side.",
    )
    _audit(
        db,
        "integration.setup_started",
        f"Started {definition['display_name']} connector setup.",
        provider=provider,
        account=account,
        payload={"status": setup_status, "missing_config": missing, "authorization_url_created": bool(authorization_url)},
    )
    db.commit()
    return response


def _integration_config_hint(
    definition: dict,
    record: Optional[IntegrationConnection],
    credential: Optional[IntegrationCredential],
    user: Optional[AccountUser] = None,
) -> str:
    provider = definition["provider"]
    if provider == "mcp":
        return "Local agent bridge only. MCP lets LLM clients call Marge; it does not connect a church tool or count as live provider readiness."
    if credential:
        if definition["auth_type"] in {"api_key", "env_api_key"}:
            return f"Workspace {definition['display_name']} API-key credentials are encrypted server-side. Use Check credentials before syncing ministry data."
        scope = "workspace user" if credential.user_id else "workspace"
        check_note = "" if credential.verified_at else " Use Check credentials before syncing ministry data."
        if credential.expires_at:
            return f"Connected for this {scope} with encrypted OAuth tokens. Access token expires {_date_label(credential.expires_at)}.{check_note}"
        return f"Connected for this {scope} with encrypted OAuth tokens. Refresh token status is stored server-side only.{check_note}"
    if record and record.config_hint:
        if user and definition["auth_type"] == "oauth":
            return "Authorize this connector for the current Marge user before syncing or writing through it."
        return record.config_hint
    env_var = definition.get("env_var")
    if provider == "breeze":
        missing = [name for name in ["BREEZE_API_KEY", "BREEZE_BASE_URL"] if not os.getenv(name)]
        if os.getenv("BREEZE_BASE_URL") and not _valid_https_base_url(os.getenv("BREEZE_BASE_URL")):
            missing.append("BREEZE_BASE_URL")
        if missing:
            return f"Add Breeze API key and base URL in secure setup, or set {', '.join(missing)} server-side."
        return "Ready to sync Breeze people and events from the server-side API key."
    if provider == "rock":
        missing = _rock_missing_config(_rock_api_config(None, None))
        if missing:
            return "Add a Rock API key and API base URL in secure setup, or set ROCK_API_KEY and ROCK_BASE_URL server-side."
        return "Ready to sync Rock people and attendance from the server-side API key."
    if definition["auth_type"] == "oauth":
        missing = _missing_oauth_config(definition)
        if missing:
            return f"Set {', '.join(missing)} server-side to enable OAuth."
        return "Ready to authorize through the provider's OAuth consent screen."
    if env_var:
        return f"Set {env_var} server-side."
    return "Use a server-side connector when this provider is implemented."


def _scopes_to_list(raw: Optional[str], fallback: List[str]) -> List[str]:
    if not raw:
        return fallback
    normalized = raw.replace(",", " ")
    scopes = [part.strip() for part in normalized.split() if part.strip()]
    return scopes or fallback


def _missing_oauth_config(definition: dict) -> List[str]:
    missing = []
    for key in ["client_id_env", "client_secret_env", "redirect_uri_env"]:
        env_name = definition.get(key)
        if env_name and not os.getenv(env_name):
            missing.append(env_name)
    if not encryption_key_is_configured():
        missing.append(ENCRYPTION_KEY_ENV)
    return missing


def _create_oauth_state(
    db: Session,
    definition: dict,
    redirect_uri: str,
    account: Optional[ChurchAccount] = None,
    user: Optional[AccountUser] = None,
) -> tuple[str, datetime]:
    now = datetime.utcnow()
    scoped_query(db.query(IntegrationOAuthState), IntegrationOAuthState, account).filter(
        IntegrationOAuthState.provider == definition["provider"],
        IntegrationOAuthState.expires_at < now,
        IntegrationOAuthState.consumed_at.is_(None),
    ).delete(synchronize_session=False)
    expires_at = now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)
    state_value = secrets.token_urlsafe(32)
    scope_string = definition.get("scope_separator", " ").join(definition["scopes"])
    db.add(IntegrationOAuthState(
        account_id=_account_id(account),
        user_id=user.id if user else None,
        provider=definition["provider"],
        state=state_value,
        redirect_uri=redirect_uri,
        scopes=scope_string,
        expires_at=expires_at,
    ))
    db.commit()
    return state_value, expires_at


def _complete_oauth_integration(provider: str, code: Optional[str], state: Optional[str], db: Session) -> dict:
    definitions = {item["provider"]: item for item in _integration_definitions()}
    definition = definitions.get(provider)
    if not definition or definition["auth_type"] != "oauth":
        raise ValueError("Choose an OAuth integration provider.")
    if not code or not state:
        raise ValueError("The provider callback did not include both code and state.")

    state_row = (
        db.query(IntegrationOAuthState)
        .filter(IntegrationOAuthState.provider == provider, IntegrationOAuthState.state == state)
        .first()
    )
    now = datetime.utcnow()
    if not state_row:
        raise ValueError("OAuth state was not recognized. Start setup again from Marge.")
    if state_row.consumed_at:
        raise ValueError("OAuth state was already used. Start setup again from Marge.")
    if state_row.expires_at < now:
        raise ValueError("OAuth state expired. Start setup again from Marge.")
    account = db.get(ChurchAccount, state_row.account_id) if state_row.account_id else None
    user = db.get(AccountUser, state_row.user_id) if state_row.user_id else None

    missing = _missing_oauth_config(definition)
    if missing:
        raise SecureTokenConfigError(f"Server config is missing: {', '.join(missing)}.")

    token_payload = _exchange_oauth_code(definition, code, state_row.redirect_uri)
    try:
        ciphertext = encrypt_token_payload(token_payload)
    except SecureTokenConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    expires_at = _token_expires_at(token_payload, now)
    scopes = token_payload.get("scope") or state_row.scopes

    credential_query = scoped_query(db.query(IntegrationCredential), IntegrationCredential, account).filter(
        IntegrationCredential.provider == provider,
        IntegrationCredential.user_id == (user.id if user else None),
    )
    credential = credential_query.first()
    if not credential:
        credential = IntegrationCredential(
            provider=provider,
            token_ciphertext=ciphertext,
            account_id=_account_id(account),
            user_id=user.id if user else None,
        )
        db.add(credential)
    credential.token_ciphertext = ciphertext
    credential.token_type = token_payload.get("token_type")
    credential.scopes = scopes
    credential.expires_at = expires_at
    credential.refresh_token_present = bool(token_payload.get("refresh_token"))
    credential.verified_at = None

    connection = scoped_query(db.query(IntegrationConnection), IntegrationConnection, account).filter(IntegrationConnection.provider == provider).first()
    if not connection:
        connection = IntegrationConnection(provider=provider, display_name=definition["display_name"], account_id=_account_id(account))
        db.add(connection)
    connection.display_name = definition["display_name"]
    connection.status = "connected"
    connection.auth_type = "oauth"
    connection.scopes = scopes
    connection.config_hint = "Connected through OAuth. Token payload is encrypted server-side."
    connection.connected_at = now
    connection.verified_at = None
    connection.error_message = None

    state_row.consumed_at = now
    _audit(
        db,
        "integration.connected",
        f"Connected {definition['display_name']} through OAuth.",
        provider=provider,
        account=account,
        payload={"scopes": scopes, "refresh_token_present": bool(token_payload.get("refresh_token")), "user_scoped": bool(user)},
    )
    db.commit()
    return {"provider": provider, "display_name": definition["display_name"], "expires_at": expires_at}


def _exchange_oauth_code(definition: dict, code: str, redirect_uri: str) -> dict:
    client_id = os.getenv(definition["client_id_env"], "")
    client_secret = os.getenv(definition["client_secret_env"], "")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    auth = None
    if definition.get("token_auth") == "basic":
        auth = (client_id, client_secret)
    else:
        data["client_id"] = client_id
        data["client_secret"] = client_secret
    if definition["provider"] == "microsoft_365":
        data["scope"] = definition.get("scope_separator", " ").join(definition["scopes"])

    try:
        response = requests.post(
            definition["token_url"],
            data=data,
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=OAUTH_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"{definition['display_name']} token exchange could not reach the provider.") from exc
    if not response.ok:
        raise RuntimeError(f"{definition['display_name']} token exchange failed with HTTP {response.status_code}.")
    payload = response.json()
    if "access_token" not in payload:
        raise RuntimeError(f"{definition['display_name']} did not return an access token.")
    return payload


def _token_expires_at(token_payload: dict, now: datetime) -> Optional[datetime]:
    try:
        expires_in = int(token_payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        return None
    if expires_in <= 0:
        return None
    return now + timedelta(seconds=expires_in)


def _oauth_callback_page(provider: str, display_name: str, ok: bool, message: str) -> HTMLResponse:
    title = f"{display_name} connected" if ok else f"{display_name} connection failed"
    status = "Connected" if ok else "Needs attention"
    body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f3ed; color: #27231d; }}
      main {{ max-width: 620px; margin: 12vh auto; padding: 32px; background: #fffaf1; border: 1px solid #ded4c2; border-radius: 8px; box-shadow: 0 24px 80px rgba(58, 47, 31, .12); }}
      p {{ line-height: 1.55; }}
      .tag {{ display: inline-block; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: #6b5a43; }}
    </style>
  </head>
  <body>
    <main>
      <div class="tag">{html.escape(status)}</div>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
      <p>Marge never exposes provider tokens in the browser, chat, logs, or API responses.</p>
    </main>
  </body>
</html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)


def _setup_steps(profile: PastorProfile, integrations: List[IntegrationStatus], needs_seed_context: bool = False) -> List[DeskItem]:
    steps: List[DeskItem] = []
    missing = _missing_profile_fields(profile)
    if missing:
        field = missing[0]
        question = _interview_question(profile)
        steps.append(DeskItem(
            id=f"setup-profile-{field}",
            type="profile_setup",
            title=f"Teach Marge: {_profile_field_label(field)}",
            subtitle=question["question"] if question else "Add the next piece of ministry context.",
            detail=(question.get("why") if question else None) or "This lets Marge prioritize and draft in a way that fits this pastor and church.",
            priority="high",
            action="Answer context question",
            source="profile",
        ))

    if needs_seed_context and not missing:
        seed_detail = _seed_context_detail(profile)
        seed_kind = _seed_context_kind(profile)
        seed_form = _seed_context_form(seed_kind)
        steps.append(DeskItem(
            id="setup-seed-first-people",
            type="data_seed",
            title=_seed_context_title(seed_kind),
            subtitle=seed_detail,
            detail="Add one person, visitor, care case, or prayer request from the follow-up burden you named so Marge has real ministry context to watch.",
            priority="high",
            action=_seed_context_action(seed_kind),
            source="seed",
            form=seed_form,
        ))

    integrations_by_provider = {item.provider: item for item in integrations}
    for provider in _recommended_providers(profile):
        integration = integrations_by_provider.get(provider)
        if not integration:
            continue
        sync_ready = integration.status in {"connected", "configured", "available"}
        if sync_ready and integration.verified_at:
            continue
        if sync_ready:
            steps.append(_integration_check_credentials_step(integration, profile))
            continue
        steps.append(DeskItem(
            id=f"setup-integration-{provider}",
            type="integration_setup",
            title=f"Connect {integration.display_name}",
            subtitle=f"Marge saw this in your tools: {_profile_tools_label(profile)}",
            detail=integration.config_hint or integration.secure_note,
            priority="high" if provider in {"google_workspace", "planning_center", "rock", "microsoft_365"} else "medium",
            action="Start secure setup",
            source="integrations",
            provider=provider,
        ))

    if _profile_is_complete(profile) and not steps:
        ready = [item for item in integrations if item.status in {"connected", "configured", "available"} and item.verified_at]
        if not ready:
            steps.append(DeskItem(
                id="setup-integration-first-tool",
                type="integration_setup",
                title="Connect the first ministry tool",
                subtitle="Start with Google Workspace, Microsoft 365, Planning Center, Rock RMS, or Breeze.",
                detail="Marge can do more once she reads from the systems the church already trusts.",
                priority="medium",
                action="Open integrations",
                source="integrations",
            ))
    return steps[:5]


def _profile_question_desk_item(question: dict) -> DeskItem:
    field = question.get("field") or "context"
    return DeskItem(
        id=f"setup-profile-{field}",
        type="profile_setup",
        title=f"Teach Marge: {_profile_field_label(field)}",
        subtitle=question.get("question") or "Add the next piece of ministry context.",
        detail=question.get("why") or "This lets Marge prioritize and draft in a way that fits this pastor and church.",
        priority="high",
        action="Answer context question",
        source="profile",
    )


def _interview_question(profile: PastorProfile) -> Optional[dict]:
    missing = _missing_profile_fields(profile)
    if not missing:
        return None
    field = missing[0]
    question = next((item for item in ONBOARDING_QUESTIONS if item["id"] == field), None)
    if not question:
        return None
    contextual = _contextual_interview_copy(profile, field, question)
    reasons = {
        "pastor_name": "Marge should address the pastor naturally.",
        "church_name": "The church name anchors drafts, briefings, and connector setup.",
        "role_title": "A solo pastor, lead pastor, and associate pastor need different kinds of help.",
        "congregation_size": "Weekly size changes how Marge thinks about scale, volunteer load, and follow-up urgency.",
        "church_context": "This keeps recommendations from sounding generic.",
        "faith_tradition": "Drafts should respect this church's theological vocabulary and language boundaries.",
        "followup_pain": "Marge should start where people are most likely to slip through cracks.",
        "ministry_priorities": "Marge should know what a useful first month should move for this pastor.",
        "support_preferences": "Marge should learn how this pastor wants to be nudged, protected, and helped when ministry pressure rises.",
        "tools_in_use": "Secure connector setup depends on the systems the church already trusts.",
        "communication_style": "Drafts should sound like the pastor, not like generic automation.",
        "weekly_rhythm": "Marge needs to protect real sermon, care, rest, and meeting rhythms before proposing calendar work.",
        "guardrails": "Marge needs clear boundaries before preparing work.",
    }
    return {
        "field": field,
        "label": question["label"],
        "question": contextual.get("question") or question["question"],
        "placeholder": contextual.get("placeholder") or question["placeholder"],
        "why": contextual.get("why") or reasons.get(field, "This helps Marge serve this pastor and church more personally."),
    }


def _contextual_interview_copy(profile: PastorProfile, field: str, question: dict) -> dict:
    church = _short_context(profile.church_name, 80) or "your church"
    role = _short_context(profile.role_title, 70)
    size = _short_context(profile.congregation_size, 40)
    context = _short_context(profile.church_context, 100)
    pain = _short_context(profile.followup_pain, 120)
    priority = _short_context(profile.ministry_priorities, 120)
    tools = _short_context(profile.tools_in_use, 100)
    voice = _short_context(profile.communication_style, 80)
    copy = {}

    if field == "role_title" and church != "your church":
        copy["question"] = f"What is your role at {church}?"
        copy["placeholder"] = "Solo pastor, lead pastor, associate pastor..."
    elif field == "congregation_size" and (role or church != "your church"):
        identity = role or f"pastor at {church}"
        copy["question"] = f"About how many people are you shepherding week to week as {identity}?"
        copy["placeholder"] = "About 85 people on Sundays; 120 active adults..."
    elif field == "church_context" and church != "your church":
        copy["question"] = f"What should Marge remember about {church} and the community around it?"
        copy["placeholder"] = "Neighborhood church, young families, rural community, tired volunteers..."
    elif field == "faith_tradition":
        copy["question"] = f"What church tradition, denomination, or ministry language should Marge respect at {church}?"
        copy["placeholder"] = "Non-denominational with Baptist roots; avoid insider language with guests..."
        copy["why"] = "Marge should draft with the pastor's theological vocabulary and avoid language that would not fit this church."
    elif field == "followup_pain":
        basis = context or f"what you know about {church}"
        copy["question"] = f"Given {basis}, where does pastoral follow-up most often break down?"
        copy["placeholder"] = "Visitors, hospital follow-up, prayer requests, absent members..."
    elif field == "ministry_priorities":
        basis = pain or context or f"what you want Marge to understand about {church}"
        copy["question"] = f"What would make Marge genuinely helpful in the first month with {basis.lower()}?"
        copy["placeholder"] = "Close loops with first-time guests, protect sermon prep, follow up on private prayer needs..."
        copy["why"] = "Marge should aim setup, drafts, and connector work at the ministry outcome that matters first."
    elif field == "support_preferences":
        basis = priority or pain or role or f"serving {church}"
        copy["question"] = f"How should Marge support you personally while helping with {basis.lower()}?"
        copy["placeholder"] = "Nudge me gently, protect my rest, surface what I am likely to miss, keep me from carrying every loop alone..."
        copy["why"] = "Marge should learn the pastor's preferred support style, not only the church's tools and tasks."
    elif field == "tools_in_use":
        if pain:
            copy["question"] = f"What tools does {church} already use for {pain.lower()}?"
            copy["why"] = "Marge should connect the systems that already hold the follow-up work you named."
        else:
            copy["question"] = f"What tools does {church} already use for people, email, calendar, or follow-up?"
        copy["placeholder"] = "Planning Center, Gmail/Google Workspace, Outlook/Microsoft 365, Rock RMS, Breeze..."
    elif field == "communication_style":
        if pain:
            copy["question"] = f"When Marge drafts around {pain.lower()}, how should she sound in your voice?"
            copy["why"] = "The first useful drafts should fit the pastor and the follow-up pressure already named."
        else:
            copy["question"] = "How should Marge sound when drafting for you?"
        copy["placeholder"] = "Warm and brief, gentle but direct, conversational, more formal..."
    elif field == "weekly_rhythm":
        if role or size or pain:
            context_bits = _human_join([bit for bit in [role, f"about {size} people" if size else None, pain] if bit])
            copy["question"] = f"What weekly rhythm should Marge protect while helping with {context_bits}?"
        else:
            copy["question"] = "What should Marge protect or remember in your weekly rhythm?"
        copy["placeholder"] = "Sermon prep Thursdays, hospital visits Tuesdays, staff meeting Monday..."
    elif field == "guardrails":
        if tools:
            copy["question"] = f"Before Marge works with {tools}, what should she never do without asking?"
        elif pain:
            copy["question"] = f"Before Marge helps with {pain.lower()}, what should she never do without asking?"
        else:
            copy["question"] = "What should Marge never do without asking?"
        copy["placeholder"] = "Do not send emails, change Planning Center, or share private prayer requests without approval."
    elif field == "pastor_name" and church != "your church":
        copy["question"] = f"What should Marge call the pastor serving {church}?"
    elif field == "church_name" and role:
        copy["question"] = f"What church are you serving as {role}?"

    return copy


def _operating_plan(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: List[DeskItem],
) -> List[dict]:
    plan = []
    missing = _missing_profile_fields(profile)
    if missing:
        interview = _interview_question(profile)
        if interview:
            plan.append({
                "title": f"Learn {interview['label'].lower()}",
                "detail": interview["question"],
                "action": "Answer in chat or open Teach Marge.",
                "status": "needs_context",
            })

    pain = _clean(profile.followup_pain)
    if pain:
        plan.append({
            "title": "Start where follow-up breaks",
            "detail": pain,
            "action": "Add or sync the people most likely to be missed first.",
            "status": "ready",
        })

    ministry_priority = _clean(profile.ministry_priorities)
    if ministry_priority:
        plan.append({
            "title": "Work toward the stated ministry priority",
            "detail": ministry_priority,
            "action": "Use setup, drafts, and connector syncs to move this first.",
            "status": "ready",
        })

    support_preferences = _clean(profile.support_preferences)
    if support_preferences:
        plan.append({
            "title": "Support the pastor personally",
            "detail": support_preferences,
            "action": "Shape nudges, summaries, and follow-up pressure around this support style.",
            "status": "ready",
        })

    tradition = _clean(profile.faith_tradition)
    if tradition:
        plan.append({
            "title": "Respect the church voice",
            "detail": tradition,
            "action": "Keep drafts and suggestions inside this church's language boundaries.",
            "status": "ready",
        })

    recommended = _recommended_provider_statuses(profile, integrations)
    if recommended:
        missing_tools = [item for item in recommended if item["status"] not in {"connected", "configured", "available"}]
        unchecked_tools = [item for item in recommended if item["status"] in {"connected", "configured", "available"} and not item.get("verified_at")]
        ready_tools = [item for item in recommended if item["status"] in {"connected", "configured", "available"} and item.get("verified_at")]
        tool_names = ", ".join(item["display_name"] for item in (missing_tools or unchecked_tools or ready_tools))
        plan.append({
            "title": "Connect the tools already in use",
            "detail": tool_names,
            "action": "Use secure setup and check credentials before Marge syncs ministry data.",
            "status": "needs_setup" if missing_tools or unchecked_tools else "ready",
        })

    rhythm = _clean(profile.weekly_rhythm)
    if rhythm:
        plan.append({
            "title": "Protect the weekly rhythm",
            "detail": rhythm,
            "action": "Use calendar suggestions around protected ministry blocks.",
            "status": "ready",
        })
    elif not missing:
        plan.append({
            "title": "Learn the weekly rhythm",
            "detail": "Sermon prep, visits, staff meetings, rest, and recurring care windows.",
            "action": "Tell Marge what time should be protected.",
            "status": "needs_context",
        })

    voice = _clean(profile.communication_style)
    if voice:
        plan.append({
            "title": "Draft in the pastor's voice",
            "detail": voice,
            "action": f"Keep {len(email_drafts)} draft(s) in review before anything is sent.",
            "status": "ready",
        })

    guardrails = _clean(profile.guardrails)
    if guardrails:
        plan.append({
            "title": "Keep the approval boundary",
            "detail": guardrails,
            "action": "Queue work for pastor review before external writes.",
            "status": "ready",
        })

    if priorities:
        plan.append({
            "title": "First pastoral priority",
            "detail": f"{priorities[0].title}: {priorities[0].action or priorities[0].detail or priorities[0].subtitle}",
            "action": "Handle this before lower-urgency admin.",
            "status": "ready",
        })
    elif calendar_blocks:
        plan.append({
            "title": "Use the next quiet block well",
            "detail": calendar_blocks[0].detail or calendar_blocks[0].subtitle or "Protect ministry preparation and follow-up time.",
            "action": calendar_blocks[0].action or "Protect time before the week fills.",
            "status": "ready",
        })

    if setup_steps and len(plan) < 5:
        first = setup_steps[0]
        plan.append({
            "title": first.title,
            "detail": first.subtitle or first.detail or "Next setup step.",
            "action": first.action or "Open setup.",
            "status": "needs_setup",
        })

    return _dedupe_plan(plan)[:8]


def _recommended_provider_statuses(profile: PastorProfile, integrations: List[IntegrationStatus]) -> List[dict]:
    by_provider = {item.provider: item for item in integrations}
    result = []
    for provider in _recommended_providers(profile):
        item = by_provider.get(provider)
        if item:
            result.append({"provider": provider, "display_name": item.display_name, "status": item.status, "verified_at": item.verified_at})
    return result


def _dedupe_plan(plan: List[dict]) -> List[dict]:
    seen = set()
    result = []
    for item in plan:
        title = item.get("title")
        if not title or title in seen:
            continue
        seen.add(title)
        result.append(item)
    return result


def _recommended_providers(profile: PastorProfile) -> List[str]:
    tools = (profile.tools_in_use or "").lower()
    mapping = [
        ("google_workspace", ["google workspace", "gmail", "google calendar"]),
        ("planning_center", ["planning center", "church center", "pco"]),
        ("rock", ["rock rms", "rock"]),
        ("breeze", ["breeze"]),
        ("microsoft_365", ["microsoft 365", "office 365", "outlook"]),
    ]
    matches = []
    for rank, (provider, needles) in enumerate(mapping):
        positions = [tools.find(needle) for needle in needles if needle in tools]
        if positions:
            matches.append((provider, min(positions), rank))
    scores = _connector_context_scores(profile)
    matches.sort(key=lambda item: (-scores.get(item[0], 0), item[1], item[2]))
    return [provider for provider, _position, _rank in matches]


def _connector_context_scores(profile: PastorProfile) -> dict[str, int]:
    context = " ".join(
        _clean(value) or ""
        for value in [profile.followup_pain, profile.ministry_priorities, profile.church_context]
    ).lower()
    scores = {
        "google_workspace": 0,
        "microsoft_365": 0,
        "planning_center": 0,
        "rock": 0,
        "breeze": 0,
    }
    if not context:
        return scores
    if _mentions(context, [
        "follow-up",
        "follow up",
        "reply",
        "draft",
        "email",
        "inbox",
        "message",
        "prayer card",
        "prayer request",
    ]):
        scores["google_workspace"] += 24
        scores["microsoft_365"] += 24
        scores["planning_center"] += 8
        scores["rock"] += 6
        scores["breeze"] += 6
    if _mentions(context, [
        "attendance",
        "absent",
        "absence",
        "care",
        "hospital",
        "grief",
        "member",
        "people",
        "group",
        "volunteer",
        "serve",
    ]):
        scores["planning_center"] += 30
        scores["rock"] += 30
        scores["breeze"] += 30
        scores["google_workspace"] += 8
        scores["microsoft_365"] += 8
    if _mentions(context, [
        "visitor",
        "first-time guest",
        "first time guest",
        "guest",
        "new family",
        "new families",
    ]):
        scores["google_workspace"] += 18
        scores["microsoft_365"] += 18
        scores["planning_center"] += 12
        scores["rock"] += 10
        scores["breeze"] += 10
    if _mentions(context, ["calendar", "schedule", "meeting", "appointment", "visit"]):
        scores["google_workspace"] += 12
        scores["microsoft_365"] += 12
        scores["planning_center"] += 10
        scores["breeze"] += 8
    return scores


def _provider_from_chat(lower: str) -> Optional[str]:
    mapping = [
        ("planning_center", ["planning center", "church center", "pco"]),
        ("microsoft_365", ["microsoft 365", "office 365", "outlook", "microsoft"]),
        ("google_workspace", ["google workspace", "gmail", "google calendar", "google"]),
        ("rock", ["rock rms", "rock"]),
        ("breeze", ["breeze", "breeze chms"]),
    ]
    for provider, needles in mapping:
        if any(needle in lower for needle in needles):
            return provider
    return None


def _connector_setup_requested(lower: str) -> bool:
    if not _connector_setup_verb_requested(lower):
        return False
    if _mentions(lower, [
        "integration",
        "integrations",
        "connector",
        "connectors",
        "tools",
        "church tool",
        "church tools",
        "ministry tool",
        "ministry tools",
        "email",
        "mail",
        "mailbox",
        "inbox",
        "calendar",
        "schedule",
        "planning center",
        "rock",
        "gmail",
        "google",
        "outlook",
        "microsoft",
        "breeze",
    ]):
        return True
    return _mentions(lower, ["connect first", "what should i connect", "which should i connect", "where should i start connecting"])


def _connector_setup_verb_requested(lower: str) -> bool:
    return (
        bool(re.search(r"\b(?:connect|setup|authorize)\b", lower))
        or "set up" in lower
        or bool(re.search(r"\bstart\b.*\bsetup\b", lower))
    )


def _open_integrations_requested(lower: str) -> bool:
    open_words = ["open", "show", "view", "see", "list"]
    integration_words = ["integrations", "integration", "connectors", "connector", "church tools", "ministry tools"]
    return _mentions(lower, open_words) and _mentions(lower, integration_words)


def _open_integrations_chat_response(
    db: Session,
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    account: Optional[ChurchAccount],
    effective_mode: Literal["demo", "live"],
) -> AssistantChatResponse:
    church_tools = [item for item in integrations if item.provider != "mcp"]
    ready = [item for item in church_tools if item.status in {"connected", "configured"} and item.verified_at]
    unchecked = [item for item in church_tools if item.status in {"connected", "configured"} and not item.verified_at]
    not_ready = [item for item in church_tools if item.status not in {"connected", "configured"}]
    setup_steps = [
        step
        for step in _setup_steps(profile, integrations, needs_seed_context=False)
        if step.type == "integration_setup"
    ]
    action_steps = _dedupe_desk_items(
        [_integration_check_credentials_step(item, profile) for item in unchecked] + setup_steps
    )

    parts = []
    if ready:
        parts.append("Ready after credential check: " + ", ".join(item.display_name for item in ready[:4]))
    else:
        parts.append("Ready after credential check: no church tools yet")
    if unchecked:
        parts.append("Needs credential check: " + ", ".join(item.display_name for item in unchecked[:4]))
    if not_ready:
        names = [item.display_name for item in not_ready[:4]]
        parts.append("Needs secure setup: " + ", ".join(names))

    reply = (
        "Here is the church-tool connection picture. "
        + ". ".join(parts)
        + ". I attached the next setup or credential-check cards I can act on here. "
        "I do not count local MCP access as a connected church tool, and I will not sync ministry data until credentials are checked."
    )
    return AssistantChatResponse(
        reply=reply,
        intent="integrations_opened",
        mode=effective_mode,
        actions=action_steps[:4],
        suggested_prompts=_open_integrations_prompts(ready, unchecked, action_steps),
        profile=_profile_response(profile, account),
    )


def _open_integrations_prompts(
    ready: List[IntegrationStatus],
    unchecked: List[IntegrationStatus],
    action_steps: List[DeskItem],
) -> List[str]:
    if ready:
        return ["Sync the connected tools.", "Show connected context.", "Explain the approval rules."]
    if unchecked:
        return [f"Check {unchecked[0].display_name} credentials.", "Open integrations.", "Explain the approval rules."]
    if action_steps:
        return [_setup_prompt(action_steps[0]), "How do secure connections work?", "Explain the approval rules."]
    return ["What should I connect first?", "How do secure connections work?", "Explain the approval rules."]


def _approval_rules_requested(lower: str) -> bool:
    if _mentions(lower, [
        "approval rules",
        "approval rule",
        "approval boundary",
        "approval guardrail",
        "writeback policy",
        "writeback rules",
        "safe to send",
        "will you send",
        "can you send",
    ]):
        return True
    return _mentions(lower, ["explain", "how do", "how does"]) and _mentions(lower, ["approval", "approvals", "writeback"])


def _defer_until_next_week_requested(lower: str) -> bool:
    if _mentions(lower, [
        "what can wait until next week",
        "what can wait till next week",
        "what can wait for next week",
        "what should wait until next week",
        "what should wait till next week",
        "what can i defer",
        "what should i defer",
        "what can move to next week",
        "what should move to next week",
        "what can we push",
        "what should we push",
    ]):
        return True
    return _mentions(lower, ["next week", "later"]) and _mentions(lower, ["wait", "defer", "push", "move"])


def _next_action_requested(lower: str) -> bool:
    return _mentions(lower, [
        "what should i handle next",
        "what should we handle next",
        "what should i handle first",
        "what should we handle first",
        "what should i do next",
        "what should we do next",
        "what should i do first",
        "what should we do first",
        "what is next",
        "what's next",
    ])


def _check_on_next_requested(lower: str) -> bool:
    return _mentions(lower, [
        "who should i check on next",
        "who should we check on next",
        "who should i check on first",
        "who should we check on first",
        "who needs a check-in",
        "who needs a check in",
        "who needs follow-up next",
        "who needs follow up next",
        "who needs attention next",
    ])


def _first_week_plan_requested(lower: str) -> bool:
    return _mentions(lower, [
        "first-week plan",
        "first week plan",
        "first-week launch plan",
        "first week launch plan",
        "marge launch plan",
        "launch plan",
    ]) or (_mentions(lower, ["prepare", "queue", "review"]) and _mentions(lower, ["first week", "first-week"]))


def _connector_verification_requested(lower: str) -> bool:
    check_action = (
        bool(re.search(r"\b(?:verify|verified|verification|test|tested|testing)\b", lower))
        or bool(re.search(r"\bcheck(?:ed|ing)?\b(?!\s*(?:-| )?ins?\b)", lower))
        or "health check" in lower
    )
    if not check_action:
        return False
    credential_or_connection_target = _mentions(lower, [
        "credential",
        "credentials",
        "connection",
        "connector",
        "connectors",
        "integration",
        "integrations",
        "access",
        "authorization",
        "auth",
        "health check",
    ])
    if credential_or_connection_target:
        return True
    provider = _provider_from_chat(lower)
    if provider:
        return True
    return False


def _next_verification_provider(profile: PastorProfile, integrations: List[IntegrationStatus]) -> Optional[str]:
    by_provider = {item.provider: item for item in integrations}
    preferred = _recommended_providers(profile) + ["google_workspace", "microsoft_365", "planning_center", "rock", "breeze"]
    seen = set()
    ordered = []
    for provider in preferred:
        if provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    for provider in ordered:
        status = by_provider.get(provider)
        if status and status.status in {"connected", "configured", "available"} and not status.verified_at:
            return provider
    for provider in ordered:
        status = by_provider.get(provider)
        if status and status.status in {"connected", "configured", "available"}:
            return provider
    return None


def _verification_identity_summary(identity: dict) -> str:
    if not identity:
        return ""
    parts = []
    for key, label in [
        ("email", "email"),
        ("display_name", "name"),
        ("name", "name"),
        ("id", "id"),
        ("people_access_confirmed", "people access"),
    ]:
        value = identity.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            parts.append(f"{label} {'confirmed' if value else 'not confirmed'}")
        else:
            parts.append(f"{label} {value}")
    return ", ".join(parts[:3])


def _sync_needs_credential_check(exc: HTTPException) -> bool:
    detail = str(exc.detail or "").lower()
    return "check credentials" in detail and "before syncing" in detail


def _verify_before_sync_chat_response(
    db: Session,
    account: Optional[ChurchAccount],
    user: Optional[AccountUser],
    profile: PastorProfile,
    user_message: str,
    mode: str,
    provider: str,
    exc: HTTPException,
) -> Optional[AssistantChatResponse]:
    if not _sync_needs_credential_check(exc):
        return None
    status = next((item for item in _integration_statuses(db, account, user) if item.provider == provider), None)
    display = status.display_name if status else provider.replace("_", " ").title()
    try:
        verification = _verify_integration(db, provider, account, user)
    except HTTPException as verify_exc:
        integrations = _integration_statuses(db, account, user)
        step = _provider_setup_or_check_step(
            profile,
            integrations,
            provider,
            subtitle=f"Reconnect {display} before syncing ministry context.",
            detail="Marge checks credentials without syncing before any people, email, calendar, or attendance context is imported.",
        )
        actions = [step] if step else []
        prompts = _connector_setup_or_check_prompts(actions) or ["Open integrations.", f"Start {display} setup."]
        return _chat_turn_response(db, account, user, user_message,
            reply=(
                f"I stopped before syncing {display} because credentials need to be checked first. "
                f"I tried the safe credential check, but it failed: {_redact_secret_text(verify_exc.detail)}. "
                "No ministry data was imported and no actions were queued."
            ),
            intent="integration_verify_failed_before_sync",
            mode=mode,
            actions=actions,
            suggested_prompts=prompts,
        )
    identity_summary = _verification_identity_summary(verification.identity)
    reply = (
        f"I checked {display} credentials first. They verified without syncing people, email, calendar, or attendance data, "
        "and I did not queue any actions yet. Ask me to sync it again when you want me to import fresh ministry context."
    )
    if identity_summary:
        reply += f" Non-secret identity check: {identity_summary}."
    return _chat_turn_response(db, account, user, user_message,
        reply=reply,
        intent="integration_verified_before_sync",
        mode=mode,
        actions=[],
        suggested_prompts=[f"Sync {display}.", "Show connected context.", "Explain the approval rules."],
    )


def _next_setup_provider(profile: PastorProfile, integrations: List[IntegrationStatus]) -> Optional[str]:
    by_provider = {item.provider: item for item in integrations}
    for provider in _recommended_providers(profile):
        status = by_provider.get(provider)
        if status and (status.status not in {"connected", "configured", "available"} or not status.verified_at):
            return provider
    for provider in ["google_workspace", "microsoft_365", "planning_center", "rock", "breeze"]:
        status = by_provider.get(provider)
        if status and (status.status not in {"connected", "configured", "available"} or not status.verified_at):
            return provider
    return None


def _connector_setup_recommendation(profile: PastorProfile, provider: str, integrations: List[IntegrationStatus]) -> str:
    display = _provider_display_name(provider)
    evidence = []
    tool_match = _connector_saved_tool_match(profile, provider)
    tools = _short_context(profile.tools_in_use, 140)
    if tool_match:
        evidence.append(f"{tool_match} is in your saved stack")
    elif tools:
        evidence.append(f"your saved stack is {tools}")
    followup = _short_context(profile.followup_pain, 130)
    priority = _short_context(profile.ministry_priorities, 130)
    if followup:
        evidence.append(f"your stated follow-up burden is {followup}")
    elif priority:
        evidence.append(f"your first ministry priority is {priority}")

    reason = _connector_provider_reason(provider)
    next_provider = _next_recommended_provider_after(profile, integrations, provider)
    next_sentence = f" After that, I would handle {_provider_display_name(next_provider)}." if next_provider else ""
    if evidence:
        return f"I would connect {display} first because {_human_join(evidence[:2])}. {reason}{next_sentence}"
    return f"I would connect {display} first because it is the next unverified church tool. {reason}{next_sentence}"


def _connector_saved_tool_match(profile: PastorProfile, provider: str) -> Optional[str]:
    tools = profile.tools_in_use or ""
    lower = tools.lower()
    aliases = {
        "google_workspace": [("Gmail", "gmail"), ("Google Calendar", "google calendar"), ("Google Workspace", "google workspace")],
        "planning_center": [("Planning Center", "planning center"), ("Church Center", "church center"), ("PCO", "pco")],
        "rock": [("Rock RMS", "rock rms"), ("Rock", "rock")],
        "breeze": [("Breeze", "breeze")],
        "microsoft_365": [("Outlook", "outlook"), ("Microsoft 365", "microsoft 365"), ("Office 365", "office 365")],
    }
    for label, needle in aliases.get(provider, []):
        if needle in lower:
            return label
    return None


def _connector_provider_reason(provider: str) -> str:
    reasons = {
        "google_workspace": "That gives me inbox and calendar context for reviewable follow-up drafts and protected schedule blocks.",
        "microsoft_365": "That gives me Outlook and calendar context for reviewable follow-up drafts and protected schedule blocks.",
        "planning_center": "That gives me people, groups, calendar, and service context from the system the church already trusts.",
        "rock": "That gives me people, attendance, and care context from the church management system.",
        "breeze": "That gives me people, event, and attendance context from the church management system.",
    }
    return reasons.get(provider, "That gives me the first real ministry context to sync securely.")


def _next_recommended_provider_after(
    profile: PastorProfile,
    integrations: List[IntegrationStatus],
    current_provider: str,
) -> Optional[str]:
    by_provider = {item.provider: item for item in integrations}
    for provider in _recommended_providers(profile):
        if provider == current_provider:
            continue
        status = by_provider.get(provider)
        if status and (status.status not in {"connected", "configured", "available"} or not status.verified_at):
            return provider
    return None


def _profile_tools_label(profile: PastorProfile) -> str:
    return profile.tools_in_use.strip() if profile.tools_in_use else "No tools saved yet"


def _suggested_prompts(profile: PastorProfile, priorities: List[DeskItem], setup_steps: Optional[List[DeskItem]] = None) -> List[str]:
    setup_steps = setup_steps or []
    if _missing_profile_fields(profile):
        return [
            "What do you still need to learn about my ministry?",
            "How will you support me?",
            "How do secure connections work?",
        ]
    if setup_steps:
        prompts = [
            _setup_prompt(setup_steps[0]),
            "Why is this the next step?",
            "Explain the approval rules.",
        ]
    elif priorities:
        prompts = [
            f"What should I do for {priorities[0].title}?",
            "What can wait until next week?",
            "Show my approvals.",
        ]
    else:
        prompts = [
            "What should I handle next?",
            "Where should I capture the next follow-up?",
            "Open integrations." if profile.tools_in_use else "How do secure connections work?",
        ]
    return prompts


def _setup_prompt(step: DeskItem) -> str:
    if step.type == "data_seed":
        if step.form == "visitor":
            return "Help me log the first real visitor."
        if step.form == "prayer":
            return "Help me add the first prayer request."
        if _data_seed_is_care(step):
            return "Help me open the first care case."
        return "Help me add the first real person."
    if step.type == "integration_setup":
        return f"Help me with {step.title}."
    if step.type == "profile_setup":
        return "What do you still need to learn about my ministry?"
    return f"Help me with {step.title}."


def _proactive_summary(profile: PastorProfile, priorities: List[DeskItem], email_drafts: List[DeskItem], calendar_blocks: List[DeskItem], setup_steps: Optional[List[DeskItem]] = None) -> str:
    setup_steps = setup_steps or []
    support = _proactive_support_clause(profile)
    if not _profile_is_complete(profile):
        next_step = setup_steps[0].title if setup_steps else "the next ministry-context question"
        return f"Marge needs a little more ministry context before she can feel truly personal. Start with {next_step}."
    if setup_steps:
        first = setup_steps[0]
        context = _proactive_context_clause(profile)
        return f"I know enough to start helping.{context} Next I would {_setup_summary_phrase(first)} so I can use the tools and rhythms you already have.{support}"
    if priorities:
        pain = f" I am watching the follow-up burden you named: {profile.followup_pain.strip().rstrip('.')}." if profile.followup_pain else ""
        return f"I would start with {priorities[0].title}, keep {len(email_drafts)} draft(s) in review, and protect {len(calendar_blocks)} calendar block(s).{pain}{support}"
    rhythm = _clean(profile.weekly_rhythm)
    if rhythm:
        return f"No current follow-up items are visible in this workspace. I would protect the rhythm you saved: {rhythm}.{support}"
    return f"No current follow-up items are visible in this workspace. Give me the next real person, prayer request, visitor, or care update and I will keep that follow-up visible.{support}"


def _proactive_support_clause(profile: PastorProfile) -> str:
    support = _short_context(profile.support_preferences, 120)
    if not support:
        return ""
    return f" I will support you the way you asked: {support}."


def _proactive_context_clause(profile: PastorProfile) -> str:
    pain = _short_context(profile.followup_pain, 120)
    priority = _short_context(profile.ministry_priorities, 120)
    if pain and priority:
        return f" Because you named {pain} and want {priority},"
    if priority:
        return f" Because you want {priority},"
    if pain:
        return f" Because you named {pain},"
    return ""


def _morning_briefing_requested(lower: str) -> bool:
    if _mentions(lower, ["what needs my attention", "what needs our attention", "what needs attention"]) and _mentions(lower, ["staff meeting", "before meeting", "before the meeting"]):
        return True
    if _mentions(lower, [
        "morning briefing",
        "daily briefing",
        "today's briefing",
        "todays briefing",
        "brief me",
        "briefing for today",
        "start my day",
        "start the day",
        "what's on my desk today",
        "what is on my desk today",
        "what should we handle today",
        "what should i handle today",
    ]):
        return True
    return _mentions(lower, ["briefing", "brief me"]) and _mentions(lower, ["today", "morning", "daily", "desk"])


def _morning_briefing_chat_response(
    profile: PastorProfile,
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: List[DeskItem],
    pending_actions: List[AssistantAction],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    pastor = pastor_display_name(_profile_pastor_name(profile))
    actions: List[DeskItem] = []

    if not _profile_is_complete(profile):
        question = _interview_question(profile)
        first_step = setup_steps[0] if setup_steps else None
        reply = (
            f"Good morning, {pastor}. I am not going to pretend I know this ministry yet. "
            f"Start with this context question: {question['question'] if question else 'answer the next ministry-context question.'} "
            "That lets me brief your real people instead of acting like a generic task list."
        )
        if first_step:
            actions.append(first_step)
        return AssistantChatResponse(
            reply=reply,
            intent="morning_briefing",
            mode=effective_mode,
            actions=actions,
            suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
            profile=_profile_response(profile, account),
        )

    seed_step = next((step for step in setup_steps if step.type == "data_seed"), None)
    support = _proactive_support_clause(profile)
    if seed_step and not priorities:
        reply = (
            f"Good morning, {pastor}. The honest briefing is that I still need the first real ministry record before I can sort people for today. "
            f"I would start with {seed_step.title}: {seed_step.subtitle or seed_step.detail or seed_step.action}. "
            f"After that I can keep visitor, care, prayer, and follow-up work in front of you without inventing people or pretending the desk is already full.{support}"
        )
        return AssistantChatResponse(
            reply=reply,
            intent="morning_briefing",
            mode=effective_mode,
            actions=[seed_step],
            suggested_prompts=_suggested_prompts(profile, priorities, setup_steps),
            profile=_profile_response(profile, account),
        )

    if priorities:
        people_lines = "; ".join(_briefing_priority_line(item) for item in priorities[:4])
        reply = f"Good morning, {pastor}. Here is the desk I would keep in front of you today: {people_lines}."
        actions.extend(priorities[:4])
    else:
        reply = (
            f"Good morning, {pastor}. I do not see overdue care, visitor, prayer, or absence follow-up in the current workspace. "
            "I would protect ministry preparation time and keep watching for new people or prayer needs."
        )
    reply += support

    if pending_actions:
        review_line = "; ".join(f"{action.title} ({action.status})" for action in pending_actions[:3])
        reply += f" Review queue: {review_line}."
        actions.extend(_desk_item_from_action(action) for action in pending_actions[:3])
    elif email_drafts:
        reply += f" I can prepare {len(email_drafts)} reviewable draft(s) from this context."

    if calendar_blocks:
        first_block = calendar_blocks[0]
        reply += f" Calendar: keep {first_block.title} in view before the day gets crowded."

    integration_steps = [step for step in setup_steps if step.type == "integration_setup"]
    if integration_steps:
        reply += f" After the people work, the next secure connector step is {integration_steps[0].title}."
        if len(actions) < 5:
            actions.append(integration_steps[0])

    reply += " Nothing is sent, scheduled, synced, or written externally without checked credentials, policy, and your approval of the exact item."
    return AssistantChatResponse(
        reply=reply,
        intent="morning_briefing",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=["What should I handle next?", "Show my approvals.", "What can wait until next week?"],
        profile=_profile_response(profile, account),
    )


def _briefing_priority_line(item: DeskItem) -> str:
    detail = item.action or item.detail or item.subtitle or "review this"
    if item.type == "visitor":
        return f"{item.title}: visitor follow-up, {detail}"
    if item.type == "care":
        return f"{item.title}: {item.subtitle or 'care follow-up'}, {detail}"
    if item.type == "prayer":
        return f"{item.title}: {item.subtitle or 'prayer follow-up'}, {detail}"
    if item.type == "absence":
        return f"{item.title}: absence check-in, {detail}"
    return f"{item.title}: {detail}"


def _setup_summary_phrase(step: DeskItem) -> str:
    if step.type == "data_seed":
        if step.form == "visitor":
            return "log the first real visitor"
        if step.form == "prayer":
            return "add the first real prayer request"
        return "add the first real person"
    if step.type == "integration_setup":
        title = step.title or "the next connector"
        return f"start secure setup for {title.replace('Connect ', '')}"
    if step.type == "profile_setup":
        return "answer the next ministry-context question"
    action = (step.action or "").strip().rstrip(".")
    if action:
        return action[:1].lower() + action[1:]
    return f"work on {step.title or 'the next setup step'}"


def _calendar_reply(calendar_blocks: List[DeskItem], profile: PastorProfile) -> str:
    if not calendar_blocks:
        return "I do not see a needed visit or care block right now. Tell me your weekly rhythm and I will protect it."
    first = calendar_blocks[0]
    rhythm = f" I will respect this rhythm: {profile.weekly_rhythm}." if profile.weekly_rhythm else ""
    return f"I would propose '{first.title}' for {first.subtitle}. {first.action}.{rhythm} I will not create an event without approval."


def _defer_until_next_week_response(
    profile: PastorProfile,
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: List[DeskItem],
    pending_actions: List[AssistantAction],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    do_not_defer: List[str] = []
    can_wait: List[str] = []
    actions: List[DeskItem] = []
    seen_actions: set[str] = set()

    def add_action(item: DeskItem) -> None:
        key = f"{item.type}:{item.id}"
        if key not in seen_actions:
            actions.append(item)
            seen_actions.add(key)

    seed_or_profile_steps = [step for step in setup_steps if step.type in {"profile_setup", "data_seed"}]
    if seed_or_profile_steps:
        step = seed_or_profile_steps[0]
        do_not_defer.append(f"{step.title}: {step.action or step.subtitle or step.detail or 'finish this setup step'}")
        add_action(step)

    urgent_priorities = [item for item in priorities if item.priority == "high"] or priorities[:2]
    for item in urgent_priorities[:3]:
        detail = item.action or item.detail or item.subtitle or "pastoral follow-up"
        do_not_defer.append(f"{item.title}: {detail}")
        add_action(item)

    review_items = pending_actions[:2]
    for action in review_items:
        do_not_defer.append(f"{action.title}: waiting for review")
        add_action(_desk_item_from_action(action))

    integration_steps = [step for step in setup_steps if step.type == "integration_setup"]
    if integration_steps:
        can_wait.append("new connector syncs that are not needed for today's people or prayer follow-up; keep them in secure setup/check/sync order")
    if calendar_blocks:
        can_wait.append("turning a proposed block into an external calendar event until you approve the exact item")
    if email_drafts and not any(item.type in {"visitor", "care", "prayer"} for item in urgent_priorities):
        can_wait.append("non-urgent draft polishing after the people most likely to be missed are handled")
    can_wait.append("work that is not tied to a real visitor, care case, private prayer request, absence check-in, or approved external action")

    if not do_not_defer:
        do_not_defer.append("I do not see a named care, visitor, prayer, or approval item that must be handled before next week")

    rhythm = f" I would still protect this rhythm: {profile.weekly_rhythm}." if _clean(profile.weekly_rhythm) else ""
    reply = (
        "I would not push these to next week: "
        + "; ".join(do_not_defer[:5])
        + ". What can wait until next week: "
        + "; ".join(can_wait[:4])
        + f".{rhythm} External sends, calendar writes, and church-system changes stay behind approval."
    )
    reply += _proactive_support_clause(profile)
    return AssistantChatResponse(
        reply=reply,
        intent="defer_triage",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=["Show my approvals.", "What should I handle next?", "Where can I fit care follow-up?"],
        profile=_profile_response(profile, account),
    )


def _next_action_response(
    profile: PastorProfile,
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: List[DeskItem],
    pending_actions: List[AssistantAction],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    actions: List[DeskItem] = []
    setup_step = next((step for step in setup_steps if step.type in {"profile_setup", "data_seed"}), None)
    if setup_step:
        reply = (
            f"Handle {setup_step.title} next: {setup_step.action or setup_step.subtitle or setup_step.detail or 'finish this setup step'}. "
            "That gives Marge real ministry context before I start guessing from an empty workspace."
        )
        actions = [setup_step]
    elif priorities:
        first = priorities[0]
        detail = first.action or first.detail or first.subtitle or "pastoral follow-up"
        reply = (
            f"Handle {first.title} next: {detail}. "
            "I am choosing that from the current care, visitor, prayer, and absence context, not from a generic task list."
        )
        actions = priorities[:3]
        if pending_actions:
            reply += f" After that, review {pending_actions[0].title} in the approval queue."
            actions.extend(_desk_item_from_action(action) for action in pending_actions[:2])
    elif pending_actions:
        first_action = pending_actions[0]
        reply = (
            f"Review {first_action.title} next. It is already staged for approval, and I will not send, schedule, "
            "or write externally until you approve the exact item."
        )
        actions = [_desk_item_from_action(action) for action in pending_actions[:4]]
    elif calendar_blocks:
        first = calendar_blocks[0]
        reply = (
            f"Use the next protected block for {first.title}: {first.action or first.detail or first.subtitle}. "
            "I will keep it as a suggestion unless you approve a calendar action."
        )
        actions = calendar_blocks[:3]
    else:
        rhythm = _short_context(profile.weekly_rhythm, 140)
        rhythm_sentence = f" I would protect your saved rhythm instead: {rhythm}." if rhythm else ""
        reply = (
            "I do not see a care, visitor, prayer, absence, setup, or approval item that must be handled next. "
            f"Give me the next real ministry update and I will turn it into reviewable follow-up.{rhythm_sentence}"
        )
        actions = []
    rhythm = _short_context(profile.weekly_rhythm, 140)
    if rhythm and "saved rhythm" not in reply:
        reply += f" Keep your saved rhythm in view: {rhythm}."
    reply += _proactive_support_clause(profile)
    return AssistantChatResponse(
        reply=reply,
        intent="next_action",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=["What can wait until next week?", "Show my approvals.", "Where can I fit care follow-up?"],
        profile=_profile_response(profile, account),
    )


def _check_on_next_response(
    db: Session,
    profile: PastorProfile,
    priorities: List[DeskItem],
    setup_steps: List[DeskItem],
    pending_actions: List[AssistantAction],
    effective_mode: Literal["demo", "live"],
    account: Optional[ChurchAccount],
) -> AssistantChatResponse:
    setup_step = next((step for step in setup_steps if step.type in {"profile_setup", "data_seed"}), None)
    if setup_step:
        reply = (
            f"Before I name people to check on, handle {setup_step.title}: "
            f"{setup_step.action or setup_step.subtitle or setup_step.detail or 'add the first real ministry context'}. "
            "That keeps me from guessing from an empty workspace."
        )
        actions = [setup_step]
    else:
        care_cases = (
            scoped_query(db.query(CareNote), CareNote, account)
            .filter(CareNote.status == "active")
            .order_by(CareNote.last_contact.asc().nullsfirst(), CareNote.created_at.asc())
            .limit(3)
            .all()
        )
        prayers = (
            scoped_query(db.query(PrayerRequest), PrayerRequest, account)
            .filter(PrayerRequest.status == "active")
            .order_by(PrayerRequest.is_private.desc(), PrayerRequest.updated_at.asc())
            .limit(3)
            .all()
        )
        recent_member = _recent_chat_member_context(db, account)
        recent_actions: List[DeskItem] = []
        if recent_member:
            recent_care = next((case for case in care_cases if case.member_id == recent_member.id), None)
            recent_prayer = next((prayer for prayer in prayers if prayer.member_id == recent_member.id), None)
            if recent_care:
                recent_actions.append(_care_desk_item(recent_care))
            if recent_prayer:
                recent_actions.append(_prayer_desk_item(recent_prayer))
        actions = _dedupe_desk_items(
            recent_actions
            + [_care_desk_item(case) for case in care_cases]
            + [_prayer_desk_item(prayer) for prayer in prayers]
            + [item for item in priorities if item.type in {"visitor", "absence"}]
        )
        if actions:
            first = actions[0]
            detail = first.detail or first.subtitle or first.action or "pastoral follow-up"
            reply = (
                f"I would check on {first.title} next: {_short_context(detail, 140)}. "
                "I am choosing that from current care, prayer, visitor, and absence context, not from a generic task list."
            )
            if pending_actions:
                reply += f" After that, review {pending_actions[0].title} in the approval queue."
                actions = _dedupe_desk_items(actions + [_desk_item_from_action(action) for action in pending_actions[:2]])
        else:
            reply = (
                "I do not see a named care, prayer, visitor, or absence follow-up to check on yet. "
                "Give me the next real ministry update and I will keep that person in view."
            )
            actions = []
    reply += _proactive_support_clause(profile)
    return AssistantChatResponse(
        reply=reply,
        intent="next_action",
        mode=effective_mode,
        actions=actions[:5],
        suggested_prompts=_check_on_next_prompts(actions),
        profile=_profile_response(profile, account),
    )


def _recent_chat_member_context(db: Session, account: Optional[ChurchAccount]) -> Optional[Member]:
    rows = (
        scoped_query(db.query(AssistantChatMessage), AssistantChatMessage, account)
        .order_by(AssistantChatMessage.id.desc())
        .limit(8)
        .all()
    )
    for row in rows:
        content = _chat_content(row.content)
        if not content:
            continue
        member = _find_mentioned_member(db, account, content, content.lower())
        if member:
            return member
    return None


def _check_on_next_prompts(actions: List[DeskItem]) -> List[str]:
    first = actions[0] if actions else None
    name = _clean(first.title if first else None)
    has_linked_name = bool(name and name not in {"Name not linked", "Member name not linked", "Visitor name not linked", "Private prayer request", "Prayer request"})
    if first and has_linked_name:
        if first.type == "care":
            return [f"Draft a care follow-up for {name}.", f"Remind me to check on {name} next week.", "What can wait until next week?"]
        if first.type == "prayer":
            return [f"Draft a prayer follow-up for {name}.", f"What do you know about {name}?", "Show my approvals."]
        if first.type == "absence":
            return [f"Draft an absence check-in for {name}.", f"What do you know about {name}?", "Show my approvals."]
    return ["Draft a care follow-up.", "Show my approvals.", "What can wait until next week?"]


def _default_reply(
    profile: PastorProfile,
    priorities: List[DeskItem],
    email_drafts: List[DeskItem],
    calendar_blocks: List[DeskItem],
    setup_steps: Optional[List[DeskItem]] = None,
) -> str:
    name = pastor_display_name(_profile_pastor_name(profile))
    if not _profile_is_complete(profile):
        missing = _missing_profile_fields(profile)
        next_question = next((q for q in ONBOARDING_QUESTIONS if q["id"] == missing[0]), ONBOARDING_QUESTIONS[0])
        return f"I can help, {name}. I still need a bit more context to serve you well: {next_question['question']}"
    if priorities:
        return f"I am with you, {name}. The first thing I would keep in view is {priorities[0].title}: {priorities[0].action or priorities[0].detail}. I also have {len(email_drafts)} draft(s) and {len(calendar_blocks)} calendar suggestion(s) ready."
    setup_steps = setup_steps or []
    if setup_steps:
        first = setup_steps[0]
        action = first.action or first.subtitle or first.detail or "take the next setup step"
        support = _proactive_support_clause(profile)
        return (
            f"I am with you, {name}. The next useful step is {first.title}: {action}. "
            "That gives me real ministry context before I treat the desk as clear, and I will keep connector syncs, sends, "
            f"and external writes behind credential checks and approval.{support}"
        )
    return f"I am with you, {name}. I do not see an urgent care follow-up in the current data, so I would protect your next ministry block and keep watching for new people or prayer needs."


def _mentions(text: str, terms: List[str]) -> bool:
    return any(term in text for term in terms)


def _field(item, name: str):
    if isinstance(item, dict):
        return item.get(name)
    if name == "member_name" and hasattr(item, "member"):
        return item.member.full_name if item.member else None
    if name == "submitted_by" and hasattr(item, "member"):
        return item.submitted_by or (item.member.full_name if item.member else None)
    value = getattr(item, name, None)
    if hasattr(value, "value"):
        return value.value
    return value


def _date_label(value) -> str:
    if not value:
        return "not logged"
    if isinstance(value, str):
        return value
    if hasattr(value, "strftime"):
        return value.strftime("%b %-d, %Y") if os.name != "nt" else value.strftime("%b %#d, %Y")
    return str(value)


def _label(value) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").title()
