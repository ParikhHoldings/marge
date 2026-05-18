#!/usr/bin/env python3
"""
Smoke-test Marge's non-Google connected provider syncs without live providers.

This verifies that Planning Center, Microsoft 365, and Breeze are more than
placeholder statuses:

- Planning Center and Microsoft 365 use OAuth setup/callback with encrypted,
  user-scoped credentials.
- Breeze can use encrypted workspace API-key credentials instead of env-only setup.
- Safe verification performs identity/config checks without syncing ministry
  data.
- Sync creates compact ConnectedContextItem rows.
- Sync queues pastor-review actions for new people, inbox items, and pastoral
  calendar events.
- Approved review actions can turn synced people and inbox items into local
  Marge memory or reviewable drafts without writing to external providers.
- Generic mailbox and calendar sync prompts choose the connected provider from
  the workspace context instead of assuming Google.
- The live-provider readiness counter only accepts fully verified external
  provider responses; MCP, weak HTTP-200 responses, and secret-shaped identity
  metadata do not count.
- Live-provider readiness verification refuses to run unscoped without a
  workspace token, even against a local API.

Usage:
  .venv/bin/python scripts/smoke_connected_providers.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services.secure_tokens import decrypt_token_payload, generate_encryption_key
from scripts import verify_live_integrations as live_verifier
from scripts.verify_live_integrations import (
    checked_local_bridges,
    evidence_payload,
    identity_has_signal,
    identity_metadata_key_paths,
    is_workspace_required_error,
    is_local_api_url,
    live_provider_next_actions,
    live_provider_rerun_command,
    print_human,
    redact_sensitive_text,
    sanitize_evidence_value,
    snapshot_workspace,
    validate_workspace_scope_for_live_provider,
    verify_provider,
    verified_external_providers,
    workspace_token_message,
)

os.environ["MARGE_ENCRYPTION_KEY"] = generate_encryption_key()
os.environ["MARGE_REQUIRE_ACCOUNT_TOKEN"] = "true"
os.environ["PLANNING_CENTER_CLIENT_ID"] = "local-smoke-pco-client"
os.environ["PLANNING_CENTER_CLIENT_SECRET"] = "local-smoke-pco-secret"
os.environ["PLANNING_CENTER_REDIRECT_URI"] = "http://testserver/assistant/integrations/planning_center/callback"
os.environ["MICROSOFT_CLIENT_ID"] = "local-smoke-ms-client"
os.environ["MICROSOFT_CLIENT_SECRET"] = "local-smoke-ms-secret"
os.environ["MICROSOFT_REDIRECT_URI"] = "http://testserver/assistant/integrations/microsoft_365/callback"
os.environ.pop("BREEZE_API_KEY", None)
os.environ.pop("BREEZE_BASE_URL", None)

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
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
from app.routers import assistant as assistant_router


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_json(client: TestClient, method: str, path: str, token: str | None = None, **kwargs) -> Any:
    headers = kwargs.pop("headers", {})
    if token:
        headers["X-Marge-Account-Token"] = token
    response = client.request(method, path, headers=headers, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    assert_true(response.status_code < 400, f"{method} {path} failed with {response.status_code}: {body}")
    return body


def request_json_status(client: TestClient, method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, Any]:
    headers = kwargs.pop("headers", {})
    if token:
        headers["X-Marge-Account-Token"] = token
    response = client.request(method, path, headers=headers, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


def ministry_record_snapshot(client: TestClient, token: str) -> dict[str, Any]:
    return {
        "members": request_json(client, "GET", "/members/?limit=100", token=token),
        "visitors": request_json(client, "GET", "/visitors/?limit=100", token=token),
        "care_cases": request_json(client, "GET", "/care/?limit=100", token=token),
        "prayer_requests": request_json(client, "GET", "/care/prayers/?limit=100", token=token),
        "connected_context": request_json(client, "GET", "/assistant/connected-items?limit=100", token=token),
    }


def cleanup_account(account_id: int) -> None:
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


def oauth_state_from_setup(setup: dict) -> str:
    authorization_url = setup.get("authorization_url") or ""
    state = parse_qs(urlparse(authorization_url).query).get("state", [None])[0]
    assert_true(bool(state), f"{setup.get('provider')} setup should return an OAuth state.")
    return state


def main() -> None:
    account_ids: list[int] = []
    original_exchange = assistant_router._exchange_oauth_code
    original_planning_get = assistant_router._planning_center_get
    original_microsoft_get = assistant_router._microsoft_graph_get
    original_microsoft_post = assistant_router._microsoft_graph_post
    original_breeze_get = assistant_router._breeze_get

    token_payloads = {
        "planning_center": {
            "access_token": "pco-access-token-secret-smoke",
            "refresh_token": "pco-refresh-token-secret-smoke",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "people calendar groups services",
        },
        "microsoft_365": {
            "access_token": "ms-access-token-secret-smoke",
            "refresh_token": "ms-refresh-token-secret-smoke",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "Mail.Read Mail.ReadWrite Calendars.Read offline_access",
        },
    }

    def fake_exchange(definition: dict, code: str, redirect_uri: str) -> dict:
        provider = definition["provider"]
        assert_true(provider in token_payloads, f"Unexpected OAuth provider in smoke: {provider}")
        assert_true(code == f"{provider}-code", f"{provider} callback should pass the provider code through.")
        expected_redirect = os.environ[definition["redirect_uri_env"]]
        assert_true(redirect_uri == expected_redirect, f"{provider} callback should use the stored redirect URI.")
        return dict(token_payloads[provider])

    def fake_planning_center_get(token: str, path: str, params: dict | None = None) -> dict:
        assert_true(token == token_payloads["planning_center"]["access_token"], "Planning Center calls should use the decrypted access token.")
        if path == "/people/v2/me":
            return {
                "data": {
                    "id": "pco-me-1",
                    "attributes": {
                        "name": "Pastor Connected Smoke",
                        "primary_email_address": "pastor.connected@example.test",
                    },
                }
            }
        if path == "/people/v2/people":
            now = datetime.now(UTC).replace(tzinfo=None)
            return {
                "data": [
                    {
                        "id": "pco-person-1",
                        "attributes": {
                            "name": "Elena Morris",
                            "first_name": "Elena",
                            "last_name": "Morris",
                            "membership": "Visitor",
                            "status": "New",
                            "created_at": (now - timedelta(days=2)).isoformat() + "Z",
                            "updated_at": (now - timedelta(hours=2)).isoformat() + "Z",
                        },
                    }
                ]
            }
        if path == "/calendar/v2/event_instances":
            start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=3)
            return {
                "data": [
                    {
                        "id": "pco-event-1",
                        "attributes": {
                            "name": "Visitor lunch with Elena",
                            "description": "<p>New family pastoral follow-up after Sunday.</p>",
                            "location": "Fellowship hall",
                            "starts_at": start.isoformat() + "Z",
                            "ends_at": (start + timedelta(hours=1)).isoformat() + "Z",
                        },
                    }
                ]
            }
        raise AssertionError(f"Unexpected Planning Center path: {path}")

    def fake_microsoft_graph_get(token: str, path: str, params: dict | None = None, headers: dict | None = None) -> dict:
        assert_true(token == token_payloads["microsoft_365"]["access_token"], "Microsoft calls should use the decrypted access token.")
        if path == "/me":
            return {
                "id": "ms-me-1",
                "displayName": "Pastor Connected Smoke",
                "userPrincipalName": "pastor.connected@example.test",
                "mail": "pastor.connected@example.test",
            }
        if path == "/me/messages":
            received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
            return {
                "value": [
                    {
                        "id": "ms-message-1",
                        "conversationId": "ms-conversation-1",
                        "subject": "Prayer request after group",
                        "from": {"emailAddress": {"name": "Marcus Hill", "address": "marcus@example.test"}},
                        "receivedDateTime": received_at.isoformat() + "Z",
                        "bodyPreview": "Could you pray with me about my dad's surgery this week?",
                        "isRead": False,
                        "webLink": "https://outlook.example.test/message/ms-message-1",
                    }
                ]
            }
        if path == "/me/calendarView":
            start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
            return {
                "value": [
                    {
                        "id": "ms-event-1",
                        "subject": "Hospital visit with Marcus",
                        "bodyPreview": "Pastoral care visit before surgery.",
                        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
                        "end": {"dateTime": (start + timedelta(hours=1)).isoformat(), "timeZone": "UTC"},
                        "location": {"displayName": "County Hospital"},
                        "attendees": [{"emailAddress": {"name": "Marcus Hill", "address": "marcus@example.test"}}],
                        "organizer": {"emailAddress": {"name": "Pastor Connected Smoke", "address": "pastor.connected@example.test"}},
                        "webLink": "https://outlook.example.test/event/ms-event-1",
                        "isCancelled": False,
                    }
                ]
            }
        raise AssertionError(f"Unexpected Microsoft Graph path: {path}")

    def fake_microsoft_graph_post(token: str, path: str, json_body: dict | None = None, headers: dict | None = None) -> dict:
        assert_true(token == token_payloads["microsoft_365"]["access_token"], "Microsoft writeback should use the decrypted access token.")
        body = json_body or {}
        if path == "/me/messages":
            assert_true(body.get("subject") == "Re: Prayer request after group", "Outlook draft should preserve the reviewable subject.")
            recipients = body.get("toRecipients") or []
            assert_true(recipients[0]["emailAddress"]["address"] == "marcus@example.test", "Outlook draft should target the synced sender.")
            assert_true("dad's surgery" in ((body.get("body") or {}).get("content") or ""), "Outlook draft should use the approved draft body.")
            return {"id": "outlook-draft-smoke-1", "webLink": "https://outlook.example.test/draft/outlook-draft-smoke-1"}
        if path == "/me/events":
            assert_true(body.get("subject") == "Hospital follow-up with Marcus", "Outlook event should preserve the approved subject.")
            assert_true((body.get("start") or {}).get("dateTime") == "2026-05-18T15:00:00", "Outlook event should preserve the approved start time.")
            assert_true((body.get("end") or {}).get("dateTime") == "2026-05-18T16:00:00", "Outlook event should preserve the approved end time.")
            assert_true((body.get("location") or {}).get("displayName") == "County Hospital", "Outlook event should preserve location.")
            attendees = body.get("attendees") or []
            assert_true(attendees[0]["emailAddress"]["address"] == "marcus@example.test", "Outlook event should preserve attendees.")
            return {"id": "outlook-event-smoke-1", "webLink": "https://outlook.example.test/event/outlook-event-smoke-1"}
        raise AssertionError(f"Unexpected Microsoft Graph POST path: {path}")

    def fake_breeze_get(path: str, params: dict | None = None, **kwargs):
        normalized_path = path if path.startswith("/") else f"/{path}"
        if normalized_path == "/people/":
            return [
                {
                    "id": "breeze-person-1",
                    "first_name": "Nina",
                    "last_name": "Brooks",
                    "name": "Nina Brooks",
                    "created_on": (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_on": datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    "details": {
                        "status": "New Guest",
                        "email": "nina@example.test",
                        "mobile": "555-0100",
                    },
                }
            ]
        if normalized_path == "/events":
            start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=4)
            return [
                {
                    "id": "breeze-event-1",
                    "name": "Prayer class follow-up",
                    "description": "Care and prayer follow-up with new members.",
                    "location": "Room 2",
                    "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": (start + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "calendar_name": "Pastoral Care",
                }
            ]
        raise AssertionError(f"Unexpected Breeze path: {path}")

    try:
        assistant_router._exchange_oauth_code = fake_exchange
        assistant_router._planning_center_get = fake_planning_center_get
        assistant_router._microsoft_graph_get = fake_microsoft_graph_get
        assistant_router._microsoft_graph_post = fake_microsoft_graph_post
        assistant_router._breeze_get = fake_breeze_get

        with TestClient(app) as client:
            signup = request_json(
                client,
                "POST",
                "/assistant/signup",
                json={
                    "pastor_name": "Pastor Connected Smoke",
                    "church_name": "Connected Provider Smoke Church",
                    "email": "connected-provider@example.test",
                    "role_title": "Solo Pastor",
                    "congregation_size": "95",
                    "church_context": "Neighborhood church with new families and a thin care team.",
                    "faith_tradition": "Non-denominational with Baptist roots; use plain language with guests.",
                    "followup_pain": "Visitors, prayer requests, and hospital follow-up.",
                    "ministry_priorities": "Close loops with first-time guests and private prayer needs.",
                    "support_preferences": "Nudge me gently and surface the people I am most likely to miss.",
                    "tools_in_use": "Planning Center, Microsoft 365, Breeze",
                    "communication_style": "warm and brief",
                    "weekly_rhythm": "Protect sermon prep Thursday mornings.",
                    "guardrails": "Do not send messages or write to external systems without approval.",
                },
            )
            token = signup["token"]
            account_ids.append(signup["account_id"])

            other_signup = request_json(
                client,
                "POST",
                "/assistant/signup",
                json={
                    "pastor_name": "Pastor Rock Scope Smoke",
                    "church_name": "Rock Scope Smoke Church",
                    "email": "rock-scope@example.test",
                },
            )
            other_token = other_signup["token"]
            account_ids.append(other_signup["account_id"])

            shared_rock_id = "rock-shared-person-1"
            first_rock_member = request_json(
                client,
                "POST",
                "/members/",
                token=token,
                json={"first_name": "Shared", "last_name": "Rock", "rock_id": shared_rock_id},
            )
            second_rock_member = request_json(
                client,
                "POST",
                "/members/",
                token=other_token,
                json={"first_name": "Shared", "last_name": "Rock", "rock_id": shared_rock_id},
            )
            assert_true(first_rock_member["id"] != second_rock_member["id"], "Different workspaces should store separate members even when Rock IDs match.")
            duplicate_rock_member = client.post(
                "/members/",
                headers={"X-Marge-Account-Token": token},
                json={"first_name": "Duplicate", "last_name": "Rock", "rock_id": shared_rock_id},
            )
            assert_true(duplicate_rock_member.status_code == 409, "Duplicate Rock IDs should be rejected inside the same workspace.")
            first_rock_search = request_json(client, "GET", "/members/?search=Shared%20Rock&limit=10", token=token)
            second_rock_search = request_json(client, "GET", "/members/?search=Shared%20Rock&limit=10", token=other_token)
            assert_true(len(first_rock_search) == 1 and first_rock_search[0]["rock_id"] == shared_rock_id, "Primary workspace should see only its Rock-scoped member.")
            assert_true(len(second_rock_search) == 1 and second_rock_search[0]["rock_id"] == shared_rock_id, "Second workspace should see only its Rock-scoped member.")

            breeze_api_key = "workspace-breeze-secret-smoke"
            breeze_credentials = request_json(
                client,
                "POST",
                "/assistant/integrations/breeze/credentials",
                token=token,
                json={"api_key": breeze_api_key, "base_url": "https://example.breezechms.com"},
            )
            assert_true(breeze_credentials["status"] == "configured", "Breeze API-key setup should store encrypted workspace credentials.")
            assert_true(breeze_api_key not in json.dumps(breeze_credentials), "Breeze credential setup response must not echo the API key.")

            for provider in ["planning_center", "microsoft_365"]:
                setup = request_json(client, "POST", f"/assistant/integrations/{provider}/start", token=token)
                assert_true(setup["status"] == "ready_to_authorize", f"{provider} should be ready for OAuth authorization.")
                state = oauth_state_from_setup(setup)
                callback = client.get(
                    f"/assistant/integrations/{provider}/callback",
                    params={"code": f"{provider}-code", "state": state},
                )
                assert_true(callback.status_code == 200, f"{provider} callback should succeed: {callback.text}")
                assert_true(token_payloads[provider]["access_token"] not in callback.text, f"{provider} callback must not expose access tokens.")
                assert_true(token_payloads[provider]["refresh_token"] not in callback.text, f"{provider} callback must not expose refresh tokens.")
                assert_true("Check credentials before syncing ministry data" in callback.text, f"{provider} callback should tell pastors to check credentials before sync.")

            integrations = request_json(client, "GET", "/assistant/integrations", token=token)
            by_provider = {item["provider"]: item for item in integrations}
            assert_true(by_provider["planning_center"]["status"] == "connected", "Planning Center should report connected after OAuth callback.")
            assert_true(by_provider["planning_center"]["credential_scope"] == "user", "Planning Center OAuth should be user-scoped.")
            assert_true(
                "check credentials before syncing ministry data" in by_provider["planning_center"]["config_hint"].lower(),
                "Unverified Planning Center status should keep the credential-check boundary visible.",
            )
            assert_true(by_provider["microsoft_365"]["status"] == "connected", "Microsoft 365 should report connected after OAuth callback.")
            assert_true(by_provider["microsoft_365"]["credential_scope"] == "user", "Microsoft 365 OAuth should be user-scoped.")
            assert_true(
                "check credentials before syncing ministry data" in by_provider["microsoft_365"]["config_hint"].lower(),
                "Unverified Microsoft 365 status should keep the credential-check boundary visible.",
            )
            assert_true(by_provider["breeze"]["status"] == "configured", "Breeze should be configured from encrypted workspace credentials.")
            assert_true(by_provider["breeze"]["credential_scope"] == "workspace", "Breeze API-key credentials should be workspace-scoped.")
            integration_text = json.dumps(integrations)
            for payload in token_payloads.values():
                assert_true(payload["access_token"] not in integration_text, "Integration statuses must not expose access tokens.")
                assert_true(payload["refresh_token"] not in integration_text, "Integration statuses must not expose refresh tokens.")
            assert_true(breeze_api_key not in integration_text, "Integration statuses must not expose workspace API keys.")

            db = SessionLocal()
            try:
                for provider, payload in token_payloads.items():
                    credential = (
                        db.query(IntegrationCredential)
                        .filter(IntegrationCredential.account_id == signup["account_id"], IntegrationCredential.provider == provider)
                        .one()
                    )
                    assert_true(credential.user_id == signup["current_user"]["id"], f"{provider} credential should be stored for the initiating Marge user.")
                    assert_true(payload["access_token"] not in credential.token_ciphertext, f"{provider} credential should store ciphertext, not plaintext.")
                    decrypted = decrypt_token_payload(credential.token_ciphertext)
                    assert_true(decrypted["access_token"] == payload["access_token"], f"{provider} credential should decrypt to the access token payload.")

                breeze_credential = (
                    db.query(IntegrationCredential)
                    .filter(IntegrationCredential.account_id == signup["account_id"], IntegrationCredential.provider == "breeze")
                    .one_or_none()
                )
                assert_true(breeze_credential is not None, "Breeze should persist an encrypted workspace credential row.")
                assert_true(breeze_credential.user_id is None, "Breeze API-key credential should be workspace-scoped, not user-scoped.")
                assert_true(breeze_api_key not in breeze_credential.token_ciphertext, "Breeze API key should be encrypted at rest.")
                breeze_payload = decrypt_token_payload(breeze_credential.token_ciphertext)
                assert_true(breeze_payload["api_key"] == breeze_api_key, "Breeze API-key credential should decrypt only in-process.")
            finally:
                db.close()

            pre_verify_ministry = ministry_record_snapshot(client, token)
            sync_before_verify = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Sync Planning Center before lunch.", "mode": "live"},
            )
            assert_true(sync_before_verify["intent"] == "integration_verified_before_sync", "Chat sync request should safely verify first when credentials have not been checked.")
            assert_true("without syncing" in sync_before_verify["reply"].lower(), "Pre-sync verification reply should say it did not import ministry data.")
            assert_true("sync it again" in sync_before_verify["reply"].lower(), "Pre-sync verification should ask for an explicit follow-up sync request.")
            assert_true(sync_before_verify["actions"] == [], "Pre-sync verification should not queue any review actions.")
            context_after_precheck = request_json(client, "GET", "/assistant/connected-items?limit=100", token=token)
            assert_true(context_after_precheck == [], "Pre-sync chat verification should not create connected ministry context.")

            chat_verification = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Check Planning Center credentials before syncing.", "mode": "live"},
            )
            assert_true(
                chat_verification["intent"] == "integration_verified",
                f"Chat should understand connector credential checks as a safe verify action. Got: {json.dumps(chat_verification, default=str)}",
            )
            assert_true("without syncing" in chat_verification["reply"].lower(), "Chat verification reply should say it did not sync ministry data.")
            assert_true("did not queue any actions" in chat_verification["reply"].lower(), "Chat verification should not pretend it prepared review work.")
            pco_after_chat_verify = next(item for item in request_json(client, "GET", "/assistant/integrations", token=token) if item["provider"] == "planning_center")
            assert_true(bool(pco_after_chat_verify["verified_at"]), "Chat verification should mark Planning Center as checked before sync.")

            def fake_empty_breeze_get(path: str, params: dict | None = None, **kwargs):
                normalized_path = path if path.startswith("/") else f"/{path}"
                if normalized_path == "/people/":
                    return []
                return fake_breeze_get(path, params, **kwargs)

            assistant_router._breeze_get = fake_empty_breeze_get
            weak_breeze_verification = client.post(
                "/assistant/integrations/breeze/verify",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(
                weak_breeze_verification.status_code == 502,
                "Breeze verification should fail instead of marking credentials checked when no usable access signal is returned.",
            )
            assert_true(
                "non-secret identity or permission metadata" in weak_breeze_verification.text,
                "Weak provider verification should explain that no affirmative credential signal was returned.",
            )
            breeze_after_weak_verify = next(item for item in request_json(client, "GET", "/assistant/integrations", token=token) if item["provider"] == "breeze")
            assert_true(
                not breeze_after_weak_verify["verified_at"],
                "Failed Breeze verification must not set verified_at.",
            )
            weak_breeze_chat_sync = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Sync Breeze before staff meeting.", "mode": "live"},
            )
            assert_true(
                weak_breeze_chat_sync["intent"] == "integration_verify_failed_before_sync",
                "Chat sync should stop at a failed Breeze credential check instead of syncing.",
            )
            assert_true(
                "safe credential check" in weak_breeze_chat_sync["reply"].lower()
                and "no ministry data" in weak_breeze_chat_sync["reply"].lower(),
                "Failed chat pre-sync verification should explain that no ministry data was imported.",
            )
            assert_true(
                any(
                    action.get("type") == "integration_setup"
                    and action.get("provider") == "breeze"
                    and action.get("action") == "Check credentials"
                    for action in weak_breeze_chat_sync.get("actions", [])
                ),
                "Failed chat pre-sync verification should attach the Breeze credential-check card.",
            )
            assert_true(
                not any(action.get("type") in {"email_draft", "pastoral_followup", "calendar_block", "person_review"} for action in weak_breeze_chat_sync.get("actions", [])),
                "Failed chat pre-sync verification should not queue review actions.",
            )
            breeze_after_weak_chat = next(item for item in request_json(client, "GET", "/assistant/integrations", token=token) if item["provider"] == "breeze")
            assert_true(
                not breeze_after_weak_chat["verified_at"],
                "Failed chat pre-sync verification must not mark Breeze as checked.",
            )
            assert_true(
                request_json(client, "GET", "/assistant/connected-items?limit=100", token=token) == [],
                "Failed chat pre-sync verification must not create connected context.",
            )
            assert_true(
                assistant_router._redact_secret_text("apiKey=leaked-chat-secret bearer leakedbearertoken123456")
                == "apiKey=<redacted> bearer <redacted>",
                "Backend chat redactor should remove token-shaped provider failure text.",
            )
            original_verify_breeze = assistant_router._verify_breeze

            def leaky_breeze_verify_failure(*_args, **_kwargs):
                raise HTTPException(status_code=502, detail="Provider echoed apiKey=leaked-breeze-secret.")

            try:
                assistant_router._verify_breeze = leaky_breeze_verify_failure
                leaky_breeze_chat_sync = request_json(
                    client,
                    "POST",
                    "/assistant/chat",
                    token=token,
                    json={"message": "Sync Breeze before staff meeting.", "mode": "live"},
                )
            finally:
                assistant_router._verify_breeze = original_verify_breeze

            assert_true(
                leaky_breeze_chat_sync["intent"] == "integration_verify_failed_before_sync",
                "Chat sync should still stop at credential verification when provider failure text includes a secret.",
            )
            assert_true(
                "leaked-breeze-secret" not in leaky_breeze_chat_sync["reply"]
                and "apiKey=<redacted>" in leaky_breeze_chat_sync["reply"],
                "Chat pre-sync verification failures should redact token-shaped provider error details.",
            )
            assistant_router._breeze_get = fake_breeze_get

            verification_results = []
            for provider in ["planning_center", "microsoft_365", "breeze"]:
                verification = request_json(client, "POST", f"/assistant/integrations/{provider}/verify", token=token, json={})
                assert_true(verification["status"] == "verified", f"{provider} should verify without syncing ministry data.")
                assert_true(bool(verification["identity"]), f"{provider} verification should return non-secret identity/config metadata.")
                if provider == "breeze":
                    assert_true(
                        verification["identity"].get("people_access_confirmed") is True,
                        "Breeze verification should expose a real credential-health label for people access.",
                    )
                    assert_true(
                        "sample_people_access" not in verification["identity"],
                        "Breeze verification metadata should not use sample/demo wording.",
                    )
                verification_results.append({
                    "provider": provider,
                    "ok": True,
                    "status": verification["status"],
                    "verified_at": verification["verified_at"],
                    "identity_keys": sorted((verification.get("identity") or {}).keys()),
                    "identity_signal": identity_has_signal(verification.get("identity") or {}),
                })
            assert_true(
                len(verified_external_providers(verification_results)) == 3,
                "Pilot live-provider readiness should count verified external church-tool providers.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "mcp",
                    "ok": True,
                    "kind": "local_agent_bridge",
                    "status": "bridge_available",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["bridge_available"],
                    "identity_signal": True,
                }]),
                "MCP bridge availability must not satisfy the live external-provider readiness requirement.",
            )
            mcp_bridge_result = {
                "provider": "mcp",
                "display_name": "MCP",
                "ok": True,
                "kind": "local_agent_bridge",
                "status": "bridge_available",
                "message": "MCP bridge checked for local LLM clients.",
                "identity_keys": ["bridge_available"],
                "identity_signal": True,
            }
            assert_true(
                len(checked_local_bridges([mcp_bridge_result])) == 1,
                "Live verifier evidence should count MCP separately as a local bridge check.",
            )
            mcp_only_evidence = evidence_payload(
                "https://marge.example.com",
                [mcp_bridge_result],
                [],
                None,
                True,
                ["Connect or configure at least one external provider."],
            )
            assert_true(
                mcp_only_evidence["external_provider_checks"] == []
                and mcp_only_evidence["local_bridge_checks"] == [mcp_bridge_result]
                and mcp_only_evidence["external_verified_count"] == 0
                and mcp_only_evidence["local_bridge_checked_count"] == 1
                and mcp_only_evidence["live_provider_ready"] is False
                and mcp_only_evidence["no_sync_side_effect_check_passed"] is None,
                "Live verifier JSON evidence should split MCP bridge checks from external provider checks.",
            )
            assert_true(
                isinstance(mcp_only_evidence.get("generated_at"), str)
                and "T" in mcp_only_evidence["generated_at"]
                and mcp_only_evidence["generated_at"].endswith("Z"),
                "Live verifier JSON evidence should timestamp when the report was generated.",
            )
            redacted_text = redact_sensitive_text(
                "access_token=secret-token apiKey=camel-secret clientSecret=client-secret "
                "Authorization: Bearer eyJabcdefghijklmnopqrstuvwx.eyJabcdefghijklmnopqrstuvwx.signature123 "
                "marge_sess_very-secret-session ya29.google-secret-token"
            )
            assert_true(
                "secret-token" not in redacted_text
                and "camel-secret" not in redacted_text
                and "client-secret" not in redacted_text
                and "very-secret-session" not in redacted_text
                and "google-secret-token" not in redacted_text
                and "<redacted" in redacted_text,
                "Live verifier should redact obvious token-shaped strings before printing or saving evidence.",
            )
            leaky_evidence = evidence_payload(
                "https://marge.example.com",
                [{
                    "provider": "planning_center",
                    "display_name": "Planning Center",
                    "ok": False,
                    "kind": "external_provider",
                    "status": "failed",
                    "message": "Provider echoed api_key=leaked-key-value and bearer leakedbearertoken123456.",
                    "identity_keys": [],
                    "identity_signal": False,
                }],
                [],
                None,
                True,
                ["Rerun with MARGE_ACCOUNT_TOKEN=marge_sess_leaked-session-token."],
                error="Upstream returned client_secret=leaked-client-secret.",
            )
            leaky_evidence_json = json.dumps(leaky_evidence)
            assert_true(
                "leaked-key-value" not in leaky_evidence_json
                and "leakedbearertoken" not in leaky_evidence_json
                and "marge_sess_leaked" not in leaky_evidence_json
                and "leaked-client-secret" not in leaky_evidence_json,
                "Live verifier evidence payloads should redact secret-shaped values before writing JSON.",
            )
            assert_true(
                sanitize_evidence_value({"message": "refreshToken=refresh-secret"})["message"] == "refreshToken=<redacted>",
                "Live verifier recursive evidence sanitizer should redact nested secret-shaped strings.",
            )
            original_live_request_json_for_message = live_verifier.request_json

            def leaky_success_request_json(method: str, url: str, token: str | None = None, payload: dict[str, Any] | None = None):
                return 200, {
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity": {"email": "pastor@example.test"},
                    "message": "Provider verified; access_token=leaked-success-token.",
                }

            try:
                live_verifier.request_json = leaky_success_request_json
                leaky_success_result = verify_provider("https://marge.example.com", "marge_sess_safe", "google_workspace", {
                    "google_workspace": {"display_name": "Google Workspace"},
                })
            finally:
                live_verifier.request_json = original_live_request_json_for_message

            leaky_success_human = StringIO()
            with redirect_stdout(leaky_success_human):
                print_human("https://marge.example.com", [leaky_success_result], [])
            assert_true(
                "leaked-success-token" not in leaky_success_result["message"]
                and "leaked-success-token" not in leaky_success_human.getvalue(),
                "Live verifier should redact token-shaped values from successful provider messages before human output.",
            )
            external_ready_evidence = evidence_payload(
                "https://marge.example.com",
                [{
                    "provider": "planning_center",
                    "display_name": "Planning Center",
                    "ok": True,
                    "kind": "external_provider",
                    "status": "verified",
                    "message": "Planning Center credentials verified without syncing.",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["id"],
                    "identity_signal": True,
                    "sensitive_identity_keys": [],
                }],
                [],
                {"ok": True},
                True,
                [],
            )
            assert_true(
                external_ready_evidence["external_verified_count"] == 1
                and external_ready_evidence["live_provider_ready"] is True
                and external_ready_evidence["no_sync_side_effect_check_passed"] is True,
                "Live verifier evidence should explicitly mark a verified external provider as ready only when no-sync checks pass.",
            )
            missing_side_effect_evidence = evidence_payload(
                "https://marge.example.com",
                external_ready_evidence["external_provider_checks"],
                [],
                None,
                True,
                ["Rerun without --skip-side-effect-check."],
            )
            assert_true(
                missing_side_effect_evidence["external_verified_count"] == 1
                and missing_side_effect_evidence["live_provider_ready"] is False
                and missing_side_effect_evidence["no_sync_side_effect_check_passed"] is None,
                "Live verifier evidence should not mark a provider ready unless the no-sync side-effect check ran.",
            )
            missing_side_effect_human = StringIO()
            with redirect_stdout(missing_side_effect_human):
                print_human(
                    "https://marge.example.com",
                    external_ready_evidence["external_provider_checks"],
                    [],
                    None,
                )
            assert_true(
                "No-sync side-effect check: not run" in missing_side_effect_human.getvalue()
                and "cannot prove live provider readiness" in missing_side_effect_human.getvalue(),
                "Live verifier human output should say when side-effect proof is missing.",
            )
            side_effect_failure_evidence = evidence_payload(
                "https://marge.example.com",
                external_ready_evidence["external_provider_checks"],
                [],
                {"ok": False},
                True,
                ["Fix verify endpoints so Check credentials does not import context."],
            )
            assert_true(
                side_effect_failure_evidence["external_verified_count"] == 1
                and side_effect_failure_evidence["live_provider_ready"] is False
                and side_effect_failure_evidence["no_sync_side_effect_check_passed"] is False,
                "Live verifier evidence should not mark a provider ready if verification mutates workspace data.",
            )
            human_output = StringIO()
            with redirect_stdout(human_output):
                print_human("https://marge.example.com", [mcp_bridge_result], [])
            human_text = human_output.getvalue()
            assert_true(
                "No external providers were verified." in human_text
                and "Local agent bridge checks (not live providers):" in human_text
                and "\nVerified:" not in human_text,
                "Live verifier human output should not present MCP bridge checks as external provider verification.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "google_workspace",
                    "ok": True,
                    "status": "connected",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["email"],
                }]),
                "HTTP-200 connector responses without status=verified must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "planning_center",
                    "ok": True,
                    "status": "verified",
                    "identity_keys": ["id"],
                }]),
                "Connector responses without verified_at must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "microsoft_365",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": [],
                }]),
                "Connector responses without non-secret identity metadata must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "breeze",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["people_access_confirmed"],
                    "identity_signal": False,
                }]),
                "Connector responses with only false credential-health metadata must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "google_workspace",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["email"],
                }]),
                "Connector readiness should require an explicit affirmative identity_signal from the verifier.",
            )
            assert_true(
                identity_has_signal({"people_access_confirmed": False}) is False,
                "False-only connector health metadata should not count as an affirmative identity signal.",
            )
            assert_true(
                identity_has_signal({"messages_total": 0, "threads_total": 0}) is False,
                "Zero-only numeric connector metadata should not count as an affirmative identity signal.",
            )
            assert_true(
                assistant_router._identity_has_signal({"messages_total": 0, "threads_total": 0}) is False,
                "Backend verification should reject zero-only numeric connector metadata.",
            )
            assert_true(
                identity_has_signal({"messages_total": -1}) is False,
                "Negative numeric connector metadata should not count as an affirmative identity signal.",
            )
            assert_true(
                assistant_router._identity_has_signal({"messages_total": -1}) is False,
                "Backend verification should reject negative numeric connector metadata.",
            )
            assert_true(
                identity_has_signal({"email": "owner@example.test"}) is True,
                "Non-empty non-secret identity metadata should count as an affirmative identity signal.",
            )
            assert_true(
                identity_has_signal({"messages_total": 12}) is True,
                "Positive numeric connector metadata can count as an affirmative signal.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "planning_center",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["id", "access_token"],
                }]),
                "Connector responses with secret-shaped identity metadata must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "planning_center",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["profile"],
                    "identity_key_paths": identity_metadata_key_paths({"profile": {"access_token": "leaked"}}),
                }]),
                "Connector responses with nested secret-shaped identity metadata must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "google_workspace",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["workspace_token"],
                    "identity_signal": True,
                }]),
                "Connector responses with generic token-shaped identity keys must not satisfy live-provider readiness.",
            )
            assert_true(
                not verified_external_providers([{
                    "provider": "breeze",
                    "ok": True,
                    "status": "verified",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["apiKey"],
                    "identity_signal": True,
                }]),
                "Connector responses with camelCase API-key identity metadata must not satisfy live-provider readiness.",
            )
            assert_true(
                assistant_router._sensitive_identity_keys({
                    "profile": {"access_token": "leaked"},
                    "workspace_token": "leaked",
                    "apiKey": "leaked",
                    "email": "safe@example.test",
                }) == ["apiKey", "profile.access_token", "workspace_token"],
                "Backend verify responses should reject secret-shaped identity metadata before returning it.",
            )
            assert_true(
                assistant_router._sensitive_identity_value_paths({
                    "profile": {"email": "accessToken=leaked-token-value"},
                    "nested": [{"message": "Bearer leakedbearertoken123456"}],
                }) == ["nested[0].message", "profile.email"],
                "Backend verify responses should reject secret-shaped identity values before returning them.",
            )
            original_verify_planning_center = assistant_router._verify_planning_center

            def leaky_planning_center_identity(_token: str) -> dict:
                return {"email": "apiKey=leaked-provider-token", "id": "safe-id"}

            try:
                assistant_router._verify_planning_center = leaky_planning_center_identity
                leaky_identity_response = client.post(
                    "/assistant/integrations/planning_center/verify",
                    headers={"X-Marge-Account-Token": token},
                    json={},
                )
            finally:
                assistant_router._verify_planning_center = original_verify_planning_center

            assert_true(
                leaky_identity_response.status_code == 500,
                "Backend verification should reject secret-shaped identity values before returning them to the browser.",
            )
            assert_true(
                "profile.email" not in leaky_identity_response.text
                and "leaked-provider-token" not in leaky_identity_response.text
                and "email" in leaky_identity_response.text,
                "Unsafe identity-value failures should name the field path without echoing the leaked value.",
            )
            no_live_provider_actions = live_provider_next_actions(
                "https://marge.example.com",
                {"google_workspace": {"display_name": "Google Workspace", "status": "planned"}},
                [{
                    "provider": "mcp",
                    "display_name": "MCP",
                    "ok": True,
                    "kind": "local_agent_bridge",
                    "status": "bridge_available",
                    "verified_at": "2026-05-17T00:00:00",
                    "identity_keys": ["bridge_available"],
                    "identity_signal": True,
                }],
                [{"provider": "google_workspace", "status": "planned", "reason": "not connected/configured"}],
                require_live_provider=True,
                evidence_file="artifacts/live-connector-verification.json",
            )
            assert_true(
                any("Connect or configure at least one external provider" in action for action in no_live_provider_actions),
                "Live-provider verifier should tell operators to connect/configure a real external church tool.",
            )
            assert_true(
                any("MCP does not count" in action for action in no_live_provider_actions),
                "Live-provider verifier should keep the MCP-not-live-provider boundary visible.",
            )
            assert_true(
                any("--include-mcp --require-live-provider" in action for action in no_live_provider_actions),
                "Live-provider verifier should print the exact rerun command.",
            )
            assert_true(
                any("--evidence-file artifacts/live-connector-verification.json" in action for action in no_live_provider_actions),
                "Live-provider verifier rerun guidance should preserve the evidence file path.",
            )
            assert_true(
                any("first sync" in action for action in no_live_provider_actions),
                "Live-provider verifier should tell operators not to sync until credential checks pass.",
            )
            missing_side_effect_actions = live_provider_next_actions(
                "https://marge.example.com",
                {"planning_center": {"display_name": "Planning Center", "status": "connected"}},
                external_ready_evidence["external_provider_checks"],
                [],
                require_live_provider=True,
                side_effects=None,
            )
            assert_true(
                any("--skip-side-effect-check" in action for action in missing_side_effect_actions),
                "Live-provider verifier should not accept skipped no-sync side-effect evidence for readiness.",
            )
            assert_true(
                is_workspace_required_error(401, {"detail": "Create or reconnect a Marge workspace before you verify connector credentials."}),
                "Live verifier should recognize workspace-required API failures.",
            )
            assert_true(
                not is_workspace_required_error(409, {"detail": "Connect this provider before checking credentials."}),
                "Live verifier should not mislabel ordinary not-connected providers as missing workspace scope.",
            )
            workspace_actions = live_provider_next_actions(
                "http://127.0.0.1:8000",
                {},
                [{
                    "provider": "mcp",
                    "display_name": "MCP",
                    "ok": False,
                    "status": "workspace_required",
                }],
                [],
                require_live_provider=True,
                evidence_file="artifacts/live-connector-verification.json",
            )
            assert_true(
                any("Create or reconnect a real Marge workspace" in action for action in workspace_actions),
                "Workspace-required live verification should tell operators to create or reconnect a workspace.",
            )
            assert_true(
                any("MARGE_ACCOUNT_TOKEN" in action for action in workspace_actions),
                "Workspace-required live verification should include the workspace token rerun path.",
            )
            assert_true(
                any("--evidence-file artifacts/live-connector-verification.json" in action for action in workspace_actions),
                "Workspace-required live verification rerun guidance should preserve the evidence file path.",
            )
            assert_true(
                "scoped to that church" in workspace_token_message("http://127.0.0.1:8000"),
                "Workspace-token guidance should explain why the token is required.",
            )
            assert_true(
                "--evidence-file artifacts/live-connector-verification.json" in live_provider_rerun_command(
                    "https://marge.example.com",
                    "artifacts/live-connector-verification.json",
                ),
                "Live-provider rerun command helper should preserve evidence file paths.",
            )
            owner_scope_ok, owner_scope_message = validate_workspace_scope_for_live_provider({
                "account_id": 42,
                "slug": "smoke-church",
                "church_name": "Smoke Church",
                "current_role": "owner",
            })
            assert_true(owner_scope_ok and "owner access" in owner_scope_message, "Owner tokens should satisfy standalone live-provider verification role checks.")
            staff_scope_ok, staff_scope_message = validate_workspace_scope_for_live_provider({
                "account_id": 42,
                "slug": "smoke-church",
                "church_name": "Smoke Church",
                "current_role": "staff",
            })
            assert_true(
                not staff_scope_ok and "role=staff" in staff_scope_message,
                "Standalone live-provider verification should reject staff tokens before provider checks.",
            )
            assert_true(is_local_api_url("http://127.0.0.1:8000"), "Live verifier should recognize local loopback URLs.")
            assert_true(not is_local_api_url("https://marge.example.com"), "Live verifier should treat public HTTPS URLs as non-local.")

            original_live_request_json = live_verifier.request_json
            snapshot_calls = []
            fake_rows = {
                "/assistant/connected-items": [{"id": f"context-{index}", "value": index} for index in range(205)],
                "/assistant/actions": [{"id": f"action-{index}", "value": index} for index in range(205)],
                "/members/": [{"id": f"member-{index}", "value": index} for index in range(205)],
                "/visitors/": [{"id": f"visitor-{index}", "value": index} for index in range(205)],
                "/care/": [{"id": f"care-{index}", "value": index} for index in range(205)],
                "/care/prayers/": [{"id": f"prayer-{index}", "value": index, "is_private": True} for index in range(205)],
            }

            def fake_live_request_json(method: str, url: str, token: str | None = None, payload: dict[str, Any] | None = None):
                parsed = urlparse(url)
                query = parse_qs(parsed.query)
                snapshot_calls.append((parsed.path, query))
                rows = fake_rows[parsed.path]
                skip = int((query.get("skip") or ["0"])[0])
                limit = int((query.get("limit") or ["200"])[0])
                return 200, rows[skip:skip + limit]

            try:
                live_verifier.request_json = fake_live_request_json
                paginated_snapshot = snapshot_workspace("https://marge.example.com", "marge_sess_snapshot")
            finally:
                live_verifier.request_json = original_live_request_json

            assert_true(
                all(detail["count"] == 205 for detail in paginated_snapshot.values()),
                "Live verifier side-effect snapshots should read beyond the first 200 rows.",
            )
            assert_true(
                any(path == "/assistant/connected-items" and query.get("skip") == ["200"] for path, query in snapshot_calls),
                "Connected-context snapshot should request a second page instead of trusting the first page.",
            )
            assert_true(
                any(path == "/assistant/actions" and query.get("skip") == ["200"] for path, query in snapshot_calls),
                "Assistant-action snapshot should request a second page instead of trusting the first page.",
            )
            assert_true(
                any(path == "/care/prayers/" and query.get("include_private") == ["true"] for path, query in snapshot_calls),
                "Prayer snapshot should include private requests so verify side effects cannot hide there.",
            )
            assert_true(
                all(query.get("limit") == ["200"] for _path, query in snapshot_calls),
                "Live verifier snapshots should use the API's maximum page size for side-effect checks.",
            )

            def malformed_live_request_json(method: str, url: str, token: str | None = None, payload: dict[str, Any] | None = None):
                return 200, [{"id": "valid-row"}, "not-an-object"]

            malformed_snapshot_rejected = False
            try:
                live_verifier.request_json = malformed_live_request_json
                try:
                    snapshot_workspace("https://marge.example.com", "marge_sess_snapshot")
                except RuntimeError as exc:
                    malformed_snapshot_rejected = "expected JSON objects" in str(exc)
            finally:
                live_verifier.request_json = original_live_request_json

            assert_true(
                malformed_snapshot_rejected,
                "Live verifier side-effect snapshots should reject malformed non-object rows.",
            )

            local_evidence = tempfile.NamedTemporaryFile(prefix="marge-live-verifier-", suffix=".json", delete=False)
            local_evidence_path = local_evidence.name
            local_evidence.close()
            os.unlink(local_evidence_path)
            local_require_live_guard = subprocess.run(
                [
                    sys.executable,
                    "scripts/verify_live_integrations.py",
                    "--api-url",
                    "http://127.0.0.1:8000",
                    "--include-mcp",
                    "--require-live-provider",
                    "--evidence-file",
                    local_evidence_path,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            assert_true(
                local_require_live_guard.returncode == 2,
                "Live-provider verification should require a workspace token even against local APIs.",
            )
            assert_true(
                "MARGE_ACCOUNT_TOKEN is required" in local_require_live_guard.stderr,
                "Local live-provider verification should explain that a workspace token is required.",
            )
            with open(local_evidence_path, "r", encoding="utf-8") as handle:
                local_evidence_report = json.load(handle)
            os.unlink(local_evidence_path)
            assert_true(
                local_evidence_report.get("required_live_provider") is True
                and local_evidence_report.get("external_verified_count") == 0,
                "Live verifier evidence files should preserve the failed live-provider gate status.",
            )
            assert_true(
                local_evidence_report.get("live_provider_ready") is False
                and local_evidence_report.get("no_sync_side_effect_check_passed") is None,
                "Early live verifier evidence should explicitly report that no live provider is ready yet.",
            )
            assert_true(
                local_evidence_report.get("external_provider_checks") == []
                and local_evidence_report.get("local_bridge_checks") == [],
                "Early live verifier evidence should include explicit typed check arrays.",
            )
            assert_true(
                "marge_sess" not in json.dumps(local_evidence_report),
                "Live verifier evidence files should not include workspace token values.",
            )
            assert_true(
                any("MARGE_ACCOUNT_TOKEN is required" in action for action in local_evidence_report.get("next_actions", [])),
                "Live verifier evidence files should preserve operator next actions.",
            )
            assert_true(
                any(f"--evidence-file {local_evidence_path}" in action for action in local_evidence_report.get("next_actions", [])),
                "Early live verifier evidence should preserve the requested evidence file path in rerun guidance.",
            )
            nonlocal_env = os.environ.copy()
            nonlocal_env.pop("MARGE_ACCOUNT_TOKEN", None)
            nonlocal_guard = subprocess.run(
                [
                    sys.executable,
                    "scripts/verify_live_integrations.py",
                    "--api-url",
                    "https://marge.example.com",
                    "--include-mcp",
                    "--require-live-provider",
                ],
                cwd=ROOT,
                env=nonlocal_env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert_true(nonlocal_guard.returncode == 2, "Non-local live verification without a workspace token should fail before provider checks.")
            assert_true(
                "MARGE_ACCOUNT_TOKEN is required" in nonlocal_guard.stderr,
                "Non-local live verification should explain that a workspace token is required.",
            )
            unavailable_guard = subprocess.run(
                [
                    sys.executable,
                    "scripts/verify_live_integrations.py",
                    "--api-url",
                    "http://127.0.0.1:9",
                    "--include-mcp",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            assert_true(unavailable_guard.returncode == 2, "Live verification should fail cleanly when the API is unreachable.")
            assert_true(
                "Could not list integrations" in unavailable_guard.stderr,
                "Unreachable API failures should explain that integration status could not be listed.",
            )
            assert_true(
                "Traceback" not in unavailable_guard.stderr,
                "Unreachable API failures should not show a Python traceback to operators.",
            )

            pre_sync_context = request_json(client, "GET", "/assistant/connected-items?limit=100", token=token)
            assert_true(pre_sync_context == [], "Safe verification should not create connected ministry context.")
            assert_true(
                ministry_record_snapshot(client, token) == pre_verify_ministry,
                "Safe verification should not create or mutate local ministry records before sync.",
            )

            pco_sync = request_json(client, "POST", "/assistant/integrations/planning_center/sync?people_limit=10&calendar_days=14", token=token, json={})
            assert_true(pco_sync["status"] == "synced", "Planning Center sync should complete.")
            assert_true(pco_sync["items_seen"] == 2, "Planning Center sync should see one person and one event.")
            assert_true(pco_sync["actions_prepared"] == 2, "Planning Center sync should queue person review and meeting prep.")

            ms_sync = request_json(client, "POST", "/assistant/integrations/microsoft_365/sync?email_limit=5&calendar_days=14", token=token, json={})
            assert_true(ms_sync["status"] == "synced", "Microsoft 365 sync should complete.")
            assert_true(ms_sync["items_seen"] == 2, "Microsoft 365 sync should see one email and one event.")
            assert_true(ms_sync["actions_prepared"] == 2, "Microsoft 365 sync should queue inbox review and meeting prep.")

            mailbox_sync = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Sync the mailbox again.", "mode": "live"},
            )
            assert_true(mailbox_sync["intent"] == "sync_microsoft_365", "Generic mailbox sync should choose the connected Microsoft 365 mailbox.")
            assert_true("Microsoft 365" in mailbox_sync["reply"], "Generic mailbox sync reply should name the chosen mail provider.")
            assert_true("Google Workspace" not in mailbox_sync["reply"], "Generic mailbox sync must not blindly try Google when Outlook is connected.")

            calendar_sync = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Sync the calendar again.", "mode": "live"},
            )
            assert_true(calendar_sync["intent"] == "sync_planning_center", "Generic calendar sync should choose the saved connected Planning Center calendar.")
            assert_true("Planning Center" in calendar_sync["reply"], "Generic calendar sync reply should name the chosen calendar provider.")
            assert_true("Google Workspace" not in calendar_sync["reply"], "Generic calendar sync must not blindly try Google when Planning Center is connected.")

            breeze_sync = request_json(client, "POST", "/assistant/integrations/breeze/sync?people_limit=10&calendar_days=14", token=token, json={})
            assert_true(breeze_sync["status"] == "synced", "Breeze sync should complete.")
            assert_true(breeze_sync["items_seen"] == 2, "Breeze sync should see one person and one event.")
            assert_true(breeze_sync["actions_prepared"] == 2, "Breeze sync should queue person review and meeting prep.")

            connected_tools_sync = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Sync the connected tools.", "mode": "live"},
            )
            connected_tools_reply = connected_tools_sync["reply"]
            assert_true(
                connected_tools_sync["intent"] == "sync_connected_tools",
                "Plural connected-tools sync should sync verified church providers, not fall back to connector status.",
            )
            assert_true("Planning Center" in connected_tools_reply, "Connected-tools sync should include verified Planning Center.")
            assert_true("Microsoft 365" in connected_tools_reply, "Connected-tools sync should include verified Microsoft 365.")
            assert_true("Breeze" in connected_tools_reply, "Connected-tools sync should include verified Breeze.")
            assert_true("Google Workspace" not in connected_tools_reply, "Connected-tools sync must not default to Google when it is not connected.")
            assert_true("read-side context only" in connected_tools_reply, "Connected-tools sync should keep review/writeback boundaries visible.")

            connected = request_json(client, "GET", "/assistant/connected-items?limit=100", token=token)
            connected_keys = {(item["provider"], item["item_type"], item["title"]) for item in connected}
            assert_true(("planning_center", "person", "Elena Morris") in connected_keys, "Planning Center person should become connected context.")
            assert_true(("planning_center", "calendar_event", "Visitor lunch with Elena") in connected_keys, "Planning Center event should become connected context.")
            assert_true(("microsoft_365", "email", "Prayer request after group") in connected_keys, "Microsoft email should become connected context.")
            assert_true(("microsoft_365", "calendar_event", "Hospital visit with Marcus") in connected_keys, "Microsoft event should become connected context.")
            assert_true(("breeze", "person", "Nina Brooks") in connected_keys, "Breeze person should become connected context.")
            assert_true(("breeze", "calendar_event", "Prayer class follow-up") in connected_keys, "Breeze event should become connected context.")
            microsoft_email_context = next(item for item in connected if item["provider"] == "microsoft_365" and item["item_type"] == "email")
            assert_true(bool(microsoft_email_context.get("action_id")), "Synced Microsoft email should expose its queued review action id.")
            microsoft_context_action = request_json(client, "GET", f"/assistant/actions/{microsoft_email_context['action_id']}", token=token)
            assert_true(microsoft_context_action["action_type"] == "email_triage", "Synced context action id should open the actual inbox review action.")
            assert_true(
                microsoft_context_action["payload"].get("connected_item_id") == microsoft_email_context["id"],
                "Opened inbox review action should point back to the synced context row.",
            )

            actions = request_json(client, "GET", "/assistant/actions?status=all&limit=100", token=token)
            action_pairs = {(action["source"], action["action_type"], action["title"]) for action in actions}
            assert_true(any(source == "planning_center" and kind == "person_review" and "Elena Morris" in title for source, kind, title in action_pairs), "Planning Center new person should queue person review.")
            assert_true(any(source == "planning_center" and kind == "meeting_prep" and "Visitor lunch" in title for source, kind, title in action_pairs), "Planning Center pastoral event should queue meeting prep.")
            assert_true(any(source == "microsoft_365" and kind == "email_triage" and "Prayer request" in title for source, kind, title in action_pairs), "Microsoft inbox should queue email triage.")
            assert_true(any(source == "microsoft_365" and kind == "meeting_prep" and "Hospital visit" in title for source, kind, title in action_pairs), "Microsoft pastoral event should queue meeting prep.")
            assert_true(any(source == "breeze" and kind == "person_review" and "Nina Brooks" in title for source, kind, title in action_pairs), "Breeze new person should queue person review.")
            assert_true(any(source == "breeze" and kind == "meeting_prep" and "Prayer class" in title for source, kind, title in action_pairs), "Breeze pastoral event should queue meeting prep.")
            ms_triage_action = next(action for action in actions if action["source"] == "microsoft_365" and action["action_type"] == "email_triage")
            ms_triage_guardrail = ((ms_triage_action.get("payload") or {}).get("guardrail") or "")
            assert_true("Outlook draft creation" in ms_triage_guardrail, "Microsoft triage guardrail should name approved Outlook draft writeback.")
            assert_true("read-side only in this MVP" not in ms_triage_guardrail, "Microsoft triage guardrail should not describe the provider as fully read-only.")

            meeting_lookup = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What meetings need prep?", "mode": "live"},
            )
            assert_true(meeting_lookup["intent"] == "meeting_prep_lookup", "Meeting-prep suggested prompts should list synced meetings, not silently prepare one item.")
            assert_true("Visitor lunch with Elena" in meeting_lookup["reply"], "Meeting-prep lookup should include Planning Center events.")
            assert_true("Hospital visit with Marcus" in meeting_lookup["reply"], "Meeting-prep lookup should include Microsoft calendar events.")
            assert_true(meeting_lookup.get("actions"), "Meeting-prep lookup should return review/context action cards.")
            actions_after_meeting_lookup = request_json(client, "GET", "/assistant/actions?status=all&limit=100", token=token)
            assert_true(len(actions_after_meeting_lookup) == len(actions), "Meeting-prep lookup should not create extra approval actions.")

            prepare_next_meeting = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Prepare my next meeting.", "mode": "live"},
            )
            assert_true(prepare_next_meeting["intent"] == "prepare_synced_meeting", "Explicit meeting-prep command should still prepare/open a reviewable brief.")
            assert_true(prepare_next_meeting.get("actions"), "Explicit meeting-prep command should return the prepared meeting action.")

            pco_context = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Show Planning Center context.", "mode": "live"},
            )
            assert_true(pco_context["intent"] == "connected_context_lookup", "Chat should show synced Planning Center context, not only connector status.")
            assert_true("Elena Morris" in pco_context["reply"], "Planning Center context lookup should name synced people.")
            assert_true("Visitor lunch with Elena" in pco_context["reply"], "Planning Center context lookup should include synced events.")
            assert_true(
                any(str(item.get("id", "")).startswith("action-") for item in pco_context.get("actions", [])),
                "Connected context action cards should open queued review items directly.",
            )
            generic_connected_context = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Show connected context.", "mode": "live"},
            )
            assert_true(
                generic_connected_context["intent"] == "connected_context_lookup",
                "Generic connected-context prompts should show synced review context.",
            )
            assert_true(
                "connected tool context" in generic_connected_context["reply"],
                "Generic connected-context copy should use readable pastor-facing wording.",
            )
            assert_true(
                "connected-tool" not in generic_connected_context["reply"],
                "Generic connected-context copy should not expose hyphenated implementation wording.",
            )

            synced_people = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Review synced people.", "mode": "live"},
            )
            assert_true(synced_people["intent"] == "connected_context_lookup", "Chat should list synced people across connected providers.")
            assert_true("Elena Morris" in synced_people["reply"], "Synced people lookup should include Planning Center people.")
            assert_true("Nina Brooks" in synced_people["reply"], "Synced people lookup should include Breeze people.")

            pco_person_action = next(action for action in actions if action["source"] == "planning_center" and action["action_type"] == "person_review")
            request_json(client, "POST", f"/assistant/actions/{pco_person_action['id']}/approve", token=token, json={})
            executed_person = request_json(client, "POST", f"/assistant/actions/{pco_person_action['id']}/execute", token=token, json={})
            person_execution = (executed_person.get("payload") or {}).get("execution") or {}
            assert_true(person_execution.get("kind") == "local_member_upsert", "Approved connected person review should create or update local Marge person memory.")
            assert_true(person_execution.get("member_name") == "Elena Morris", "Connected person execution should preserve the synced person name.")

            elena_context = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What do you know about Elena Morris?", "mode": "live"},
            )
            assert_true(elena_context["intent"] == "person_context_lookup", "Synced-and-approved person should be available to chat memory.")
            assert_true("Planning Center" in elena_context["reply"], "Person memory should include the connector import note.")

            nina_import = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Go ahead and add Nina Brooks to Marge.", "mode": "live"},
            )
            assert_true(nina_import["intent"] == "connected_person_imported", "Chat should turn an explicit synced-person add request into local Marge memory, even with go-ahead approval wording.")
            assert_true(nina_import["saved"], "Chat-imported synced person should be saved.")
            assert_true("Nina Brooks" in nina_import["reply"], "Synced person import reply should name the imported person.")
            assert_true("did not write back" in nina_import["reply"], "Synced person import should state that it did not write back to the source system.")
            assert_true(nina_import.get("actions"), "Synced person import should return local person context actions.")
            nina_context = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What do you know about Nina Brooks?", "mode": "live"},
            )
            assert_true(nina_context["intent"] == "person_context_lookup", "Chat-imported Breeze person should be available to chat memory.")
            assert_true("Breeze" in nina_context["reply"], "Breeze-imported person memory should include the connector import note.")

            queued_replies = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Queue replies for these.", "mode": "live"},
            )
            assert_true(queued_replies["intent"] == "draft_synced_email_replies_queued", "Marge's synced-inbox suggested prompt should queue reply drafts.")
            assert_true(queued_replies.get("saved"), "Queued synced-inbox replies should persist in chat history.")
            assert_true("Prayer request after group" in queued_replies["reply"], "Queued synced-inbox reply should name the synced message.")
            assert_true(queued_replies.get("actions"), "Queued synced-inbox reply should return approval action cards.")
            queued_draft_id = int(str(queued_replies["actions"][0]["id"]).replace("action-", ""))

            ms_email_action = ms_triage_action
            request_json(client, "POST", f"/assistant/actions/{ms_email_action['id']}/approve", token=token, json={})
            executed_email = request_json(client, "POST", f"/assistant/actions/{ms_email_action['id']}/execute", token=token, json={})
            email_execution = (executed_email.get("payload") or {}).get("execution") or {}
            assert_true(email_execution.get("kind") == "email_reply_drafted", "Approved Microsoft inbox review should create a reviewable reply draft.")
            draft_action_id = email_execution.get("draft_action_id")
            assert_true(bool(draft_action_id), "Microsoft email triage execution should return the generated draft action id.")
            assert_true(draft_action_id == queued_draft_id, "Email triage execution should reuse the chat-queued synced inbox draft.")
            draft_actions = request_json(client, "GET", "/assistant/actions?status=all&limit=120", token=token)
            ms_draft = next((action for action in draft_actions if action["id"] == draft_action_id), None)
            assert_true(ms_draft is not None, "Microsoft email triage should create a separate draft action.")
            assert_true(ms_draft["action_type"] == "email_draft", "Microsoft email triage should create an email draft.")
            assert_true(ms_draft["status"] == "pending", "Generated Microsoft email draft should still require pastor review.")
            assert_true(ms_draft.get("external_provider") == "microsoft_365", "Microsoft draft should be eligible for approved Outlook draft writeback.")
            assert_true("dad's surgery" in ((ms_draft.get("payload") or {}).get("email") or {}).get("body", ""), "Microsoft draft should use the synced ministry inbox context.")

            approve_draft_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Approve the Outlook draft for Marcus.", "mode": "live"},
            )
            assert_true(approve_draft_chat["intent"] == "assistant_action_approved", "Chat should approve the matching Outlook draft action.")
            send_refusal = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Send the Outlook draft to Marcus.", "mode": "live"},
            )
            assert_true(send_refusal["intent"] == "email_send_refused", "Chat must refuse to send mail directly.")
            blocked_ms_write = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Create the Outlook draft for Marcus.", "mode": "live"},
            )
            assert_true(blocked_ms_write["intent"] == "assistant_action_execution_blocked", "Microsoft draft writeback should be blocked until policy allows it.")
            assert_true("writeback is disabled" in blocked_ms_write["reply"], "Chat writeback block should name church policy.")
            ms_policy = request_json(
                client,
                "PATCH",
                "/assistant/policies/microsoft_365",
                token=token,
                json={"write_enabled": True, "require_approval": True, "allowed_actions": ["email_draft"]},
            )
            assert_true(ms_policy["allowed_actions"] == ["email_draft"], "Microsoft writeback policy should stay narrowed to email drafts.")
            execute_draft_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Create the Outlook draft for Marcus.", "mode": "live"},
            )
            assert_true(execute_draft_chat["intent"] == "assistant_action_executed", "Chat should execute an approved Outlook draft after policy allows it.")
            executed_draft = request_json(client, "GET", f"/assistant/actions/{draft_action_id}", token=token)
            assert_true(executed_draft["status"] == "executed", "Chat-executed Outlook draft should mark the action executed.")
            draft_execution = (executed_draft.get("payload") or {}).get("execution") or {}
            assert_true(draft_execution.get("kind") == "outlook_draft", "Approved Microsoft draft should create an Outlook draft, not send mail.")
            assert_true(draft_execution.get("provider_id") == "outlook-draft-smoke-1", "Outlook draft execution should store the provider draft id.")

            actions_before_calendar_help = request_json(client, "GET", "/assistant/actions?status=all&limit=100", token=token)
            missing_calendar_details = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={
                    "message": "Queue an Outlook calendar event for Hospital follow-up with Marcus.",
                    "mode": "live",
                },
            )
            assert_true(
                missing_calendar_details["intent"] == "calendar_event_missing_details",
                "Incomplete calendar event prompts should ask for details instead of creating an action.",
            )
            assert_true(
                "What calendar details do you need?" in (missing_calendar_details.get("suggested_prompts") or []),
                "Calendar missing-details prompt should offer a usable details-help follow-up.",
            )
            assert_true(not missing_calendar_details.get("actions"), "Incomplete calendar event prompts should not queue approval actions.")

            calendar_details_help = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What calendar details do you need?", "mode": "live"},
            )
            calendar_details_reply = calendar_details_help["reply"]
            assert_true(calendar_details_help["intent"] == "calendar_event_details_help", "Calendar details help prompt should have a real chat route.")
            assert_true("YYYY-MM-DD" in calendar_details_reply, "Calendar details help should name the required date format.")
            assert_true("start time" in calendar_details_reply.lower(), "Calendar details help should ask for a start time.")
            assert_true(
                "approval" in calendar_details_reply.lower() or "approve" in calendar_details_reply.lower(),
                "Calendar details help should keep approval boundaries visible.",
            )
            assert_true("Microsoft 365" in calendar_details_reply, "Calendar details help should name the connected Outlook calendar provider.")
            assert_true(not calendar_details_help.get("actions"), "Calendar details help for a connected write provider should not queue actions.")
            actions_after_calendar_help = request_json(client, "GET", "/assistant/actions?status=all&limit=100", token=token)
            assert_true(len(actions_after_calendar_help) == len(actions_before_calendar_help), "Calendar details help should not create approval actions.")

            queue_calendar_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={
                    "message": "Queue an Outlook calendar event for Hospital follow-up with Marcus on 2026-05-18 at 3pm for 1 hour location County Hospital with marcus@example.test.",
                    "mode": "live",
                },
            )
            assert_true(queue_calendar_chat["intent"] == "calendar_event_queued", "Chat should queue a concrete Outlook calendar event action.")
            calendar_action_id = int(queue_calendar_chat["actions"][0]["id"].replace("action-", ""))
            calendar_action = request_json(client, "GET", f"/assistant/actions/{calendar_action_id}", token=token)
            assert_true(calendar_action["external_provider"] == "microsoft_365", "Chat-queued Outlook calendar event should target Microsoft 365.")
            assert_true(calendar_action["action_type"] == "calendar_block", "Chat-queued calendar event should be a reviewable calendar_block action.")
            request_json(client, "POST", f"/assistant/actions/{calendar_action['id']}/approve", token=token, json={})
            blocked_calendar_status, blocked_calendar_body = request_json_status(
                client,
                "POST",
                f"/assistant/actions/{calendar_action['id']}/execute",
                token=token,
                json={},
            )
            assert_true(blocked_calendar_status == 403, "Microsoft calendar writeback should be blocked when policy allows only email drafts.")
            assert_true("calendar_block is not allowed" in json.dumps(blocked_calendar_body), "Calendar writeback block should name the disallowed action.")
            ms_policy = request_json(
                client,
                "PATCH",
                "/assistant/policies/microsoft_365",
                token=token,
                json={"write_enabled": True, "require_approval": True, "allowed_actions": ["email_draft", "calendar_block"]},
            )
            assert_true(ms_policy["allowed_actions"] == ["email_draft", "calendar_block"], "Microsoft writeback policy should allow approved drafts and calendar events when explicitly enabled.")
            executed_calendar = request_json(client, "POST", f"/assistant/actions/{calendar_action['id']}/execute", token=token, json={})
            assert_true(executed_calendar["status"] == "executed", "Approved Microsoft calendar action should mark executed after policy allows it.")
            calendar_execution = (executed_calendar.get("payload") or {}).get("execution") or {}
            assert_true(calendar_execution.get("kind") == "outlook_calendar_event", "Approved Microsoft calendar action should create an Outlook calendar event.")
            assert_true(calendar_execution.get("provider_id") == "outlook-event-smoke-1", "Outlook calendar execution should store the provider event id.")

            post_sync_text = json.dumps(request_json(client, "GET", "/assistant/audit-log?limit=100", token=token))
            for payload in token_payloads.values():
                assert_true(payload["access_token"] not in post_sync_text, "Audit logs must not expose OAuth access tokens.")
                assert_true(payload["refresh_token"] not in post_sync_text, "Audit logs must not expose OAuth refresh tokens.")
            assert_true(breeze_api_key not in post_sync_text, "Audit logs must not expose the Breeze API key.")

        print("Marge connected provider smoke passed.")
        print(json.dumps({
            "checked_providers": ["planning_center", "microsoft_365", "breeze"],
            "oauth_credentials": "encrypted_user_scoped",
            "safe_verification_without_sync": "verified",
            "live_provider_readiness_counter": "verified",
            "live_provider_no_sync_required": "verified",
            "live_provider_workspace_token_guard": "verified",
            "live_provider_workspace_role_guard": "verified",
            "chat_connector_verification": "verified",
            "chat_sync_precheck_verification": "verified",
            "planning_center_sync": "verified",
            "microsoft_365_sync": "verified",
            "approved_outlook_draft_writeback": "verified",
            "approved_outlook_calendar_writeback": "verified",
            "chat_action_approval_execution": "verified",
            "generic_mailbox_sync_provider_selection": "verified",
            "generic_calendar_sync_provider_selection": "verified",
            "plural_connected_tools_sync": "verified",
            "meeting_prep_lookup": "verified",
            "calendar_details_help": "verified",
            "breeze_sync": "verified",
            "workspace_api_key_credentials": "verified",
            "rock_id_workspace_scope": "verified",
            "connected_context": "verified",
            "chat_connected_context_lookup": "verified",
            "chat_synced_person_import": "verified",
            "chat_synced_inbox_reply_queue": "verified",
            "person_review_to_local_memory": "verified",
            "inbox_triage_to_reviewable_draft": "verified",
            "secret_redaction": "verified",
        }, indent=2))
    finally:
        assistant_router._exchange_oauth_code = original_exchange
        assistant_router._planning_center_get = original_planning_get
        assistant_router._microsoft_graph_get = original_microsoft_get
        assistant_router._microsoft_graph_post = original_microsoft_post
        assistant_router._breeze_get = original_breeze_get
        for account_id in account_ids:
            cleanup_account(account_id)


if __name__ == "__main__":
    main()
