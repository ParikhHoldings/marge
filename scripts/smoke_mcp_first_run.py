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
import re
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


async def assert_mcp_tool_descriptions_avoid_fake_people(mcp_server_module) -> None:
    tools = await mcp_server_module.list_tools()
    tell_marge_tool = next((tool for tool in tools if tool.name == "tell_marge"), None)
    assert_true(tell_marge_tool is not None, "MCP should expose the tell_marge tool.")
    description = tell_marge_tool.description or ""
    assert_true(
        all(name not in description for name in ["Martha", "Tom Henderson", "Wilson family"]),
        "MCP tell_marge schema should not teach external LLMs with fake people.",
    )
    assert_true(
        "real person, family, or ministry situation" in description,
        "MCP tell_marge schema should tell external LLMs to use the pastor's real context.",
    )


async def assert_mcp_missing_name_output_is_pastoral(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        if path == "/care/":
            return [{
                "id": 101,
                "member_name": None,
                "category": "hospital",
                "status": "active",
                "last_contact": None,
            }]
        if path == "/care/prayers/":
            return [{
                "id": 202,
                "member_name": None,
                "submitted_by": None,
                "request_text": "Please pray for a private family need.",
                "is_private": True,
                "status": "active",
            }]
        return original_get(path, params=params)

    try:
        mcp_server_module._get = fake_get
        care_output = await tool_text(mcp_server_module, "list_care_cases", {})
        prayer_output = await tool_text(mcp_server_module, "list_prayer_requests", {"include_private": True})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "Unknown" not in care_output and "Name not linked" in care_output,
        "MCP care listing should describe missing member links without Unknown placeholders.",
    )
    assert_true(
        "Anonymous" not in prayer_output and "Name withheld" in prayer_output,
        "MCP prayer listing should describe private/missing submitters without Anonymous placeholders.",
    )


async def assert_mcp_api_errors_use_safe_detail(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        request = httpx.Request("GET", f"{BASE_URL}{path}")
        response = httpx.Response(
            403,
            json={"detail": "Viewing pastoral care requires pastor, admin, or owner access. apiKey=leaked-mcp-secret"},
            request=request,
        )
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    try:
        mcp_server_module._get = fake_get
        output = await tool_text(mcp_server_module, "list_care_cases", {})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "Viewing pastoral care requires pastor, admin, or owner access." in output,
        "MCP API errors should surface the safe backend detail.",
    )
    assert_true(
        "leaked-mcp-secret" not in output and "apiKey=<redacted>" in output,
        "MCP API errors should redact token-shaped backend details before external LLM clients see them.",
    )
    assert_true(
        '{"detail"' not in output and "Forbidden" not in output,
        "MCP API errors should not expose raw JSON response bodies or transport noise.",
    )
    assert_true(
        "leaked-payload-secret" not in mcp_server_module._compact_payload({"execution": {"provider_id": "apiKey=leaked-payload-secret"}}),
        "MCP compact payload rendering should redact token-shaped provider payload values.",
    )


