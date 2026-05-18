#!/usr/bin/env python3
"""
Verify the first-run pastor workflow against a running Marge API.

Unlike the public bootstrap verifier, this script writes data: it creates a
temporary church workspace, exchanges the signup token for a revocable session,
teaches Marge ministry context through chat, logs the first real visitor through
chat, and checks that a reviewable welcome draft is queued. Local workspaces are
cleaned up automatically; remote deployments require --allow-remote-write and
must be cleaned up manually if desired.

Usage:
  .venv/bin/python scripts/verify_first_run_workspace.py

  MARGE_API_URL=https://marge.yourchurch.org \
  .venv/bin/python scripts/verify_first_run_workspace.py --allow-remote-write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_API_URL = "http://127.0.0.1:8000"


def is_local_url(api_url: str) -> bool:
    return "localhost" in api_url or "127.0.0.1" in api_url


def request(method: str, api_url: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> Any:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Marge-Account-Token"] = token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{api_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {detail}") from exc


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def pastor_display_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "Pastor"
    titled_prefixes = ("pastor", "rev", "reverend", "bishop", "elder", "father", "dr")
    first_word = cleaned.split()[0].strip(".").lower()
    if first_word in titled_prefixes:
        return cleaned
    return f"Pastor {cleaned}"


def cleanup_local_account(api_url: str, account_id: int | None) -> None:
    if account_id is None or not is_local_url(api_url):
        return

    from app.database import SessionLocal
    from app.models import (
        AccountLoginLink,
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
            AccountLoginLink,
            AccountSession,
            AccountPastorProfile,
            AccountUser,
        ]:
            db.query(model).filter(model.account_id == account_id).delete(synchronize_session=False)
        account = db.query(ChurchAccount).filter(ChurchAccount.id == account_id).one_or_none()
        if account:
            db.delete(account)
        db.commit()
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Marge's first-run workspace workflow.")
    parser.add_argument("--api-url", default=os.getenv("MARGE_API_URL", DEFAULT_API_URL), help="Marge API URL.")
    parser.add_argument("--allow-remote-write", action="store_true", help="Allow creating a disposable workspace on a non-local deployment.")
    parser.add_argument("--keep-local-workspace", action="store_true", help="Do not clean up the local disposable workspace after verification.")
    parser.add_argument("--church-name", help="Override the generated verification church name.")
    parser.add_argument("--pastor-name", help="Override the generated verification pastor name.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    local = is_local_url(api_url)
    if not local and not args.allow_remote_write:
        print(
            "Refusing to create a workspace on a non-local Marge API without --allow-remote-write.",
            file=sys.stderr,
        )
        return 2

    suffix = int(time.time())
    today = date.today().isoformat()
    church_name = args.church_name or f"First Run Verification Church {suffix}"
    pastor_name = args.pastor_name or f"First Run Verification {suffix}"
    pastor_display = pastor_display_name(pastor_name)
    email = f"first-run-verification-{suffix}@example.test"
    account_id = None

    try:
        signup = request(
            "POST",
            api_url,
            "/assistant/signup",
            {"church_name": church_name, "pastor_name": pastor_name, "email": email},
        )
        account_id = signup["account_id"]
        assert_true(signup["current_user"]["role"] == "owner", "Signup should return an owner workspace user.")
        owner_token = signup["token"]

        session = request("POST", api_url, "/assistant/sessions", {"duration_hours": 1}, token=owner_token)
        assert_true(session["token_type"] == "session", "Signup token should exchange for a revocable session token.")
        session_token = session["token"]

        current_session = request("GET", api_url, "/assistant/sessions/current", token=session_token)
        account = current_session.get("account") or {}
        assert_true(current_session.get("token_type") == "session", "Session lookup should report a session token.")
        assert_true(account.get("church_name") == church_name, "Session should stay scoped to the new church workspace.")

        initial_desk = request("GET", api_url, "/assistant/desk?mode=auto", token=session_token)
        initial_question = initial_desk.get("interview_question") or {}
        assert_true(initial_desk.get("mode") == "live", "A real workspace should start in live mode.")
        assert_true(
            f"Good morning, {pastor_display}." in (initial_desk.get("greeting") or ""),
            "First-run desk greeting should use a pastoral display name even when signup used a bare name.",
        )
        assert_true(church_name in (initial_question.get("question") or ""), "First question should name the pastor's church.")
        assert_true("Here are your people for today" not in (initial_desk.get("greeting") or ""), "New live workspaces should not show demo people.")

        onboarding_messages = [
            "I'm a bi-vocational solo pastor; we have 95 on Sundays.",
            f"{church_name} is a neighborhood church with first-time guests, young families, and tired volunteers.",
            "Our church tradition is non-denominational with Baptist roots; avoid insider language with guests.",
            "Our biggest pain is visitor follow-up and private prayer follow-up; they fall through the cracks.",
            "My first priority this month is closing loops with first-time guests and private prayer needs.",
            "Nudge me gently, protect my rest, and surface the people I am most likely to miss.",
            "Our stack is Planning Center and Gmail.",
            "Keep my drafts warm and brief. Fridays are my day off, Thursdays are sermon prep, hospital visits are Tuesday afternoons, and ask me before sending or changing anything.",
        ]
        for message in onboarding_messages:
            response = request("POST", api_url, "/assistant/chat", {"message": message, "mode": "live"}, token=session_token)
            assert_true(response.get("saved"), f"Onboarding chat should save context from: {message}")

        profile = request("GET", api_url, "/assistant/profile", token=session_token)
        assert_true(profile.get("completion_percent") == 100, "Onboarding should complete the ministry profile.")
        assert_true("Planning Center" in (profile.get("tools_in_use") or ""), "Saved tools should include Planning Center.")
        assert_true("Nudge me gently" in (profile.get("support_preferences") or ""), "Saved support preferences should preserve how the pastor wants Marge to help.")
        assert_true("warm and brief" in (profile.get("communication_style") or "").lower(), "Saved drafting voice should be preserved.")

        desk = request("GET", api_url, "/assistant/desk?mode=auto", token=session_token)
        setup_steps = desk.get("setup_steps") or []
        setup_titles = [step.get("title") for step in setup_steps]
        assert_true("Log the first real visitor" in setup_titles, "Completed empty workspace should ask for the first real visitor.")
        assert_true(any(step.get("provider") == "planning_center" for step in setup_steps), "Setup should recommend Planning Center from saved tools.")
        assert_true(any(step.get("provider") == "google_workspace" for step in setup_steps), "Setup should recommend Google Workspace from saved Gmail context.")

        morning = request("POST", api_url, "/assistant/chat", {"message": "Give me my morning briefing.", "mode": "live"}, token=session_token)
        morning_reply = morning.get("reply") or ""
        assert_true(morning.get("intent") == "morning_briefing", "Morning briefing should use the dedicated first-run route.")
        assert_true("first real ministry record" in morning_reply.lower(), "Empty briefing should ask for first real context, not pretend all is clear.")

        visitor_message = (
            f"Log the first visitor: Talia Brooks came Sunday {today}, "
            "talia.brooks@example.test, 555-0199, and asked about kids ministry."
        )
        visitor_chat = request("POST", api_url, "/assistant/chat", {"message": visitor_message, "mode": "live"}, token=session_token)
        assert_true(visitor_chat.get("intent") == "visitor_logged", "Concrete visitor chat should save the visitor.")
        assert_true(visitor_chat.get("saved"), "Visitor chat should persist the visitor.")
        assert_true("Talia Brooks" in (visitor_chat.get("reply") or ""), "Visitor reply should name the saved visitor.")

        actions = request("GET", api_url, "/assistant/actions?status=all&limit=80", token=session_token)
        welcome_actions = [
            action
            for action in actions
            if action.get("action_type") == "email_draft"
            and action.get("related_type") == "visitor"
            and "Talia Brooks" in (action.get("description") or "")
        ]
        assert_true(welcome_actions, "First visitor should queue a reviewable welcome draft.")
        welcome_payload = welcome_actions[0].get("payload") or {}
        welcome_email = welcome_payload.get("email") or {}
        draft_context = welcome_payload.get("draft_context") or {}
        assert_true(welcome_email.get("to") == "talia.brooks@example.test", "Welcome draft should preserve the visitor email.")
        assert_true("kids ministry" in (welcome_email.get("body") or "").lower(), "Welcome draft should use the visitor's actual note.")
        assert_true(draft_context.get("drafting_voice") == "warm and brief", "Welcome draft should carry saved drafting voice metadata.")

        visitors_lookup = request("POST", api_url, "/assistant/chat", {"message": "Show visitors needing follow-up.", "mode": "live"}, token=session_token)
        assert_true(
            "Talia Brooks" in (visitors_lookup.get("reply") or ""),
            f"Chat should recall saved visitor context. Got {visitors_lookup.get('intent')}: {visitors_lookup.get('reply')}",
        )

        print("Marge first-run workspace verification passed.")
        print(json.dumps({
            "api_url": api_url,
            "account_id": account_id,
            "church_name": church_name,
            "profile_completion": profile.get("completion_percent"),
            "setup_steps": setup_titles,
            "welcome_action": welcome_actions[0].get("title"),
            "remote_workspace_created": not local,
        }, indent=2))
        return 0
    finally:
        if local and not args.keep_local_workspace:
            cleanup_local_account(api_url, account_id)


if __name__ == "__main__":
    raise SystemExit(main())
