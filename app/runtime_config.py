"""
Runtime safety checks for Marge deployments.

Local development should stay easy, but a real pastoral-data deployment should
fail closed when the operator has not enabled the production guardrails that
the readiness verifier expects.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from cryptography.fernet import Fernet


PRODUCTION_ENV_VALUES = {"prod", "production"}


def truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def falsey(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"0", "false", "no", "off"}


def value(name: str) -> str:
    return os.getenv(name, "").strip()


def production_runtime_enabled() -> bool:
    return value("MARGE_ENV").lower() in PRODUCTION_ENV_VALUES or truthy("MARGE_ENFORCE_PRODUCTION_CONFIG")


def placeholder_value(raw: str) -> bool:
    cleaned = raw.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ["replace_with", "changeme", "placeholder"]):
        return True
    parsed = urlparse(cleaned)
    hostname = parsed.hostname or ""
    if not hostname and "@" in cleaned:
        hostname = cleaned.rsplit("@", 1)[-1].strip("<> ")
    example_hosts = {"example.com", "example.org", "example.net"}
    return hostname in example_hosts or any(hostname.endswith(f".{example_host}") for example_host in example_hosts)


def placeholder_names(names: list[str]) -> list[str]:
    return [name for name in names if placeholder_value(value(name))]


def valid_https_url(raw: str) -> bool:
    parsed = urlparse(raw)
    return parsed.scheme == "https" and bool(parsed.netloc)


def valid_fernet_key(raw: str) -> bool:
    if not raw:
        return False
    try:
        Fernet(raw.encode("ascii"))
    except Exception:
        return False
    return True


def valid_app_url(raw: str) -> bool:
    parsed = urlparse(raw)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path.rstrip("/") == "/app"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def valid_cors_origin(raw: str) -> bool:
    parsed = urlparse(raw)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def production_safety_errors() -> list[str]:
    if not production_runtime_enabled():
        return []

    errors: list[str] = []
    database_url = value("DATABASE_URL")
    if not database_url:
        errors.append("DATABASE_URL must be set.")
    elif placeholder_value(database_url):
        errors.append("DATABASE_URL still contains a placeholder value.")
    elif database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must use a managed database, not SQLite, when MARGE_ENV=production.")

    if not falsey("MARGE_AUTO_CREATE_SCHEMA"):
        errors.append("MARGE_AUTO_CREATE_SCHEMA must be explicitly false; run Alembic migrations before startup.")

    if not truthy("MARGE_REQUIRE_ACCOUNT_TOKEN"):
        errors.append("MARGE_REQUIRE_ACCOUNT_TOKEN must be true before exposing real pastoral data.")

    if not valid_fernet_key(value("MARGE_ENCRYPTION_KEY")):
        errors.append("MARGE_ENCRYPTION_KEY must be a valid Fernet key.")

    if not truthy("MARGE_SESSION_COOKIE_SECURE"):
        errors.append("MARGE_SESSION_COOKIE_SECURE must be true behind HTTPS.")

    samesite = value("MARGE_SESSION_COOKIE_SAMESITE").lower() or "lax"
    if samesite not in {"lax", "strict", "none"}:
        errors.append("MARGE_SESSION_COOKIE_SAMESITE must be lax, strict, or none.")
    elif samesite == "none" and not truthy("MARGE_SESSION_COOKIE_SECURE"):
        errors.append("MARGE_SESSION_COOKIE_SAMESITE=none requires secure cookies.")

    app_url = value("MARGE_APP_URL")
    if placeholder_value(app_url) or not valid_app_url(app_url):
        errors.append("MARGE_APP_URL must be the exact public HTTPS /app URL.")

    cors_origins = [item.strip() for item in value("CORS_ORIGINS").split(",") if item.strip()]
    if not cors_origins:
        errors.append("CORS_ORIGINS must list exact HTTPS frontend origin(s).")
    elif "*" in cors_origins:
        errors.append("CORS_ORIGINS must not contain a wildcard in production.")
    elif any(placeholder_value(origin) or not valid_cors_origin(origin) for origin in cors_origins):
        errors.append("Every CORS_ORIGINS value must be an exact HTTPS origin with no path, query, wildcard, or placeholder.")

    if not value("SMTP_HOST") or not value("MARGE_INVITE_EMAIL_FROM"):
        errors.append("SMTP_HOST and MARGE_INVITE_EMAIL_FROM must be set for invite/passwordless login delivery.")
    elif placeholder_value(value("SMTP_HOST")) or placeholder_value(value("MARGE_INVITE_EMAIL_FROM")):
        errors.append("SMTP_HOST and MARGE_INVITE_EMAIL_FROM must not contain placeholder values.")
    elif not truthy("SMTP_STARTTLS") and value("SMTP_PORT") != "465":
        errors.append("SMTP_STARTTLS must be true unless SMTP_PORT is 465.")

    oauth_groups = [
        ("Planning Center", ["PLANNING_CENTER_CLIENT_ID", "PLANNING_CENTER_CLIENT_SECRET", "PLANNING_CENTER_REDIRECT_URI"]),
        ("Google Workspace", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]),
        ("Microsoft 365", ["MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REDIRECT_URI"]),
    ]
    for label, names in oauth_groups:
        present = [name for name in names if value(name)]
        placeholders = placeholder_names(names)
        if present and len(present) != len(names):
            missing = ", ".join(name for name in names if not value(name))
            errors.append(f"{label} OAuth config is partial; missing {missing}.")
        elif placeholders:
            errors.append(f"{label} OAuth config still has placeholder values: {', '.join(placeholders)}.")
        elif len(present) == len(names) and not valid_https_url(value(names[-1])):
            errors.append(f"{names[-1]} must be HTTPS in production.")

    breeze_names = ["BREEZE_API_KEY", "BREEZE_BASE_URL"]
    breeze_present = [name for name in breeze_names if value(name)]
    if breeze_present and len(breeze_present) != len(breeze_names):
        missing = ", ".join(name for name in breeze_names if not value(name))
        errors.append(f"Breeze config is partial; missing {missing}.")
    elif placeholder_names(breeze_names):
        errors.append(f"Breeze config still has placeholder values: {', '.join(placeholder_names(breeze_names))}.")

    if placeholder_value(value("ROCK_HALLMARK_API_KEY")):
        errors.append("Rock RMS server-side config still has a placeholder value.")

    return errors


def assert_production_runtime_safe() -> None:
    errors = production_safety_errors()
    if errors:
        details = "\n- " + "\n- ".join(errors)
        raise RuntimeError(f"Marge production runtime safety check failed:{details}")
