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

Usage:
  .venv/bin/python scripts/smoke_connected_providers.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services.secure_tokens import decrypt_token_payload, generate_encryption_key

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
                    "role_title": "Solo Pastor",
                    "congregation_size": "95",
                    "church_context": "Neighborhood church with new families and a thin care team.",
                    "faith_tradition": "Non-denominational with Baptist roots; use plain language with guests.",
                    "followup_pain": "Visitors, prayer requests, and hospital follow-up.",
                    "ministry_priorities": "Close loops with first-time guests and private prayer needs.",
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

            integrations = request_json(client, "GET", "/assistant/integrations", token=token)
            by_provider = {item["provider"]: item for item in integrations}
            assert_true(by_provider["planning_center"]["status"] == "connected", "Planning Center should report connected after OAuth callback.")
            assert_true(by_provider["planning_center"]["credential_scope"] == "user", "Planning Center OAuth should be user-scoped.")
            assert_true(by_provider["microsoft_365"]["status"] == "connected", "Microsoft 365 should report connected after OAuth callback.")
            assert_true(by_provider["microsoft_365"]["credential_scope"] == "user", "Microsoft 365 OAuth should be user-scoped.")
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
            assert_true(chat_verification["intent"] == "integration_verified", "Chat should understand connector credential checks as a safe verify action.")
            assert_true("without syncing" in chat_verification["reply"].lower(), "Chat verification reply should say it did not sync ministry data.")
            assert_true("did not queue any actions" in chat_verification["reply"].lower(), "Chat verification should not pretend it prepared review work.")
            pco_after_chat_verify = next(item for item in request_json(client, "GET", "/assistant/integrations", token=token) if item["provider"] == "planning_center")
            assert_true(bool(pco_after_chat_verify["verified_at"]), "Chat verification should mark Planning Center as checked before sync.")

            for provider in ["planning_center", "microsoft_365", "breeze"]:
                verification = request_json(client, "POST", f"/assistant/integrations/{provider}/verify", token=token, json={})
                assert_true(verification["status"] == "verified", f"{provider} should verify without syncing ministry data.")
                assert_true(bool(verification["identity"]), f"{provider} verification should return non-secret identity/config metadata.")

            pre_sync_context = request_json(client, "GET", "/assistant/connected-items?limit=100", token=token)
            assert_true(pre_sync_context == [], "Safe verification should not create connected ministry context.")

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