async def assert_mcp_formatters_redact_secret_text(mcp_server_module) -> None:
    chat_output = mcp_server_module._format_chat_message({
        "role": "user",
        "intent": "chat",
        "content": "Please remember apiKey=leaked-chat-secret",
    })
    tell_output = mcp_server_module._format_tell_marge_response({
        "reply": "I heard accessToken=leaked-reply-secret.",
        "actions": [{
            "title": "Check bearer leakedactiontoken123456",
            "priority": "medium",
            "action": "Review",
            "detail": "clientSecret=leaked-action-secret",
        }],
        "suggested_prompts": ["Try refreshToken=leaked-prompt-secret"],
    })
    member_missing = mcp_server_module._member_not_found_message(
        "apiKey=leaked-name-secret",
        "I did not save anything.",
    )
    assert_true(
        "leaked-chat-secret" not in chat_output and "apiKey=<redacted>" in chat_output,
        "MCP chat history formatting should redact token-shaped saved chat content.",
    )
    assert_true(
        all(secret not in tell_output for secret in ["leaked-reply-secret", "leakedactiontoken", "leaked-action-secret", "leaked-prompt-secret"]),
        "MCP tell_marge formatting should redact token-shaped reply, action, and prompt text.",
    )
    assert_true(
        "leaked-name-secret" not in member_missing and "apiKey=<redacted>" in member_missing,
        "MCP safe capture guidance should redact token-shaped user-provided names.",
    )

    original_get = mcp_server_module._get
    original_post = mcp_server_module._post
    original_delete = mcp_server_module._delete

    def fake_member(name: str = "apiKey=leaked-member-secret") -> dict[str, Any]:
        return {
            "id": 707,
            "full_name": name,
            "first_name": name,
            "last_name": "",
            "last_attendance": "accessToken=leaked-attendance-secret",
        }

    def fake_get(path: str, params: dict | None = None):
        params = params or {}
        if path == "/briefing/today":
            return {"plain_text": "Morning briefing apiKey=leaked-briefing-secret"}
        if path == "/members/":
            return [fake_member(params.get("q") or "apiKey=leaked-member-secret")]
        if path == "/care/":
            return [{
                "id": 101,
                "member_name": "apiKey=leaked-care-name-secret",
                "category": "apiKey=leaked-care-category-secret",
                "status": "active",
                "last_contact": "accessToken=leaked-care-date-secret",
            }]
        if path == "/care/prayers/":
            return [{
                "id": 202,
                "member_name": None,
                "submitted_by": "apiKey=leaked-prayer-name-secret",
                "request_text": "Please pray refreshToken=leaked-prayer-text-secret",
                "is_private": True,
                "status": "apiKey=leaked-prayer-status-secret",
            }]
        if path == "/assistant/desk":
            return {
                "greeting": "Hello apiKey=leaked-desk-greeting-secret",
                "mode": "apiKey=leaked-desk-mode-secret",
                "pastor_name": "apiKey=leaked-desk-pastor-secret",
                "church_name": "apiKey=leaked-desk-church-secret",
                "profile": {"completion_percent": 10},
                "proactive_summary": "Summary accessToken=leaked-desk-summary-secret",
                "setup_steps": [{
                    "title": "Setup apiKey=leaked-desk-step-secret",
                    "action": "Check",
                    "detail": "bearer leakeddeskstepdetailtoken123456",
                }],
                "suggested_prompts": ["Try clientSecret=leaked-desk-prompt-secret"],
            }
        if path == "/assistant/chat/history":
            return [{
                "role": "user",
                "intent": "apiKey=leaked-history-intent-secret",
                "content": "Remember apiKey=leaked-history-secret",
            }]
        if path == "/assistant/profile":
            return {
                "completion_percent": 12,
                "pastor_name": "apiKey=leaked-profile-pastor-secret",
                "church_name": "apiKey=leaked-profile-church-secret",
                "role_title": "apiKey=leaked-profile-role-secret",
                "church_context": "apiKey=leaked-profile-context-secret",
                "followup_pain": "apiKey=leaked-profile-pain-secret",
                "support_preferences": "apiKey=leaked-profile-support-secret",
                "tools_in_use": "apiKey=leaked-profile-tools-secret",
                "communication_style": "apiKey=leaked-profile-style-secret",
                "weekly_rhythm": "apiKey=leaked-profile-rhythm-secret",
                "guardrails": "apiKey=leaked-profile-guardrail-secret",
                "missing_fields": ["apiKey=leaked-profile-missing-secret"],
            }
        if path == "/assistant/integrations":
            return [{
                "provider": "google_workspace",
                "display_name": "Google Workspace apiKey=leaked-integration-name-secret",
                "status": "apiKey=leaked-integration-status-secret",
                "write_enabled": False,
                "verified_at": "accessToken=leaked-integration-verified-secret",
                "config_hint": "Check clientSecret=leaked-integration-hint-secret",
            }]
        if path == "/assistant/connected-items":
            return [{
                "id": 303,
                "provider": "apiKey=leaked-context-provider-secret",
                "item_type": "apiKey=leaked-context-type-secret",
                "title": "Title apiKey=leaked-context-title-secret",
                "snippet": "Snippet refreshToken=leaked-context-snippet-secret",
                "occurred_at": "accessToken=leaked-context-date-secret",
            }]
        if path == "/assistant/actions":
            return [{
                "id": 404,
                "title": "Review apiKey=leaked-action-title-secret",
                "status": "apiKey=leaked-action-status-secret",
                "action_type": "apiKey=leaked-action-type-secret",
                "privacy_level": "apiKey=leaked-action-privacy-secret",
                "description": "Description accessToken=leaked-action-description-secret",
            }]
        return original_get(path, params=params)

    def fake_post(path: str, body: dict, params: dict | None = None):
        if path == "/visitors/":
            return {
                "full_name": "apiKey=leaked-visitor-name-secret",
                "visit_date": "accessToken=leaked-visitor-date-secret",
                "email": "visitor@example.test",
            }
        if path == "/care/":
            return {"id": 606, "category": "apiKey=leaked-open-care-category-secret"}
        if path.endswith("/contact"):
            return {"member_name": "apiKey=leaked-contact-name-secret"}
        if path == "/care/prayers/":
            return {"is_private": False}
        if path.endswith("/notes"):
            return {}
        if path == "/drafts/":
            return {"draft": "Draft accessToken=leaked-draft-body-secret"}
        if path == "/assistant/integrations/google_workspace/start":
            return {
                "display_name": "Google Workspace apiKey=leaked-setup-name-secret",
                "status": "apiKey=leaked-setup-status-secret",
                "secure_note": "Note clientSecret=leaked-setup-note-secret",
                "authorization_url": "https://provider.example/auth?accessToken=leaked-setup-url-secret",
                "missing_config": ["apiKey=leaked-setup-config-secret"],
                "instructions": ["Set refreshToken=leaked-setup-instruction-secret"],
            }
        if path == "/assistant/integrations/google_workspace/sync":
            return {
                "provider": "apiKey=leaked-sync-provider-secret",
                "status": "apiKey=leaked-sync-status-secret",
                "synced_at": "accessToken=leaked-sync-date-secret",
                "message": "Synced refreshToken=leaked-sync-message-secret",
            }
        if path == "/assistant/integrations/google_workspace/verify":
            return {
                "provider": "apiKey=leaked-verify-provider-secret",
                "status": "apiKey=leaked-verify-status-secret",
                "verified_at": "accessToken=leaked-verify-date-secret",
                "credential_scope": "apiKey=leaked-verify-scope-secret",
                "message": "Verified clientSecret=leaked-verify-message-secret",
                "identity": {"email": "apiKey=leaked-verify-identity-secret"},
            }
        if path == "/assistant/actions/prepare":
            return [{
                "id": 505,
                "title": "Prepare apiKey=leaked-prepare-title-secret",
                "description": "Prepare accessToken=leaked-prepare-description-secret",
            }]
        if path == "/assistant/chat":
            return {"reply": "Reply apiKey=leaked-chat-reply-secret"}
        return original_post(path, body, params=params)

    def fake_delete(path: str):
        if path == "/assistant/chat/history":
            return {"message": "Cleared apiKey=leaked-clear-history-secret"}
        if path == "/assistant/integrations/google_workspace":
            return {
                "provider": "apiKey=leaked-disconnect-provider-secret",
                "status": "apiKey=leaked-disconnect-status-secret",
                "disconnected_at": "accessToken=leaked-disconnect-date-secret",
                "message": "Disconnected refreshToken=leaked-disconnect-message-secret",
            }
        return original_delete(path)

    try:
        mcp_server_module._get = fake_get
        mcp_server_module._post = fake_post
        mcp_server_module._delete = fake_delete
        rendered_outputs = [
            await tool_text(mcp_server_module, "get_morning_briefing", {}),
            await tool_text(mcp_server_module, "list_members", {}),
            await tool_text(mcp_server_module, "log_visitor", {"first_name": "A", "last_name": "B"}),
            await tool_text(mcp_server_module, "log_care_event", {
                "member_name": "apiKey=leaked-care-event-member-secret",
                "category": "general",
                "description": "Pastoral note",
            }),
            await tool_text(mcp_server_module, "list_care_cases", {}),
            await tool_text(mcp_server_module, "mark_contacted", {"care_id": 1}),
            await tool_text(mcp_server_module, "add_prayer_request", {
                "request_text": "Pray",
                "submitted_by": "apiKey=leaked-add-prayer-name-secret",
            }),
            await tool_text(mcp_server_module, "list_prayer_requests", {"include_private": True}),
            await tool_text(mcp_server_module, "add_member_note", {
                "member_name": "apiKey=leaked-note-member-secret",
                "note_text": "Pastoral note",
            }),
            await tool_text(mcp_server_module, "draft_message", {
                "member_name": "apiKey=leaked-draft-member-secret",
            }),
            await tool_text(mcp_server_module, "get_assistant_desk", {}),
            await tool_text(mcp_server_module, "list_assistant_chat_history", {}),
            await tool_text(mcp_server_module, "clear_assistant_chat_history", {}),
            await tool_text(mcp_server_module, "get_ministry_profile", {}),
            await tool_text(mcp_server_module, "list_integrations", {}),
            await tool_text(mcp_server_module, "start_integration_setup", {"provider": "google_workspace"}),
            await tool_text(mcp_server_module, "sync_integration", {"provider": "google_workspace"}),
            await tool_text(mcp_server_module, "verify_integration", {"provider": "google_workspace"}),
            await tool_text(mcp_server_module, "disconnect_integration", {"provider": "google_workspace"}),
            await tool_text(mcp_server_module, "list_connected_context", {}),
            await tool_text(mcp_server_module, "list_approval_queue", {"status": "pending"}),
            await tool_text(mcp_server_module, "prepare_approval_queue", {}),
            await tool_text(mcp_server_module, "tell_marge", {"message": "hello"}),
        ]
    finally:
        mcp_server_module._get = original_get
        mcp_server_module._post = original_post
        mcp_server_module._delete = original_delete

    combined = "\n".join(rendered_outputs)
    leaked_values = sorted(set(re.findall(r"leaked-[A-Za-z0-9-]+", combined)))
    assert_true(
        not leaked_values,
        f"MCP rendered tool outputs should redact token-shaped text before external LLM clients see it: {leaked_values}",
    )
    assert_true(
        "<redacted>" in combined,
        "MCP rendered tool output redaction smoke should prove replacement text is present.",
    )


