#!/usr/bin/env python3
"""
Smoke-test the pilot readiness gate planner without network or provider calls.

This keeps the combined readiness command from regressing into remote writes or
live connector checks before an operator workspace token is present.

Usage:
  .venv/bin/python scripts/smoke_pilot_readiness_gate.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import runtime_config
from scripts import verify_pilot_readiness as gate
from scripts import verify_production_config as production_config


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_gate_with(
    argv: list[str],
    validate_result: tuple[bool, str] = (True, "Token resolves to owner access for Smoke Church."),
    step_results: dict[str, int] | None = None,
    evidence_result: tuple[int, str] = (0, "Evidence confirms one verified external provider and no sync side effects."),
) -> tuple[int, list[str], str]:
    original_argv = sys.argv[:]
    original_run_step = gate.run_step
    original_validate = gate.validate_operator_workspace_token
    original_validate_evidence = gate.validate_live_evidence_step
    original_clear_evidence = gate.clear_live_evidence_step
    step_titles: list[str] = []
    step_commands: list[tuple[str, list[str]]] = []

    def fake_run_step(title: str, command: list[str], env: dict[str, str]) -> int:
        step_titles.append(title)
        step_commands.append((title, list(command)))
        return (step_results or {}).get(title, 0)

    def fake_validate_operator_workspace_token(api_url: str, token: str) -> tuple[bool, str]:
        return validate_result

    def fake_validate_live_evidence_step(path: str, expected_api_url: str | None = None) -> int:
        step_titles.append("Live connector evidence")
        step_commands.append(("Live connector evidence", [path, expected_api_url or ""]))
        code, message = evidence_result
        if code == 0:
            print(f"PASS Live connector evidence: {message}")
        else:
            print(f"FAIL Live connector evidence (exit {code})\n{message}")
        return code

    def fake_clear_live_evidence_step(path: str) -> int:
        step_titles.append("Clear live connector evidence")
        step_commands.append(("Clear live connector evidence", [path]))
        print(f"PASS Clear live connector evidence: Removed previous live connector evidence at {path}.")
        return 0

    output = io.StringIO()
    try:
        sys.argv = ["verify_pilot_readiness.py", *argv]
        gate.run_step = fake_run_step
        gate.validate_operator_workspace_token = fake_validate_operator_workspace_token
        gate.validate_live_evidence_step = fake_validate_live_evidence_step
        gate.clear_live_evidence_step = fake_clear_live_evidence_step
        with contextlib.redirect_stdout(output):
            code = gate.main()
    finally:
        gate.clear_live_evidence_step = original_clear_evidence
        gate.validate_live_evidence_step = original_validate_evidence
        gate.validate_operator_workspace_token = original_validate
        gate.run_step = original_run_step
        sys.argv = original_argv
    run_gate_with.last_commands = step_commands  # type: ignore[attr-defined]
    return code, step_titles, output.getvalue()


def check_token_with(
    env: dict[str, str],
    validate_result: tuple[bool, str] = (True, "Token resolves to owner access for Smoke Church."),
) -> tuple[int, str]:
    original_validate = gate.validate_operator_workspace_token

    def fake_validate_operator_workspace_token(api_url: str, token: str) -> tuple[bool, str]:
        return validate_result

    output = io.StringIO()
    try:
        gate.validate_operator_workspace_token = fake_validate_operator_workspace_token
        with contextlib.redirect_stdout(output):
            code = gate.check_operator_workspace_token(env)
        return code, output.getvalue()
    finally:
        gate.validate_operator_workspace_token = original_validate


def validate_token_with_response(status: int, body: object) -> tuple[bool, str]:
    original_request = gate.request_json

    def fake_request_json(api_url: str, path: str, token: str) -> tuple[int, object]:
        return status, body

    try:
        gate.request_json = fake_request_json
        return gate.validate_operator_workspace_token("https://marge.example.com", "marge_sess_test")
    finally:
        gate.request_json = original_request


def build_env_with(
    env_file_text: str,
    environ: dict[str, str],
    api_url: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    original_environ = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "candidate.env"
            env_file.write_text(env_file_text, encoding="utf-8")
            return gate.build_env(str(env_file), api_url, token)
    finally:
        os.environ.clear()
        os.environ.update(original_environ)


def complete_production_env_values(**overrides: str) -> dict[str, str]:
    values = {
        "MARGE_ENV": "production",
        "DATABASE_URL": "postgresql://marge_user:strong-secret@db.marge-prod.test:5432/marge",
        "MARGE_AUTO_CREATE_SCHEMA": "false",
        "MARGE_REQUIRE_ACCOUNT_TOKEN": "true",
        "MARGE_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "MARGE_SESSION_COOKIE_SECURE": "true",
        "MARGE_SESSION_COOKIE_SAMESITE": "lax",
        "MARGE_APP_URL": "https://marge-prod.test/app",
        "CORS_ORIGINS": "https://marge-prod.test",
        "MARGE_INVITE_EMAIL_FROM": "Marge <no-reply@marge-prod.test>",
        "SMTP_HOST": "smtp.marge-prod.test",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "smtp-user",
        "SMTP_PASSWORD": "smtp-password",
        "SMTP_STARTTLS": "true",
    }
    values.update(overrides)
    return values


def env_file_text(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in values.items())


def runtime_errors_with(values: dict[str, str]) -> list[str]:
    original_environ = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(values)
        return runtime_config.production_safety_errors()
    finally:
        os.environ.clear()
        os.environ.update(original_environ)


def production_template_keys() -> set[str]:
    keys: set[str] = set()
    for raw_line in (ROOT / ".env.production.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def local_template_text() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def main() -> None:
    local_template = local_template_text()
    assert_true("ROCK_API_KEY=" in local_template, ".env.example should document the generic ROCK_API_KEY setting.")
    assert_true(
        "ROCK_HALLMARK_API_KEY" not in local_template,
        ".env.example should not reintroduce the legacy Hallmark-specific Rock key.",
    )
    assert_true(
        "This file alone never proves a pastor's live tools are connected" in local_template,
        ".env.example should warn that env placeholders are not live-provider proof.",
    )
    assert_true(
        "run Check credentials before the first sync" in local_template,
        ".env.example should keep the no-sync credential-check boundary visible.",
    )
    assert_true(
        "MCP/local API access is useful for LLM clients, but it is not a live church-tool provider" in local_template,
        ".env.example should state that MCP/local access is not a live church-tool provider.",
    )

    template_keys = production_template_keys()
    template_exceptions = {
        "MARGE_ENFORCE_PRODUCTION_CONFIG",  # MARGE_ENV=production is the preferred production flag.
        "ROCK_HALLMARK_API_KEY",  # Legacy local fallback; production template should not encourage it.
    }
    missing_template_keys = sorted((production_config.CHECKED_ENV_NAMES - template_exceptions) - template_keys)
    assert_true(
        not missing_template_keys,
        f".env.production.example is missing production verifier keys: {', '.join(missing_template_keys)}",
    )
    assert_true(
        "ROCK_HALLMARK_API_KEY" not in template_keys,
        ".env.production.example should not include the legacy ROCK_HALLMARK_API_KEY fallback.",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / "complete-production.env"
        env_file.write_text(env_file_text(complete_production_env_values()), encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "MARGE_ENV": "development",
            "DATABASE_URL": "sqlite:///ambient.db",
            "MARGE_AUTO_CREATE_SCHEMA": "true",
            "MARGE_REQUIRE_ACCOUNT_TOKEN": "false",
            "MARGE_SESSION_COOKIE_SECURE": "false",
            "MARGE_APP_URL": "http://localhost:8000/app",
            "CORS_ORIGINS": "*",
            "SMTP_HOST": "",
            "MARGE_INVITE_EMAIL_FROM": "",
        })
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 0, "A complete candidate production env file should pass production config verification.")
        assert_true("FAIL " not in result.stdout, "A complete candidate production env file should not emit failing checks.")
        assert_true(
            '"failures": 0' in result.stdout,
            "A complete candidate production env file should report zero production config failures.",
        )
        assert_true(
            "Before pilot use, connect or configure at least one real external provider" in result.stdout,
            "Production config verification should keep live-provider setup visible even when env guardrails pass.",
        )
        assert_true(
            not runtime_errors_with(complete_production_env_values()),
            "The runtime production guard should accept a complete production env.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        insecure_values = complete_production_env_values(
            BREEZE_API_KEY="breeze-secret",
            BREEZE_BASE_URL="https://127.0.0.1",
            ROCK_API_KEY="rock-secret",
            ROCK_BASE_URL="https://user:pass@rock.marge-prod.test/api/v2?api_key=leak",
        )
        env_file = Path(tmpdir) / "insecure-connector-base.env"
        env_file.write_text(env_file_text(insecure_values), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 1, "Production config verification should reject unsafe server-side connector base URLs.")
        assert_true(
            "FAIL BREEZE_BASE_URL: BREEZE_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment." in result.stdout,
            "Breeze server-side base URL should require a public HTTPS base URL.",
        )
        assert_true(
            "FAIL ROCK_BASE_URL: ROCK_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment." in result.stdout,
            "Rock server-side base URL should reject username, password, query, and fragment URL parts.",
        )
        runtime_errors = runtime_errors_with(insecure_values)
        assert_true(
            "BREEZE_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment in production." in runtime_errors,
            "Runtime guard should reject localhost/private Breeze base URLs.",
        )
        assert_true(
            "ROCK_BASE_URL must be a public HTTPS base URL without username, password, query, or fragment in production." in runtime_errors,
            "Runtime guard should reject Rock base URLs that smuggle URL credentials or query strings.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        mismatched_origin_values = complete_production_env_values(
            MARGE_APP_URL="https://app.marge-prod.test/app",
            CORS_ORIGINS="https://api.marge-prod.test",
        )
        env_file = Path(tmpdir) / "mismatched-app-cors.env"
        env_file.write_text(env_file_text(mismatched_origin_values), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 1, "Production config verification should reject app/CORS origin mismatches.")
        assert_true(
            "FAIL CORS_ORIGINS: CORS_ORIGINS must include the MARGE_APP_URL origin so the first-run app can call the API." in result.stdout,
            "Production config verification should explain the app/CORS origin mismatch.",
        )
        assert_true(
            "CORS_ORIGINS must include the MARGE_APP_URL origin so the first-run app can call the API." in runtime_errors_with(mismatched_origin_values),
            "Runtime guard should reject app/CORS origin mismatches.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        valid_oauth_values = complete_production_env_values(
            GOOGLE_CLIENT_ID="google-client",
            GOOGLE_CLIENT_SECRET="google-secret",
            GOOGLE_REDIRECT_URI="https://marge-prod.test/assistant/integrations/google_workspace/callback",
        )
        env_file = Path(tmpdir) / "valid-oauth.env"
        env_file.write_text(env_file_text(valid_oauth_values), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 0, "Production config verification should accept a same-origin OAuth callback.")
        assert_true(
            not runtime_errors_with(valid_oauth_values),
            "Runtime guard should accept a same-origin OAuth callback.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        mismatched_oauth_values = complete_production_env_values(
            GOOGLE_CLIENT_ID="google-client",
            GOOGLE_CLIENT_SECRET="google-secret",
            GOOGLE_REDIRECT_URI="https://oauth.marge-prod.test/assistant/integrations/google_workspace/callback",
            MICROSOFT_CLIENT_ID="microsoft-client",
            MICROSOFT_CLIENT_SECRET="microsoft-secret",
            MICROSOFT_REDIRECT_URI="https://marge-prod.test/oauth/microsoft_365/callback",
        )
        env_file = Path(tmpdir) / "mismatched-oauth.env"
        env_file.write_text(env_file_text(mismatched_oauth_values), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 1, "Production config verification should reject OAuth callback origin/path mismatches.")
        assert_true(
            "FAIL GOOGLE_REDIRECT_URI: GOOGLE_REDIRECT_URI must use the same origin as MARGE_APP_URL." in result.stdout,
            "Production config verification should explain OAuth origin mismatches.",
        )
        assert_true(
            "FAIL MICROSOFT_REDIRECT_URI: MICROSOFT_REDIRECT_URI must use the /assistant/integrations/microsoft_365/callback callback path." in result.stdout,
            "Production config verification should explain OAuth callback path mismatches.",
        )
        runtime_errors = runtime_errors_with(mismatched_oauth_values)
        assert_true(
            "GOOGLE_REDIRECT_URI must use the same origin as MARGE_APP_URL." in runtime_errors,
            "Runtime guard should reject OAuth origin mismatches.",
        )
        assert_true(
            "MICROSOFT_REDIRECT_URI must use the /assistant/integrations/microsoft_365/callback callback path." in runtime_errors,
            "Runtime guard should reject OAuth callback path mismatches.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / "missing-database.env"
        env_file.write_text(
            "\n".join([
                "MARGE_ENV=production",
                "MARGE_AUTO_CREATE_SCHEMA=false",
                "MARGE_REQUIRE_ACCOUNT_TOKEN=true",
                "MARGE_SESSION_COOKIE_SECURE=true",
                "MARGE_APP_URL=https://marge.test/app",
                "CORS_ORIGINS=https://marge.test",
                "MARGE_SESSION_COOKIE_SAMESITE=lax",
            ]),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql://ambient.example.test/marge"
        result = subprocess.run(
            [sys.executable, "scripts/verify_production_config.py", "--env-file", str(env_file)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(result.returncode == 1, "A candidate env file missing DATABASE_URL should fail production config verification.")
        assert_true(
            "FAIL DATABASE_URL: DATABASE_URL must be set." in result.stdout,
            "Production config verification should not let ambient DATABASE_URL mask a missing env-file value.",
        )
        assert_true(
            "Provision a managed production database" in result.stdout
            and "Rerun `.venv/bin/python scripts/verify_production_config.py --env-file" in result.stdout,
            "Production config verification should print actionable next steps for missing deployment config.",
        )

    preserved_env = build_env_with(
        "MARGE_API_URL=\nMARGE_ACCOUNT_TOKEN=\n",
        {
            "MARGE_API_URL": "https://shell.example.com",
            "MARGE_ACCOUNT_TOKEN": "marge_sess_shell",
        },
    )
    assert_true(
        preserved_env["MARGE_API_URL"] == "https://shell.example.com",
        "A blank env-file MARGE_API_URL should not erase the shell API URL.",
    )
    assert_true(
        preserved_env["MARGE_ACCOUNT_TOKEN"] == "marge_sess_shell",
        "A blank env-file MARGE_ACCOUNT_TOKEN should not erase the shell operator token.",
    )
    file_env = build_env_with(
        "MARGE_API_URL=https://file.example.com\nMARGE_ACCOUNT_TOKEN=marge_sess_file\n",
        {
            "MARGE_API_URL": "https://shell.example.com",
            "MARGE_ACCOUNT_TOKEN": "marge_sess_shell",
        },
    )
    assert_true(file_env["MARGE_API_URL"] == "https://file.example.com", "A non-blank env-file API URL should be used.")
    assert_true(file_env["MARGE_ACCOUNT_TOKEN"] == "marge_sess_file", "A non-blank env-file token should be used.")
    cli_env = build_env_with(
        "MARGE_API_URL=https://file.example.com\nMARGE_ACCOUNT_TOKEN=marge_sess_file\n",
        {},
        api_url="https://cli.example.com",
        token="marge_sess_cli",
    )
    assert_true(cli_env["MARGE_API_URL"] == "https://cli.example.com", "The --api-url flag should override env-file values.")
    assert_true(cli_env["MARGE_ACCOUNT_TOKEN"] == "marge_sess_cli", "The --token flag should override env-file values.")

    remote_missing_code, remote_missing_output = check_token_with({"MARGE_API_URL": "https://marge.example.com"})
    assert_true(
        remote_missing_code == 2,
        "Non-local pilot readiness should require an operator workspace token.",
    )
    assert_true("MARGE_ACCOUNT_TOKEN is required" in remote_missing_output, "Token preflight should explain the missing token.")
    local_missing_code, local_missing_output = check_token_with({"MARGE_API_URL": "http://127.0.0.1:8000"})
    assert_true(
        local_missing_code == 2,
        "Local pilot readiness should still require a workspace token for live connector checks.",
    )
    assert_true(
        "live connector credential checks must be scoped to a real workspace" in local_missing_output,
        "Local missing-token preflight should explain the workspace-scoped connector boundary.",
    )
    remote_with_code, _remote_with_output = check_token_with({
        "MARGE_API_URL": "https://marge.example.com",
        "MARGE_ACCOUNT_TOKEN": "marge_sess_test",
    })
    assert_true(
        remote_with_code == 0,
        "Non-local pilot readiness should pass the operator-token preflight when a token is present.",
    )
    invalid_code, invalid_output = check_token_with(
        {"MARGE_API_URL": "https://marge.example.com", "MARGE_ACCOUNT_TOKEN": "bad-token"},
        (False, "MARGE_ACCOUNT_TOKEN did not resolve to a workspace (401): invalid token"),
    )
    assert_true(invalid_code == 2, "Non-local pilot readiness should reject invalid operator tokens.")
    assert_true("invalid token" in invalid_output, "Invalid-token preflight should expose the safe backend reason.")
    staff_code, staff_output = check_token_with(
        {"MARGE_API_URL": "https://marge.example.com", "MARGE_ACCOUNT_TOKEN": "marge_sess_staff"},
        (False, "MARGE_ACCOUNT_TOKEN resolved to role=staff; use an admin, owner, pastor token for pilot readiness."),
    )
    assert_true(staff_code == 2, "Non-local pilot readiness should reject staff/viewer tokens.")
    assert_true("role=staff" in staff_output, "Underprivileged-token preflight should name the resolved role.")

    owner_valid, owner_message = validate_token_with_response(200, {"current_role": "owner", "church_name": "Smoke Church"})
    assert_true(owner_valid and "owner access" in owner_message, "Owner tokens should validate for pilot readiness.")
    admin_valid, admin_message = validate_token_with_response(200, {"current_role": "admin", "church_name": "Smoke Church"})
    assert_true(admin_valid and "admin access" in admin_message, "Admin tokens should validate for pilot readiness.")
    pastor_valid, pastor_message = validate_token_with_response(200, {"current_role": "pastor", "church_name": "Smoke Church"})
    assert_true(pastor_valid and "pastor access" in pastor_message, "Pastor tokens should validate for pilot readiness.")
    staff_valid, staff_message = validate_token_with_response(200, {"current_role": "staff", "church_name": "Smoke Church"})
    assert_true(not staff_valid and "role=staff" in staff_message, "Staff tokens should not validate for pilot readiness.")
    missing_valid, missing_message = validate_token_with_response(401, {"detail": "No valid Marge account token was provided."})
    assert_true(not missing_valid and "401" in missing_message, "Invalid tokens should not validate for pilot readiness.")

    missing_code, missing_steps, missing_output = run_gate_with([
        "--api-url",
        "https://marge.example.com",
        "--skip-migrations",
    ])
    assert_true(missing_code == 1, "Missing non-local operator token should make the combined gate fail.")
    assert_true(
        missing_steps == ["Production configuration", "Deployment bootstrap"],
        "Missing non-local operator token should skip remote first-run writes and live connector checks.",
    )
    assert_true("Workspace operator token" in missing_output, "Combined gate should print the operator-token preflight.")
    assert_true("First-run workspace" not in missing_steps, "Missing non-local token should skip the first-run write step.")
    assert_true("Live connector credentials" not in missing_steps, "Missing non-local token should skip live connector checks.")

    production_failure_code, production_failure_steps, production_failure_output = run_gate_with(
        [
            "--api-url",
            "https://marge.example.com",
            "--token",
            "marge_sess_test",
            "--skip-migrations",
        ],
        step_results={"Production configuration": 1},
    )
    assert_true(production_failure_code == 1, "A production config failure should fail the combined pilot gate.")
    assert_true(
        production_failure_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "First-run workspace",
            "Clear live connector evidence",
            "Live connector credentials",
            "Live connector evidence",
        ],
        "Production config failures should still let later fake readiness steps run for a complete summary.",
    )
    assert_true(
        "Production configuration failed with exit 1." in production_failure_output,
        "Combined gate should summarize production configuration failures.",
    )
    assert_true(
        ".venv/bin/python scripts/verify_production_config.py" in production_failure_output,
        "Production config failures should print the exact rerun command.",
    )

    token_code, token_steps, _token_output = run_gate_with([
        "--api-url",
        "https://marge.example.com",
        "--token",
        "marge_sess_test",
        "--skip-migrations",
    ])
    assert_true(token_code == 0, "Fake successful readiness steps should pass when the non-local operator token is present.")
    assert_true(
        token_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "First-run workspace",
            "Clear live connector evidence",
            "Live connector credentials",
            "Live connector evidence",
        ],
        "Non-local pilot gate with an operator token should include first-run, live connector, and evidence checks.",
    )
    live_command = next(command for title, command in run_gate_with.last_commands if title == "Live connector credentials")  # type: ignore[attr-defined]
    assert_true(
        "--evidence-file" in live_command
        and "artifacts/live-connector-verification.json" in live_command,
        "Combined pilot gate should pass the default live connector evidence file to the live verifier.",
    )

    skipped_code, skipped_steps, _skipped_output = run_gate_with([
        "--api-url",
        "https://marge.example.com",
        "--token",
        "marge_sess_test",
        "--skip-migrations",
        "--skip-first-run-workspace",
    ])
    assert_true(skipped_code == 0, "Fake successful no-write readiness run should pass with an operator token.")
    assert_true(
        skipped_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "Clear live connector evidence",
            "Live connector credentials",
            "Live connector evidence",
        ],
        "Skipping the first-run workspace should still keep live connector verification and evidence validation in the gate.",
    )
    custom_evidence_code, custom_evidence_steps, _custom_evidence_output = run_gate_with([
        "--api-url",
        "https://marge.example.com",
        "--token",
        "marge_sess_test",
        "--skip-migrations",
        "--skip-first-run-workspace",
        "--live-evidence-file",
        "artifacts/custom-live-evidence.json",
    ])
    assert_true(custom_evidence_code == 0, "Custom live evidence file should not change successful gate planning.")
    assert_true(
        custom_evidence_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "Clear live connector evidence",
            "Live connector credentials",
            "Live connector evidence",
        ],
        "Custom evidence path should still run live connector verification and evidence validation.",
    )
    custom_live_command = next(command for title, command in run_gate_with.last_commands if title == "Live connector credentials")  # type: ignore[attr-defined]
    assert_true(
        "--evidence-file" in custom_live_command
        and "artifacts/custom-live-evidence.json" in custom_live_command,
        "Combined pilot gate should pass custom live connector evidence paths to the live verifier.",
    )
    custom_evidence_command = next(command for title, command in run_gate_with.last_commands if title == "Live connector evidence")  # type: ignore[attr-defined]
    assert_true(
        custom_evidence_command == ["artifacts/custom-live-evidence.json", "https://marge.example.com"],
        "Combined pilot gate should validate the same custom evidence path and API URL it passes to the live verifier.",
    )
    custom_clear_command = next(command for title, command in run_gate_with.last_commands if title == "Clear live connector evidence")  # type: ignore[attr-defined]
    assert_true(
        custom_clear_command == ["artifacts/custom-live-evidence.json"],
        "Combined pilot gate should clear the same custom evidence path before live verification.",
    )

    live_failure_code, live_failure_steps, live_failure_output = run_gate_with(
        [
            "--api-url",
            "https://marge.example.com",
            "--token",
            "marge_sess_test",
            "--skip-migrations",
        ],
        step_results={"Live connector credentials": 2},
    )
    assert_true(live_failure_code == 1, "A live connector verification failure should fail the combined pilot gate.")
    assert_true(
        live_failure_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "First-run workspace",
            "Clear live connector evidence",
            "Live connector credentials",
        ],
        "Live connector failures should occur after production, bootstrap, and first-run checks.",
    )
    assert_true(
        "Live connector credentials failed with exit 2." in live_failure_output,
        "Combined gate should summarize live connector verification failures.",
    )
    assert_true(
        ".venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider --evidence-file artifacts/live-connector-verification.json" in live_failure_output,
        "Live connector failures should print the exact rerun command with the evidence file.",
    )

    evidence_failure_code, evidence_failure_steps, evidence_failure_output = run_gate_with(
        [
            "--api-url",
            "https://marge.example.com",
            "--token",
            "marge_sess_test",
            "--skip-migrations",
            "--skip-first-run-workspace",
        ],
        evidence_result=(3, "Live connector evidence did not set live_provider_ready=true."),
    )
    assert_true(evidence_failure_code == 1, "Invalid live connector evidence should fail the combined pilot gate.")
    assert_true(
        evidence_failure_steps == [
            "Production configuration",
            "Deployment bootstrap",
            "Clear live connector evidence",
            "Live connector credentials",
            "Live connector evidence",
        ],
        "Evidence validation should run immediately after a successful live connector verification.",
    )
    assert_true(
        "Live connector evidence failed with exit 3." in evidence_failure_output
        and "legacy verified array" in evidence_failure_output,
        "Evidence validation failures should summarize the evidence problem and warn against proxy readiness fields.",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        evidence_path = Path(tmpdir) / "live-evidence.json"
        current_generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        valid_side_effect_check = {
            "ok": True,
            "collections": {
                name: {
                    "ok": True,
                    "inspected_before": 0,
                    "inspected_after": 0,
                    "added_ids": [],
                    "removed_ids": [],
                    "changed_ids": [],
                }
                for name in gate.LIVE_EVIDENCE_SIDE_EFFECT_COLLECTIONS
            },
        }
        valid_evidence = {
            "api_url": "https://marge.example.com",
            "required_live_provider": True,
            "generated_at": current_generated_at,
            "workspace": {
                "account_id": 42,
                "slug": "smoke-church",
                "church_name": "Smoke Church",
                "current_role": "owner",
            },
            "live_provider_ready": True,
            "no_sync_side_effect_check_passed": True,
            "side_effect_check": valid_side_effect_check,
            "external_provider_checks": [{
                "provider": "planning_center",
                "kind": "external_provider",
                "ok": True,
                "status": "verified",
                "verified_at": "2026-05-17T00:00:00",
                "identity_keys": ["id"],
                "identity_signal": True,
                "sensitive_identity_keys": [],
            }],
            "external_verified_count": 1,
        }
        evidence_path.write_text(json.dumps(valid_evidence), encoding="utf-8")
        code, _message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(code == 0, "Evidence validation should accept matching API URL evidence with a verified external provider.")
        missing_required_live = dict(valid_evidence)
        missing_required_live.pop("required_live_provider")
        evidence_path.write_text(json.dumps(missing_required_live), encoding="utf-8")
        required_live_code, required_live_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            required_live_code == 3 and "required_live_provider" in required_live_message,
            "Evidence validation should reject reports not generated in strict live-provider mode.",
        )
        missing_generated_at = dict(valid_evidence)
        missing_generated_at.pop("generated_at")
        evidence_path.write_text(json.dumps(missing_generated_at), encoding="utf-8")
        generated_at_code, generated_at_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            generated_at_code == 3 and "generated_at" in generated_at_message,
            "Evidence validation should reject reports that do not say when they were generated.",
        )
        invalid_generated_at = dict(valid_evidence, generated_at="not-a-date")
        evidence_path.write_text(json.dumps(invalid_generated_at), encoding="utf-8")
        invalid_generated_at_code, invalid_generated_at_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            invalid_generated_at_code == 3 and "ISO timestamp" in invalid_generated_at_message,
            "Evidence validation should reject reports with malformed generated_at timestamps.",
        )
        missing_timezone = dict(valid_evidence, generated_at=datetime.now().replace(microsecond=0).isoformat())
        evidence_path.write_text(json.dumps(missing_timezone), encoding="utf-8")
        timezone_code, timezone_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            timezone_code == 3 and "timezone" in timezone_message,
            "Evidence validation should reject generated_at timestamps without a timezone.",
        )
        stale_generated_at = dict(
            valid_evidence,
            generated_at=(datetime.now(timezone.utc) - gate.LIVE_EVIDENCE_MAX_AGE - timedelta(minutes=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        evidence_path.write_text(json.dumps(stale_generated_at), encoding="utf-8")
        stale_code, stale_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            stale_code == 3 and "stale" in stale_message,
            "Evidence validation should reject live connector evidence outside the freshness window.",
        )
        future_generated_at = dict(
            valid_evidence,
            generated_at=(datetime.now(timezone.utc) + gate.LIVE_EVIDENCE_FUTURE_SKEW + timedelta(minutes=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        evidence_path.write_text(json.dumps(future_generated_at), encoding="utf-8")
        future_code, future_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            future_code == 3 and "future" in future_message,
            "Evidence validation should reject live connector evidence dated too far in the future.",
        )
        evidence_path.write_text(json.dumps(valid_evidence), encoding="utf-8")
        mismatch_code, mismatch_message = gate.validate_live_evidence_file(str(evidence_path), "https://other.example.com")
        assert_true(
            mismatch_code == 3 and "api_url mismatch" in mismatch_message,
            "Evidence validation should reject stale evidence generated for a different API URL.",
        )
        missing_workspace = dict(valid_evidence)
        missing_workspace.pop("workspace")
        evidence_path.write_text(json.dumps(missing_workspace), encoding="utf-8")
        workspace_code, workspace_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            workspace_code == 3 and "workspace scope" in workspace_message,
            "Evidence validation should reject reports that are not scoped to a Marge workspace.",
        )
        staff_workspace = dict(valid_evidence)
        staff_workspace["workspace"] = dict(valid_evidence["workspace"], current_role="staff")
        evidence_path.write_text(json.dumps(staff_workspace), encoding="utf-8")
        role_code, role_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            role_code == 3 and "workspace role" in role_message,
            "Evidence validation should reject reports generated from a non-pastoral workspace role.",
        )
        missing_side_effect_check = dict(valid_evidence)
        missing_side_effect_check.pop("side_effect_check")
        evidence_path.write_text(json.dumps(missing_side_effect_check), encoding="utf-8")
        missing_side_effect_code, missing_side_effect_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            missing_side_effect_code == 3 and "side_effect_check" in missing_side_effect_message,
            "Evidence validation should reject reports without no-sync side-effect collection details.",
        )
        missing_side_effect_collection = json.loads(json.dumps(valid_evidence))
        missing_side_effect_collection["side_effect_check"]["collections"].pop("prayer_requests")
        evidence_path.write_text(json.dumps(missing_side_effect_collection), encoding="utf-8")
        missing_collection_code, missing_collection_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            missing_collection_code == 3 and "prayer_requests" in missing_collection_message,
            "Evidence validation should reject no-sync reports missing the private/public prayer snapshot collection.",
        )
        failed_side_effect_collection = json.loads(json.dumps(valid_evidence))
        failed_side_effect_collection["side_effect_check"]["collections"]["assistant_actions"]["ok"] = False
        evidence_path.write_text(json.dumps(failed_side_effect_collection), encoding="utf-8")
        failed_collection_code, failed_collection_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            failed_collection_code == 3 and "assistant_actions" in failed_collection_message,
            "Evidence validation should reject no-sync reports with failed collection checks.",
        )
        unsupported_provider = dict(valid_evidence)
        unsupported_provider["external_provider_checks"] = [dict(valid_evidence["external_provider_checks"][0], provider="not_a_provider")]
        evidence_path.write_text(json.dumps(unsupported_provider), encoding="utf-8")
        provider_code, provider_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            provider_code == 3 and "supported external provider" in provider_message,
            "Evidence validation should reject verified-looking providers outside Marge's supported external connector set.",
        )
        secret_identity = dict(valid_evidence)
        secret_identity["external_provider_checks"] = [
            dict(
                valid_evidence["external_provider_checks"][0],
                identity_key_paths=["profile.access_token"],
                sensitive_identity_keys=[],
            )
        ]
        evidence_path.write_text(json.dumps(secret_identity), encoding="utf-8")
        secret_code, secret_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            secret_code == 3 and "sensitive identity metadata" in secret_message,
            "Evidence validation should independently reject secret-shaped identity metadata keys.",
        )
        leaked_evidence_key = json.loads(json.dumps(valid_evidence))
        leaked_evidence_key["workspace"]["account_token"] = "marge_sess_leaked"
        evidence_path.write_text(json.dumps(leaked_evidence_key), encoding="utf-8")
        leaked_key_code, leaked_key_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            leaked_key_code == 3 and "secret-shaped key" in leaked_key_message and "workspace.account_token" in leaked_key_message,
            "Evidence validation should reject secret-shaped keys anywhere in the report.",
        )
        leaked_evidence_value = json.loads(json.dumps(valid_evidence))
        leaked_evidence_value["external_provider_checks"][0]["message"] = "Provider returned apiKey=leaked-token-value."
        evidence_path.write_text(json.dumps(leaked_evidence_value), encoding="utf-8")
        leaked_value_code, leaked_value_message = gate.validate_live_evidence_file(str(evidence_path), "https://marge.example.com")
        assert_true(
            leaked_value_code == 3
            and "secret-shaped value" in leaked_value_message
            and "external_provider_checks[0].message" in leaked_value_message,
            "Evidence validation should reject obvious secret-shaped values even under generic keys.",
        )
        evidence_path.write_text(json.dumps(valid_evidence), encoding="utf-8")
        clear_code, _clear_message = gate.clear_live_evidence_file(str(evidence_path))
        assert_true(clear_code == 0 and not evidence_path.exists(), "Pilot gate should be able to remove stale live evidence before rerun.")

    print("Marge pilot readiness gate smoke passed.")
    print(json.dumps({
        "local_env_template_connector_boundary": "verified",
        "nonlocal_operator_token_preflight": "verified",
        "operator_token_role_validation": "verified",
        "invalid_operator_token_rejection": "verified",
        "production_env_template_coverage": "verified",
        "production_failure_rerun_command": "verified",
        "complete_production_env_file": "verified",
        "production_env_file_isolation": "verified",
        "app_cors_origin_match": "verified",
        "oauth_callback_origin_and_path": "verified",
        "public_connector_base_urls": "verified",
        "blank_env_file_operator_values": "preserved",
        "workspace_write_skip_without_token": "verified",
        "live_connector_skip_without_token": "verified",
        "live_connector_failure_blocks_pilot": "verified",
        "live_connector_evidence_validation_blocks_pilot": "verified",
        "live_connector_evidence_strict_mode": "verified",
        "live_connector_evidence_generated_at": "verified",
        "live_connector_evidence_freshness": "verified",
        "live_connector_evidence_api_url_match": "verified",
        "live_connector_evidence_role_scope": "verified",
        "live_connector_evidence_side_effect_collections": "verified",
        "live_connector_evidence_supported_provider": "verified",
        "live_connector_evidence_sensitive_identity_keys": "verified",
        "live_connector_evidence_secret_key_scan": "verified",
        "live_connector_evidence_secret_value_scan": "verified",
        "live_connector_evidence_stale_file_clear": "verified",
        "live_connector_evidence_file_passthrough": "verified",
        "first_run_default_with_token": "verified",
        "troubleshooting_skip_first_run": "verified",
    }, indent=2))


if __name__ == "__main__":
    main()
