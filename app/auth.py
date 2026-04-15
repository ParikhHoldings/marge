"""Authentication, authorization, and tenant scoping helpers."""

import os
import json
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request


ROLE_PASTOR = "pastor"
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_READ_ONLY = "read-only"
ALLOWED_ROLES = {ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF, ROLE_READ_ONLY}


@dataclass
class AuthContext:
    user_id: str
    role: str
    church_id: str
    auth_type: str


class AuthError(Exception):
    """Raised when authentication cannot be established."""


def _default_church_id() -> str:
    return os.getenv("DEFAULT_CHURCH_ID", "default-church")


def _session_map() -> dict[str, AuthContext]:
    """
    Parse AUTH_SESSION_TOKENS env var.

    Format (comma-separated):
      token|role|church_id|user_id

    Example:
      dev-pastor|pastor|hallmark|nathan,dev-admin|admin|hallmark|ops1
    """
    raw = os.getenv("AUTH_SESSION_TOKENS", "")
    mapping: dict[str, AuthContext] = {}
    if not raw:
        return mapping

    entries = [e.strip() for e in raw.split(",") if e.strip()]
    for entry in entries:
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 3:
            continue
        token, role, church_id = parts[0], parts[1], parts[2]
        user_id = parts[3] if len(parts) > 3 else f"session:{token[:8]}"
        if role not in ALLOWED_ROLES:
            continue
        mapping[token] = AuthContext(
            user_id=user_id,
            role=role,
            church_id=church_id or _default_church_id(),
            auth_type="session",
        )
    return mapping


def _decode_jwt(token: str) -> AuthContext:
    secret = os.getenv("AUTH_JWT_SECRET")
    if not secret:
        raise AuthError("JWT auth is not configured")

    algorithms = [a.strip() for a in os.getenv("AUTH_JWT_ALGORITHMS", "HS256").split(",") if a.strip()]
    issuer = os.getenv("AUTH_JWT_ISSUER")
    audience = os.getenv("AUTH_JWT_AUDIENCE")

    options = {"verify_aud": bool(audience), "verify_iss": bool(issuer)}
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=algorithms,
            issuer=issuer if issuer else None,
            audience=audience if audience else None,
            options=options,
        )
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid JWT token") from exc

    role = payload.get("role")
    if not role:
        roles = payload.get("roles")
        if isinstance(roles, list) and roles:
            role = roles[0]
    if role not in ALLOWED_ROLES:
        raise AuthError("JWT role is missing or invalid")

    church_id = payload.get("church_id") or payload.get("tenant") or _default_church_id()
    user_id = str(payload.get("sub") or payload.get("user_id") or "unknown")

    return AuthContext(
        user_id=user_id,
        role=role,
        church_id=str(church_id),
        auth_type="jwt",
    )


def validate_auth_headers(request: Request) -> AuthContext:
    """
    Validate either a Bearer JWT or X-Session-Token.

    Priority:
      1) Authorization: Bearer <jwt>
      2) X-Session-Token: <opaque token>
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthError("Missing bearer token")
        return _decode_jwt(token)

    session_token = request.headers.get("X-Session-Token")
    if session_token:
        sessions = _session_map()
        context = sessions.get(session_token)
        if context:
            return context
        raise AuthError("Invalid session token")

    raise AuthError("Missing auth headers")


def get_auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return context


def require_roles(*allowed_roles: str) -> Callable:
    def dependency(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if context.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Forbidden: role not permitted")
        return context

    return dependency


def require_same_church(resource_church_id: str, context: AuthContext) -> None:
    if resource_church_id != context.church_id:
        raise HTTPException(status_code=403, detail="Forbidden: cross-church access denied")