async def assert_mcp_incomplete_titles_are_pastoral(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        if path == "/assistant/connected-items":
            return [{
                "id": 303,
                "provider": "google_workspace",
                "item_type": "calendar_event",
                "title": None,
                "occurred_at": None,
                "created_at": None,
                "snippet": "",
            }]
        if path == "/assistant/actions":
            return [{
                "id": 404,
                "title": None,
                "status": "pending",
                "action_type": "meeting_prep",
                "privacy_level": "pastoral",
                "description": "",
            }]
        return original_get(path, params=params)

    try:
        mcp_server_module._get = fake_get
        context_output = await tool_text(mcp_server_module, "list_connected_context", {})
        approval_output = await tool_text(mcp_server_module, "list_approval_queue", {"status": "pending"})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "Untitled" not in context_output
        and "unknown date" not in context_output
        and "title not included by provider" in context_output
        and "date not included by provider" in context_output,
        "MCP connected-context output should describe missing provider fields instead of placeholder titles/dates.",
    )
    assert_true(
        "Untitled" not in approval_output and "Assistant action title not included" in approval_output,
        "MCP approval output should describe missing action titles instead of placeholder titles.",
    )


async def assert_mcp_empty_states_are_actionable(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        if path in {"/members/", "/care/", "/care/prayers/", "/assistant/connected-items", "/assistant/actions"}:
            return []
        return original_get(path, params=params)

    try:
        mcp_server_module._get = fake_get
        members_output = await tool_text(mcp_server_module, "list_members", {})
        member_search_output = await tool_text(mcp_server_module, "list_members", {"search": "Marcus Reed"})
        care_output = await tool_text(mcp_server_module, "list_care_cases", {})
        prayer_output = await tool_text(mcp_server_module, "list_prayer_requests", {})
        context_output = await tool_text(mcp_server_module, "list_connected_context", {})
        actions_output = await tool_text(mcp_server_module, "list_approval_queue", {"status": "pending"})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "Add the first real person" in members_output
        and "directory as checked" in members_output
        and "No members found" not in members_output,
        "MCP empty member lists should guide the agent toward first real context.",
    )
    assert_true(
        "No saved person matched 'Marcus Reed'" in member_search_output
        and "before creating care, prayer, or follow-up work" in member_search_output,
        "MCP empty member searches should preserve the searched name and safe capture guidance.",
    )
    assert_true(
        "Add the first real person needing care" in care_output
        and "No care cases found" not in care_output,
        "MCP empty care lists should guide first real care context.",
    )
    assert_true(
        "Add the first real prayer request" in prayer_output
        and "No prayer requests found" not in prayer_output,
        "MCP empty prayer lists should guide first real prayer context.",
    )
    assert_true(
        "no-sync credential check" in context_output
        and "ask Marge to sync when the pastor is ready" in context_output
        and "No synced connected context found" not in context_output,
        "MCP empty connected-context lists should preserve secure connector order.",
    )
    assert_true(
        "Prepare reviewable work" in actions_output
        and "first real ministry record" in actions_output
        and "No assistant actions found" not in actions_output,
        "MCP empty approval lists should not imply the ministry desk is fully clear.",
    )


