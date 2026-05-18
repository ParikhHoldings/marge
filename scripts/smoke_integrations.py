#!/usr/bin/env python3
"""
Smoke-test Marge's secure OAuth connector lifecycle without live providers.

This uses FastAPI's in-process TestClient and a mocked token exchange so the
local check can prove the important safety properties:

- OAuth setup requires server-side config and creates account-scoped state
- callback stores encrypted tokens without returning secrets to the browser/API
- OAuth state is consumed once and cannot be replayed
- user tokens can mint revocable, expiring sessions
- workspace invites can be delivered without persisting raw tokens
- passwordless login links exchange into revocable sessions
- another church workspace cannot see the first workspace's credential
- Google writeback remains disabled until the church policy explicitly allows it
- strict account-token mode rejects missing-token access to protected routes
- safe connector verification proves credentials without syncing ministry data
- mocked Google sync turns mail/calendar context into reviewable pastor work
- approved synced email triage creates a separate reviewable draft action
- disconnect removes encrypted OAuth credentials and shuts writeback back off

Usage:
  .venv/bin/python scripts/smoke_integrations.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from email import policy as email_policy
from email.parser import Parser
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services.secure_tokens import decrypt_token_payload, generate_encryption_key

os.environ["MARGE_ENCRYPTION_KEY"] = generate_encryption_key()
os.environ["GOOGLE_CLIENT_ID"] = "local-smoke-google-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "local-smoke-google-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://testserver/assistant/integrations/google_workspace/callback"
os.environ["MARGE_REQUIRE_ACCOUNT_TOKEN"] = "true"

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    AccountPastorProfile,
    AccountLoginLink,
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
            AccountLoginLink,
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


def main() -> None:
    account_ids: list[int] = []
    invite_outbox = os.path.join(tempfile.gettempdir(), f"marge-invite-smoke-{os.getpid()}.eml")
    os.environ["MARGE_INVITE_EMAIL_OUTBOX"] = invite_outbox
    os.environ["MARGE_APP_URL"] = "http://testserver/app"
    os.environ["MARGE_INVITE_EMAIL_FROM"] = "Marge Smoke <marge@example.test>"
    original_exchange = assistant_router._exchange_oauth_code
    original_fetch_google_messages = assistant_router._fetch_google_messages
    original_fetch_google_calendar_events = assistant_router._fetch_google_calendar_events
    original_get = assistant_router.requests.get
    original_post = assistant_router.requests.post
    token_payload = {
        "access_token": "access-token-secret-smoke",
        "refresh_token": "refresh-token-secret-smoke",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/calendar.events",
    }

    def fake_exchange(definition: dict, code: str, redirect_uri: str) -> dict:
        assert_true(definition["provider"] == "google_workspace", "Smoke should only mock Google Workspace.")
        assert_true(code == "local-smoke-code", "Callback should pass the provider code through to exchange.")
        assert_true(redirect_uri == os.environ["GOOGLE_REDIRECT_URI"], "Callback should use the stored redirect URI.")
        return dict(token_payload)

    def fake_google_messages(token: str, limit: int) -> list[dict]:
        assert_true(token == token_payload["access_token"], "Google sync should use the decrypted access token.")
        assert_true(limit > 0, "Google message sync should honor a positive limit.")
        now = datetime.now(UTC).replace(tzinfo=None)
        return [{
            "id": "gmail-smoke-message-1",
            "thread_id": "gmail-smoke-thread-1",
            "from": "Jordan Visitor <jordan@example.test>",
            "subject": "Follow-up after Sunday",
            "date": now - timedelta(hours=2),
            "snippet": "We visited Sunday and would like to learn about small groups.",
            "label_ids": ["INBOX"],
        }]

    def fake_google_events(token: str, days: int) -> list[dict]:
        assert_true(token == token_payload["access_token"], "Google calendar sync should use the decrypted access token.")
        assert_true(days > 0, "Google calendar sync should honor a positive window.")
        starts_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2)
        return [{
            "id": "google-calendar-smoke-event-1",
            "summary": "Visitor coffee with Jordan",
            "description": "Pastoral follow-up with a new family after Sunday.",
            "location": "Church lobby",
            "start": {"dateTime": starts_at.isoformat()},
            "end": {"dateTime": (starts_at + timedelta(hours=1)).isoformat()},
            "start_at": starts_at,
            "when": "In two days",
        }]

    def fake_google_post(url: str, headers: dict | None = None, json: dict | None = None, **kwargs) -> Any:
        assert_true(url == "https://gmail.googleapis.com/gmail/v1/users/me/drafts", "Approved Gmail draft execution should use the drafts endpoint.")
        assert_true((headers or {}).get("Authorization") == "Bearer access-token-secret-smoke", "Gmail draft execution should use the decrypted bearer token.")
        assert_true((headers or {}).get("Accept") == "application/json", "Gmail draft execution should request JSON.")
        raw = (((json or {}).get("message") or {}).get("raw") or "")
        assert_true(bool(raw), "Gmail draft execution should send a raw MIME message.")
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="replace")
        assert_true("To: jordan@example.test" in decoded, "Gmail draft MIME should include the synced recipient.")
        assert_true("Subject: Re: Follow-up after Sunday" in decoded, "Gmail draft MIME should include the reviewable reply subject.")
        assert_true("Hi Jordan" in decoded, "Gmail draft MIME should include the pastor-facing draft body.")
        assert_true("small groups" in decoded, "Gmail draft MIME should include the relevant email context.")
        assert_true("Draft note for review" not in decoded, "Gmail draft MIME must not include internal review notes.")
        assert_true("Do not send emails" not in decoded, "Gmail draft MIME must not leak internal approval guardrails.")
        assert_true("warm and brief" not in decoded, "Gmail draft MIME must not leak draft metadata.")

        class FakeResponse:
            ok = True
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"id": "gmail-draft-smoke-1"}

        return FakeResponse()

    def fake_google_get(url: str, headers: dict | None = None, **kwargs) -> Any:
        assert_true(url == "https://gmail.googleapis.com/gmail/v1/users/me/profile", "Google verification should use Gmail's profile endpoint.")
        assert_true((headers or {}).get("Authorization") == "Bearer access-token-secret-smoke", "Google verification should use the decrypted bearer token.")
        assert_true((headers or {}).get("Accept") == "application/json", "Google verification should request JSON.")

        class FakeResponse:
            ok = True
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {
                    "emailAddress": "owner@example.test",
                    "messagesTotal": 12,
                    "threadsTotal": 5,
                }

        return FakeResponse()

    def fake_google_get_failure(url: str, headers: dict | None = None, **kwargs) -> Any:
        assert_true(url == "https://gmail.googleapis.com/gmail/v1/users/me/profile", "Failed Google verification should still use Gmail's profile endpoint.")
        assert_true((headers or {}).get("Authorization") == "Bearer access-token-secret-smoke", "Failed Google verification should still use the decrypted bearer token.")

        class FakeResponse:
            ok = False
            status_code = 503

            @staticmethod
            def json() -> dict:
                return {"error": "temporarily_unavailable"}

        return FakeResponse()

    try:
        with TestClient(app) as client:
            config = request_json(client, "GET", "/assistant/config")
            assert_true(config["require_account_token"], "Public config should expose strict account-token mode.")

            unauthenticated = client.get("/assistant/desk?mode=live")
            assert_true(unauthenticated.status_code == 401, "Strict account-token mode should reject missing-token desk access.")
            assert_true("account token is required" in unauthenticated.text.lower(), "Missing-token rejection should be explicit.")

            original_token_requirement = os.environ.get("MARGE_REQUIRE_ACCOUNT_TOKEN")
            os.environ["MARGE_REQUIRE_ACCOUNT_TOKEN"] = "false"
            try:
                relaxed_connector_setup = client.post("/assistant/integrations/google_workspace/start", json={})
                assert_true(
                    relaxed_connector_setup.status_code == 401,
                    "Connector setup should still require a real workspace even when local legacy token mode is relaxed.",
                )
                assert_true(
                    "workspace" in relaxed_connector_setup.text.lower(),
                    "Unscoped connector setup rejection should tell the operator to create or reconnect a workspace.",
                )
                relaxed_rock_sync = client.post("/members/sync/rock", json={})
                assert_true(
                    relaxed_rock_sync.status_code == 401,
                    "Legacy Rock sync should not run against unscoped rows even when local legacy token mode is relaxed.",
                )
                assert_true(
                    "workspace" in relaxed_rock_sync.text.lower(),
                    "Unscoped legacy Rock sync rejection should mention the workspace requirement.",
                )
            finally:
                if original_token_requirement is None:
                    os.environ.pop("MARGE_REQUIRE_ACCOUNT_TOKEN", None)
                else:
                    os.environ["MARGE_REQUIRE_ACCOUNT_TOKEN"] = original_token_requirement

            signup = request_json(
                client,
                "POST",
                "/assistant/signup",
                json={"pastor_name": "Pastor OAuth Smoke", "church_name": "OAuth Smoke Church", "email": "oauth-smoke@example.test"},
            )
            token = signup["token"]
            account_ids.append(signup["account_id"])
            assert_true(signup["current_user"]["role"] == "owner", "Signup should return an owner-scoped user token.")
            account_response = request_json(client, "GET", "/assistant/account", token=token)
            assert_true(account_response["current_role"] == "owner", "Owner user token should resolve to owner access.")
            assert_true(account_response["current_user"]["email"] == "oauth-smoke@example.test", "Signup should preserve the owner recovery email.")
            unknown_sync = client.post(
                "/assistant/integrations/not_a_provider/sync",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(unknown_sync.status_code == 404, "Unknown connector sync requests should return a provider error, not a placeholder.")
            assert_true("unknown integration provider" in unknown_sync.text.lower(), "Unknown connector sync should explain the provider is not configured.")
            mcp_sync = client.post(
                "/assistant/integrations/mcp/sync",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(mcp_sync.status_code == 422, "MCP should be listed but not treated as an external data sync source.")
            assert_true("does not import external ministry data" in mcp_sync.text.lower(), "MCP sync rejection should give capability guidance.")
            assert_true("not implemented" not in mcp_sync.text.lower(), "Connector capability errors should not leak placeholder wording.")
            mcp_verify = request_json(
                client,
                "POST",
                "/assistant/integrations/mcp/verify",
                token=token,
                json={},
            )
            assert_true(mcp_verify["status"] == "bridge_available", "MCP verification should report bridge availability, not provider credentials.")
            assert_true(
                "not a church-tool credential check" in mcp_verify["message"],
                "MCP verification should not read as external provider credential proof.",
            )
            assert_true(
                "does not prove an external provider is connected" in mcp_verify["message"],
                "MCP verification should say it does not count as live provider readiness.",
            )
            assert_true("credentials verified" not in mcp_verify["message"].lower(), "MCP verification should not use credential-verified wording.")
            assert_true(mcp_verify["verified_at"] is None, "MCP verification should not return a credential verification timestamp.")
            mcp_restore: dict[str, Any] | None = None
            mcp_stale_created = False
            db = SessionLocal()
            try:
                mcp_connection = db.query(IntegrationConnection).filter(IntegrationConnection.provider == "mcp").one_or_none()
                if mcp_connection:
                    mcp_restore = {
                        "account_id": mcp_connection.account_id,
                        "display_name": mcp_connection.display_name,
                        "status": mcp_connection.status,
                        "auth_type": mcp_connection.auth_type,
                        "config_hint": mcp_connection.config_hint,
                        "verified_at": mcp_connection.verified_at,
                    }
                else:
                    mcp_connection = IntegrationConnection(provider="mcp", display_name="MCP", status="connected", auth_type="local")
                    db.add(mcp_connection)
                    mcp_stale_created = True
                mcp_connection.account_id = signup["account_id"]
                mcp_connection.display_name = "MCP"
                mcp_connection.status = "connected"
                mcp_connection.auth_type = "local"
                mcp_connection.config_hint = "Verified MCP credentials without syncing ministry data."
                mcp_connection.verified_at = datetime.now(UTC).replace(tzinfo=None)
                db.commit()
            finally:
                db.close()
            try:
                integrations_after_mcp_verify = request_json(client, "GET", "/assistant/integrations", token=token)
                mcp_status = next(item for item in integrations_after_mcp_verify if item["provider"] == "mcp")
                assert_true(mcp_status["verified_at"] is None, "MCP bridge checks should not mark MCP as a verified church-tool connector.")
                assert_true(
                    "does not connect a church tool" in mcp_status["config_hint"],
                    "MCP integration status should keep the agent-bridge boundary visible.",
                )
            finally:
                db = SessionLocal()
                try:
                    mcp_connection = db.query(IntegrationConnection).filter(IntegrationConnection.provider == "mcp").one_or_none()
                    if mcp_connection and mcp_stale_created:
                        db.delete(mcp_connection)
                    elif mcp_connection and mcp_restore:
                        for field, value in mcp_restore.items():
                            setattr(mcp_connection, field, value)
                    db.commit()
                finally:
                    db.close()
            session = request_json(client, "POST", "/assistant/sessions", token=token, json={"duration_hours": 2})
            session_token = session["token"]
            assert_true(session["token_type"] == "session", "User token should mint a shorter-lived session token.")
            assert_true(session_token.startswith("marge_sess_"), "Session token should use the session prefix.")
            assert_true(client.cookies.get("marge_session") == session_token, "Session creation should set an HttpOnly Marge session cookie.")
            cookie_account = request_json(client, "GET", "/assistant/account")
            assert_true(cookie_account["id"] == signup["account_id"], "Session cookie should authenticate scoped routes without a token header.")
            session_status = request_json(client, "GET", "/assistant/sessions/current", token=session_token)
            assert_true(session_status["token_type"] == "session", "Session token should resolve as a session.")
            assert_true(session_status["current_user"]["role"] == "owner", "Session token should preserve the user's role.")
            session_account = request_json(client, "GET", "/assistant/account", token=session_token)
            assert_true(session_account["id"] == signup["account_id"], "Session token should scope to the same church account.")
            revoked_session = request_json(client, "DELETE", "/assistant/sessions/current", token=session_token)
            assert_true(revoked_session["token_type"] == "session", "Revoking a session should return the session status.")
            assert_true(client.cookies.get("marge_session") is None, "Revoking the current session should clear the browser cookie.")
            revoked_session_access = client.get("/assistant/account", headers={"X-Marge-Account-Token": session_token})
            assert_true(revoked_session_access.status_code == 401, "Revoked session token should stop authenticating.")

            staff_invite = request_json(
                client,
                "POST",
                "/assistant/users/invite",
                token=token,
                json={"name": "Ministry Staff", "email": "staff@example.test", "role": "staff"},
            )
            staff_token = staff_invite["token"]
            assert_true(staff_invite["user"]["role"] == "staff", "Owner should be able to create a staff-scoped user token.")
            assert_true(staff_invite["delivery"]["status"] == "sent", "Configured invite outbox should mark staff invite delivery sent.")
            assert_true(staff_invite["delivery"]["channel"] == "file", "Smoke invite should use the deterministic file outbox.")
            with open(invite_outbox, "r", encoding="utf-8") as outbox:
                invite_text = outbox.read()
            invite_body = Parser(policy=email_policy.default).parsestr(invite_text.split("\n\n---\n\n", 1)[0]).get_content()
            assert_true(staff_token in invite_body, "Invite email should contain the one-time workspace token.")
            assert_true("invite_token=" in invite_body, "Invite email should include a frontend invite-token link.")

            request_json(
                client,
                "POST",
                "/assistant/login-links/request",
                json={"email": "staff@example.test", "church_slug": signup["slug"]},
            )
            with open(invite_outbox, "r", encoding="utf-8") as outbox:
                login_text = outbox.read()
            delivered_messages = login_text.rstrip().split("\n\n---\n\n")
            assert_true(len(delivered_messages) == 2, "Outbox should contain the staff invite and one passwordless login email.")
            request_json(
                client,
                "POST",
                "/assistant/login-links/request",
                json={"email": "staff@example.test", "church_slug": signup["slug"]},
            )
            with open(invite_outbox, "r", encoding="utf-8") as outbox:
                duplicate_login_text = outbox.read()
            assert_true(
                len(duplicate_login_text.rstrip().split("\n\n---\n\n")) == len(delivered_messages),
                "Repeated passwordless login requests inside the cooldown should not send another email.",
            )
            login_body = Parser(policy=email_policy.default).parsestr(login_text.rstrip().split("\n\n---\n\n")[-1]).get_content()
            login_match = re.search(r"login_token=([A-Za-z0-9_\\-]+)", login_body)
            assert_true(bool(login_match), "Passwordless login email should include a frontend login-token link.")
            login_token = login_match.group(1)
            assert_true(login_token.startswith("marge_login_"), "Passwordless login token should use the login-token prefix.")
            login_session = request_json(
                client,
                "POST",
                "/assistant/login-links/exchange",
                json={"token": login_token, "duration_hours": 2},
            )
            login_session_token = login_session["token"]
            assert_true(login_session_token.startswith("marge_sess_"), "Passwordless login should mint a normal Marge session.")
            assert_true(login_session["current_user"]["email"] == "staff@example.test", "Passwordless login should resolve the requested active user.")
            assert_true(client.cookies.get("marge_session") == login_session_token, "Passwordless login should set the HttpOnly session cookie.")
            replay_login = client.post("/assistant/login-links/exchange", json={"token": login_token, "duration_hours": 2})
            assert_true(replay_login.status_code == 401, "Passwordless login links should be single-use.")
            request_json(client, "DELETE", "/assistant/sessions/current", token=login_session_token)

            staff_account = request_json(client, "GET", "/assistant/account", token=staff_token)
            assert_true(staff_account["id"] == signup["account_id"], "Staff user token should stay scoped to the same church account.")
            assert_true(staff_account["current_role"] == "staff", "Staff user token should expose staff role.")
            staff_desk = request_json(client, "GET", "/assistant/desk?mode=auto", token=staff_token)
            assert_true(staff_desk["mode"] == "live", "Staff token should still read the live assistant desk.")
            assert_true(staff_desk.get("approvals") == [], "Staff desk access should not expose the assistant approval queue.")
            staff_profile_update = client.patch(
                "/assistant/profile",
                headers={"X-Marge-Account-Token": staff_token},
                json={"guardrails": "Staff should not change the pastor guardrails."},
            )
            assert_true(staff_profile_update.status_code == 403, "Staff token should not change the ministry profile.")
            staff_policy = client.patch(
                "/assistant/policies/google_workspace",
                headers={"X-Marge-Account-Token": staff_token},
                json={"write_enabled": True},
            )
            assert_true(staff_policy.status_code == 403, "Staff token should not change connector writeback policy.")
            assert_true("admin" in staff_policy.text.lower() and "owner" in staff_policy.text.lower(), "Role rejection should name the required admin roles.")
            staff_actions = client.get(
                "/assistant/actions?status=all&limit=10",
                headers={"X-Marge-Account-Token": staff_token},
            )
            assert_true(staff_actions.status_code == 403, "Staff token should not view assistant approval actions.")
            staff_action_create = client.post(
                "/assistant/actions",
                headers={"X-Marge-Account-Token": staff_token},
                json={"action_type": "email_draft", "title": "Staff draft", "description": "Should not save."},
            )
            assert_true(staff_action_create.status_code == 403, "Staff token should not create assistant approval actions.")
            staff_prepare = client.post(
                "/assistant/actions/prepare",
                headers={"X-Marge-Account-Token": staff_token},
                json={},
            )
            assert_true(staff_prepare.status_code == 403, "Staff token should not prepare assistant approval actions.")
            sensitive_member = request_json(
                client,
                "POST",
                "/members/",
                token=token,
                json={"first_name": "Sensitive", "last_name": "Member", "email": "sensitive.member@example.test"},
            )
            sensitive_visitor = request_json(
                client,
                "POST",
                "/visitors/",
                token=token,
                json={
                    "first_name": "Sensitive",
                    "last_name": "Visitor",
                    "email": "sensitive.visitor@example.test",
                    "visit_date": datetime.now(UTC).date().isoformat(),
                    "notes": "Needs pastoral follow-up.",
                },
            )
            staff_directory = request_json(client, "GET", "/members/?search=Sensitive%20Member&limit=10", token=staff_token)
            assert_true(len(staff_directory) == 1, "Staff token should still read the basic member directory.")
            staff_visitor_list = request_json(client, "GET", "/visitors/?limit=10", token=staff_token)
            assert_true(
                any(visitor["id"] == sensitive_visitor["id"] for visitor in staff_visitor_list),
                "Staff token should still read the basic visitor list.",
            )
            staff_desk_after_action = request_json(client, "GET", "/assistant/desk?mode=auto", token=staff_token)
            assert_true(
                staff_desk_after_action.get("approvals") == [],
                "Staff desk access should hide pending visitor welcome approvals.",
            )
            staff_member_create = client.post(
                "/members/",
                headers={"X-Marge-Account-Token": staff_token},
                json={"first_name": "Staff", "last_name": "Write"},
            )
            assert_true(staff_member_create.status_code == 403, "Staff token should not create congregation members.")
            staff_member_detail = client.get(
                f"/members/{sensitive_member['id']}",
                headers={"X-Marge-Account-Token": staff_token},
            )
            assert_true(staff_member_detail.status_code == 403, "Staff token should not read member pastoral details.")
            staff_member_note = client.post(
                f"/members/{sensitive_member['id']}/notes",
                headers={"X-Marge-Account-Token": staff_token},
                json={"note_text": "Staff should not save pastoral notes.", "context_tag": "care"},
            )
            assert_true(staff_member_note.status_code == 403, "Staff token should not add pastoral notes.")
            staff_visitor_update = client.patch(
                f"/visitors/{sensitive_visitor['id']}",
                headers={"X-Marge-Account-Token": staff_token},
                json={"notes": "Staff should not change visitor pastoral notes."},
            )
            assert_true(staff_visitor_update.status_code == 403, "Staff token should not update visitor records.")
            staff_visitor_draft = client.get(
                f"/visitors/{sensitive_visitor['id']}/draft?day=1",
                headers={"X-Marge-Account-Token": staff_token},
            )
            assert_true(staff_visitor_draft.status_code == 403, "Staff token should not draft visitor follow-up.")
            staff_care_list = client.get("/care/", headers={"X-Marge-Account-Token": staff_token})
            assert_true(staff_care_list.status_code == 403, "Staff token should not read care cases.")
            staff_prayer_list = client.get("/care/prayers/", headers={"X-Marge-Account-Token": staff_token})
            assert_true(staff_prayer_list.status_code == 403, "Staff token should not read prayer requests.")
            staff_draft = client.post(
                "/drafts/",
                headers={"X-Marge-Account-Token": staff_token},
                json={"kind": "absence", "member_id": sensitive_member["id"]},
            )
            assert_true(staff_draft.status_code == 403, "Staff token should not generate pastoral drafts.")
            staff_briefing = client.get("/briefing/today?mode=live", headers={"X-Marge-Account-Token": staff_token})
            assert_true(staff_briefing.status_code == 403, "Staff token should not read the pastoral morning briefing.")
            staff_legacy_chat = client.post(
                "/chat/",
                headers={"X-Marge-Account-Token": staff_token},
                json={"message": "New visitor Staff Test came Sunday.", "mode": "live"},
            )
            assert_true(staff_legacy_chat.status_code == 403, "Staff token should not bypass role gates through legacy chat.")
            last_owner_block = client.patch(
                f"/assistant/users/{signup['current_user']['id']}",
                headers={"X-Marge-Account-Token": token},
                json={"active": False},
            )
            assert_true(last_owner_block.status_code == 409, "Marge should not allow deactivating the last workspace owner.")
            deactivated_staff = request_json(
                client,
                "PATCH",
                f"/assistant/users/{staff_invite['user']['id']}",
                token=token,
                json={"active": False},
            )
            assert_true(not deactivated_staff["active"], "Owner should be able to deactivate a staff user token.")
            revoked = client.get("/assistant/account", headers={"X-Marge-Account-Token": staff_token})
            assert_true(revoked.status_code == 401, "Deactivated staff token should stop authenticating.")

            setup = request_json(client, "POST", "/assistant/integrations/google_workspace/start", token=token)
            assert_true(setup["status"] == "ready_to_authorize", "Google setup should be ready when server config is present.")
            assert_true(not setup["missing_config"], "Smoke-provided server config should satisfy OAuth setup.")
            assert_true(setup["authorization_url"], "OAuth setup should return a provider authorization URL.")

            state = parse_qs(urlparse(setup["authorization_url"]).query).get("state", [None])[0]
            assert_true(bool(state), "Authorization URL should include a state parameter.")

            db = SessionLocal()
            try:
                state_row = db.query(IntegrationOAuthState).filter(IntegrationOAuthState.state == state).one()
                assert_true(state_row.account_id == signup["account_id"], "OAuth state should be scoped to the church account.")
                assert_true(state_row.user_id == signup["current_user"]["id"], "OAuth state should be scoped to the initiating Marge user.")
                assert_true(state_row.consumed_at is None, "New OAuth state should be unconsumed before callback.")
            finally:
                db.close()

            assistant_router._exchange_oauth_code = fake_exchange
            callback = client.get(
                "/assistant/integrations/google_workspace/callback",
                params={"code": "local-smoke-code", "state": state},
            )
            assert_true(callback.status_code == 200, f"OAuth callback should succeed: {callback.text}")
            assert_true("access-token-secret-smoke" not in callback.text, "Callback page must not expose the access token.")
            assert_true("refresh-token-secret-smoke" not in callback.text, "Callback page must not expose the refresh token.")
            assert_true("Check credentials before syncing ministry data" in callback.text, "Callback page should tell pastors to check credentials before sync.")

            integrations = request_json(client, "GET", "/assistant/integrations", token=token)
            google = next(item for item in integrations if item["provider"] == "google_workspace")
            assert_true(google["status"] == "connected", "Google Workspace should report connected after callback.")
            assert_true("access-token-secret-smoke" not in json.dumps(google), "Integration status must not expose access tokens.")
            assert_true("refresh-token-secret-smoke" not in json.dumps(google), "Integration status must not expose refresh tokens.")
            assert_true(google["credential_scope"] == "user", "Google Workspace should report a user-scoped credential after user-token OAuth.")
            assert_true(not google["write_enabled"], "Google writeback should stay disabled by default.")
            assert_true(google["require_approval"], "Google policy should require pastor approval by default.")
            assert_true(google["verified_at"] is None, "OAuth callback should not mark Google ready to sync until credentials are checked.")
            assert_true(
                "check credentials before syncing ministry data" in google["config_hint"].lower(),
                "Unverified OAuth connector status should keep the credential-check boundary visible.",
            )
            desk_after_callback = request_json(client, "GET", "/assistant/desk?mode=auto", token=token)
            assert_true(
                desk_after_callback.get("stats", {}).get("connectors") == 0,
                "Unverified OAuth callback should not count as a ready church-tool connector on the desk.",
            )
            connector_status_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What tools are connected?", "mode": "live"},
            )
            assert_true(connector_status_chat["intent"] == "integration_status", "Connector status chat should stay read-only after OAuth callback.")
            assert_true(not connector_status_chat.get("actions"), "Connector status chat should not queue setup actions.")
            assert_true(
                "no church tools connected yet" in connector_status_chat["reply"].lower(),
                "Unverified connectors should not be described as ready church tools.",
            )
            assert_true(
                "credential check" in connector_status_chat["reply"].lower(),
                "Unverified connected tools should point to credential checks before readiness.",
            )
            unverified_connect_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Connect Google Workspace.", "mode": "live"},
            )
            unverified_connect_titles = {action.get("title") for action in unverified_connect_chat.get("actions", [])}
            assert_true(
                unverified_connect_chat["intent"] == "integration_setup_started",
                "Chat connector setup should stay actionable after OAuth callback.",
            )
            assert_true(
                "check credentials before syncing ministry data" in unverified_connect_chat["reply"].lower(),
                "Chat connector setup should point unchecked OAuth connectors to credential verification.",
            )
            assert_true(
                "Check Google Workspace credentials" in unverified_connect_titles,
                "Chat connector setup should attach a Check credentials card for unchecked OAuth connectors.",
            )
            assert_true(
                "Sync Google Workspace." not in (unverified_connect_chat.get("suggested_prompts") or []),
                "Unchecked connector setup should not suggest sync before credentials are checked.",
            )
            unverified_calendar_help = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What calendar details do you need?", "mode": "live"},
            )
            unverified_calendar_reply = unverified_calendar_help["reply"]
            unverified_calendar_titles = {action.get("title") for action in unverified_calendar_help.get("actions", [])}
            assert_true(
                unverified_calendar_help["intent"] == "calendar_event_details_help",
                "Calendar details help should stay available after OAuth callback.",
            )
            assert_true(
                "credential-checked google workspace or microsoft 365 calendar" in unverified_calendar_reply.lower(),
                "Unverified OAuth calendars should not be described as write-ready.",
            )
            unverified_calendar_text = json.dumps(unverified_calendar_help).lower()
            assert_true(
                "marcus" not in unverified_calendar_text and "example.test" not in unverified_calendar_text,
                "Calendar help should avoid fake person names and test email addresses in pastor-facing guidance.",
            )
            assert_true(
                "your connected google workspace calendar can stage" not in unverified_calendar_reply.lower(),
                "Calendar help should not claim unverified Google can stage writeback work.",
            )
            assert_true(
                "Check Google Workspace credentials" in unverified_calendar_titles,
                "Unverified Google calendar help should return a credential-check setup card.",
            )
            unverified_calendar_queue = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={
                    "message": "Queue a Google calendar event for Hospital follow-up with Marcus on 2026-05-18 at 3pm for 1 hour location County Hospital with marcus@example.test.",
                    "mode": "live",
                },
            )
            unverified_queue_titles = {action.get("title") for action in unverified_calendar_queue.get("actions", [])}
            assert_true(
                unverified_calendar_queue["intent"] == "calendar_event_provider_not_ready",
                "Complete calendar event prompts should not become generic missing-details prompts when the provider is unverified.",
            )
            assert_true(
                "enough event details" in unverified_calendar_queue["reply"].lower(),
                "Provider-not-ready calendar prompts should acknowledge the event details were understood.",
            )
            assert_true(
                "Check Google Workspace credentials" in unverified_queue_titles,
                "Provider-not-ready calendar prompts should attach a credential-check card.",
            )
            unverified_sync = client.post(
                "/assistant/integrations/google_workspace/sync?email_limit=5&calendar_days=14",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(unverified_sync.status_code == 409, "Connected Google credentials should not sync before a credential check.")
            assert_true("check credentials" in unverified_sync.text.lower(), "Pre-verification sync rejection should tell the pastor to check credentials.")
            assistant_router.requests.get = fake_google_get_failure
            try:
                failed_chat_sync = request_json(
                    client,
                    "POST",
                    "/assistant/chat",
                    token=token,
                    json={"message": "Sync Google Workspace.", "mode": "live"},
                )
            finally:
                assistant_router.requests.get = original_get
            failed_chat_sync_titles = {action.get("title") for action in failed_chat_sync.get("actions", [])}
            failed_chat_sync_prompts = failed_chat_sync.get("suggested_prompts") or []
            assert_true(
                failed_chat_sync["intent"] == "integration_verify_failed_before_sync",
                "Chat-triggered sync should stop at failed no-sync verification before importing Google context.",
            )
            assert_true(
                "No ministry data was imported and no actions were queued" in failed_chat_sync.get("reply", ""),
                "Failed chat-triggered sync should make the no-side-effect boundary explicit.",
            )
            assert_true(
                "Check Google Workspace credentials" in failed_chat_sync_titles,
                "Failed chat-triggered sync should attach the Google credential-check card.",
            )
            assert_true(
                "Check Google Workspace credentials." in failed_chat_sync_prompts,
                "Failed chat-triggered sync should suggest the exact credential-check prompt.",
            )
            assert_true(
                "Sync Google Workspace." not in failed_chat_sync_prompts,
                "Failed chat-triggered sync should not suggest retrying sync before credentials pass.",
            )
            request_json(
                client,
                "PATCH",
                "/assistant/policies/google_workspace",
                token=token,
                json={"write_enabled": True, "require_approval": True, "allowed_actions": ["email_draft"]},
            )
            unverified_write_action = request_json(
                client,
                "POST",
                "/assistant/actions",
                token=token,
                json={
                    "action_type": "email_draft",
                    "title": "Unverified Google draft",
                    "payload": {"email": {"to": "person@example.test", "subject": "Hello", "body": "Body"}},
                    "external_provider": "google_workspace",
                    "privacy_level": "pastoral",
                },
            )
            request_json(client, "POST", f"/assistant/actions/{unverified_write_action['id']}/approve", token=token, json={})
            unverified_write = client.post(
                f"/assistant/actions/{unverified_write_action['id']}/execute",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(unverified_write.status_code == 409, "External writeback should require checked credentials even after policy approval.")
            assert_true(
                "check credentials" in unverified_write.text.lower() and "before writing externally" in unverified_write.text.lower(),
                "Unverified writeback rejection should name the credential-check boundary.",
            )
            request_json(
                client,
                "PATCH",
                "/assistant/policies/google_workspace",
                token=token,
                json={"write_enabled": False, "require_approval": True, "allowed_actions": []},
            )

            valid_encryption_key = os.environ["MARGE_ENCRYPTION_KEY"]
            os.environ["MARGE_ENCRYPTION_KEY"] = "not-a-fernet-key"
            try:
                blocked_rock_setup = request_json(client, "POST", "/assistant/integrations/rock/start", token=token, json={})
                assert_true(
                    "MARGE_ENCRYPTION_KEY" in blocked_rock_setup.get("missing_config", []),
                    "Connector setup should treat an invalid encryption key as missing secure token storage.",
                )
                assert_true(
                    "ROCK_API_KEY" in blocked_rock_setup.get("missing_config", [])
                    and "ROCK_BASE_URL" in blocked_rock_setup.get("missing_config", []),
                    "Rock connector setup should ask for generic Rock API key and base URL config.",
                )
                assert_true(
                    "ROCK_HALLMARK_API_KEY" not in blocked_rock_setup.get("missing_config", []),
                    "Rock connector setup should not expose the old Hallmark-specific env var name.",
                )
                blocked_rock_credentials = client.post(
                    "/assistant/integrations/rock/credentials",
                    headers={"X-Marge-Account-Token": token},
                    json={"api_key": "rock-secret-smoke", "base_url": "https://rock.example.test"},
                )
                assert_true(
                    blocked_rock_credentials.status_code == 409,
                    "API-key connector credentials should not save when token encryption is misconfigured.",
                )
                assert_true(
                    "valid fernet key" in blocked_rock_credentials.text.lower(),
                    "Invalid encryption-key errors should tell the operator how to fix secure storage.",
                )
            finally:
                os.environ["MARGE_ENCRYPTION_KEY"] = valid_encryption_key
            rock_setup = request_json(client, "POST", "/assistant/integrations/rock/start", token=token, json={})
            assert_true(
                "MARGE_ENCRYPTION_KEY" not in rock_setup.get("missing_config", []),
                "Rock setup should accept workspace credentials when encrypted storage is configured.",
            )
            assert_true(
                "ROCK_API_KEY" in rock_setup.get("missing_config", [])
                and "ROCK_BASE_URL" in rock_setup.get("missing_config", []),
                "Rock setup should still surface optional server-side API key/base URL names before workspace credentials are saved.",
            )
            rock_setup_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Connect Rock RMS.", "mode": "live"},
            )
            rock_setup_titles = {action.get("title") for action in rock_setup_chat.get("actions", [])}
            rock_setup_actions = {action.get("action") for action in rock_setup_chat.get("actions", [])}
            assert_true(
                rock_setup_chat["intent"] == "integration_setup_started",
                "Chat should start Rock setup when workspace credentials can be entered.",
            )
            assert_true(
                "encrypted workspace credential setup" in rock_setup_chat["reply"].lower(),
                "Rock setup chat should point admins to encrypted workspace credentials instead of server config only.",
            )
            assert_true("Connect Rock RMS" in rock_setup_titles, "Rock setup chat should attach the Rock setup card.")
            assert_true(
                "Add encrypted credentials" in rock_setup_actions,
                "Rock setup card should be actionable when encrypted workspace credential storage is ready.",
            )
            missing_rock_base = client.post(
                "/assistant/integrations/rock/credentials",
                headers={"X-Marge-Account-Token": token},
                json={"api_key": "rock-secret-smoke"},
            )
            assert_true(missing_rock_base.status_code == 422, "Rock workspace credentials should require the church's API base URL.")
            assert_true("base url" in missing_rock_base.text.lower(), "Rock base URL validation should explain what is missing.")
            insecure_rock_base = client.post(
                "/assistant/integrations/rock/credentials",
                headers={"X-Marge-Account-Token": token},
                json={"api_key": "rock-secret-smoke", "base_url": "http://rock.example.test/api/v2"},
            )
            assert_true(insecure_rock_base.status_code == 422, "Rock workspace credentials should reject non-HTTPS API base URLs.")
            assert_true("https" in insecure_rock_base.text.lower(), "Insecure Rock base URL validation should tell the operator to use HTTPS.")
            private_rock_base = client.post(
                "/assistant/integrations/rock/credentials",
                headers={"X-Marge-Account-Token": token},
                json={"api_key": "rock-secret-smoke", "base_url": "https://127.0.0.1/api/v2"},
            )
            assert_true(private_rock_base.status_code == 422, "Rock workspace credentials should reject localhost/private-network API base URLs.")
            assert_true("public https" in private_rock_base.text.lower(), "Private Rock base URL validation should require a public HTTPS host.")
            unsafe_rock_base = client.post(
                "/assistant/integrations/rock/credentials",
                headers={"X-Marge-Account-Token": token},
                json={"api_key": "rock-secret-smoke", "base_url": "https://user:pass@rock.example.test/api/v2?api_key=leak"},
            )
            assert_true(unsafe_rock_base.status_code == 422, "Rock workspace credentials should reject base URLs that smuggle secrets or query strings.")
            assert_true(
                "without username, password, query, or fragment" in unsafe_rock_base.text.lower(),
                "Unsafe Rock base URL validation should explain which URL parts are not allowed.",
            )
            configured_rock = request_json(
                client,
                "POST",
                "/assistant/integrations/rock/credentials",
                token=token,
                json={"api_key": "rock-secret-smoke", "base_url": "https://rock.example.test/api/v2"},
            )
            assert_true(configured_rock["status"] == "configured", "Rock workspace credentials should save encrypted when key and base URL are present.")
            legacy_rock_sync = client.post(
                "/members/sync/rock",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(
                legacy_rock_sync.status_code == 409,
                "Legacy member Rock sync should not bypass credential verification.",
            )
            assert_true(
                "check credentials" in legacy_rock_sync.text.lower(),
                "Legacy member Rock sync should route through the verify-before-sync boundary.",
            )

            db = SessionLocal()
            try:
                pre_chat_verify_action_count = (
                    db.query(AssistantAction).filter(AssistantAction.account_id == signup["account_id"]).count()
                )
                pre_chat_verify_context_count = (
                    db.query(ConnectedContextItem).filter(ConnectedContextItem.account_id == signup["account_id"]).count()
                )
            finally:
                db.close()

            assistant_router.requests.get = fake_google_get
            chat_verification = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Check Google Workspace credentials.", "mode": "live"},
            )
            assert_true(
                chat_verification["intent"] == "integration_verified",
                "Following Marge's Check credentials prompt should run verification through chat.",
            )
            assert_true(
                "verified without syncing people, email, calendar, or attendance data" in chat_verification["reply"],
                "Chat credential checks should say they verified without syncing ministry data.",
            )
            assert_true(
                "did not queue any actions" in chat_verification["reply"].lower(),
                "Chat credential checks should not queue pastor actions.",
            )
            assert_true(
                chat_verification.get("actions") == [],
                "Chat credential checks should return no action cards after successful verification.",
            )
            assert_true(
                "Sync Google Workspace." in (chat_verification.get("suggested_prompts") or []),
                "Chat credential checks may suggest sync only after credentials are verified.",
            )
            short_chat_verification = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Check Google Workspace.", "mode": "live"},
            )
            assert_true(
                short_chat_verification["intent"] == "integration_verified",
                "Short Check provider prompts should also run no-sync credential verification through chat.",
            )
            assert_true(
                "verified without syncing people, email, calendar, or attendance data" in short_chat_verification["reply"],
                "Short Check provider prompts should still preserve the no-sync boundary.",
            )
            assert_true(
                short_chat_verification.get("actions") == [],
                "Short Check provider prompts should not queue pastor actions.",
            )
            db = SessionLocal()
            try:
                post_chat_verify_action_count = (
                    db.query(AssistantAction).filter(AssistantAction.account_id == signup["account_id"]).count()
                )
                post_chat_verify_context_count = (
                    db.query(ConnectedContextItem).filter(ConnectedContextItem.account_id == signup["account_id"]).count()
                )
                credential = (
                    db.query(IntegrationCredential)
                    .filter(IntegrationCredential.account_id == signup["account_id"], IntegrationCredential.provider == "google_workspace")
                    .one()
                )
                assert_true(credential.verified_at is not None, "Chat credential check should persist the verification timestamp.")
                assert_true(
                    post_chat_verify_action_count == pre_chat_verify_action_count,
                    "Chat credential checks should not create review actions.",
                )
                assert_true(
                    post_chat_verify_context_count == pre_chat_verify_context_count,
                    "Chat credential checks should not import connected context rows.",
                )
            finally:
                db.close()

            verification = request_json(client, "POST", "/assistant/integrations/google_workspace/verify", token=token, json={})
            assert_true(verification["status"] == "verified", "Google verification should complete without syncing ministry data.")
            assert_true(verification["credential_scope"] == "user", "Google verification should use the current user's OAuth credential.")
            assert_true(verification["identity"]["email"] == "owner@example.test", "Google verification should return non-secret account identity.")
            verified_integrations = request_json(client, "GET", "/assistant/integrations", token=token)
            verified_google = next(item for item in verified_integrations if item["provider"] == "google_workspace")
            assert_true(bool(verified_google["verified_at"]), "Google status should expose the non-secret credential check timestamp after verification.")
            desk_after_verification = request_json(client, "GET", "/assistant/desk?mode=auto", token=token)
            assert_true(
                desk_after_verification.get("stats", {}).get("connectors") == 1,
                "Verified external provider should count as one ready church-tool connector.",
            )
            verified_status_chat = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What tools are connected?", "mode": "live"},
            )
            assert_true(
                "ready now: google workspace" in verified_status_chat["reply"].lower(),
                "Connector status chat should name verified external providers as ready.",
            )
            verified_calendar_help = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What calendar details do you need?", "mode": "live"},
            )
            assert_true(
                "connected Google Workspace calendar can stage" in verified_calendar_help["reply"],
                "Credential-checked Google calendar should be described as ready to stage review items.",
            )
            assert_true(
                not verified_calendar_help.get("actions"),
                "Credential-checked calendar help should not attach setup cards.",
            )
            request_json(
                client,
                "PATCH",
                "/assistant/profile",
                token=token,
                json={"tools_in_use": "Google Workspace"},
            )

            db = SessionLocal()
            try:
                state_row = db.query(IntegrationOAuthState).filter(IntegrationOAuthState.state == state).one()
                assert_true(state_row.consumed_at is not None, "OAuth state should be consumed after callback.")
                credential = (
                    db.query(IntegrationCredential)
                    .filter(IntegrationCredential.account_id == signup["account_id"], IntegrationCredential.provider == "google_workspace")
                    .one()
                )
                assert_true(credential.user_id == signup["current_user"]["id"], "OAuth credential should be stored for the initiating Marge user.")
                assert_true(credential.verified_at is not None, "OAuth credential should persist its own verification timestamp.")
                assert_true("access-token-secret-smoke" not in credential.token_ciphertext, "Credential row should store ciphertext, not plaintext access token.")
                decrypted = decrypt_token_payload(credential.token_ciphertext)
                assert_true(decrypted["access_token"] == token_payload["access_token"], "Encrypted credential should decrypt to the token payload.")
            finally:
                db.close()

            pastor_invite = request_json(
                client,
                "POST",
                "/assistant/users/invite",
                token=token,
                json={"name": "Associate Pastor", "email": "pastor@example.test", "role": "pastor"},
            )
            pastor_token = pastor_invite["token"]
            pastor_integrations = request_json(client, "GET", "/assistant/integrations", token=pastor_token)
            pastor_google = next(item for item in pastor_integrations if item["provider"] == "google_workspace")
            assert_true(pastor_google["status"] == "needs_authorization", "Another pastor user should not inherit the owner's Google OAuth credential.")
            assert_true(pastor_google["credential_scope"] is None, "Unconnected pastor user should not see a credential scope.")
            pastor_prepared = request_json(client, "POST", "/assistant/actions/prepare?mode=live", token=pastor_token, json={})
            pastor_prepared_titles = {action["title"] for action in pastor_prepared}
            assert_true(
                "Connect Google Workspace" in pastor_prepared_titles,
                "Preparing actions for another pastor should use that pastor's OAuth readiness, not the owner's verified credential.",
            )
            pastor_sync = client.post(
                "/assistant/integrations/google_workspace/sync?email_limit=5&calendar_days=14",
                headers={"X-Marge-Account-Token": pastor_token},
                json={},
            )
            assert_true(pastor_sync.status_code == 409, "Pastor user without their own OAuth credential should not sync owner-connected Google data.")
            assert_true("not connected for this marge user" in pastor_sync.text.lower(), "User-specific sync rejection should name the missing user credential.")
            pastor_verify = client.post(
                "/assistant/integrations/google_workspace/verify",
                headers={"X-Marge-Account-Token": pastor_token},
                json={},
            )
            assert_true(pastor_verify.status_code == 409, "Pastor user without their own OAuth credential should not verify owner-connected Google data.")

            replay = client.get(
                "/assistant/integrations/google_workspace/callback",
                params={"code": "local-smoke-code", "state": state},
            )
            assert_true(replay.status_code == 400, "OAuth state replay should be rejected.")
            assert_true("already used" in replay.text, "Replay rejection should explain that the state was consumed.")

            audit = request_json(client, "GET", "/assistant/audit-log?limit=20", token=token)
            audit_text = json.dumps(audit)
            assert_true("access-token-secret-smoke" not in audit_text, "Audit log must not expose access tokens.")
            assert_true("refresh-token-secret-smoke" not in audit_text, "Audit log must not expose refresh tokens.")
            assert_true(staff_token not in audit_text, "Audit log must not expose workspace invite tokens.")
            assert_true(login_token not in audit_text, "Audit log must not expose passwordless login tokens.")

            request_json(
                client,
                "PATCH",
                "/assistant/profile",
                token=token,
                json={
                    "church_context": "Visitor follow-up matters because many new families are arriving tired.",
                    "support_preferences": "Nudge me gently and surface follow-up loops I am likely to miss.",
                    "communication_style": "warm and brief",
                    "guardrails": "Do not send emails or write to external systems without my approval.",
                },
            )
            assistant_router._fetch_google_messages = fake_google_messages
            assistant_router._fetch_google_calendar_events = fake_google_events
            sync = request_json(client, "POST", "/assistant/integrations/google_workspace/sync?email_limit=5&calendar_days=14", token=token)
            assert_true(sync["status"] == "synced", "Mocked Google sync should complete.")
            assert_true(sync["items_seen"] == 2, "Mocked Google sync should see one email and one calendar event.")
            assert_true(sync["actions_prepared"] == 2, "Mocked Google sync should queue email triage and meeting prep.")
            actions_after_sync = request_json(client, "GET", "/assistant/actions?status=all&limit=50", token=token)
            email_triage = next((action for action in actions_after_sync if action["action_type"] == "email_triage" and action["source"] == "google_workspace"), None)
            meeting_prep = next((action for action in actions_after_sync if action["action_type"] == "meeting_prep" and action["source"] == "google_workspace"), None)
            assert_true(email_triage is not None, "Google email sync should queue an email triage action.")
            assert_true("writeback policy" in json.dumps(email_triage.get("payload") or {}).lower(), "Google email triage should carry the writeback guardrail.")
            assert_true(meeting_prep is not None, "Google calendar sync should queue a meeting-prep action.")
            meeting_payload = meeting_prep.get("payload") or {}
            assert_true(bool(meeting_payload.get("brief")), "Google meeting-prep action should include a reviewable brief.")
            assert_true("Visitor follow-up matters" in meeting_payload["brief"], "Google meeting brief should use saved ministry context.")
            assert_true("Do not send emails" in meeting_payload["brief"], "Google meeting brief should preserve approval guardrails.")

            request_json(client, "POST", f"/assistant/actions/{email_triage['id']}/approve", token=token, json={})
            executed_triage = request_json(client, "POST", f"/assistant/actions/{email_triage['id']}/execute", token=token, json={})
            triage_execution = (executed_triage.get("payload") or {}).get("execution") or {}
            assert_true(triage_execution.get("kind") == "email_reply_drafted", "Executing email triage should prepare a reply draft.")
            draft_action_id = triage_execution.get("draft_action_id")
            assert_true(bool(draft_action_id), "Email triage execution should return the draft action id.")
            actions_after_draft = request_json(client, "GET", "/assistant/actions?status=all&limit=50", token=token)
            reply_draft = next((action for action in actions_after_draft if action["id"] == draft_action_id), None)
            assert_true(reply_draft is not None, "Email triage execution should create a separate draft action.")
            assert_true(reply_draft["action_type"] == "email_draft", "Email triage should create an email_draft action.")
            assert_true(reply_draft["status"] == "pending", "The generated email draft should still require pastor review.")
            assert_true(reply_draft.get("external_provider") == "google_workspace", "Google-sourced reply draft should retain its external provider.")
            draft_email = (reply_draft.get("payload") or {}).get("email") or {}
            assert_true(draft_email.get("source_message_id") == "gmail-smoke-message-1", "Reply draft should link back to the synced Gmail message.")
            assert_true("Draft note for review" not in (draft_email.get("body") or ""), "Sendable draft body should not include internal review notes.")
            draft_context = (reply_draft.get("payload") or {}).get("draft_context") or {}
            assert_true(draft_context.get("drafting_voice") == "warm and brief", "Reply draft should keep the saved drafting voice in review metadata.")
            assert_true("Do not send emails" in (draft_context.get("guardrail") or ""), "Reply draft metadata should preserve explicit approval guardrails.")

            second = request_json(
                client,
                "POST",
                "/assistant/signup",
                json={"pastor_name": "Pastor Other Smoke", "church_name": "OAuth Other Smoke Church", "email": "oauth-other-smoke@example.test"},
            )
            account_ids.append(second["account_id"])
            second_integrations = request_json(client, "GET", "/assistant/integrations", token=second["token"])
            second_google = next(item for item in second_integrations if item["provider"] == "google_workspace")
            assert_true(second_google["status"] != "connected", "Another church should not inherit the first church's OAuth credential.")

            action = request_json(
                client,
                "POST",
                "/assistant/actions",
                token=token,
                json={
                    "action_type": "email_draft",
                    "title": "Smoke Google draft",
                    "payload": {"email": {"to": "person@example.test", "subject": "Hello", "body": "Body"}},
                    "external_provider": "google_workspace",
                    "privacy_level": "pastoral",
                },
            )
            request_json(client, "POST", f"/assistant/actions/{action['id']}/approve", token=token, json={})
            blocked = client.post(f"/assistant/actions/{action['id']}/execute", headers={"X-Marge-Account-Token": token}, json={})
            assert_true(blocked.status_code == 403, f"Google writeback should be blocked by default policy: {blocked.text}")
            assert_true("writeback is disabled" in blocked.text, "Default writeback block should name church policy.")

            policy = request_json(
                client,
                "PATCH",
                "/assistant/policies/google_workspace",
                token=token,
                json={"write_enabled": True, "require_approval": True, "allowed_actions": ["email_draft"]},
            )
            assert_true(policy["write_enabled"], "Google writeback should be enabled only after explicit church policy.")
            assert_true(policy["require_approval"], "Google writeback policy should still require pastor approval.")
            assert_true(policy["allowed_actions"] == ["email_draft"], "Google writeback policy should stay narrowed to email drafts.")

            assistant_router.requests.post = fake_google_post
            request_json(client, "POST", f"/assistant/actions/{reply_draft['id']}/approve", token=token, json={})
            executed_draft = request_json(client, "POST", f"/assistant/actions/{reply_draft['id']}/execute", token=token, json={})
            draft_execution = (executed_draft.get("payload") or {}).get("execution") or {}
            assert_true(executed_draft["status"] == "executed", "Approved Google reply draft should execute after writeback policy is enabled.")
            assert_true(draft_execution.get("kind") == "gmail_draft", "Google reply draft execution should create a Gmail draft.")
            assert_true(draft_execution.get("provider") == "google_workspace", "Google reply draft execution should retain provider metadata.")
            assert_true(draft_execution.get("provider_id") == "gmail-draft-smoke-1", "Google reply draft execution should store the provider draft id.")

            disconnect = request_json(client, "DELETE", "/assistant/integrations/google_workspace", token=token)
            assert_true(disconnect["removed_credentials"] == 1, "Disconnect should remove the encrypted Google OAuth credential.")
            assert_true(disconnect["remaining_credentials"] == 0, "Disconnect should leave no Google credentials for the workspace.")
            assert_true(disconnect["status"] == "disconnected", "Disconnect should report the connector as disconnected when no credentials remain.")
            assert_true(not disconnect["write_enabled"], "Disconnect should shut Google writeback policy back off.")
            integrations_after_disconnect = request_json(client, "GET", "/assistant/integrations", token=token)
            google_after_disconnect = next(item for item in integrations_after_disconnect if item["provider"] == "google_workspace")
            assert_true(google_after_disconnect["status"] == "needs_authorization", "Current user should need reauthorization after disconnect.")
            assert_true(google_after_disconnect["credential_scope"] is None, "Disconnected provider status should not expose a credential scope.")
            policies_after_disconnect = request_json(client, "GET", "/assistant/policies", token=token)
            google_policy_after_disconnect = next(item for item in policies_after_disconnect if item["provider"] == "google_workspace")
            assert_true(not google_policy_after_disconnect["write_enabled"], "Google writeback policy should be disabled after disconnect.")
            verify_after_disconnect = client.post(
                "/assistant/integrations/google_workspace/verify",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(verify_after_disconnect.status_code == 409, "Disconnected Google credentials should not verify.")
            assert_true("not connected for this marge user" in verify_after_disconnect.text.lower(), "Disconnect verify failure should name the missing user credential.")
            sync_after_disconnect = client.post(
                "/assistant/integrations/google_workspace/sync?email_limit=5&calendar_days=14",
                headers={"X-Marge-Account-Token": token},
                json={},
            )
            assert_true(sync_after_disconnect.status_code == 409, "Disconnected Google credentials should not sync.")
            cached_google_context_after_disconnect = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Show Google Workspace context.", "mode": "live"},
            )
            cached_google_prompts = cached_google_context_after_disconnect.get("suggested_prompts") or []
            assert_true(
                cached_google_context_after_disconnect["intent"] == "connected_context_lookup",
                "Cached Google context should remain visible after disconnect.",
            )
            assert_true(
                "Visitor coffee with Jordan" in cached_google_context_after_disconnect.get("reply", "")
                or "Follow-up after Sunday" in cached_google_context_after_disconnect.get("reply", ""),
                "Cached Google context after disconnect should still show prior synced review context.",
            )
            assert_true(
                "secure setup" in cached_google_context_after_disconnect.get("reply", "").lower()
                and "no-sync credential check" in cached_google_context_after_disconnect.get("reply", "").lower(),
                "Cached Google context after disconnect should require setup/check before refresh.",
            )
            assert_true(
                "Sync Google Workspace again." not in cached_google_prompts,
                "Disconnected cached context should not suggest syncing Google again.",
            )
            assert_true(
                any(action.get("provider") == "google_workspace" for action in cached_google_context_after_disconnect.get("actions", [])),
                "Disconnected cached context should attach the Google reconnect/check card.",
            )
            cached_inbox_after_disconnect = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What is in my inbox?", "mode": "live"},
            )
            cached_inbox_prompts = cached_inbox_after_disconnect.get("suggested_prompts") or []
            assert_true(
                cached_inbox_after_disconnect["intent"] == "synced_inbox",
                "Cached synced inbox should remain visible after disconnect.",
            )
            assert_true(
                "secure setup" in cached_inbox_after_disconnect.get("reply", "").lower()
                and "no-sync credential check" in cached_inbox_after_disconnect.get("reply", "").lower(),
                "Cached synced inbox after disconnect should require setup/check before refresh.",
            )
            assert_true(
                "Sync the mailbox again." not in cached_inbox_prompts,
                "Disconnected cached inbox should not suggest syncing the mailbox again.",
            )
            assert_true(
                any(action.get("provider") == "google_workspace" for action in cached_inbox_after_disconnect.get("actions", [])),
                "Disconnected cached inbox should attach the Google reconnect/check card.",
            )
            queued_replies_after_disconnect = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "Queue replies for these.", "mode": "live"},
            )
            queued_reply_prompts = queued_replies_after_disconnect.get("suggested_prompts") or []
            assert_true(
                queued_replies_after_disconnect["intent"] == "draft_synced_email_replies_queued",
                "Cached synced inbox replies may still be drafted locally after disconnect.",
            )
            assert_true(
                "Sync the mailbox again." not in queued_reply_prompts,
                "Disconnected cached inbox reply drafts should not suggest mailbox sync.",
            )
            assert_true(
                any(action.get("provider") == "google_workspace" for action in queued_replies_after_disconnect.get("actions", [])),
                "Disconnected cached inbox reply drafts should keep the Google reconnect/check card visible.",
            )
            cached_meetings_after_disconnect = request_json(
                client,
                "POST",
                "/assistant/chat",
                token=token,
                json={"message": "What meetings need prep?", "mode": "live"},
            )
            cached_meeting_prompts = cached_meetings_after_disconnect.get("suggested_prompts") or []
            assert_true(
                cached_meetings_after_disconnect["intent"] == "meeting_prep_lookup",
                "Cached synced meetings should remain visible after disconnect.",
            )
            assert_true(
                "secure setup" in cached_meetings_after_disconnect.get("reply", "").lower()
                and "no-sync credential check" in cached_meetings_after_disconnect.get("reply", "").lower(),
                "Cached synced meetings after disconnect should require setup/check before refresh.",
            )
            assert_true(
                "Sync the calendar again." not in cached_meeting_prompts,
                "Disconnected cached meetings should not suggest calendar sync.",
            )
            assert_true(
                any(action.get("provider") == "google_workspace" for action in cached_meetings_after_disconnect.get("actions", [])),
                "Disconnected cached meetings should attach the Google reconnect/check card.",
            )

            db = SessionLocal()
            try:
                remaining_google_credentials = (
                    db.query(IntegrationCredential)
                    .filter(IntegrationCredential.account_id == signup["account_id"], IntegrationCredential.provider == "google_workspace")
                    .count()
                )
                assert_true(remaining_google_credentials == 0, "Disconnect should delete stored Google credential rows.")
            finally:
                db.close()

            disconnect_audit = request_json(client, "GET", "/assistant/audit-log?limit=10", token=token)
            disconnect_audit_text = json.dumps(disconnect_audit)
            assert_true("integration.disconnected" in disconnect_audit_text, "Disconnect should be audit logged.")
            assert_true("access-token-secret-smoke" not in disconnect_audit_text, "Disconnect audit must not expose access tokens.")
            assert_true("refresh-token-secret-smoke" not in disconnect_audit_text, "Disconnect audit must not expose refresh tokens.")

        print("Marge integration security smoke passed.")
        print(json.dumps({
            "checked_provider": "google_workspace",
            "oauth_state_replay_blocked": True,
            "token_storage": "encrypted",
            "revocable_sessions": "verified",
            "session_cookie_transport": "verified",
            "invite_email_delivery": "verified",
            "passwordless_login_links": "verified",
            "writeback_default": "disabled",
            "approved_gmail_draft_writeback": "verified",
            "role_scoped_user_tokens": "verified",
            "revoked_user_tokens": "verified",
            "user_scoped_oauth": "verified",
            "safe_connector_verification": "verified",
            "oauth_disconnect": "verified",
            "account_isolation": "verified",
            "strict_account_tokens": "verified",
            "mock_google_sync": "verified",
            "email_triage_to_draft": "verified",
        }, indent=2))
    finally:
        assistant_router._exchange_oauth_code = original_exchange
        assistant_router._fetch_google_messages = original_fetch_google_messages
        assistant_router._fetch_google_calendar_events = original_fetch_google_calendar_events
        assistant_router.requests.get = original_get
        assistant_router.requests.post = original_post
        for account_id in account_ids:
            cleanup_account(account_id)
        if os.path.exists(invite_outbox):
            os.remove(invite_outbox)


if __name__ == "__main__":
    main()
