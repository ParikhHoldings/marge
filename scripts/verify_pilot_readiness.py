#!/usr/bin/env python3
"""
Run the production/pilot readiness gates for Marge.

This is the one command to use before a real pastor relies on Marge with live
ministry data. It combines:

- production environment safety checks,
- public deployment bootstrap checks,
- migration/schema drift verification,
- first-run workspace journey verification,
- no-sync live connector verification requiring at least one external provider.

Usage:
  MARGE_API_URL=https://marge.yourchurch.org \
  MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
  .venv/bin/python scripts/verify_pilot_readiness.py

  .venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate

  # Save the live connector verification report somewhere custom:
  .venv/bin/python scripts/verify_pilot_readiness.py --live-evidence-file artifacts/pilot/live.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "http://127.0.0.1:8000"
OPERATOR_ROLES = {"admin", "owner", "pastor"}
ENV_FILE_BLANK_PRESERVE_KEYS = {"MARGE_API_URL", "MARGE_ACCOUNT_TOKEN"}
LIVE_EXTERNAL_PROVIDERS = {"google_workspace", "microsoft_365", "planning_center", "breeze", "rock"}
LIVE_EVIDENCE_MAX_AGE = timedelta(hours=24)
LIVE_EVIDENCE_FUTURE_SKEW = timedelta(minutes=15)
LIVE_EVIDENCE_SIDE_EFFECT_COLLECTIONS = {
    "connected_context",
    "assistant_actions",
    "members",
    "visitors",
    "care_cases",
    "prayer_requests",
}
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
}
SENSITIVE_EVIDENCE_VALUE_PATTERNS = [
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


def is_local_api_url(api_url: str) -> bool:
    parsed = urlparse(api_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def build_env(env_file: str | None, api_url: str | None, token: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_file:
        values = dotenv_values(env_file)
        for key, value in values.items():
            if value is None:
                continue
            if key in ENV_FILE_BLANK_PRESERVE_KEYS and not value.strip() and env.get(key):
                continue
            env[key] = value
    if api_url:
        env["MARGE_API_URL"] = api_url
    elif not env.get("MARGE_API_URL"):
        env["MARGE_API_URL"] = DEFAULT_API_URL
    if token:
        env["MARGE_ACCOUNT_TOKEN"] = token
    return env


def run_step(title: str, command: list[str], env: dict[str, str]) -> int:
    print(f"\n== {title} ==", flush=True)
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if result.returncode == 0:
        print(f"PASS {title}", flush=True)
    else:
        print(f"FAIL {title} (exit {result.returncode})", flush=True)
    return result.returncode


def _evidence_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def clear_live_evidence_file(path: str) -> tuple[int, str]:
    target = _evidence_path(path)
    if not target.exists():
        return 0, f"No previous live connector evidence at {target}."
    if not target.is_file():
        return 3, f"Live connector evidence path is not a file: {target}"
    try:
        target.unlink()
    except OSError as exc:
        return 3, f"Could not remove previous live connector evidence file {target}: {exc}"
    return 0, f"Removed previous live connector evidence at {target}."


def sensitive_evidence_identity_keys(item: dict[str, Any]) -> list[str]:
    raw_keys = item.get("identity_key_paths") or item.get("identity_keys") or []
    if not isinstance(raw_keys, list):
        raw_keys = []
    found = []
    for key in raw_keys:
        normalized = str(key).lower().replace("-", "_")
        if any(term in normalized for term in SENSITIVE_IDENTITY_KEY_TERMS):
            found.append(str(key))
    return sorted(found)


def sensitive_evidence_key_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).lower().replace("-", "_")
            if any(term in normalized for term in SENSITIVE_IDENTITY_KEY_TERMS):
                found.append(path)
            found.extend(sensitive_evidence_key_paths(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(sensitive_evidence_key_paths(nested, f"{prefix}[{index}]"))
    return sorted(found)


def sensitive_evidence_value_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.extend(sensitive_evidence_value_paths(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(sensitive_evidence_value_paths(nested, f"{prefix}[{index}]"))
    elif isinstance(value, str) and any(pattern.search(value) for pattern in SENSITIVE_EVIDENCE_VALUE_PATTERNS):
        found.append(prefix or "<root>")
    return sorted(found)


def validate_side_effect_evidence(report: dict[str, Any]) -> tuple[int, str]:
    side_effect_check = report.get("side_effect_check")
    if not isinstance(side_effect_check, dict):
        return 3, "Live connector evidence is missing side_effect_check details."
    if side_effect_check.get("ok") is not True:
        return 3, "Live connector evidence side_effect_check did not pass."
    collections = side_effect_check.get("collections")
    if not isinstance(collections, dict):
        return 3, "Live connector evidence side_effect_check is missing inspected collections."
    missing = sorted(LIVE_EVIDENCE_SIDE_EFFECT_COLLECTIONS - set(collections))
    if missing:
        return 3, f"Live connector evidence side_effect_check is missing collection(s): {', '.join(missing)}."
    for name in sorted(LIVE_EVIDENCE_SIDE_EFFECT_COLLECTIONS):
        detail = collections.get(name)
        if not isinstance(detail, dict):
            return 3, f"Live connector evidence side_effect_check collection {name} is not an object."
        if detail.get("ok") is not True:
            return 3, f"Live connector evidence side_effect_check collection {name} did not pass."
        for key in ["inspected_before", "inspected_after"]:
            value = detail.get(key)
            if not isinstance(value, int) or value < 0:
                return 3, f"Live connector evidence side_effect_check collection {name} is missing numeric {key}."
    return 0, "Live connector evidence includes complete no-sync side-effect collection details."


def validate_live_evidence_file(path: str, expected_api_url: str | None = None) -> tuple[int, str]:
    target = _evidence_path(path)
    if not target.is_file():
        return 3, f"Live connector evidence file was not written: {target}"
    try:
        report = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return 3, f"Live connector evidence file is not readable JSON: {exc}"
    if not isinstance(report, dict):
        return 3, "Live connector evidence file did not contain a JSON object."
    sensitive_key_paths = sensitive_evidence_key_paths(report)
    if sensitive_key_paths:
        return 3, f"Live connector evidence contains secret-shaped key(s): {', '.join(sensitive_key_paths)}."
    sensitive_value_paths = sensitive_evidence_value_paths(report)
    if sensitive_value_paths:
        return 3, f"Live connector evidence contains secret-shaped value(s): {', '.join(sensitive_value_paths)}."
    if report.get("required_live_provider") is not True:
        return 3, "Live connector evidence was not generated with required_live_provider=true."
    generated_at = str(report.get("generated_at") or "").strip()
    if not generated_at:
        return 3, "Live connector evidence is missing generated_at."
    try:
        generated_at_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return 3, "Live connector evidence generated_at is not a valid ISO timestamp."
    if generated_at_at.tzinfo is None:
        return 3, "Live connector evidence generated_at must include a timezone."
    now = datetime.now(timezone.utc)
    generated_at_utc = generated_at_at.astimezone(timezone.utc)
    if generated_at_utc > now + LIVE_EVIDENCE_FUTURE_SKEW:
        return 3, "Live connector evidence generated_at is too far in the future."
    if now - generated_at_utc > LIVE_EVIDENCE_MAX_AGE:
        hours = int(LIVE_EVIDENCE_MAX_AGE.total_seconds() // 3600)
        return 3, f"Live connector evidence is stale; generated_at must be within the last {hours} hours."
    if expected_api_url:
        reported_api_url = str(report.get("api_url") or "").rstrip("/")
        expected = expected_api_url.rstrip("/")
        if reported_api_url != expected:
            return 3, f"Live connector evidence api_url mismatch: expected {expected}, found {reported_api_url or 'missing'}."
    workspace = report.get("workspace")
    if not isinstance(workspace, dict) or not workspace.get("account_id") or not workspace.get("slug"):
        return 3, "Live connector evidence is missing workspace scope."
    role = str(workspace.get("current_role") or "").strip().lower()
    if role not in OPERATOR_ROLES:
        allowed = ", ".join(sorted(OPERATOR_ROLES))
        return 3, f"Live connector evidence workspace role is {role or 'unknown'}; expected {allowed}."
    if report.get("live_provider_ready") is not True:
        return 3, "Live connector evidence did not set live_provider_ready=true."
    if report.get("no_sync_side_effect_check_passed") is not True:
        return 3, "Live connector evidence did not prove the no-sync side-effect check passed."
    side_effect_code, side_effect_message = validate_side_effect_evidence(report)
    if side_effect_code != 0:
        return side_effect_code, side_effect_message
    external_checks = report.get("external_provider_checks")
    if not isinstance(external_checks, list):
        return 3, "Live connector evidence is missing external_provider_checks."
    sensitive_evidence_keys = [
        key
        for item in external_checks
        if isinstance(item, dict)
        for key in sensitive_evidence_identity_keys(item)
    ]
    if sensitive_evidence_keys:
        return 3, f"Live connector evidence contains sensitive identity metadata keys: {', '.join(sensitive_evidence_keys)}."
    verified_external = [
        item
        for item in external_checks
        if isinstance(item, dict)
        and item.get("ok") is True
        and item.get("kind") == "external_provider"
        and item.get("provider") in LIVE_EXTERNAL_PROVIDERS
        and item.get("status") == "verified"
        and bool(item.get("verified_at"))
        and bool(item.get("identity_keys"))
        and item.get("identity_signal") is True
        and not item.get("sensitive_identity_keys")
    ]
    if not verified_external:
        providers = ", ".join(sorted(LIVE_EXTERNAL_PROVIDERS))
        return 3, f"Live connector evidence contains no verified supported external provider check. Expected one of: {providers}."
    if report.get("external_verified_count", 0) < 1:
        return 3, "Live connector evidence external_verified_count is zero."
    return 0, f"Evidence confirms {len(verified_external)} verified external provider(s) and no sync side effects."


def validate_live_evidence_step(path: str, expected_api_url: str | None = None) -> int:
    print("\n== Live connector evidence ==", flush=True)
    code, message = validate_live_evidence_file(path, expected_api_url)
    if code == 0:
        print(f"PASS Live connector evidence: {message}", flush=True)
    else:
        print(f"FAIL Live connector evidence (exit {code})\n{message}", flush=True)
    return code


def clear_live_evidence_step(path: str) -> int:
    print("\n== Clear live connector evidence ==", flush=True)
    code, message = clear_live_evidence_file(path)
    if code == 0:
        print(f"PASS Clear live connector evidence: {message}", flush=True)
    else:
        print(f"FAIL Clear live connector evidence (exit {code})\n{message}", flush=True)
    return code


def request_json(api_url: str, path: str, token: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{api_url}{path}",
        headers={"Accept": "application/json", "X-Marge-Account-Token": token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"detail": body or exc.reason}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, {"detail": str(exc.reason)}


def error_detail(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        return json.dumps(body, default=str)
    return str(body)


def validate_operator_workspace_token(api_url: str, token: str) -> tuple[bool, str]:
    status, body = request_json(api_url, "/assistant/account", token)
    if status == 0:
        return False, f"Could not validate MARGE_ACCOUNT_TOKEN against {api_url}/assistant/account: {error_detail(body)}"
    if status >= 400:
        return False, f"MARGE_ACCOUNT_TOKEN did not resolve to a workspace ({status}): {error_detail(body)}"
    if not isinstance(body, dict):
        return False, "MARGE_ACCOUNT_TOKEN validation did not return workspace JSON."
    role = str(body.get("current_role") or "").strip().lower()
    if role not in OPERATOR_ROLES:
        allowed = ", ".join(sorted(OPERATOR_ROLES))
        return False, f"MARGE_ACCOUNT_TOKEN resolved to role={role or 'unknown'}; use an {allowed} token for pilot readiness."
    church = body.get("church_name") or body.get("slug") or "workspace"
    return True, f"Token resolves to {role} access for {church}."


def check_operator_workspace_token(env: dict[str, str]) -> int:
    print("\n== Workspace operator token ==", flush=True)
    api_url = (env.get("MARGE_API_URL") or DEFAULT_API_URL).rstrip("/")
    token = (env.get("MARGE_ACCOUNT_TOKEN") or "").strip()
    if token:
        if is_local_api_url(api_url):
            print("PASS Workspace operator token: local token present; live checks will remain workspace-scoped.", flush=True)
            return 0
        valid, message = validate_operator_workspace_token(api_url, token)
        if valid:
            print(f"PASS Workspace operator token: {message}", flush=True)
            return 0
        print(
            f"FAIL Workspace operator token (exit 2)\n"
            f"{message} Skipping remote first-run writes and live connector checks.",
            flush=True,
        )
        return 2
    if is_local_api_url(api_url):
        print(
            "FAIL Workspace operator token (exit 2)\n"
            "MARGE_ACCOUNT_TOKEN is required for pilot readiness even against a local API because live connector "
            "credential checks must be scoped to a real workspace. Skipping first-run writes and live connector checks.",
            flush=True,
        )
        return 2
    print(
        "FAIL Workspace operator token (exit 2)\n"
        "MARGE_ACCOUNT_TOKEN is required for non-local pilot readiness so live connector checks are scoped "
        "to an owner/admin/pastor workspace. Skipping remote first-run writes and live connector checks.",
        flush=True,
    )
    return 2


def next_actions_for(title: str, env_file: str | None, live_evidence_file: str | None = None) -> list[str]:
    env_target = env_file or "the deployment environment"
    production_rerun = (
        f".venv/bin/python scripts/verify_production_config.py --env-file {env_file}"
        if env_file
        else ".venv/bin/python scripts/verify_production_config.py"
    )
    live_evidence_arg = f" --evidence-file {live_evidence_file}" if live_evidence_file else ""
    if title == "Production configuration":
        return [
            f"Fill {env_target} with MARGE_ENV=production and real production values for DATABASE_URL, MARGE_ENCRYPTION_KEY, MARGE_APP_URL, CORS_ORIGINS, SMTP, and secure session settings.",
            "Use the exact public HTTPS /app URL for MARGE_APP_URL and exact HTTPS origins with no path for CORS_ORIGINS.",
            "Set MARGE_REQUIRE_ACCOUNT_TOKEN=true and MARGE_AUTO_CREATE_SCHEMA=false after migrations are applied.",
            f"Rerun `{production_rerun}` until it exits 0.",
        ]
    if title == "Deployment bootstrap":
        return [
            "Confirm MARGE_API_URL points at the running API origin and MARGE_APP_URL points at the exact public HTTPS /app URL.",
            "Check that /health, /assistant/config, and /app are reachable from the operator environment.",
            "Start production with MARGE_REQUIRE_ACCOUNT_TOKEN=true so /assistant/config reports strict workspace-token mode.",
        ]
    if title == "First-run workspace":
        return [
            "Verify /assistant/signup, /assistant/sessions, /assistant/chat, /assistant/desk, and /assistant/actions work against the target deployment.",
            "If this ran against a remote deployment, remove the disposable verification workspace manually if you do not want to keep it.",
            "Fix first-run chat/profile/setup regressions before letting a pilot pastor onboard.",
        ]
    if title == "Migration schema":
        return [
            "Run alembic upgrade head against the target database.",
            "If verify_migrations reports drift, add or fix an Alembic migration instead of relying on startup schema creation.",
        ]
    if title == "Workspace operator token":
        return [
            "Create or open the pilot workspace and exchange the owner/admin/pastor user token for a session token.",
            "Set MARGE_ACCOUNT_TOKEN or pass --token with that owner/admin/pastor session token before running the combined pilot gate.",
            "If a token is present but rejected, verify it with GET /assistant/account and confirm current_role is owner, admin, or pastor.",
            "Run scripts/verify_first_run_workspace.py separately only if you intentionally want a disposable workspace check without live connector verification.",
        ]
    if title == "Live connector credentials":
        return [
            "Create or open a real pastor workspace and connect at least one provider the pilot church uses.",
            "Run the provider's Check credentials flow or POST /assistant/integrations/{provider}/verify before any sync.",
            "If the no-sync side-effect check fails, fix verification so it does not create connected context, assistant actions, members, visitors, care cases, or prayer requests.",
            f"Rerun `MARGE_API_URL=https://... MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION .venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider{live_evidence_arg}` with an owner/admin/pastor session token.",
        ]
    if title == "Live connector evidence":
        return [
            "Inspect the live connector evidence JSON and confirm generated_at is timezone-bearing and fresh, live_provider_ready=true, no_sync_side_effect_check_passed=true, side_effect_check.collections contains every required passing collection, and at least one external_provider_checks item is verified.",
            "Do not treat the legacy verified array or local_bridge_checks as pilot readiness proof.",
            f"Rerun `MARGE_API_URL=https://... MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION .venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider{live_evidence_arg}` with an owner/admin/pastor session token.",
        ]
    if title == "Clear live connector evidence":
        return [
            "Remove or fix the live connector evidence path so the combined gate can require a fresh report from this run.",
            "Do not reuse stale live connector evidence from another workspace, role, or deployment.",
        ]
    return [f"Review the {title} output above and rerun the readiness gate after fixing it."]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Marge production/pilot readiness gates.")
    parser.add_argument("--env-file", help="Optional production candidate env file to load for all readiness checks.")
    parser.add_argument("--api-url", help=f"Marge API URL. Defaults to MARGE_API_URL from env or env file, then {DEFAULT_API_URL}.")
    parser.add_argument("--token", help="Workspace operator session token. Defaults to MARGE_ACCOUNT_TOKEN from env or env file.")
    parser.add_argument(
        "--live-evidence-file",
        default="artifacts/live-connector-verification.json",
        help="JSON evidence file passed to verify_live_integrations.py. Defaults to artifacts/live-connector-verification.json.",
    )
    parser.add_argument("--skip-migrations", action="store_true", help="Skip local Alembic migration drift verification.")
    first_run_group = parser.add_mutually_exclusive_group()
    first_run_group.add_argument(
        "--include-first-run-workspace",
        action="store_true",
        help="Deprecated no-op; first-run workspace verification is included by default.",
    )
    first_run_group.add_argument(
        "--skip-first-run-workspace",
        action="store_true",
        help="Skip the disposable first-run pastor journey. Use only for targeted troubleshooting because pilot readiness normally requires it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = None
    if args.env_file:
        candidate = Path(args.env_file).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not candidate.is_file():
            print(f"Missing env file: {candidate}", file=sys.stderr)
            return 1
        env_file = str(candidate)

    env = build_env(env_file, args.api_url, args.token)

    steps: list[tuple[str, list[str]]] = []
    production_command = [sys.executable, "scripts/verify_production_config.py"]
    if env_file:
        production_command.extend(["--env-file", env_file])
    steps.append(("Production configuration", production_command))

    steps.append(("Deployment bootstrap", [sys.executable, "scripts/verify_deployment_bootstrap.py"]))

    if not args.skip_migrations:
        steps.append(("Migration schema", [sys.executable, "scripts/verify_migrations.py"]))

    failed_steps: list[tuple[str, int]] = []
    for title, command in steps:
        code = run_step(title, command, env)
        if code != 0:
            failed_steps.append((title, code))

    operator_token_code = check_operator_workspace_token(env)
    operator_token_ok = operator_token_code == 0
    if not operator_token_ok:
        failed_steps.append(("Workspace operator token", operator_token_code))

    gated_steps: list[tuple[str, list[str]]] = []
    if operator_token_ok and not args.skip_first_run_workspace:
        gated_steps.append(("First-run workspace", [sys.executable, "scripts/verify_first_run_workspace.py", "--allow-remote-write"]))

    if operator_token_ok:
        live_command = [
            sys.executable,
            "scripts/verify_live_integrations.py",
            "--include-mcp",
            "--require-live-provider",
        ]
        if args.live_evidence_file:
            live_command.extend(["--evidence-file", args.live_evidence_file])
        gated_steps.append(("Live connector credentials", live_command))

    for title, command in gated_steps:
        if title == "Live connector credentials" and args.live_evidence_file:
            clear_code = clear_live_evidence_step(args.live_evidence_file)
            if clear_code != 0:
                failed_steps.append(("Clear live connector evidence", clear_code))
                continue
        code = run_step(title, command, env)
        if code != 0:
            failed_steps.append((title, code))
        elif title == "Live connector credentials" and args.live_evidence_file:
            evidence_code = validate_live_evidence_step(args.live_evidence_file, env.get("MARGE_API_URL") or DEFAULT_API_URL)
            if evidence_code != 0:
                failed_steps.append(("Live connector evidence", evidence_code))

    print("\n== Summary ==", flush=True)
    if failed_steps:
        print(f"Pilot readiness failed: {len(failed_steps)} gate(s) need attention.")
        print("\nNext actions:", flush=True)
        seen: set[str] = set()
        for title, code in failed_steps:
            print(f"- {title} failed with exit {code}.", flush=True)
            for action in next_actions_for(title, env_file, args.live_evidence_file):
                if action in seen:
                    continue
                seen.add(action)
                print(f"  {action}", flush=True)
        return 1
    print("Pilot readiness passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