async def assert_mcp_integration_list_marks_mcp_as_bridge(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        if path == "/assistant/integrations":
            return [
                {
                    "provider": "mcp",
                    "display_name": "MCP",
                    "status": "connected",
                    "write_enabled": True,
                    "secure_note": "Available to local LLM clients.",
                },
                {
                    "provider": "google_workspace",
                    "display_name": "Google Workspace",
                    "status": "connected",
                    "write_enabled": False,
                    "verified_at": None,
                    "config_hint": "Check credentials before syncing.",
                },
            ]
        return original_get(path, params=params)

    try:
        mcp_server_module._get = fake_get
        output = await tool_text(mcp_server_module, "list_integrations", {})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "local agent bridge" in output
        and "not a church-tool provider" in output
        and "does not prove Google Workspace" in output,
        "MCP integration listing should mark MCP as agent access, not a live church-tool connector.",
    )
    assert_true(
        "live provider readiness requires a verified church-tool connector" in output
        and "Google Workspace: connected" in output
        and "needs credential check before sync" in output,
        "MCP integration listing should preserve real connector status and credential-check guidance.",
    )


async def assert_mcp_member_list_handles_incomplete_names(mcp_server_module) -> None:
    original_get = mcp_server_module._get

    def fake_get(path: str, params: dict | None = None):
        if path == "/members/":
            return [
                {"id": 707, "full_name": None, "first_name": None, "last_name": None, "last_attendance": None},
                {"id": 708, "full_name": "", "first_name": "Elena", "last_name": None, "last_attendance": "2026-05-10"},
            ]
        return original_get(path, params=params)

    try:
        mcp_server_module._get = fake_get
        output = await tool_text(mcp_server_module, "list_members", {})
    finally:
        mcp_server_module._get = original_get

    assert_true(
        "Member name not linked" in output and "Elena" in output,
        "MCP member lists should preserve partial names and mark missing names as incomplete.",
    )
    assert_true(
        "None" not in output and "Unknown" not in output,
        "MCP member lists should not leak Python None or Unknown placeholders for incomplete names.",
    )


