#!/usr/bin/env python3
"""
Check whether Marge's environment is safe enough for a non-local deployment.

This does not contact live providers. It verifies the deployment guardrails that
should be true before real pastoral data is exposed: strict account tokens,
valid encryption, HTTPS session cookies, constrained CORS, migration-first DB
startup, and email delivery for passwordless/invite links.

Usage:
  .venv/bin/python scripts/verify_production_config.py
"""

from __future__ import annotations

import json
import os
import sys
import argparse
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from dotenv import load_dotenv


@dataclass
class Check:
    level: str
    name: str
    message: str


def truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def falsey(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"0", "false", "no", "off"}


def value(name: str) -> str:
    return os.getenv(name, "").strip()


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


def valid_https_url(raw: str, *, allow_localhost: bool = False) -> bool:
    parsed = urlparse(raw)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if allow_localhost and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
        return True
    return False


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


def valid_fernet_key(raw: str) -> bool:
    if not raw:
        return False
    try:
        Fernet(raw.encode("ascii"))
    except Exception:
        return False
    return True


def production_checks() -> list[Check]:
    checks: list[Check] = []

    marge_env = value("MARGE_ENV").lower()
    enforce_runtime = truthy("MARGE_ENFORCE_PRODUCTION_CONFIG")
    if marge_env in {"prod", "production"} or enforce_runtime:
        checks.append(Check("pass", "MARGE_ENV", "Production runtime safety checks are enabled."))
    else:
        checks.append(Check("fail", "MARGE_ENV", "Set MARGE_ENV=production or MARGE_ENFORCE_PRODUCTION_CONFIG=true so unsafe deployments fail closed at startup."))

    database_url = value("DATABASE_URL")
    if not database_url:
        checks.append(Check("fail", "DATABASE_URL", "DATABASE_URL must be set."))
    elif placeholder_value(database_url):
        checks.append(Check("fail", "DATABASE_URL", "DATABASE_URL still contains a placeholder value."))
    elif database_url.startswith("sqlite"):
        checks.append(Check("fail", "DATABASE_URL", "Production should use Postgres or another managed database, not SQLite."))
    else:
        checks.append(Check("pass", "DATABASE_URL", "Database URL is not SQLite."))

    if falsey("MARGE_AUTO_CREATE_SCHEMA"):
        checks.append(Check("pass", "MARGE_AUTO_CREATE_SCHEMA", "Startup schema creation is disabled."))
    else:
        checks.append(Check("fail", "MARGE_AUTO_CREATE_SCHEMA", "Set MARGE_AUTO_CREATE_SCHEMA=false after running Alembic migrations."))

    if truthy("MARGE_REQUIRE_ACCOUNT_TOKEN"):
        checks.append(Check("pass", "MARGE_REQUIRE_ACCOUNT_TOKEN", "Scoped routes require workspace tokens."))
    else:
        checks.append(Check("fail", "MARGE_REQUIRE_ACCOUNT_TOKEN", "Set MARGE_REQUIRE_ACCOUNT_TOKEN=true before exposing real pastoral data."))

    if valid_fernet_key(value("MARGE_ENCRYPTION_KEY")):
        checks.append(Check("pass", "MARGE_ENCRYPTION_KEY", "OAuth token encryption key is valid."))
    else:
        checks.append(Check("fail", "MARGE_ENCRYPTION_KEY", "MARGE_ENCRYPTION_KEY must be a valid Fernet key."))

    if truthy("MARGE_SESSION_COOKIE_SECURE"):
        checks.append(Check("pass", "MARGE_SESSION_COOKIE_SECURE", "Session cookies require HTTPS."))
    else:
        checks.append(Check("fail", "MARGE_SESSION_COOKIE_SECURE", "Set MARGE_SESSION_COOKIE_SECURE=true behind HTTPS."))

    samesite = value("MARGE_SESSION_COOKIE_SAMESITE").lower() or "lax"
    if samesite not in {"lax", "strict", "none"}:
        checks.append(Check("fail", "MARGE_SESSION_COOKIE_SAMESITE", "Use lax, strict, or none."))
    elif samesite == "none" and not truthy("MARGE_SESSION_COOKIE_SECURE"):
        checks.append(Check("fail", "MARGE_SESSION_COOKIE_SAMESITE", "SameSite=None requires secure cookies."))
    else:
        checks.append(Check("pass", "MARGE_SESSION_COOKIE_SAMESITE", f"Session cookie SameSite is {samesite}."))

    app_url = value("MARGE_APP_URL")
    if placeholder_value(app_url):
        checks.append(Check("fail", "MARGE_APP_URL", "Replace the placeholder app URL with the real HTTPS /app URL."))
    elif valid_app_url(app_url):
        checks.append(Check("pass", "MARGE_APP_URL", "App URL is the exact HTTPS /app URL."))
    else:
        checks.append(Check("fail", "MARGE_APP_URL", "Set MARGE_APP_URL to the exact HTTPS /app URL used in invite and login links, for example https://marge.yourchurch.org/app."))

    cors_origins = [item.strip() for item in value("CORS_ORIGINS").split(",") if item.strip()]
    if not cors_origins:
        checks.append(Check("fail", "CORS_ORIGINS", "Set CORS_ORIGINS to the exact HTTPS frontend origin(s)."))
    elif "*" in cors_origins:
        checks.append(Check("fail", "CORS_ORIGINS", "Do not use wildcard CORS in production."))
    elif any(placeholder_value(origin) for origin in cors_origins):
        checks.append(Check("fail", "CORS_ORIGINS", "Replace placeholder CORS origins with the real HTTPS frontend origin(s)."))
    elif any(not valid_cors_origin(origin) for origin in cors_origins):
        checks.append(Check("fail", "CORS_ORIGINS", "Every production CORS origin should be an exact HTTPS origin with no path, query, or wildcard."))
    else:
        checks.append(Check("pass", "CORS_ORIGINS", f"{len(cors_origins)} HTTPS CORS origin(s) configured."))

    if value("SMTP_HOST") and value("MARGE_INVITE_EMAIL_FROM"):
        smtp_placeholders = placeholder_names(["SMTP_HOST", "MARGE_INVITE_EMAIL_FROM", "SMTP_USERNAME", "SMTP_PASSWORD"])
        if smtp_placeholders:
            checks.append(Check("fail", "SMTP", f"SMTP config still has placeholder values: {', '.join(smtp_placeholders)}."))
        elif truthy("SMTP_STARTTLS") or value("SMTP_PORT") == "465":
            checks.append(Check("pass", "SMTP", "Invite and passwordless email delivery is configured."))
        else:
            checks.append(Check("fail", "SMTP_STARTTLS", "Enable SMTP_STARTTLS=true unless using an implicit TLS SMTP port such as 465."))
    else:
        checks.append(Check("fail", "SMTP", "Set SMTP_HOST and MARGE_INVITE_EMAIL_FROM so users can receive invite/login links."))

    configured_oauth = []
    oauth_groups = [
        ("Planning Center", ["PLANNING_CENTER_CLIENT_ID", "PLANNING_CENTER_CLIENT_SECRET", "PLANNING_CENTER_REDIRECT_URI"]),
        ("Google Workspace", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]),
        ("Microsoft 365", ["MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REDIRECT_URI"]),
    ]
    for label, names in oauth_groups:
        present = [name for name in names if value(name)]
        if present and len(present) != len(names):
            missing = ", ".join(name for name in names if not value(name))
            checks.append(Check("fail", label, f"OAuth config is partial; missing {missing}."))
        elif placeholder_names(names):
            checks.append(Check("fail", label, f"OAuth config still has placeholder values: {', '.join(placeholder_names(names))}."))
        elif len(present) == len(names):
            redirect_name = names[-1]
            if valid_https_url(value(redirect_name)):
                configured_oauth.append(label)
            else:
                checks.append(Check("fail", redirect_name, f"{redirect_name} must be HTTPS."))

    breeze_names = ["BREEZE_API_KEY", "BREEZE_BASE_URL"]
    breeze_present = [name for name in breeze_names if value(name)]
    breeze_configured = len(breeze_present) == len(breeze_names) and not placeholder_names(breeze_names)
    rock_configured = value("ROCK_HALLMARK_API_KEY") and not placeholder_value(value("ROCK_HALLMARK_API_KEY"))
    if breeze_present and len(breeze_present) != len(breeze_names):
        missing = ", ".join(name for name in breeze_names if not value(name))
        checks.append(Check("fail", "Breeze", f"Breeze server-side config is partial; missing {missing}."))
    elif placeholder_names(breeze_names):
        checks.append(Check("fail", "Breeze", f"Breeze server-side config still has placeholder values: {', '.join(placeholder_names(breeze_names))}."))
    if value("ROCK_HALLMARK_API_KEY") and not rock_configured:
        checks.append(Check("fail", "Rock RMS", "Rock RMS server-side config still has a placeholder value."))

    if configured_oauth or breeze_configured or rock_configured:
        providers = configured_oauth[:]
        if breeze_configured:
            providers.append("Breeze")
        if rock_configured:
            providers.append("Rock RMS")
        checks.append(Check("pass", "CONNECTORS", f"Server-side connector config present for: {', '.join(providers)}."))
    else:
        checks.append(Check("warn", "CONNECTORS", "No live connector server config is present yet; pastors can onboard, but tool connection will remain setup-only."))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether Marge's environment is safe enough for non-local deployment.")
    parser.add_argument("--env-file", help="Optional env file to load before checking, for example .env.production.candidate.")
    args = parser.parse_args()

    if args.env_file:
        load_dotenv(args.env_file, override=True)
    else:
        load_dotenv()

    checks = production_checks()
    for check in checks:
        marker = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[check.level]
        print(f"{marker} {check.name}: {check.message}")

    failures = [check for check in checks if check.level == "fail"]
    warnings = [check for check in checks if check.level == "warn"]
    print(json.dumps({"failures": len(failures), "warnings": len(warnings)}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
