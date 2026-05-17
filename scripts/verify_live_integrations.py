#!/usr/bin/env python3
"""
Verify live Marge connector credentials without syncing ministry data.

This calls the same safe /assistant/integrations/{provider}/verify endpoint the
product uses after setup. Verification performs minimal identity/config checks
and this script checks that it does not import people, email, calendar events,
or queue assistant actions.

Usage:
  MARGE_API_URL=http://127.0.0.1:8000 \
  MARGE_ACCOUNT_TOKEN=marge_sess_... \
  .venv/bin/python scripts/verify_live_integrations.py

  # Verify specific providers even if status is still planned/needs setup:
  .venv/bin/python scripts/verify_live_integrations.py google_workspace planning_center --include-not-ready

  # Production gate: require at least one real church-tool connector, not only MCP:
  .venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PROVIDERS = ["google_workspace", "microsoft_365", "planning_center", "breeze", "rock"]
READY_STATUSES = {"connected", "configured", "available"}
SIDE_EFFECT_PATHS = {
    "connected_context": "/assistant/connected-items?limit=200",
    "assistant_actions": "/assistant/actions?status=all&limit=200",
}


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


def status_by_provider(api_url: str, token: str | None) -> dict[str, dict[str, Any]]:
    status, body = request_json("GET", f"{api_url}/assistant/integrations", token=token)
    if status >= 400:
        detail = error_detail(body)
        raise RuntimeError(f"Could not list integrations ({status}): {detail}")
    return {item.get("provider"): item for item in body or [] if item.get("provider")}


def snapshot_workspace(api_url: str, token: str | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name, path in SIDE_EFFECT_PATHS.items():
        status, body = request_json("GET", f"{api_url}{path}", token=token)
        if status >= 400:
            raise RuntimeError(f"Could not snapshot {name} ({status}): {error_detail(body)}")
        rows = body or []
        snapshot[name] = {
            "count": len(rows),
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
            return detail
        return json.dumps(body, default=str)
    return str(body)


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
    result: dict[str, Any] = {
        "provider": provider,
        "display_name": provider_label(provider, statuses),
        "http_status": status,
        "ok": status < 400,
    }
    if status < 400:
        result.update({
            "status": (body or {}).get("status"),
            "credential_scope": (body or {}).get("credential_scope"),
            "identity_keys": sorted(((body or {}).get("identity") or {}).keys()),
            "message": (body or {}).get("message"),
        })
    else:
        result.update({
            "status": "failed",
            "message": error_detail(body),
        })
    return result


def print_human(api_url: str, results: list[dict[str, Any]], skipped: list[dict[str, str]], side_effects: dict[str, Any] | None = None) -> None:
    print(f"Marge live connector verification against {api_url}", flush=True)
    if skipped:
        print("\nSkipped:", flush=True)
        for item in skipped:
            print(f"  - {item['provider']}: {item['status']} ({item['reason']})", flush=True)
    if results:
        print("\nVerified:", flush=True)
        for result in results:
            mark = "OK" if result["ok"] else "FAIL"
            scope = f", scope={result['credential_scope']}" if result.get("credential_scope") else ""
            identity = f", identity_keys={','.join(result.get('identity_keys') or [])}" if result.get("identity_keys") else ""
            print(f"  - {mark} {result['display_name']} ({result['provider']}): {result['message']}{scope}{identity}", flush=True)
    else:
        print("\nNo providers were verified.", flush=True)
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


def verified_external_providers(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in results if result.get("ok") and result.get("provider") in DEFAULT_PROVIDERS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify live Marge connector credentials without syncing ministry data.")
    parser.add_argument("providers", nargs="*", help="Provider keys to verify. Defaults to currently connected/configured external providers.")
    parser.add_argument("--api-url", default=os.getenv("MARGE_API_URL", "http://127.0.0.1:8000"), help="Marge API URL.")
    parser.add_argument("--token", default=os.getenv("MARGE_ACCOUNT_TOKEN", ""), help="Workspace user/session token.")
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
        "--skip-side-effect-check",
        action="store_true",
        help="Skip the default no-sync check that connected context and assistant actions are unchanged by verification.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    token = args.token.strip() or None
    try:
        statuses = status_by_provider(api_url, token)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    providers, skipped = providers_to_verify(args, statuses)
    before_snapshot = None
    side_effects = None
    if providers and not args.skip_side_effect_check:
        try:
            before_snapshot = snapshot_workspace(api_url, token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    results = [verify_provider(api_url, token, provider, statuses) for provider in providers]
    if before_snapshot is not None:
        try:
            after_snapshot = snapshot_workspace(api_url, token)
            side_effects = compare_snapshots(before_snapshot, after_snapshot)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    external_verified = verified_external_providers(results)
    output = {
        "api_url": api_url,
        "verified": results,
        "skipped": skipped,
        "side_effect_check": side_effects,
        "external_verified_count": len(external_verified),
        "required_live_provider": bool(args.require_live_provider),
    }
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

    if not results:
        return 2
    if not all(result["ok"] for result in results):
        return 1
    if side_effects and not side_effects.get("ok"):
        return 1
    if args.require_live_provider and not external_verified:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