async def assert_mcp_prayer_request_preserves_unmatched_submitter(mcp_server_module) -> None:
    original_find_member = mcp_server_module._find_member
    original_post = mcp_server_module._post
    captured_body: dict[str, Any] = {}

    def fake_find_member(name: str):
        return None

    def fake_post(path: str, body: dict[str, Any], params: dict[str, Any] | None = None):
        if path == "/care/prayers/":
            captured_body.update(body)
            return {
                "id": 505,
                "submitted_by": body.get("submitted_by"),
                "request_text": body.get("request_text"),
                "is_private": body.get("is_private"),
                "status": "active",
            }
        return original_post(path, body, params=params)

    try:
        mcp_server_module._find_member = fake_find_member
        mcp_server_module._post = fake_post
        output = await tool_text(
            mcp_server_module,
            "add_prayer_request",
            {
                "member_name": "Ruth Carter",
                "request_text": "Please pray for her family this week.",
                "is_private": True,
            },
        )
    finally:
        mcp_server_module._find_member = original_find_member
        mcp_server_module._post = original_post

    assert_true(
        captured_body.get("submitted_by") == "Ruth Carter" and "member_id" not in captured_body,
        "MCP prayer creation should preserve an unmatched name as submitted_by instead of dropping it.",
    )
    assert_true(
        "Ruth Carter" in output and "not linked to a saved member yet" in output,
        "MCP prayer creation should tell the agent when the request is saved but not linked to a member.",
    )


