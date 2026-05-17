#!/usr/bin/env python3
"""
Run the production/pilot readiness gates for Marge.

This is the one command to use before a real pastor relies on Marge with live
ministry data. It combines:

- production environment safety checks,
- public deployment bootstrap checks,
- optional first-run workspace journey verification,
- migration/schema drift verification,
- no-sync live connector verification requiring at least one external provider.

Usage:
  MARGE_API_URL=https://marge.yourchurch.org \
  MARGE_ACCOUNT_TOKEN=marge_sess_... \
  .venv/bin/python scripts/verify_pilot_readiness.py

  .venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "http://127.0.0.1:8000"


def build_env(env_file: str | None, api_url: str | None, token: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_file:
        values = dotenv_values(env_file)
        env.update({key: value for key, value in values.items() if value is not None})
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


def next_actions_for(title: str, env_file: str | None) -> list[str]:
    env_target = env_file or "the deployment environment"
    if title == "Production configuration":
        return [
            f"Fill {env_target} with MARGE_ENV=production and real production values for DATABASE_URL, MARGE_ENCRYPTION_KEY, MARGE_APP_URL, CORS_ORIGINS, SMTP, and secure session settings.",
            "Use the exact public HTTPS /app URL for MARGE_APP_URL and exact HTTPS origins with no path for CORS_ORIGINS.",
            "Set MARGE_REQUIRE_ACCOUNT_TOKEN=true and MARGE_AUTO_CREATE_SCHEMA=false after migrations are applied.",
            "Rerun scripts/verify_production_config.py against the same env file/environment until it exits 0.",
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
    if title == "Live connector credentials":
        return [
            "Create or open a real pastor workspace and connect at least one provider the pilot church uses.",
            "Run the provider's Check credentials flow or POST /assistant/integrations/{provider}/verify before any sync.",
            "If the no-sync side-effect check fails, fix verification so it does not create connected context or assistant actions.",
            "Rerun scripts/verify_live_integrations.py --include-mcp --require-live-provider with an owner/admin/pastor session token.",
        ]
    return [f"Review the {title} output above and rerun the readiness gate after fixing it."]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Marge production/pilot readiness gates.")
    parser.add_argument("--env-file", help="Optional production candidate env file to load for all readiness checks.")
    parser.add_argument("--api-url", help=f"Marge API URL. Defaults to MARGE_API_URL from env or env file, then {DEFAULT_API_URL}.")
    parser.add_argument("--token", help="Workspace operator session token. Defaults to MARGE_ACCOUNT_TOKEN from env or env file.")
    parser.add_argument("--skip-migrations", action="store_true", help="Skip local Alembic migration drift verification.")
    parser.add_argument(
        "--include-first-run-workspace",
        action="store_true",
        help="Create a disposable workspace and verify the first-run pastor journey. This writes to the target deployment.",
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

    if args.include_first_run_workspace:
        steps.append(("First-run workspace", [sys.executable, "scripts/verify_first_run_workspace.py", "--allow-remote-write"]))

    if not args.skip_migrations:
        steps.append(("Migration schema", [sys.executable, "scripts/verify_migrations.py"]))

    steps.append((
        "Live connector credentials",
        [
            sys.executable,
            "scripts/verify_live_integrations.py",
            "--include-mcp",
            "--require-live-provider",
        ],
    ))

    failed_steps: list[tuple[str, int]] = []
    for title, command in steps:
        code = run_step(title, command, env)
        if code != 0:
            failed_steps.append((title, code))

    print("\n== Summary ==", flush=True)
    if failed_steps:
        print(f"Pilot readiness failed: {len(failed_steps)} gate(s) need attention.")
        print("\nNext actions:", flush=True)
        seen: set[str] = set()
        for title, code in failed_steps:
            print(f"- {title} failed with exit {code}.", flush=True)
            for action in next_actions_for(title, env_file):
                if action in seen:
                    continue
                seen.add(action)
                print(f"  {action}", flush=True)
        return 1
    print("Pilot readiness passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
