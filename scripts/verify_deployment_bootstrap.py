#!/usr/bin/env python3
"""
Verify that a deployed Marge app can bootstrap the first pastor experience.

This is intentionally shallow and no-write: it checks the public health,
frontend shell, and assistant config endpoints that must work before a pastor
can create or reconnect a workspace.

Usage:
  MARGE_API_URL=https://marge.yourchurch.org \
  MARGE_APP_URL=https://marge.yourchurch.org/app \
  .venv/bin/python scripts/verify_deployment_bootstrap.py

  # Local development, where strict account tokens are usually disabled:
  .venv/bin/python scripts/verify_deployment_bootstrap.py --allow-relaxed-account-tokens
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_API_URL = "http://127.0.0.1:8000"


@dataclass
class Check:
    level: str
    name: str
    message: str


def request(method: str, url: str, *, accept: str = "*/*", timeout: int = 20) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, headers={"Accept": accept}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, body, headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return exc.code, body, headers
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def parse_json(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def check_health(api_url: str) -> Check:
    url = f"{api_url}/health"
    try:
        status, body, _headers = request("GET", url, accept="application/json")
    except RuntimeError as exc:
        return Check("fail", "HEALTH", f"{url} is unreachable: {exc}.")
    parsed = parse_json(body)
    if status >= 400:
        return Check("fail", "HEALTH", f"{url} returned HTTP {status}.")
    if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() in {"healthy", "ok"}:
        return Check("pass", "HEALTH", "API health endpoint is reachable.")
    return Check("fail", "HEALTH", f"{url} did not return a healthy JSON status.")


def check_assistant_config(api_url: str, *, allow_relaxed_account_tokens: bool) -> list[Check]:
    url = f"{api_url}/assistant/config"
    try:
        status, body, _headers = request("GET", url, accept="application/json")
    except RuntimeError as exc:
        return [Check("fail", "ASSISTANT_CONFIG", f"{url} is unreachable: {exc}.")]
    parsed = parse_json(body)
    checks: list[Check] = []
    if status >= 400:
        return [Check("fail", "ASSISTANT_CONFIG", f"{url} returned HTTP {status}.")]
    if not isinstance(parsed, dict):
        return [Check("fail", "ASSISTANT_CONFIG", f"{url} did not return JSON.")]
    if parsed.get("signup_enabled") is True:
        checks.append(Check("pass", "SIGNUP_CONFIG", "Workspace signup is enabled."))
    else:
        checks.append(Check("fail", "SIGNUP_CONFIG", "Assistant config does not report signup_enabled=true."))
    if parsed.get("require_account_token") is True:
        checks.append(Check("pass", "ACCOUNT_TOKEN_RUNTIME", "Runtime requires workspace tokens for scoped routes."))
    elif allow_relaxed_account_tokens:
        checks.append(Check("warn", "ACCOUNT_TOKEN_RUNTIME", "Runtime allows missing workspace tokens; acceptable only for local development."))
    else:
        checks.append(Check("fail", "ACCOUNT_TOKEN_RUNTIME", "Runtime reports require_account_token=false. Start production with MARGE_REQUIRE_ACCOUNT_TOKEN=true."))
    return checks


def check_app_shell(app_url: str) -> Check:
    try:
        status, body, headers = request("GET", app_url, accept="text/html")
    except RuntimeError as exc:
        return Check("fail", "APP_SHELL", f"{app_url} is unreachable: {exc}.")
    content_type = headers.get("content-type", "")
    if status >= 400:
        return Check("fail", "APP_SHELL", f"{app_url} returned HTTP {status}.")
    if "html" not in content_type.lower():
        return Check("fail", "APP_SHELL", f"{app_url} did not return HTML content.")
    required_fragments = ["<title>Marge", "Create workspace", "Marge"]
    missing = [fragment for fragment in required_fragments if fragment not in body]
    if missing:
        return Check("fail", "APP_SHELL", f"{app_url} is missing expected first-run shell text: {', '.join(missing)}.")
    return Check("pass", "APP_SHELL", "Frontend app shell is reachable and contains first-run workspace copy.")


def default_app_url(api_url: str) -> str:
    return f"{api_url}/app/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify public Marge deployment bootstrap endpoints.")
    parser.add_argument("--api-url", default=os.getenv("MARGE_API_URL", DEFAULT_API_URL), help="Marge API origin/base URL.")
    parser.add_argument("--app-url", default=os.getenv("MARGE_APP_URL", ""), help="Exact public /app URL. Defaults to MARGE_APP_URL or <api-url>/app/.")
    parser.add_argument("--allow-relaxed-account-tokens", action="store_true", help="Allow require_account_token=false for local development checks.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    app_url = (args.app_url or default_app_url(api_url)).rstrip("/") or default_app_url(api_url)

    checks = [check_health(api_url)]
    checks.extend(check_assistant_config(api_url, allow_relaxed_account_tokens=args.allow_relaxed_account_tokens))
    checks.append(check_app_shell(app_url))

    for check in checks:
        marker = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[check.level]
        print(f"{marker} {check.name}: {check.message}")

    failures = [check for check in checks if check.level == "fail"]
    warnings = [check for check in checks if check.level == "warn"]
    print(json.dumps({"failures": len(failures), "warnings": len(warnings)}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