async def assert_mcp_unmatched_member_writes_are_safe(mcp_server_module) -> None:
    original_find_member = mcp_server_module._find_member

    def fake_find_member(name: str):
        return None

    try:
        mcp_server_module._find_member = fake_find_member
        care_output = await tool_text(
            mcp_server_module,
            "log_care_event",
            {"member_name": "Marcus Reed", "category": "hospital", "description": "Surgery recovery."},
        )
        note_output = await tool_text(
            mcp_server_module,
            "add_member_note",
            {"member_name": "Marcus Reed", "note_text": "Prefers calls.", "context_tag": "preference"},
        )
        draft_output = await tool_text(
            mcp_server_module,
            "draft_message",
            {"member_name": "Marcus Reed", "situation": "hospital follow-up"},
        )
    finally:
        mcp_server_module._find_member = original_find_member

    combined = "\n".join([care_output, note_output, draft_output])
    assert_true(
        "No saved person matched 'Marcus Reed'" in combined
        and "Help me add Marcus Reed as a person." in combined,
        "MCP unmatched-member writes should preserve the name and point to safe person capture.",
    )
    assert_true(
        "I did not open a care case" in care_output
        and "I did not save the note" in note_output
        and "I did not draft a message" in draft_output,
        "MCP unmatched-member writes should clearly state that no unsafe write happened.",
    )
    assert_true(
        "Try list_members" not in combined and "Could not find a member named" not in combined,
        "MCP unmatched-member writes should not dead-end on generic member-search copy.",
    )


