#!/usr/bin/env python3
"""
Verify live Marge connector credentials without syncing ministry data.

This calls the same safe /assistant/integrations/{provider}/verify endpoint the
product uses after setup. Verification performs minimal identity/config checks
and this script checks that it does not import people, email, calendar events,
or queue assistant actions. The no-sync proof snapshots every page of the
workspace collections it inspects, including private prayer requests.

Usage:
  MARGE_API_URL=http://127.0.0.1:8000 \
  MARGE_ACCOUNT_TOKEN=marge_sess_... \
  .venv/bin/python scripts/verify_live_integrations.py

  # Verify specific providers even if status is still planned/needs setup:
  .venv/bin/python scripts/verify_live_integrations.py google_workspace planning_center --include-not-ready

  # Production gate: require a workspace token and at least one real
  # church-tool connector, not only MCP:
  .venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider

  # Save the operator evidence report without relying on shell redirection:
  .venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider \
    --evidence-file artifacts/live-connector-verification.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

DEFAULT_PROVIDERS = ["google_workspace", "microsoft_365", "planning_center", "breeze", "rock"]
READY_STATUSES = {"connected", "configured", "available"}
OPERATOR_ROLES = {"admin", "owner", "pastor"}
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
SIDE_EFFECT_PATHS = {
    "connected_context": "/assistant/connected-items",
    "assistant_actions": "/assistant/actions?status=all",
    "members": "/members/",
    "visitors": "/visitors/",
    "care_cases": "/care/",
    "prayer_requests": "/care/prayers/?include_private=true",
}
SIDE_EFFECT_PAGE_LIMIT = 200
SIDE_EFFECT_MAX_PAGES = 100
WORKSPACE_REQUIRED_PHRASES = (
    "create or reconnect a marge workspace",
    "no valid marge account token",
    "marge_account_token is required",
    "workspace token",
    "account token",
)
SENSITIVE_OUTPUT_VALUE_PATTERNS = [
    (
        re.compile(
            r"\b(access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|token[_-]?ciphertext)\s*[:=]\s*[^\s,;]+",
            re.IGNORECASE,
        ),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"\b(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (
        re.compile(r"\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}", re.IGNORECASE),
        r"\1<redacted>",
    ),
    (re.compile(r"\bmarge_sess_[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-marge-session>"),
    (re.compile(r"\bya29\.[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-google-token>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-jwt>",
    ),
]


def redact_sensitive_text(value: str) -> str:
    redacted = str(value)
    for pattern, replacement in SENSITIVE_OUTPUT_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_evidence_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_evidence_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [sanitize_evidence_value(nested) for nested in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def is_local_api_url(api_url: str) -> bool:
    parsed = urlparse(api_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def request_json(method: str, url: str, token: str | None = None, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Marge-Account-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
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


def status_by_provider(api_url: str, token: str | None) -> dict[str, dict[str, Any]]:
    status, body = request_json("GET", f"{api_url}/assistant/integrations", token=token)
    if status == 0 or status >= 400:
        detail = error_detail(body)
        raise RuntimeError(f"Could not list integrations ({status}): {detail}")
    return {item.get("provider"): item for item in body or [] if item.get("provider")}


def workspace_scope(api_url: str, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    status, body = request_json("GET", f"{api_url}/assistant/account", token=token)
    if status == 0 or status >= 400:
        raise RuntimeError(f"Could not resolve workspace scope ({status}): {error_detail(body)}")
    if not isinstance(body, dict):
        raise RuntimeError("Workspace scope endpoint did not return JSON.")
    return {
        "account_id": body.get("id"),
        "slug": body.get("slug"),
        "church_name": body.get("church_name"),
        "current_role": body.get("current_role"),
    }


def validate_workspace_scope_for_live_provider(workspace: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(workspace, dict) or not workspace.get("account_id") or not workspace.get("slug"):
        return False, "MARGE_ACCOUNT_TOKEN did not resolve to a scoped Marge workspace."
    role = str(workspace.get("current_role") or "").strip().lower()
    if role not in OPERATOR_ROLES:
        allowed = ", ".join(sorted(OPERATOR_ROLES))
        return False, f"MARGE_ACCOUNT_TOKEN resolved to role={role or 'unknown'}; use an {allowed} token for live provider verification."
    return True, f"MARGE_ACCOUNT_TOKEN resolved to {workspace.get('church_name') or workspace.get('slug')} with {role} access."


def page_path(path: str, *, skip: int, limit: int) -> str:
    parts = urlsplit(path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["skip"] = str(skip)
    query["limit"] = str(limit)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fetch_side_effect_rows(api_url: str, token: str | None, path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(SIDE_EFFECT_MAX_PAGES):
        page_start = len(rows)
        current_path = page_path(path, skip=page_start, limit=SIDE_EFFECT_PAGE_LIMIT)
        status, body = request_json("GET", f"{api_url}{current_path}", token=token)
        if status == 0 or status >= 400:
            raise RuntimeError(f"Could not snapshot {path} ({status}): {error_detail(body)}")
        if not isinstance(body, list):
            raise RuntimeError(f"Could not snapshot {path}: expected a JSON list.")
        for index, item in enumerate(body):
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"Could not snapshot {path}: expected JSON objects; "
                    f"row {page_start + index} was {type(item).__name__}."
                )
            rows.append(item)
        if len(body) < SIDE_EFFECT_PAGE_LIMIT:
            return rows
    raise RuntimeError(
        f"Could not snapshot {path}: exceeded {SIDE_EFFECT_MAX_PAGES} pages. "
        "Use a narrower pilot workspace or add a count/checksum endpoint before trusting no-sync verification."
    )


def snapshot_workspace(api_url: str, token: str | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name, path in SIDE_EFFECT_PATHS.items():
        rows = fetch_side_effect_rows(api_url, token, path)
        snapshot[name] = {
            "count": len(rows),
            "page_limit": SIDE_EFFECT_PAGE_LIMIT,
            "items": {
                str(item.get("id", index)): json.dumps(item, sort_keys=True, default=str)
                for index, item in enumerate(rows)
            },
        }
    return snapshot


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    collections: dict[str, Any] = {}
    ok = True
    for name in SIDE_EFFECT_PATHS:
        before_items = (before.get(name) or {}).get("items") or {}
        after_items = (after.get(name) or {}).get("items") or {}
        added = sorted(set(after_items) - set(before_items))
        removed = sorted(set(before_items) - set(after_items))
        changed = sorted(key for key in set(before_items) & set(after_items) if before_items[key] != after_items[key])
        collection_ok = not added and not removed and not changed
        ok = ok and collection_ok
        collections[name] = {
            "ok": collection_ok,
            "inspected_before": (before.get(name) or {}).get("count", 0),
            "inspected_after": (after.get(name) or {}).get("count", 0),
            "added_ids": added[:20],
            "removed_ids": removed[:20],
            "changed_ids": changed[:20],
        }
    return {"ok": ok, "collections": collections}


def error_detail(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return redact_sensitive_text(detail)
        return redact_sensitive_text(json.dumps(body, default=str))
    return redact_sensitive_text(str(body))


def is_workspace_required_error(status: int, body: Any) -> bool:
    if status not in {401, 403}:
        return False
    detail = error_detail(body).lower()
    return any(phrase in detail for phrase in WORKSPACE_REQUIRED_PHRASES)


def live_provider_rerun_command(api_url: str, evidence_file: str | None = None) -> str:
    evidence_arg = f" --evidence-file {shlex.quote(evidence_file)}" if evidence_file else ""
    return (
        f"Rerun MARGE_API_URL={api_url} MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION "
        f".venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider{evidence_arg}"
    )


def workspace_token_message(api_url: str) -> str:
    return (
        f"MARGE_ACCOUNT_TOKEN is required when checking live connector credentials against {api_url}. "
        "Create or open the pilot Marge workspace and use an owner/admin/pastor session token so "
        "OAuth state, API-key credentials, verification timestamps, and no-sync side-effect checks "
        "are scoped to that church."
    )


def provider_label(provider: str, statuses: dict[str, dict[str, Any]]) -> str:
    return (statuses.get(provider) or {}).get("display_name") or provider.replace("_", " ").title()


def providers_to_verify(args: argparse.Namespace, statuses: dict[str, dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
    requested = list(args.providers or [])
    skipped: list[dict[str, str]] = []
    if not requested and args.all:
        requested = list(DEFAULT_PROVIDERS)
    if not requested:
        for provider in DEFAULT_PROVIDERS:
            status = (statuses.get(provider) or {}).get("status")
            if status in READY_STATUSES:
                requested.append(provider)
            else:
                skipped.append({"provider": provider, "status": status or "unknown", "reason": "not connected/configured"})
    if args.include_mcp and "mcp" not in requested:
        requested.append("mcp")
    if args.include_not_ready:
        return requested, []
    filtered: list[str] = []
    for provider in requested:
        if provider == "mcp":
            filtered.append(provider)
            continue
        status = (statuses.get(provider) or {}).get("status")
        if status in READY_STATUSES:
            filtered.append(provider)
        else:
            skipped.append({"provider": provider, "status": status or "unknown", "reason": "not connected/configured"})
    return filtered, skipped


def verify_provider(api_url: str, token: str | None, provider: str, statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status, body = request_json("POST", f"{api_url}/assistant/integrations/{provider}/verify", token=token, payload={})
    identity = ((body or {}).get("identity") or {}) if isinstance(body, dict) else {}
    identity_keys = sorted(identity.keys()) if isinstance(identity, dict) else []
    identity_key_paths = identity_metadata_key_paths(identity)
    sensitive_identity_keys = sensitive_identity_key_names(identity_key_paths)
    identity_signal = identity_has_signal(identity)
    result: dict[str, Any] = {
        "provider": provider,
        "display_name": provider_label(provider, statuses),
        "kind": "local_agent_bridge" if provider == "mcp" else "external_provider",
        "http_status": status,
        "ok": 0 < status < 400 and not sensitive_identity_keys and identity_signal,
    }
    if 0 < status < 400:
        body_message = redact_sensitive_text(str((body or {}).get("message") or ""))
        result.update({
            "status": "unsafe_identity" if sensitive_identity_keys else ("empty_identity" if not identity_signal else (body or {}).get("status")),
            "verified_at": (body or {}).get("verified_at"),
            "credential_scope": (body or {}).get("credential_scope"),
            "identity_keys": identity_keys,
            "identity_key_paths": identity_key_paths,
            "identity_signal": identity_signal,
            "sensitive_identity_keys": sensitive_identity_keys,
            "message": (
                f"Connector verification exposed sensitive identity metadata keys: {', '.join(sensitive_identity_keys)}."
                if sensitive_identity_keys
                else "Connector verification returned no affirmative identity/config metadata."
                if not identity_signal
                else body_message
            ),
        })
    elif is_workspace_required_error(status, body):
        result.update({
            "status": "workspace_required",
            "message": workspace_token_message(api_url),
        })
    else:
        result.update({
            "status": "failed",
            "message": error_detail(body),
        })
    return result


def identity_metadata_key_paths(identity: Any) -> list[str]:
    paths: list[str] = []

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                paths.append(path)
                walk(nested, path)
        elif isinstance(value, list):
            for index, nested in enumerate(value[:20]):
                if isinstance(nested, dict):
                    walk(nested, f"{prefix}[{index}]")

    walk(identity)
    return sorted(paths)


def identity_has_signal(identity: Any) -> bool:
    def value_has_signal(value: Any) -> bool:
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


def sensitive_identity_key_names(identity_keys: list[str]) -> list[str]:
    found = []
    for key in identity_keys:
        normalized = str(key).lower().replace("-", "_")
        if any(term in normalized for term in SENSITIVE_IDENTITY_KEY_TERMS):
            found.append(str(key))
    return sorted(found)


def print_human(api_url: str, results: list[dict[str, Any]], skipped: list[dict[str, str]], side_effects: dict[str, Any] | None = None) -> None:
    print(f"Marge live connector verification against {api_url}", flush=True)
    if skipped:
        print("\nSkipped:", flush=True)
        for item in skipped:
            print(f"  - {item['provider']}: {item['status']} ({item['reason']})", flush=True)

    external_results = [result for result in results if result.get("kind") != "local_agent_bridge"]
    bridge_results = [result for result in results if result.get("kind") == "local_agent_bridge"]

    if external_results:
        print("\nExternal provider credential checks:", flush=True)
        for result in external_results:
            mark = "OK" if result["ok"] else "FAIL"
            scope = f", scope={result['credential_scope']}" if result.get("credential_scope") else ""
            identity = f", identity_keys={','.join(result.get('identity_keys') or [])}" if result.get("identity_keys") else ""
            print(f"  - {mark} {result['display_name']} ({result['provider']}): {result['message']}{scope}{identity}", flush=True)
    else:
        print("\nNo external providers were verified.", flush=True)

    if bridge_results:
        print("\nLocal agent bridge checks (not live providers):", flush=True)
        for result in bridge_results:
            mark = "OK" if result["ok"] else "FAIL"
            identity = f", identity_keys={','.join(result.get('identity_keys') or [])}" if result.get("identity_keys") else ""
            print(f"  - {mark} {result['display_name']} ({result['provider']}): {result['message']}{identity}", flush=True)
    if side_effects:
        print("\nNo-sync side-effect check:", flush=True)
        for name, detail in side_effects.get("collections", {}).items():
            mark = "OK" if detail.get("ok") else "FAIL"
            label = name.replace("_", " ")
            print(
                f"  - {mark} {label}: inspected {detail.get('inspected_before', 0)} before / "
                f"{detail.get('inspected_after', 0)} after",
                flush=True,
            )
            if not detail.get("ok"):
                print(
                    "    added={added} removed={removed} changed={changed}".format(
                        added=",".join(detail.get("added_ids") or []) or "none",
                        removed=",".join(detail.get("removed_ids") or []) or "none",
                        changed=",".join(detail.get("changed_ids") or []) or "none",
                    ),
                    flush=True,
                )
    elif external_results:
        print(
            "\nNo-sync side-effect check: not run. Evidence from this run cannot prove live provider readiness.",
            flush=True,
        )


def print_next_actions(next_actions: list[str]) -> None:
    if not next_actions:
        return
    print("\nNext actions:", flush=True)
    for action in next_actions:
        print(f"  - {action}", flush=True)


def write_evidence_file(path: str, output: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    if target.parent != Path("."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sanitize_evidence_value(output), indent=2, default=str) + "\n", encoding="utf-8")


def write_evidence_or_fail(path: str | None, output: dict[str, Any]) -> bool:
    if not path:
        return True
    try:
        write_evidence_file(path, output)
    except OSError as exc:
        print(f"Could not write evidence file {path}: {exc}", file=sys.stderr)
        return False
    return True


def verified_external_providers(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        result
        for result in results
        if result.get("ok")
        and result.get("provider") in DEFAULT_PROVIDERS
        and result.get("status") == "verified"
        and bool(result.get("verified_at"))
        and bool(result.get("identity_keys"))
        and result.get("identity_signal") is True
        and not result.get("sensitive_identity_keys")
        and not sensitive_identity_key_names([
            str(key)
            for key in (result.get("identity_key_paths") or result.get("identity_keys", []))
        ])
    ]


def checked_local_bridges(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        result
        for result in results
        if result.get("ok")
        and result.get("provider") == "mcp"
        and result.get("kind") == "local_agent_bridge"
        and result.get("status") == "bridge_available"
    ]


def evidence_payload(
    api_url: str,
    results: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    side_effects: dict[str, Any] | None,
    require_live_provider: bool,
    next_actions: list[str],
    *,
    workspace: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    external_provider_checks = [result for result in results if result.get("kind") != "local_agent_bridge"]
    local_bridge_checks = [result for result in results if result.get("kind") == "local_agent_bridge"]
    external_verified = verified_external_providers(results)
    no_sync_side_effect_check_passed = None if side_effects is None else bool(side_effects.get("ok"))
    output = {
        "api_url": api_url,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "workspace": workspace,
        # Backward-compatible legacy field. Prefer the typed fields below for new evidence readers.
        "verified": results,
        "external_provider_checks": external_provider_checks,
        "local_bridge_checks": local_bridge_checks,
        "skipped": skipped,
        "side_effect_check": side_effects,
        "external_verified_count": len(external_verified),
        "local_bridge_checked_count": len(checked_local_bridges(results)),
        "no_sync_side_effect_check_passed": no_sync_side_effect_check_passed,
        "live_provider_ready": bool(external_verified)
        and (no_sync_side_effect_check_passed is True),
        "required_live_provider": bool(require_live_provider),
        "next_actions": next_actions,
    }
    if error:
        output["error"] = error
    return sanitize_evidence_value(output)


def live_provider_next_actions(
    api_url: str,
    statuses: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    skipped: list[dict[str, str]],
    *,
    require_live_provider: bool,
    side_effects: dict[str, Any] | None = None,
    evidence_file: str | None = None,
) -> list[str]:
    actions: list[str] = []
    workspace_required = [
        result.get("display_name") or result.get("provider")
        for result in results
        if result.get("status") == "workspace_required"
    ]
    failed = [
        result.get("display_name") or result.get("provider")
        for result in results
        if not result.get("ok") and result.get("status") != "workspace_required"
    ]
    if workspace_required:
        actions.append(
            "Create or reconnect a real Marge workspace and rerun with MARGE_ACCOUNT_TOKEN before "
            f"checking connector credentials for: {', '.join(str(item) for item in workspace_required)}."
        )
        actions.append(live_provider_rerun_command(api_url, evidence_file))
    if failed:
        actions.append(f"Fix failed credential checks for: {', '.join(str(item) for item in failed)}.")
    if side_effects and not side_effects.get("ok"):
        actions.append("Fix verify endpoints so Check credentials does not import context, mutate ministry records, or queue assistant actions.")
    if require_live_provider and verified_external_providers(results) and side_effects is None:
        actions.append("Rerun without --skip-side-effect-check so the evidence proves Check credentials did not import context, mutate records, or queue actions.")
    if require_live_provider and not verified_external_providers(results):
        ready = [
            provider_label(provider, statuses)
            for provider in DEFAULT_PROVIDERS
            if (statuses.get(provider) or {}).get("status") in READY_STATUSES
        ]
        skipped_external = [
            provider_label(item.get("provider", ""), statuses)
            for item in skipped
            if item.get("provider") in DEFAULT_PROVIDERS
        ]
        if ready:
            actions.append(f"Run Check credentials for a ready external provider: {', '.join(ready)}.")
        else:
            skipped_text = f" Current skipped providers: {', '.join(skipped_external)}." if skipped_external else ""
            actions.append(
                "Connect or configure at least one external provider in /app Integrations: "
                "Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS."
                f"{skipped_text}"
            )
        actions.append("The provider check must set verified_at and return affirmative non-secret identity/config metadata; MCP does not count.")
        actions.append(live_provider_rerun_command(api_url, evidence_file))
        actions.append("Only after this passes should a pastor/admin explicitly run the first sync.")
    deduped: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        deduped.append(action)
    return deduped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify live Marge connector credentials without syncing ministry data.")
    parser.add_argument("providers", nargs="*", help="Provider keys to verify. Defaults to currently connected/configured external providers.")
    parser.add_argument("--api-url", default=os.getenv("MARGE_API_URL", "http://127.0.0.1:8000"), help="Marge API URL.")
    parser.add_argument(
        "--token",
        default=os.getenv("MARGE_ACCOUNT_TOKEN", ""),
        help="Workspace user/session token. Required for non-local API URLs and --require-live-provider.",
    )
    parser.add_argument("--all", action="store_true", help="Consider all supported external providers.")
    parser.add_argument("--include-not-ready", action="store_true", help="Call verify even for providers whose status is not connected/configured.")
    parser.add_argument("--include-mcp", action="store_true", help="Also verify the local MCP marker.")
    parser.add_argument(
        "--require-live-provider",
        action="store_true",
        help="Fail unless at least one external church-tool provider verifies successfully. MCP does not count.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument(
        "--evidence-file",
        help="Write the structured verification report to this JSON file. The report excludes workspace tokens and provider secrets.",
    )
    parser.add_argument(
        "--skip-side-effect-check",
        action="store_true",
        help="Diagnostic only: skip the no-sync snapshot check. Evidence from this mode cannot prove live provider readiness.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    token = args.token.strip() or None
    workspace: dict[str, Any] | None = None

    def fail_early(message: str, *, next_actions: list[str] | None = None, skipped: list[dict[str, str]] | None = None) -> int:
        actions = list(next_actions or [message])
        if args.require_live_provider and not any("verify_live_integrations.py" in action for action in actions):
            actions.append(live_provider_rerun_command(api_url, args.evidence_file))
        output = evidence_payload(
            api_url,
            [],
            skipped or [],
            None,
            bool(args.require_live_provider),
            actions,
            workspace=workspace,
            error=message,
        )
        write_ok = write_evidence_or_fail(args.evidence_file, output)
        print(message, file=sys.stderr)
        if args.json:
            print(json.dumps(output, indent=2, default=str))
        return 2 if write_ok else 1

    if not token and args.require_live_provider:
        return fail_early(workspace_token_message(api_url))
    if not token and args.evidence_file:
        print(
            "Warning: evidence report will be scoped only by API status because no MARGE_ACCOUNT_TOKEN was provided.",
            file=sys.stderr,
        )
    if not token and not is_local_api_url(api_url):
        return fail_early(
            "MARGE_ACCOUNT_TOKEN is required when verifying a non-local Marge API. "
            "Use an owner/admin/pastor session token so credential checks are scoped to the pilot workspace."
        )
    if token:
        try:
            workspace = workspace_scope(api_url, token)
        except RuntimeError as exc:
            return fail_early(
                str(exc),
                next_actions=["Confirm MARGE_ACCOUNT_TOKEN resolves to an owner/admin/pastor workspace and rerun live connector verification."],
            )
        if args.require_live_provider:
            workspace_ok, workspace_message = validate_workspace_scope_for_live_provider(workspace)
            if not workspace_ok:
                return fail_early(
                    workspace_message,
                    next_actions=[
                        "Use an owner/admin/pastor session token for live provider verification.",
                        "Verify the token with GET /assistant/account before rerunning live connector verification.",
                    ],
                )
    try:
        statuses = status_by_provider(api_url, token)
    except RuntimeError as exc:
        return fail_early(
            str(exc),
            next_actions=["Confirm MARGE_API_URL points at a reachable Marge API and rerun live connector verification."],
        )

    providers, skipped = providers_to_verify(args, statuses)
    before_snapshot = None
    side_effects = None
    if providers and not args.skip_side_effect_check:
        try:
            before_snapshot = snapshot_workspace(api_url, token)
        except RuntimeError as exc:
            return fail_early(
                str(exc),
                next_actions=["Fix workspace-scoped snapshot endpoints before trusting no-sync credential verification."],
                skipped=skipped,
            )
    results = [verify_provider(api_url, token, provider, statuses) for provider in providers]
    if before_snapshot is not None:
        try:
            after_snapshot = snapshot_workspace(api_url, token)
            side_effects = compare_snapshots(before_snapshot, after_snapshot)
        except RuntimeError as exc:
            output = evidence_payload(
                api_url,
                results,
                skipped,
                None,
                bool(args.require_live_provider),
                ["Fix workspace-scoped snapshot endpoints before trusting no-sync credential verification."],
                workspace=workspace,
                error=str(exc),
            )
            write_ok = write_evidence_or_fail(args.evidence_file, output)
            print(str(exc), file=sys.stderr)
            if args.json:
                print(json.dumps(output, indent=2, default=str))
            return 2 if write_ok else 1
    external_verified = verified_external_providers(results)
    next_actions = live_provider_next_actions(
        api_url,
        statuses,
        results,
        skipped,
        require_live_provider=bool(args.require_live_provider),
        side_effects=side_effects,
        evidence_file=args.evidence_file,
    )
    output = evidence_payload(
        api_url,
        results,
        skipped,
        side_effects,
        bool(args.require_live_provider),
        next_actions,
        workspace=workspace,
    )
    write_ok = write_evidence_or_fail(args.evidence_file, output)
    if args.json:
        print(json.dumps(output, indent=2, default=str))
    else:
        print_human(api_url, results, skipped, side_effects)
        if side_effects and not side_effects.get("ok"):
            print(
                "\nFAIL: Connector verification changed synced context or assistant actions. "
                "The verify path must remain a credential check only, with no import and no queued work.",
            )
        if args.require_live_provider and not external_verified:
            sys.stdout.flush()
            print(
                "\nFAIL: No live external church-tool provider verified. "
                "MCP is useful for local/LLM access, but production readiness needs at least one configured provider "
                "such as Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS.",
            )
        if args.require_live_provider and external_verified and side_effects is None:
            print(
                "\nFAIL: Live provider readiness requires the no-sync side-effect check. "
                "Rerun without --skip-side-effect-check before first sync.",
            )
        print_next_actions(next_actions)

    if not write_ok:
        return 1
    if not results:
        return 2
    if not all(result["ok"] for result in results):
        return 1
    if side_effects and not side_effects.get("ok"):
        return 1
    if args.require_live_provider and side_effects is None:
        return 2
    if args.require_live_provider and not external_verified:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
