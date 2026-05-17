#!/usr/bin/env python3
"""
Smoke-test the MCP-facing first-run path against a running local Marge server.

This proves external LLM clients can use Marge as a secretary, not just as CRUD:

- create a workspace through the API
- send first-run ministry context through the MCP tell_marge tool
- keep intent/saved/profile metadata and action cards in MCP output
- show contextual setup steps through get_assistant_desk
- preserve chat history for the agent client

Usage:
  .venv/bin/python scripts/smoke_mcp_first_run.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE_URL = os.getenv("MARGE_API_URL", "http://127.0.0.1:8000").rstrip("/")


def request(method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Marge-Account-Token"] = token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {detail}") from exc


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def cleanup_account(account_id: int) -> None:
    if "localhost" not in BASE_URL and "127.0.0.1" not in BASE_URL:
        print(f"Skipping local DB cleanup for non-local MARGE_API_URL: {BASE_URL}")
        return

    from app.database import SessionLocal
    from app.models import (
        AccountPastorProfile,
        AccountSession,
        AccountUser,
        AssistantAction,
        AssistantChatMessage,
        AuditLog,
        CareNote,
        ChurchAccount,
        ConnectedContextItem,
        IntegrationConnection,
        IntegrationCredential,
        IntegrationOAuthState,
        IntegrationPolicy,
        Member,
        MemberNote,
        PrayerRequest,
        Visitor,
    )

    db = SessionLocal()
    try:
        for model in [
            AuditLog,
            AssistantChatMessage,
            ConnectedContextItem,
            AssistantAction,
            IntegrationCredential,
            IntegrationOAuthState,
            IntegrationPolicy,
            IntegrationConnection,
            MemberNote,
            CareNote,
            PrayerRequest,
            Visitor,
            Member,
            AccountPastorProfile,
            AccountSession,
            AccountUser,
        ]:
            db.query(model).filter(model.account_id == account_id).delete(synchronize_session=False)
        account = db.query(ChurchAccount).filter(ChurchAccount.id == account_id).one_or_none()
        if account:
            db.delete(account)
        db.commit()
    finally:
        db.close()


async def tool_text(mcp_server_module, name: str, arguments: dict[str, Any]) -> str:
    contents = await mcp_server_module.call_tool(name, arguments)
    return "\n".join(item.text for item in contents)


async def main() -> None:
    suffix = int(time.time())
    account_id = None
    try:
        signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor MCP Smoke",
                "church_name": f"MCP Smoke Church {suffix}",
            },
        )
        token = signup["token"]
        account_id = signup["account_id"]

        from mcp_server import server as mcp_server_module

        mcp_server_module.MARGE_API_URL = BASE_URL
        mcp_server_module.MARGE_ACCOUNT_TOKEN = token

        original_post = mcp_server_module._post
        precheck_calls = []

        def fake_post(path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
            precheck_calls.append((path, params))
            if path == "/assistant/integrations/planning_center/sync":
                request_obj = httpx.Request("POST", f"{BASE_URL}{path}")
                response = httpx.Response(
                    409,
                    json={
                        "detail": (
                            "Check credentials for Planning Center before syncing ministry data. "
                            "Verification confirms access without importing people, email, or calendar context."
                        )
                    },
                    request=request_obj,
                )
                raise httpx.HTTPStatusError("Conflict", request=request_obj, response=response)
            if path == "/assistant/integrations/planning_center/verify":
                return {
                    "provider": "planning_center",
                    "status": "verified",
                    "verified_at": "2026-05-17T12:00:00",
                    "credential_scope": "current user",
                    "identity": {"id": "pc-smoke", "name": "MCP Planning Center"},
                    "message": "Planning Center credentials verified without syncing ministry data.",
                }
            return original_post(path, body, params=params)

        try:
            mcp_server_module._post = fake_post
            precheck_output = await tool_text(
                mcp_server_module,
                "sync_integration",
                {"provider": "planning_center", "email_limit": 5, "people_limit": 25, "calendar_days": 14},
            )
        finally:
            mcp_server_module._post = original_post

        assert_true(
            [call[0] for call in precheck_calls]
            == ["/assistant/integrations/planning_center/sync", "/assistant/integrations/planning_center/verify"],
            "MCP sync should verify and stop when the backend rejects an unchecked connector sync.",
        )
        assert_true("credentials verified" in precheck_output.lower(), "MCP sync precheck should report successful verification.")
        assert_true("without syncing" in precheck_output.lower(), "MCP sync precheck should say no ministry data was synced.")
        assert_true("did not queue actions" in precheck_output.lower(), "MCP sync precheck should say no actions were queued.")
        assert_true("sync this connector again" in precheck_output.lower(), "MCP sync precheck should ask for an explicit follow-up sync.")
        assert_true("Marge API error" not in precheck_output, "MCP sync precheck should not surface the raw sync rejection.")

        first_desk = await tool_text(mcp_server_module, "get_assistant_desk", {"mode": "auto"})
        assert_true("Mode: live" in first_desk, "MCP assistant desk should stay live for a real workspace.")
        assert_true("Profile:" in first_desk, "MCP assistant desk should expose profile completion.")

        messages = [
            "I am a solo pastor and we average about 90 people each week.",
            "We are a neighborhood church with many new families and a tired volunteer core.",
            "Our church tradition is non-denominational with Baptist roots; keep language plain for guests.",
            "Visitor follow-up and hospital care are where people slip through the cracks.",
            "My first priority this month is closing loops with guests and hospital follow-up.",
            "We use Planning Center and Microsoft 365.",
            "Write in a warm, brief voice. Protect sermon prep Thursday mornings. Do not send emails or write to external systems without approval.",
        ]
        outputs = []
        for message in messages:
            outputs.append(await tool_text(mcp_server_module, "tell_marge", {"message": message, "mode": "live"}))

        combined = "\n".join(outputs)
        assert_true("Response metadata:" in combined, "MCP tell_marge should keep response metadata for agent clients.")
        assert_true("saved=True" in combined, "MCP tell_marge should show whether Marge saved context.")
        assert_true("Profile:" in combined, "MCP tell_marge should include profile completion.")
        assert_true("non-denominational" in combined.lower(), "MCP first-run output should reflect church voice and tradition.")
        assert_true("visitor follow-up" in combined.lower(), "MCP first-run output should reflect the pastor's unique follow-up burden.")
        assert_true("guests and hospital follow-up" in combined.lower(), "MCP first-run output should reflect the pastor's first ministry priority.")
        assert_true("Suggested prompts:" in combined, "MCP tell_marge should keep suggested next prompts.")

        completed_desk = await tool_text(mcp_server_module, "get_assistant_desk", {"mode": "auto"})
        assert_true("Log the first real visitor" in completed_desk, "MCP desk should expose the concrete first real-record setup step.")
        assert_true("Connect Planning Center" in completed_desk, "MCP desk should expose saved-tool connector setup.")
        assert_true("Connect Microsoft 365" in completed_desk, "MCP desk should expose Microsoft 365 connector setup.")

        first_visitor = await tool_text(
            mcp_server_module,
            "tell_marge",
            {
                "message": "Log the first visitor: Talia Brooks came Sunday, talia.brooks@example.test, 555-0199, and asked about kids ministry.",
                "mode": "live",
            },
        )
        assert_true("intent=visitor_logged" in first_visitor, "MCP tell_marge should save a concrete first-visitor prompt, not return generic setup guidance.")
        assert_true("saved=True" in first_visitor, "MCP tell_marge should show that the first visitor was saved.")
        assert_true("Talia Brooks" in first_visitor, "MCP tell_marge should name the visitor saved from chat.")
        assert_true("Returned action cards:" in first_visitor, "MCP tell_marge should expose visitor/welcome action cards.")
        talia_actions = request("GET", "/assistant/actions?status=all&limit=60", token=token)
        talia_action = next(
            (
                action
                for action in talia_actions
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Talia Brooks" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(talia_action is not None, "MCP tell_marge first visitor should queue a welcome email draft action.")
        talia_email = (talia_action.get("payload") or {}).get("email") or {}
        assert_true(talia_email.get("to") == "talia.brooks@example.test", "MCP tell_marge first visitor should preserve the email recipient.")
        assert_true(bool(talia_email.get("body")), "MCP tell_marge first visitor should include reviewable welcome body text.")
        pending_after_first_visitor = request("GET", "/assistant/actions?status=pending&limit=60", token=token)
        assert_true(
            not any(action.get("action_type") == "data_seed" for action in pending_after_first_visitor),
            "MCP first visitor should retire the first-real-record setup action from the pending queue.",
        )
        data_seed_action = next((action for action in talia_actions if action.get("action_type") == "data_seed"), None)
        assert_true(
            data_seed_action and data_seed_action.get("status") == "executed",
            "MCP first visitor should mark the completed setup prompt executed.",
        )
        approval_question = await tool_text(
            mcp_server_module,
            "tell_marge",
            {"message": "What should I approve first?", "mode": "live"},
        )
        assert_true(
            "intent=approval_queue_lookup" in approval_question,
            "MCP approval questions should summarize the queue, not approve an item.",
        )
        assert_true("Review Visitor welcome" in approval_question, "MCP approval lookup should name the visitor welcome draft.")
        talia_after_question = request("GET", "/assistant/actions?status=all&limit=60", token=token)
        talia_action_after_question = next(
            (
                action
                for action in talia_after_question
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Talia Brooks" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(
            talia_action_after_question and talia_action_after_question.get("status") == "pending",
            "MCP approval lookup should leave the visitor welcome draft pending.",
        )

        first_person = await tool_text(
            mcp_server_module,
            "tell_marge",
            {
                "message": "Help me add the first real person: Ruth Carter, ruth.carter@example.test. She is grieving her mother and asked for private prayer.",
                "mode": "live",
            },
        )
        assert_true("Ruth Carter" in first_person, "MCP tell_marge should return the created person's name.")
        assert_true("Returned action cards:" in first_person, "MCP tell_marge should expose action cards after saving first-person context.")

        visitor_output = await tool_text(
            mcp_server_module,
            "log_visitor",
            {
                "first_name": "Jordan",
                "last_name": "Parker",
                "email": "jordan.parker@example.test",
                "phone": "555-0134",
                "visit_date": "2026-05-17",
                "source": "MCP smoke",
                "notes": "Visited worship and asked about small groups.",
            },
        )
        assert_true("Jordan Parker" in visitor_output, "MCP log_visitor should name the logged visitor.")
        assert_true("queued a welcome draft" in visitor_output.lower(), "MCP log_visitor should say the welcome draft is queued for review.")
        assert_true("nothing was sent" in visitor_output.lower(), "MCP log_visitor should preserve the approval boundary.")
        approval_queue = await tool_text(mcp_server_module, "list_approval_queue", {"status": "all", "limit": 30})
        assert_true("Review Visitor welcome" in approval_queue, "MCP visitor logging should create a reviewable welcome action.")
        visitor_actions = request("GET", "/assistant/actions?status=all&limit=60", token=token)
        jordan_action = next(
            (
                action
                for action in visitor_actions
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Jordan Parker" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(jordan_action is not None, "MCP visitor logging should queue a visitor welcome email draft action.")
        jordan_email = (jordan_action.get("payload") or {}).get("email") or {}
        assert_true(jordan_email.get("to") == "jordan.parker@example.test", "MCP visitor welcome draft should preserve the visitor email recipient.")
        assert_true(bool(jordan_email.get("body")), "MCP visitor welcome draft should include reviewable body text.")

        history = await tool_text(mcp_server_module, "list_assistant_chat_history", {"limit": 20})
        assert_true("visitor follow-up" in history.lower(), "MCP chat history should preserve first-run context.")
        assert_true("Talia Brooks" in history, "MCP chat history should preserve the first visitor logged through tell_marge.")
        assert_true("Ruth Carter" in history, "MCP chat history should preserve later pastoral updates.")

        profile = await tool_text(mcp_server_module, "get_ministry_profile", {})
        assert_true("Profile completion: 100%" in profile, "MCP ministry profile should show complete first-run context.")
        assert_true("Planning Center" in profile and "Microsoft 365" in profile, "MCP ministry profile should show saved tools.")

        print("Marge MCP first-run smoke passed.")
        print(json.dumps({
            "account_id": account_id,
            "checked_tools": [
                "get_assistant_desk",
                "tell_marge",
                "list_assistant_chat_history",
                "get_ministry_profile",
                "log_visitor",
                "list_approval_queue",
            ],
            "mcp_first_run_context": "verified",
            "mcp_action_cards": "verified",
            "mcp_visitor_welcome_draft": "verified",
            "mcp_sync_precheck_verification": "verified",
        }, indent=2))
    finally:
        if account_id is not None:
            cleanup_account(account_id)


if __name__ == "__main__":
    asyncio.run(main())