async def assert_mcp_member_matching_avoids_ambiguous_writes(mcp_server_module) -> None:
    original_get = mcp_server_module._get
    original_post = mcp_server_module._post
    post_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(path: str, params: dict | None = None):
        if path == "/members/":
            query = (params or {}).get("q")
            if query == "Marcus Reed":
                return [
                    {"id": 1, "full_name": "Marcus Reed", "first_name": "Marcus", "last_name": "Reed"},
                    {"id": 2, "full_name": "Marcus Reed Jr", "first_name": "Marcus", "last_name": "Reed Jr"},
                ]
            if query == "Marcus":
                return [
                    {"id": 1, "full_name": "Marcus Reed", "first_name": "Marcus", "last_name": "Reed"},
                    {"id": 2, "full_name": "Marcus Cole", "first_name": "Marcus", "last_name": "Cole"},
                ]
        return original_get(path, params=params)

    def fake_post(path: str, body: dict[str, Any], params: dict[str, Any] | None = None):
        post_calls.append((path, body))
        return {"id": 606, "category": body.get("category"), "member_name": "Marcus Reed"}

    try:
        mcp_server_module._get = fake_get
        mcp_server_module._post = fake_post
        exact_output = await tool_text(
            mcp_server_module,
            "log_care_event",
            {"member_name": "Marcus Reed", "category": "hospital", "description": "Surgery recovery."},
        )
        ambiguous_output = await tool_text(
            mcp_server_module,
            "log_care_event",
            {"member_name": "Marcus", "category": "hospital", "description": "Surgery recovery."},
        )
    finally:
        mcp_server_module._get = original_get
        mcp_server_module._post = original_post

    assert_true(
        post_calls and post_calls[0][1].get("member_id") == 1,
        "MCP member matching should allow an exact full-name match even when partial results include similar people.",
    )
    assert_true(
        len(post_calls) == 1,
        "MCP member matching should not write when the supplied name is ambiguous.",
    )
    assert_true("Care case opened for Marcus Reed" in exact_output, "Exact MCP member matches should still allow care writes.")
    assert_true(
        "No saved person matched 'Marcus'" in ambiguous_output
        and "I did not open a care case" in ambiguous_output,
        "Ambiguous MCP member names should route to safe capture guidance instead of picking the first result.",
    )


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
                "email": f"mcp-smoke-{suffix}@example.test",
            },
        )
        token = signup["token"]
        account_id = signup["account_id"]

        from mcp_server import server as mcp_server_module

        await assert_mcp_tool_descriptions_avoid_fake_people(mcp_server_module)
        await assert_mcp_missing_name_output_is_pastoral(mcp_server_module)
        await assert_mcp_api_errors_use_safe_detail(mcp_server_module)
        await assert_mcp_formatters_redact_secret_text(mcp_server_module)
        await assert_mcp_incomplete_titles_are_pastoral(mcp_server_module)
        await assert_mcp_empty_states_are_actionable(mcp_server_module)
        await assert_mcp_integration_list_marks_mcp_as_bridge(mcp_server_module)
        await assert_mcp_member_list_handles_incomplete_names(mcp_server_module)
        await assert_mcp_prayer_request_preserves_unmatched_submitter(mcp_server_module)
        await assert_mcp_unmatched_member_writes_are_safe(mcp_server_module)
        await assert_mcp_member_matching_avoids_ambiguous_writes(mcp_server_module)

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
                    "identity": {"id": "pc-smoke", "name": "MCP Planning Center", "people_access_confirmed": True},
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
            verify_output = await tool_text(
                mcp_server_module,
                "verify_integration",
                {"provider": "planning_center"},
            )
        finally:
            mcp_server_module._post = original_post

        assert_true(
            [call[0] for call in precheck_calls[:2]]
            == ["/assistant/integrations/planning_center/sync", "/assistant/integrations/planning_center/verify"],
            "MCP sync should verify and stop when the backend rejects an unchecked connector sync.",
        )
        assert_true("credentials verified" in precheck_output.lower(), "MCP sync precheck should report successful verification.")
        assert_true("without syncing" in precheck_output.lower(), "MCP sync precheck should say no ministry data was synced.")
        assert_true("did not queue actions" in precheck_output.lower(), "MCP sync precheck should say no actions were queued.")
        assert_true("sync this connector again" in precheck_output.lower(), "MCP sync precheck should ask for an explicit follow-up sync.")
        assert_true("Marge API error" not in precheck_output, "MCP sync precheck should not surface the raw sync rejection.")
        assert_true("people access: confirmed" in precheck_output.lower(), "MCP sync precheck should format credential-health labels clearly.")
        assert_true("sample_people_access" not in precheck_output, "MCP sync precheck should not expose sample/demo identity labels.")
        assert_true("people access: confirmed" in verify_output.lower(), "MCP verify output should format credential-health labels clearly.")
        assert_true("sample_people_access" not in verify_output, "MCP verify output should not expose sample/demo identity labels.")

        first_desk = await tool_text(mcp_server_module, "get_assistant_desk", {"mode": "auto"})
        assert_true("Mode: live" in first_desk, "MCP assistant desk should stay live for a real workspace.")
        assert_true("Profile:" in first_desk, "MCP assistant desk should expose profile completion.")

        messages = [
            "I am a solo pastor and we average about 90 people each week.",
            "We are a neighborhood church with many new families and a tired volunteer core.",
            "Our church tradition is non-denominational with Baptist roots; keep language plain for guests.",
            "Visitor follow-up and hospital care are where people slip through the cracks.",
            "My first priority this month is closing loops with guests and hospital follow-up.",
            "Nudge me gently, protect sermon prep, and surface the people I am most likely to miss.",
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
        assert_true("nudge me gently" in combined.lower(), "MCP first-run output should reflect the pastor's personal support style.")
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
        assert_true("Support style: Nudge me gently" in profile, "MCP ministry profile should show saved support preferences.")
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
