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
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from dotenv import load_dotenv


CHECKED_ENV_NAMES = {
    "MARGE_ENV",
    "MARGE_ENFORCE_PRODUCTION_CONFIG",
    "DATABASE_URL",
    "MARGE_AUTO_CREATE_SCHEMA",
    "MARGE_REQUIRE_ACCOUNT_TOKEN",
    "MARGE_ENCRYPTION_KEY",
    "MARGE_SESSION_COOKIE_SECURE",
    "MARGE_SESSION_COOKIE_SAMESITE",
    "MARGE_APP_URL",
    "CORS_ORIGINS",
    "SMTP_HOST",
    "MARGE_INVITE_EMAIL_FROM",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_STARTTLS",
    "SMTP_PORT",
    "PLANNING_CENTER_CLIENT_ID",
    "PLANNING_CENTER_CLIENT_SECRET",
    "PLANNING_CENTER_REDIRECT_URI",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "MICROSOFT_CLIENT_ID",
    "MICROSOFT_CLIENT_SECRET",
    "MICROSOFT_REDIRECT_URI",
    "BREEZE_API_KEY",
    "BREEZE_BASE_URL",
    "ROCK_API_KEY",
    "ROCK_BASE_URL",
    "ROCK_HALLMARK_API_KEY",
}


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
    if (
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    ):
        return True
    if (
        allow_localhost
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    ):
        return True
    return False


def valid_connector_base_url(raw: str) -> bool:
    parsed = urlparse(raw)
    return valid_https_url(raw) and public_connector_hostname(parsed.hostname)


def public_connector_hostname(hostname: str | None) -> bool:
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


def origin_for_url(raw: str) -> str:
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def valid_oauth_redirect_uri(raw: str, *, provider: str, app_url: str) -> tuple[bool, str]:
    if not valid_https_url(raw):
        return False, "must be HTTPS."
    parsed = urlparse(raw)
    expected_path = f"/assistant/integrations/{provider}/callback"
    if parsed.path.rstrip("/") != expected_path:
        return False, f"must use the {expected_path} callback path."
    if valid_app_url(app_url) and origin_for_url(raw) != origin_for_url(app_url):
        return False, "must use the same origin as MARGE_APP_URL."
    return True, ""


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
        checks.append(Check("pass", "MARGE_ENCRYPTION_KEY", "Connector credential encryption key is valid."))
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
    elif valid_app_url(app_url) and origin_for_url(app_url) not in cors_origins:
        checks.append(Check("fail", "CORS_ORIGINS", "CORS_ORIGINS must include the MARGE_APP_URL origin so the first-run app can call the API."))
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
        ("Planning Center", "planning_center", ["PLANNING_CENTER_CLIENT_ID", "PLANNING_CENTER_CLIENT_SECRET", "PLANNING_CENTER_REDIRECT_URI"]),
        ("Google Workspace", "google_workspace", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]),
        ("Microsoft 365", "microsoft_365", ["MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REDIRECT_URI"]),
    ]
    for label, provider, names in oauth_groups:
        present = [name for name in names if value(name)]
        if present and len(present) != len(names):
            missing = ", ".join(name for name in names if not value(name))
            checks.append(Check("fail", label, f"OAuth config is partial; missing {missing}."))
        elif placeholder_names(names):
            checks.append(Check("fail", label, f"OAuth config still has placeholder values: {', '.join(placeholder_names(names))}."))
        elif len(present) == len(names):
            redirect_name = names[-1]
            redirect_ok, redirect_message = valid_oauth_redirect_uri(value(redirect_name), provider=provider, app_url=app_url)
            if redirect_ok:
                configured_oauth.append(label)
            else:
                checks.append(Check("fail", redirect_name, f"{redirect_name} {redirect_message}"))

    breeze_names = ["BREEZE_API_KEY", "BREEZE_BASE_URL"]
    breeze_present = [name for name in breeze_names if value(name)]
    breeze_configured = False
    rock_names = ["ROCK_API_KEY", "ROCK_BASE_URL"]
    rock_present = [name for name in rock_names if value(name)]
    rock_configured = False
    if breeze_present and len(breeze_present) != len(breeze_names):
        missing = ", ".join(name for name in breeze_names if not value(name))
        checks.append(Check("fail", "Breeze", f"Breeze server-side config is partial; missing {missing}."))
    elif placeholder_names(breeze_names):
        checks.append(Check("fail", "Breeze", f"Breeze server-side config still has placeholder values: {', '.join(placeholder_names(breeze_names))}."))
    elif len(breeze_present) == len(breeze_names):
        if valid_connector_base_url(value("BREEZE_BASE_URL")):
            breeze_configured = True
        else:
            checks.append(Check("fail", "BREEZE_BASE_URL", "BREEZE_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment."))
    if value("ROCK_HALLMARK_API_KEY"):
        checks.append(Check("fail", "Rock RMS", "Use ROCK_API_KEY for production Rock config; ROCK_HALLMARK_API_KEY is a legacy local fallback."))
    if rock_present and len(rock_present) != len(rock_names):
        missing = ", ".join(name for name in rock_names if not value(name))
        checks.append(Check("fail", "Rock RMS", f"Rock RMS server-side config is partial; missing {missing}."))
    elif placeholder_names(rock_names):
        checks.append(Check("fail", "Rock RMS", f"Rock RMS server-side config still has placeholder values: {', '.join(placeholder_names(rock_names))}."))
    elif len(rock_present) == len(rock_names):
        if valid_connector_base_url(value("ROCK_BASE_URL")):
            rock_configured = True
        else:
            checks.append(Check("fail", "ROCK_BASE_URL", "ROCK_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment."))

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


