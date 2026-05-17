"""
Lightweight account scoping helpers for Marge.

The local MVP accepts workspace tokens through X-Marge-Account-Token and
browser session cookies. Only a SHA-256 token hash is stored in the database.
"""

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

from app.models import AccountSession, AccountUser, ChurchAccount

REQUIRE_ACCOUNT_TOKEN_ENV = "MARGE_REQUIRE_ACCOUNT_TOKEN"
SESSION_COOKIE_NAME_ENV = "MARGE_SESSION_COOKIE_NAME"
SESSION_COOKIE_SECURE_ENV = "MARGE_SESSION_COOKIE_SECURE"
SESSION_COOKIE_SAMESITE_ENV = "MARGE_SESSION_COOKIE_SAMESITE"
ACCOUNT_ROLES = {"owner", "admin", "pastor", "staff", "viewer"}
ADMIN_ROLES = {"owner", "admin"}
PASTORAL_ROLES = {"owner", "admin", "pastor"}
STAFF_ROLES = {"owner", "admin", "pastor", "staff"}


@dataclass
class AccountAccess:
    account: Optional[ChurchAccount]
    user: Optional[AccountUser]
    session: Optional[AccountSession]
    role: Optional[str]
    token_type: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def account_tokens_required() -> bool:
    return os.getenv(REQUIRE_ACCOUNT_TOKEN_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def session_cookie_name() -> str:
    return os.getenv(SESSION_COOKIE_NAME_ENV, "marge_session").strip() or "marge_session"


def session_cookie_secure() -> bool:
    return os.getenv(SESSION_COOKIE_SECURE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def session_cookie_samesite() -> str:
    value = os.getenv(SESSION_COOKIE_SAMESITE_ENV, "lax").strip().lower()
    return value if value in {"lax", "strict", "none"} else "lax"


def normalize_role(role: Optional[str], default: str = "staff") -> str:
    cleaned = (role or default).strip().lower()
    return cleaned if cleaned in ACCOUNT_ROLES else default


def account_access_from_token(db: Session, token: Optional[str]) -> AccountAccess:
    cleaned = (token or "").strip()
    if not cleaned:
        if account_tokens_required():
            raise HTTPException(status_code=401, detail="Marge account token is required.")
        return AccountAccess(account=None, user=None, session=None, role=None, token_type="legacy_unscoped")

    hashed = token_hash(cleaned)
    now = datetime.utcnow()
    for session in db.query(AccountSession).filter(AccountSession.revoked_at.is_(None)).all():
        if not hmac.compare_digest(session.token_hash, hashed):
            continue
        if session.expires_at <= now:
            session.revoked_at = now
            raise HTTPException(status_code=401, detail="Marge session expired.")
        user = db.get(AccountUser, session.user_id)
        account = db.get(ChurchAccount, session.account_id)
        if not user or not user.active or not account:
            raise HTTPException(status_code=401, detail="Invalid Marge session.")
        session.last_seen_at = now
        user.last_seen_at = now
        return AccountAccess(account=account, user=user, session=session, role=normalize_role(user.role, "staff"), token_type="session")

    for user in db.query(AccountUser).filter(AccountUser.active.is_(True)).all():
        if hmac.compare_digest(user.token_hash, hashed):
            account = db.get(ChurchAccount, user.account_id)
            if not account:
                raise HTTPException(status_code=401, detail="Invalid Marge user token.")
            user.last_seen_at = now
            return AccountAccess(account=account, user=user, session=None, role=normalize_role(user.role, "staff"), token_type="user")

    for account in db.query(ChurchAccount).all():
        if hmac.compare_digest(account.token_hash, hashed):
            return AccountAccess(account=account, user=None, session=None, role="owner", token_type="legacy_account")

    raise HTTPException(status_code=401, detail="Invalid Marge account token.")


def account_from_token(db: Session, token: Optional[str]) -> Optional[ChurchAccount]:
    return account_access_from_token(db, token).account


def require_role(access: AccountAccess, allowed_roles: set[str], action: str = "perform this action") -> None:
    if access.account is None and access.role is None:
        return
    role = normalize_role(access.role, "viewer")
    if role not in allowed_roles:
        allowed = ", ".join(sorted(allowed_roles))
        raise HTTPException(status_code=403, detail=f"Only {allowed} users can {action}.")


def scoped_query(query: Query, model, account: Optional[ChurchAccount]) -> Query:
    if not hasattr(model, "account_id"):
        return query
    if account:
        return query.filter(model.account_id == account.id)
    return query.filter(model.account_id.is_(None))


def account_id(account: Optional[ChurchAccount]) -> Optional[int]:
    return account.id if account else None