def production_next_actions(checks: list[Check], *, env_file: str | None = None) -> list[str]:
    failing_names = {check.name for check in checks if check.level == "fail"}
    warning_names = {check.name for check in checks if check.level == "warn"}
    actions: list[str] = []
    env_target = env_file or ".env.production.candidate"

    if "MARGE_ENV" in failing_names:
        actions.append("Set MARGE_ENV=production or MARGE_ENFORCE_PRODUCTION_CONFIG=true so unsafe deployments fail closed.")
    if "DATABASE_URL" in failing_names:
        actions.append("Provision a managed production database and set DATABASE_URL; do not use SQLite for real pastoral data.")
    if "MARGE_AUTO_CREATE_SCHEMA" in failing_names:
        actions.append("Run Alembic migrations during deployment, then set MARGE_AUTO_CREATE_SCHEMA=false.")
    if "MARGE_REQUIRE_ACCOUNT_TOKEN" in failing_names:
        actions.append("Set MARGE_REQUIRE_ACCOUNT_TOKEN=true so scoped routes require a pastor/admin/owner workspace token.")
    if "MARGE_ENCRYPTION_KEY" in failing_names:
        actions.append("Generate a stable Fernet key with `python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` and set MARGE_ENCRYPTION_KEY.")
    if "MARGE_SESSION_COOKIE_SECURE" in failing_names or "MARGE_SESSION_COOKIE_SAMESITE" in failing_names:
        actions.append("Set secure browser-session cookie settings for HTTPS, usually MARGE_SESSION_COOKIE_SECURE=true and MARGE_SESSION_COOKIE_SAMESITE=lax.")
    if "MARGE_APP_URL" in failing_names or "CORS_ORIGINS" in failing_names:
        actions.append("Set MARGE_APP_URL to the exact public https://.../app URL and CORS_ORIGINS to the matching origin with no path or wildcard.")
    if "SMTP" in failing_names or "SMTP_STARTTLS" in failing_names:
        actions.append("Configure SMTP_HOST, MARGE_INVITE_EMAIL_FROM, and SMTP TLS/auth settings so invite and passwordless login links can be delivered.")

    oauth_failures = [
        name
        for name in failing_names
        if name in {
            "Planning Center",
            "Google Workspace",
            "Microsoft 365",
            "PLANNING_CENTER_REDIRECT_URI",
            "GOOGLE_REDIRECT_URI",
            "MICROSOFT_REDIRECT_URI",
        }
    ]
    if oauth_failures:
        actions.append("Complete each configured OAuth provider as a full client id, client secret, and same-origin HTTPS callback path.")

    connector_failures = [
        name
        for name in failing_names
        if name in {"Breeze", "Rock RMS", "BREEZE_BASE_URL", "ROCK_BASE_URL"}
    ]
    if connector_failures:
        actions.append("Fix Breeze/Rock server-side API-key config or leave it blank and use encrypted workspace credentials; base URLs must be clean public HTTPS URLs.")

    if "CONNECTORS" in warning_names:
        actions.append("Before pilot use, connect or configure at least one real external provider and run the no-sync Check credentials flow.")

    if failing_names:
        actions.append(f"Rerun `.venv/bin/python scripts/verify_production_config.py --env-file {env_target}` until failures are 0.")
    elif warning_names:
        actions.append("Production guardrails pass, but resolve warnings before a first pastor pilot if they affect live tool connection.")

    return actions


def print_next_actions(actions: list[str]) -> None:
    if not actions:
        return
    print("\nNext actions:")
    for action in actions:
        print(f"  - {action}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether Marge's environment is safe enough for non-local deployment.")
    parser.add_argument("--env-file", help="Optional env file to load before checking, for example .env.production.candidate.")
    args = parser.parse_args()

    if args.env_file:
        for name in CHECKED_ENV_NAMES:
            os.environ.pop(name, None)
        load_dotenv(args.env_file, override=True)
    else:
        load_dotenv()

    checks = production_checks()
    for check in checks:
        marker = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[check.level]
        print(f"{marker} {check.name}: {check.message}")

    failures = [check for check in checks if check.level == "fail"]
    warnings = [check for check in checks if check.level == "warn"]
    next_actions = production_next_actions(checks, env_file=args.env_file)
    print(json.dumps({"failures": len(failures), "warnings": len(warnings), "next_actions": next_actions}, indent=2))
    print_next_actions(next_actions)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
