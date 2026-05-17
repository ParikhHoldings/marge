#!/usr/bin/env python3
"""
Smoke-test Marge's first-run pastor journey against a running local server.

This is intentionally lightweight and stdlib-only. It verifies the product path
that should make a new pastor feel Marge understands his ministry:

- create a church workspace
- teach Marge context through chat
- persist first-run chat history for continuity after reloads
- clear chat history without deleting saved ministry context
- preserve explicit approval guardrails
- learn the pastor's weekly rhythm before declaring first-run complete
- stay in live mode without demo people
- avoid placeholder calendar/approval work before Marge knows the rhythm
- queue data-seed, secure connector setup, and first-week approval work
- queue a reviewable welcome draft as soon as the first real visitor is logged
- queue the same welcome draft when a visitor is logged through chat
- keep legacy /chat/ routed through the connected assistant behavior
- keep saved pastor titles from being duplicated in generated drafts

Usage:
  .venv/bin/python scripts/smoke_first_run.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

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


def request_status(method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Marge-Account-Token"] = token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        try:
            parsed = json.loads(detail) if detail else None
        except json.JSONDecodeError:
            parsed = {"detail": detail}
        return exc.code, parsed


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


def main() -> None:
    suffix = int(time.time())
    church_name = f"First Run Smoke Church {suffix}"
    account_id = None
    identity_answer_account_id = None
    command_answer_account_id = None
    terse_answer_account_id = None
    prayer_answer_account_id = None
    care_answer_account_id = None
    try:
        missing_church_status, missing_church_body = request_status(
            "POST",
            "/assistant/signup",
            {"pastor_name": "Pastor Missing Church"},
        )
        assert_true(missing_church_status == 422, "Signup should reject blank church names instead of creating a generic workspace.")
        assert_true(
            "church name" in json.dumps(missing_church_body).lower(),
            "Blank church-name signup rejection should explain that church name is required.",
        )

        identity_answer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "church_name": f"Identity Answer Smoke Church {suffix}",
            },
        )
        identity_answer_token = identity_answer_signup["token"]
        identity_answer_account_id = identity_answer_signup["account_id"]
        identity_answer_reply = request(
            "POST",
            "/assistant/chat",
            {"message": "Pastor Ben.", "mode": "live"},
            identity_answer_token,
        )
        identity_answer_profile = request("GET", "/assistant/profile", token=identity_answer_token)
        assert_true(identity_answer_reply.get("saved") is True, "Pastor-name chat answers should be saved during first-run onboarding.")
        assert_true(
            identity_answer_profile.get("pastor_name") == "Pastor Ben",
            "Pastor-name chat answers should be normalized without trailing punctuation.",
        )

        signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor First Run Smoke",
                "church_name": church_name,
            },
        )
        token = signup["token"]
        account_id = signup["account_id"]
        assert_true(signup["current_user"]["role"] == "owner", "First-run signup should return an owner user token.")

        initial_desk = request("GET", "/assistant/desk?mode=auto", token=token)
        assert_true(initial_desk.get("mode") == "live", "A real workspace in auto mode should stay live at signup.")
        assert_true("Here are your people for today" not in initial_desk.get("greeting", ""), "A new empty workspace should not greet the pastor as if real people data already exists.")
        initial_question = initial_desk.get("interview_question") or {}
        assert_true(initial_question.get("field") == "role_title", "First-run desk should start with the next ministry-context question.")
        assert_true(church_name in (initial_question.get("question") or ""), "The first interview question should name the pastor's church.")
        initial_setup_steps = initial_desk.get("setup_steps") or []
        assert_true(initial_setup_steps and church_name in (initial_setup_steps[0].get("subtitle") or ""), "The first setup card should use the same contextual ministry question.")
        assert_true(initial_desk.get("stats", {}).get("connectors") == 0, "Local MCP should not count as a connected church tool.")
        assert_true(initial_desk.get("calendar_blocks") == [], "A new incomplete workspace should not show placeholder calendar blocks.")
        assert_true(
            not any(item.get("source") == "calendar" for item in initial_desk.get("approvals", [])),
            "A new incomplete workspace should not show placeholder calendar approvals.",
        )
        initial_prompts_text = " ".join(initial_desk.get("suggested_prompts") or []).lower()
        assert_true("draft" not in initial_prompts_text and "before noon" not in initial_prompts_text, "Incomplete first-run prompts should ask ministry-context questions instead of operational placeholder work.")

        messages = [
            "I'm a bi-vocational solo pastor; we have 85 on Sundays.",
            f"We're in a small neighborhood around {church_name} with a lot of new families, first-time guests, and tired volunteers.",
            "Our church tradition is non-denominational with Baptist roots; avoid insider language with guests.",
            "Our biggest pain is visitor follow-up and prayer follow-up; they fall through the cracks.",
            "My first priority this month is closing loops with first-time guests and private prayer needs.",
            "Our stack is Planning Center and Gmail.",
            "Keep my drafts warm and brief. Fridays are my day off, Thursdays are sermon prep, hospital visits are Tuesday afternoons, and ask me before sending or changing anything.",
        ]
        turns = []
        for index, message in enumerate(messages):
            turns.append(request("POST", "/assistant/chat", {"message": message, "mode": "live"}, token))
            if index == 2:
                tradition_desk = request("GET", "/assistant/desk?mode=auto", token=token)
                tradition_question = tradition_desk.get("interview_question") or {}
                assert_true(tradition_question.get("field") == "followup_pain", "After church voice is learned, the next question should ask where follow-up breaks.")
                assert_true(
                    "non-denominational" in turns[-1].get("reply", "").lower(),
                    "Marge should reflect the saved church voice back to the pastor.",
                )
            if index == 3:
                partial_desk = request("GET", "/assistant/desk?mode=auto", token=token)
                partial_question = partial_desk.get("interview_question") or {}
                assert_true(partial_question.get("field") == "ministry_priorities", "After follow-up pain is learned, the next question should ask what Marge should help move first.")
                assert_true(
                    "visitor follow-up" in (partial_question.get("question") or "").lower(),
                    "The priority question should be contextual to the pastor's named follow-up burden.",
                )
                contextual_chat_question = request(
                    "POST",
                    "/assistant/chat",
                    {"message": "What do you still need to learn about my ministry?", "mode": "live"},
                    token,
                )
                assert_true(contextual_chat_question.get("intent") == "onboarding", "Chat should answer first-run context questions.")
                assert_true(
                    "visitor follow-up" in contextual_chat_question.get("reply", "").lower(),
                    "Chat should ask the next context question using the pastor's named follow-up burden.",
                )
                assert_true(
                    "first month" in contextual_chat_question.get("reply", "").lower()
                    or "priority" in contextual_chat_question.get("reply", "").lower(),
                    "The contextual chat question should ask what Marge should help move first before secure tool setup.",
                )
                context_usage = request(
                    "POST",
                    "/assistant/chat",
                    {"message": "How will you use this context?", "mode": "live"},
                    token,
                )
                context_usage_reply = context_usage.get("reply", "")
                assert_true(
                    context_usage.get("intent") == "ministry_context_usage",
                    "Suggested first-run context-usage prompts should have a dedicated chat response.",
                )
                assert_true(
                    "visitor follow-up" in context_usage_reply.lower(),
                    "Context-usage reply should explain how Marge uses the pastor's saved follow-up burden.",
                )
                assert_true(
                    "non-denominational" in context_usage_reply.lower(),
                    "Context-usage reply should preserve the saved church voice and tradition.",
                )
                assert_true(
                    "approval" in context_usage_reply.lower(),
                    "Context-usage reply should keep pastor approval boundaries visible.",
                )
                assert_true(
                    any(action.get("type") == "profile_setup" for action in context_usage.get("actions", [])),
                    "Context-usage reply should attach the next first-run setup question while onboarding is incomplete.",
                )
                partial_prompts_text = " ".join(partial_desk.get("suggested_prompts") or []).lower()
                assert_true("draft" not in partial_prompts_text and "before noon" not in partial_prompts_text, "Partial first-run prompts should stay focused on context and secure setup.")
        profile = request("GET", "/assistant/profile", token=token)
        briefing = request("GET", "/briefing/today?mode=auto", token=token)
        desk = request("GET", "/assistant/desk?mode=auto", token=token)
        actions = request("GET", "/assistant/actions?status=all&limit=20", token=token)
        chat_history = request("GET", "/assistant/chat/history?limit=40", token=token)

        setup_steps = desk.get("setup_steps", [])
        setup_titles = {step.get("title") for step in setup_steps}
        action_types = {action.get("action_type") for action in actions}
        first_week_action = next((action for action in actions if action.get("action_type") == "first_week_plan"), None)
        guardrails = profile.get("guardrails") or ""

        assert_true(all(turn.get("saved") for turn in turns), "Every onboarding chat turn should save context.")
        assert_true(
            not any(turn.get("intent") == "visitor_logged" for turn in turns),
            "Generic first-run church-context mentions of guests or new families should not be logged as a visitor.",
        )
        final_turn_prompts_text = " ".join(turns[-1].get("suggested_prompts") or []).lower()
        assert_true(
            "first real visitor" in final_turn_prompts_text,
            "The completion chat turn should point to the first concrete ministry record Marge needs next.",
        )
        assert_true(
            "draft" not in final_turn_prompts_text and "before noon" not in final_turn_prompts_text,
            "The completion chat turn should not show generic operational prompts before real data exists.",
        )
        assert_true(len(chat_history) >= len(messages) * 2, "First-run chat turns should be persisted as user and Marge messages.")
        assert_true(chat_history[-1].get("role") == "assistant", "Persisted chat history should include Marge's latest reply.")
        assert_true(
            any("visitor follow-up" in (message.get("content") or "").lower() for message in chat_history),
            "Persisted chat history should preserve the pastor's unique follow-up burden.",
        )
        assert_true(profile.get("completion_percent") == 100, "Profile should be complete after the first-run chat sequence.")
        assert_true("Pastor Pastor" not in briefing.get("greeting", ""), "Briefing greeting should not duplicate the Pastor title when the saved name includes it.")
        assert_true("Pastor First Run Smoke" in briefing.get("greeting", ""), "Briefing greeting should still address the saved workspace pastor by name.")
        assert_true(profile.get("missing_fields") == [], "No required first-run profile fields should remain missing.")
        assert_true(profile.get("role_title") == "Bivocational Solo Pastor", "Natural role wording should preserve bivocational context.")
        assert_true(profile.get("congregation_size") == "85", "Congregation size should be extracted from chat.")
        assert_true(profile.get("church_name") == church_name, "Named church-context sentences should not overwrite the saved church name with a description.")
        assert_true("new families" in (profile.get("church_context") or ""), "Natural church context should be personal, not generic.")
        assert_true("non-denominational" in (profile.get("faith_tradition") or "").lower(), "Church voice and tradition should be saved from natural first-run wording.")
        assert_true("insider language" in (profile.get("faith_tradition") or "").lower(), "Church voice should preserve language boundaries the pastor gives.")
        assert_true("visitor follow-up" in (profile.get("followup_pain") or "").lower(), "Follow-up burden should be saved.")
        assert_true("prayer follow-up" in (profile.get("followup_pain") or "").lower(), "Natural fall-through-cracks wording should preserve the prayer follow-up burden.")
        assert_true("first-time guests" in (profile.get("ministry_priorities") or "").lower(), "First-run chat should save the pastor's stated first ministry priority.")
        assert_true(profile.get("tools_in_use") == "Planning Center, Gmail", "Known tools should be extracted.")
        assert_true(profile.get("communication_style") == "warm and brief", "Drafting voice should be saved.")
        assert_true("Fridays are my day off" in (profile.get("weekly_rhythm") or ""), "Weekly rhythm should preserve day-off context.")
        assert_true("Thursdays are sermon prep" in (profile.get("weekly_rhythm") or ""), "Weekly rhythm should be saved before setup actions are prepared.")
        assert_true("warm and brief" not in (profile.get("weekly_rhythm") or "").lower(), "Weekly rhythm should not absorb drafting voice when fields are answered together.")
        assert_true("do not send" not in (profile.get("weekly_rhythm") or "").lower(), "Weekly rhythm should not absorb approval guardrails when fields are answered together.")
        assert_true("ask me before" not in (profile.get("weekly_rhythm") or "").lower(), "Weekly rhythm should not absorb ask-me-before guardrails when fields are answered together.")
        assert_true(desk.get("stats", {}).get("connectors") == 0, "Planned providers should not count as ready church tools.")
        assert_true(
            not any(item.get("source") == "calendar" for item in desk.get("approvals", [])),
            "Calendar suggestions should not appear as approval-queue items until Marge has prepared a real action.",
        )
        assert_true(
            guardrails.lower().startswith(("do not", "never", "no ", "ask me before")) or " not " in guardrails.lower(),
            "Guardrails must preserve the pastor's approval boundary.",
        )
        assert_true("Ask me before sending" in guardrails, "Natural ask-me-before guardrails should be saved.")
        assert_true(desk.get("mode") == "live", "A real workspace in auto mode should stay live, not demo.")
        assert_true("Log the first real visitor" in setup_titles, "Live-empty workspace should ask for the first relevant real ministry record.")
        assert_true(any(step.get("form") == "visitor" for step in setup_steps), "Visitor pain should route data seed to visitor form.")
        assert_true("Connect Google Workspace" in setup_titles, "Gmail should recommend Google Workspace setup.")
        assert_true("Connect Planning Center" in setup_titles, "Planning Center should recommend secure setup.")
        assert_true({"data_seed", "integration_setup", "first_week_plan"}.issubset(action_types), "Setup actions should be queued.")

        secure_connections = request(
            "POST",
            "/assistant/chat",
            {"message": "How do secure connections work?", "mode": "live"},
            token,
        )
        secure_connections_reply = secure_connections.get("reply", "")
        assert_true(
            secure_connections.get("intent") == "secure_connections_explained",
            "Secure-connection prompt should explain the connector safety model, not only list status.",
        )
        assert_true(
            "check credentials" in secure_connections_reply.lower(),
            "Secure-connection explanation should mention the no-sync credential check.",
        )
        assert_true(
            "passwords in chat" in secure_connections_reply.lower(),
            "Secure-connection explanation should state that Marge does not ask for passwords in chat.",
        )
        assert_true(
            "approval" in secure_connections_reply.lower(),
            "Secure-connection explanation should preserve external-write approval boundaries.",
        )
        assert_true(
            any(action.get("type") == "integration_setup" for action in secure_connections.get("actions", [])),
            "Secure-connection explanation should attach connector setup cards when tools are known.",
        )

        proactive_summary = desk.get("proactive_summary", "")
        assert_true("log the first real visitor" in proactive_summary.lower(), "Proactive first-run summary should name the concrete next ministry setup action.")
        assert_true("seed marge" not in proactive_summary.lower(), "Proactive first-run summary should not expose internal setup labels.")
        completed_setup_prompts = " ".join(desk.get("suggested_prompts") or []).lower()
        assert_true("log the first real visitor" in completed_setup_prompts, "Complete but empty first-run prompts should point to the first real ministry record.")
        assert_true("draft" not in completed_setup_prompts and "before noon" not in completed_setup_prompts, "Complete but empty first-run prompts should not imply drafts or priorities already exist.")

        setup_reason = request(
            "POST",
            "/assistant/chat",
            {"message": "Why is this the next step?", "mode": "live"},
            token,
        )
        setup_reason_reply = setup_reason.get("reply", "")
        assert_true(
            setup_reason.get("intent") == "setup_step_reason",
            "Suggested setup-reason prompts should explain the current setup step instead of falling through.",
        )
        assert_true(
            "real visitor" in setup_reason_reply.lower(),
            "Setup-reason reply should explain why the first real visitor is next for visitor follow-up pain.",
        )
        assert_true(
            "approval" in setup_reason_reply.lower(),
            "Setup-reason reply should keep approval boundaries visible.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in setup_reason.get("actions", [])),
            "Setup-reason reply should attach the active first-record setup card.",
        )

        ministry_update_help = request(
            "POST",
            "/assistant/chat",
            {"message": "Help me add a ministry update.", "mode": "live"},
            token,
        )
        ministry_update_reply = ministry_update_help.get("reply", "")
        assert_true(
            ministry_update_help.get("intent") == "ministry_update_guidance",
            "Visible ministry-update prompt should guide concrete pastoral logging instead of generic onboarding.",
        )
        assert_true(
            "first real ministry record" in ministry_update_reply.lower() and "real visitor" in ministry_update_reply.lower(),
            "Ministry-update guidance should point to the current first-record setup when the workspace is still empty.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in ministry_update_help.get("actions", [])),
            "Ministry-update guidance should attach the active first-record setup card while data seed is pending.",
        )

        priority_guidance = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I do for first-time guests and private prayer needs?", "mode": "live"},
            token,
        )
        priority_guidance_reply = priority_guidance.get("reply", "")
        assert_true(
            priority_guidance.get("intent") == "ministry_priority_guidance",
            "Saved-priority fallback prompts should guide the pastor from profile context instead of generic onboarding.",
        )
        assert_true(
            "first-time guests" in priority_guidance_reply.lower() and "first real visitor" in priority_guidance_reply.lower(),
            "Priority guidance should connect the saved ministry priority to the active first-record setup.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in priority_guidance.get("actions", [])),
            "Priority guidance should attach the active first-record setup card while data seed is pending.",
        )

        data_seed_action = next((action for action in actions if action.get("action_type") == "data_seed"), None)
        assert_true(data_seed_action is not None, "Data-seed setup should become an approval-card action.")
        data_seed_step = (data_seed_action.get("payload") or {}).get("setup_step") or {}
        assert_true(data_seed_step.get("form") == "visitor", "Data-seed approval card should preserve the visitor form target.")
        assert_true(data_seed_step.get("action") == "Log first visitor", "Data-seed approval card should expose the concrete visitor CTA.")

        setup_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "Show my setup steps.", "mode": "live"},
            token,
        )
        assert_true(
            setup_lookup.get("intent") == "setup_steps_lookup",
            "Suggested setup-step prompts should return the concrete setup path.",
        )
        setup_lookup_actions = setup_lookup.get("actions") or []
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in setup_lookup_actions),
            "Setup-step lookup should include the visitor data-seed card.",
        )
        assert_true(
            any(action.get("provider") == "google_workspace" for action in setup_lookup_actions)
            and any(action.get("provider") == "planning_center" for action in setup_lookup_actions),
            "Setup-step lookup should include the recommended secure connector cards.",
        )

        new_family_coaching = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I ask a new family?", "mode": "live"},
            token,
        )
        new_family_reply = new_family_coaching.get("reply", "")
        assert_true(
            new_family_coaching.get("intent") == "first_record_coaching",
            "Suggested first-record coaching prompts should not fall through to the generic fallback.",
        )
        assert_true("names" in new_family_reply.lower() and "contact" in new_family_reply.lower(), "New-family coaching should name useful visitor details.")
        assert_true("welcome draft" in new_family_reply.lower(), "New-family coaching should explain the reviewable welcome draft outcome.")
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in new_family_coaching.get("actions", [])),
            "New-family coaching should keep the visitor data-seed card attached.",
        )

        google_setup_action = next((action for action in actions if action.get("title") == "Connect Google Workspace"), None)
        planning_setup_action = next((action for action in actions if action.get("title") == "Connect Planning Center"), None)
        assert_true(google_setup_action is not None, "Google Workspace setup should become an approval-card action.")
        assert_true(planning_setup_action is not None, "Planning Center setup should become an approval-card action.")
        assert_true(
            ((google_setup_action.get("payload") or {}).get("setup_step") or {}).get("provider") == "google_workspace",
            "Google setup approval card should preserve its provider key for secure setup.",
        )
        assert_true(
            ((planning_setup_action.get("payload") or {}).get("setup_step") or {}).get("provider") == "planning_center",
            "Planning Center setup approval card should preserve its provider key for secure setup.",
        )

        connect_first = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I connect first?", "mode": "live"},
            token,
        )
        assert_true(connect_first.get("intent") == "integration_setup_started", "A generic connect-first prompt should start secure setup, not only list connector status.")
        assert_true(connect_first.get("actions"), "Connect-first chat should return a setup action card.")
        assert_true(
            connect_first["actions"][0].get("provider") == "google_workspace",
            "Connect-first chat should choose the first relevant saved tool and preserve its provider key.",
        )

        assert_true(first_week_action is not None, "Profile completion should queue a first-week launch plan.")
        first_week_plan = (first_week_action.get("payload") or {}).get("plan") or []
        first_week_text = json.dumps(first_week_plan)
        first_week_titles = {item.get("title") for item in first_week_plan}
        assert_true("Log the first real visitor" in first_week_titles, "First-week plan should start with the first relevant real ministry record.")
        assert_true("Connect Google Workspace" in first_week_titles, "First-week plan should include Google Workspace setup.")
        assert_true("Connect Planning Center" in first_week_titles, "First-week plan should include Planning Center setup.")
        assert_true("Thursdays are sermon prep" in first_week_text, "First-week plan should preserve the pastor's weekly rhythm.")
        assert_true("non-denominational" in first_week_text.lower(), "First-week plan should preserve church voice and tradition.")
        assert_true("Ask me before sending" in first_week_text, "First-week plan should preserve explicit approval guardrails.")

        prepared_first_week = request(
            "POST",
            "/assistant/chat",
            {"message": "Prepare my first-week plan.", "mode": "live"},
            token,
        )
        prepared_first_week_reply = prepared_first_week.get("reply", "")
        assert_true(
            prepared_first_week.get("intent") == "first_week_plan_prepared",
            "Suggested first-week plan prompts should return a concrete reviewable plan, not the generic fallback.",
        )
        assert_true(
            "Log the first real visitor" in prepared_first_week_reply,
            "First-week plan chat should start with the first relevant real ministry record.",
        )
        assert_true(
            "Connect Google Workspace" in prepared_first_week_reply and "Connect Planning Center" in prepared_first_week_reply,
            "First-week plan chat should include the saved tool setup path.",
        )
        assert_true(
            any(action.get("type") == "first_week_plan" for action in prepared_first_week.get("actions", [])),
            "First-week plan chat should return the first-week plan action card.",
        )

        connector_status = request(
            "POST",
            "/assistant/chat",
            {"message": "What tools are connected?", "mode": "live"},
            token,
        )
        assert_true(connector_status.get("intent") == "integration_status", "A connector status question should stay read-only.")
        assert_true(not connector_status.get("actions"), "A connector status question should not queue setup actions.")
        assert_true(
            "no church tools connected yet" in connector_status.get("reply", "").lower(),
            "Connector status should not imply MCP is a connected church tool.",
        )
        assert_true("mcp only" not in connector_status.get("reply", "").lower(), "Connector status should not use MCP as a pastor-facing ready tool.")

        open_integrations = request(
            "POST",
            "/assistant/chat",
            {"message": "Open integrations.", "mode": "live"},
            token,
        )
        open_integrations_reply = open_integrations.get("reply", "")
        open_integration_action_providers = {action.get("provider") for action in open_integrations.get("actions", [])}
        assert_true(
            open_integrations.get("intent") == "integrations_opened",
            "Open integrations should return actionable connector cards instead of a generic chat fallback.",
        )
        assert_true(
            {"google_workspace", "planning_center"}.issubset(open_integration_action_providers),
            "Open integrations should attach setup cards for the saved Google and Planning Center tools.",
        )
        assert_true(
            "local mcp" in open_integrations_reply.lower(),
            "Open integrations should explicitly keep MCP out of pastor-facing church-tool readiness.",
        )
        assert_true(
            "no church tools yet" in open_integrations_reply.lower(),
            "Open integrations should show no verified church tools before secure setup/check.",
        )

        connected_tools_sync = request(
            "POST",
            "/assistant/chat",
            {"message": "Sync the connected tools.", "mode": "live"},
            token,
        )
        connected_tools_sync_reply = connected_tools_sync.get("reply", "")
        connected_tools_sync_action_providers = {action.get("provider") for action in connected_tools_sync.get("actions", [])}
        assert_true(
            connected_tools_sync.get("intent") == "connected_tools_sync_precheck",
            "The suggested connected-tools sync prompt should run a credential-aware precheck, not generic connector status.",
        )
        assert_true(
            "no church tool has completed secure setup" in connected_tools_sync_reply.lower(),
            "Connected-tools sync should explain setup and credential checks are required before sync.",
        )
        assert_true(
            "no ministry data was imported" in connected_tools_sync_reply.lower(),
            "Connected-tools sync precheck should say it did not import ministry data.",
        )
        assert_true(
            {"google_workspace", "planning_center"}.issubset(connected_tools_sync_action_providers),
            "Connected-tools sync precheck should return setup cards for the saved Google and Planning Center tools.",
        )
        assert_true(
            "mcp" not in connected_tools_sync_reply.lower(),
            "Connected-tools sync precheck should not treat MCP as a pastor-facing church tool.",
        )
        assert_true(
            "i synced google workspace" not in connected_tools_sync_reply.lower(),
            "Connected-tools sync precheck must not default to syncing Google when no church tool is verified.",
        )

        actions_before_calendar_help = request("GET", "/assistant/actions?status=all&limit=100", token=token)
        calendar_details_no_provider = request(
            "POST",
            "/assistant/chat",
            {"message": "What calendar details do you need?", "mode": "live"},
            token,
        )
        calendar_details_no_provider_reply = calendar_details_no_provider.get("reply", "")
        calendar_details_no_provider_actions = calendar_details_no_provider.get("actions", [])
        calendar_details_no_provider_providers = {action.get("provider") for action in calendar_details_no_provider_actions}
        assert_true(
            calendar_details_no_provider.get("intent") == "calendar_event_details_help",
            "Calendar details help should be a real first-run chat route before any calendar provider is connected.",
        )
        assert_true(
            "YYYY-MM-DD" in calendar_details_no_provider_reply and "start time" in calendar_details_no_provider_reply.lower(),
            "Calendar details help should explain the required date and start-time fields.",
        )
        assert_true(
            "Google Workspace or Microsoft 365" in calendar_details_no_provider_reply,
            "Calendar details help should name the supported calendar write providers when none is connected.",
        )
        assert_true(
            "approval" in calendar_details_no_provider_reply.lower() or "approve" in calendar_details_no_provider_reply.lower(),
            "Calendar details help should keep calendar write approval boundaries visible.",
        )
        assert_true(
            {"google_workspace", "microsoft_365"}.issubset(calendar_details_no_provider_providers),
            "Calendar details help should attach Google Workspace and Microsoft 365 setup/check cards when no write provider is connected.",
        )
        actions_after_calendar_help = request("GET", "/assistant/actions?status=all&limit=100", token=token)
        assert_true(
            len(actions_after_calendar_help) == len(actions_before_calendar_help),
            "Calendar details help should not create approval actions.",
        )

        pre_connector_help = request(
            "POST",
            "/assistant/chat",
            {"message": "What can you do before tools are connected?", "mode": "live"},
            token,
        )
        pre_connector_reply = pre_connector_help.get("reply", "")
        assert_true(pre_connector_help.get("intent") == "pre_connector_help", "Pre-connector help should explain useful local work, not only connector status.")
        assert_true("local people" in pre_connector_reply.lower(), "Pre-connector help should mention local ministry memory.")
        assert_true("draft reviewable follow-up" in pre_connector_reply.lower(), "Pre-connector help should mention reviewable local drafts.")
        assert_true("secure connectors come next" in pre_connector_reply.lower(), "Pre-connector help should preserve the secure setup/check/sync order.")
        assert_true(
            any(action.get("type") == "data_seed" for action in pre_connector_help.get("actions", [])),
            "Pre-connector help should return the concrete first-record setup card.",
        )

        approval_rules = request(
            "POST",
            "/assistant/chat",
            {"message": "Explain the approval rules.", "mode": "live"},
            token,
        )
        approval_reply = approval_rules.get("reply", "")
        assert_true(approval_rules.get("intent") == "approval_rules", "Approval-rule questions should explain guardrails, not just list the queue.")
        assert_true("connector credentials" in approval_reply.lower(), "Approval rules should mention checked connector credentials.")
        assert_true("writeback policy" in approval_reply.lower(), "Approval rules should mention church writeback policy.")
        assert_true("approve the exact item" in approval_reply.lower(), "Approval rules should preserve per-action pastor approval.")

        memory_recap = request(
            "POST",
            "/assistant/chat",
            {"message": "What have you learned about my ministry?", "mode": "live"},
            token,
        )
        memory_reply = memory_recap.get("reply", "")
        assert_true(memory_recap.get("intent") == "ministry_operating_plan", "Chat should summarize saved ministry memory.")
        assert_true("Solo Pastor" in memory_reply, "Ministry recap should include the pastor's role.")
        assert_true("non-denominational" in memory_reply.lower(), "Ministry recap should include church voice and tradition.")
        assert_true("visitor follow-up" in memory_reply.lower(), "Ministry recap should include the named follow-up burden.")
        assert_true("first-time guests" in memory_reply.lower(), "Ministry recap should include the pastor's first stated priority.")
        assert_true("Planning Center" in memory_reply and "Gmail" in memory_reply, "Ministry recap should include saved tools.")
        assert_true("Thursdays are sermon prep" in memory_reply, "Ministry recap should include weekly rhythm.")

        church_recap = request(
            "POST",
            "/assistant/chat",
            {"message": f"What do you know about {church_name}?", "mode": "live"},
            token,
        )
        church_recap_reply = church_recap.get("reply", "")
        assert_true(
            church_recap.get("intent") == "ministry_operating_plan",
            "Named-church recap prompts should summarize ministry context, not search for a missing person.",
        )
        assert_true(church_name in church_recap_reply, "Named-church recap should include the saved church name.")
        assert_true("new families" in church_recap_reply.lower(), "Named-church recap should include saved church context.")
        assert_true(
            "person_context_not_found" not in church_recap.get("intent", ""),
            "Named-church recap should not be treated as a failed person lookup.",
        )

        broad_help = request(
            "POST",
            "/assistant/chat",
            {"message": "How can you help me this week?", "mode": "live"},
            token,
        )
        broad_help_reply = broad_help.get("reply", "")
        assert_true(
            broad_help.get("intent") == "ministry_operating_plan",
            "Broad help questions should use the saved ministry operating plan, not the generic fallback.",
        )
        assert_true(
            "visitor follow-up" in broad_help_reply.lower(),
            "Broad help should anchor itself in the pastor's named follow-up burden.",
        )
        assert_true(
            "first-time guests" in broad_help_reply.lower(),
            "Broad help should include the pastor's first stated priority.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in broad_help.get("actions", [])),
            "Broad help should return the concrete first-record setup card while the workspace is still empty.",
        )

        attention_seed = request(
            "POST",
            "/assistant/chat",
            {"message": "What needs my attention before noon?", "mode": "live"},
            token,
        )
        assert_true(
            attention_seed.get("intent") == "data_seed_guidance",
            "A complete but empty live workspace should ask for real context instead of saying priorities are clear.",
        )
        assert_true(
            "real visitor" in attention_seed.get("reply", "").lower(),
            "Visitor follow-up pain should make the first priority prompt ask for a real visitor.",
        )
        assert_true(
            any(action.get("form") == "visitor" for action in attention_seed.get("actions", [])),
            "The priority data-seed card should route to the visitor form.",
        )

        draft_seed = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft the replies I should review.", "mode": "live"},
            token,
        )
        assert_true(
            draft_seed.get("intent") == "data_seed_guidance",
            "A complete but empty live workspace should ask for real context instead of claiming draft replies are ready.",
        )
        assert_true(
            "real visitor" in draft_seed.get("reply", "").lower(),
            "Visitor follow-up pain should make the first draft prompt ask for a real visitor.",
        )
        assert_true(
            any(action.get("form") == "visitor" for action in draft_seed.get("actions", [])),
            "The draft data-seed card should route to the visitor form.",
        )
        empty_morning_briefing = request(
            "POST",
            "/assistant/chat",
            {"message": "Give me my morning briefing.", "mode": "live"},
            token,
        )
        empty_morning_reply = empty_morning_briefing.get("reply", "")
        assert_true(empty_morning_briefing.get("intent") == "morning_briefing", "Morning briefing prompts should have a dedicated route.")
        assert_true(
            "first real ministry record" in empty_morning_reply.lower() and "Log the first real visitor" in empty_morning_reply,
            "Empty live morning briefings should ask for the first real record instead of pretending the desk is clear.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in empty_morning_briefing.get("actions", [])),
            "Empty live morning briefings should attach the first-record setup card.",
        )

        first_visitor_chat = request(
            "POST",
            "/assistant/chat",
            {
                "message": "Log the first visitor: Talia Brooks came Sunday, talia.brooks@example.test, 555-0199, and asked about kids ministry.",
                "mode": "live",
            },
            token,
        )
        assert_true(
            first_visitor_chat.get("intent") == "visitor_logged",
            "A concrete first-visitor chat prompt should save the visitor instead of returning setup guidance.",
        )
        assert_true(first_visitor_chat.get("saved"), "First-visitor chat prompt should persist the visitor.")
        assert_true("Talia Brooks" in first_visitor_chat.get("reply", ""), "First-visitor chat reply should name the saved visitor.")
        assert_true(
            any(action.get("type") == "visitor" and action.get("title") == "Talia Brooks" for action in first_visitor_chat.get("actions", [])),
            "First-visitor chat response should return a visitor card.",
        )
        first_visitor_actions = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        talia_welcome_actions = [
            action
            for action in first_visitor_actions
            if action.get("action_type") == "email_draft"
            and action.get("related_type") == "visitor"
            and "Talia Brooks" in (action.get("description") or "")
        ]
        assert_true(talia_welcome_actions, "A concrete first visitor from chat should queue a reviewable welcome draft.")
        talia_welcome_payload = talia_welcome_actions[0].get("payload") or {}
        talia_welcome_email = talia_welcome_payload.get("email") or {}
        talia_draft_context = talia_welcome_payload.get("draft_context") or {}
        assert_true(talia_welcome_email.get("to") == "talia.brooks@example.test", "First-visitor welcome draft should preserve email.")
        assert_true(
            "kids ministry" in (talia_welcome_email.get("body") or "").lower(),
            "First-visitor welcome draft should use the visitor's actual note instead of generic welcome copy.",
        )
        assert_true(
            talia_draft_context.get("drafting_voice") == "warm and brief",
            "First-visitor welcome draft should carry the saved pastor drafting voice in review metadata.",
        )
        assert_true(
            "non-denominational" in (talia_draft_context.get("faith_tradition") or "").lower(),
            "First-visitor welcome draft should carry the saved church voice in review metadata.",
        )
        assert_true(
            "Ask me before sending" in (talia_draft_context.get("guardrail") or ""),
            "First-visitor welcome draft should carry the saved approval guardrail in review metadata.",
        )
        first_visitor_pending = request("GET", "/assistant/actions?status=pending&limit=80", token=token)
        assert_true(
            not any(action.get("action_type") == "data_seed" for action in first_visitor_pending),
            "After the first real visitor is saved, the data-seed setup action should leave the pending queue.",
        )
        visitor_context_after_first = request(
            "POST",
            "/assistant/chat",
            {"message": "Show visitors needing follow-up.", "mode": "live"},
            token,
        )
        assert_true(
            visitor_context_after_first.get("intent") == "visitor_context_lookup",
            "Visitor follow-up lookup prompts should route to visitor context, not generic care follow-up.",
        )
        assert_true(
            "Talia Brooks" in (visitor_context_after_first.get("reply") or ""),
            "Visitor follow-up lookup should recall the saved visitor.",
        )
        completed_data_seed = next((action for action in first_visitor_actions if action.get("action_type") == "data_seed"), None)
        assert_true(
            completed_data_seed and completed_data_seed.get("status") == "executed",
            "The completed first-real-record setup action should be marked executed, not left pending.",
        )
        completed_by = (completed_data_seed.get("payload") or {}).get("completed_by") or {}
        assert_true(
            completed_by.get("related_type") == "visitor",
            "Completed data-seed setup should record which real context retired it.",
        )
        desk_after_first_visitor = request("GET", "/assistant/desk?mode=auto", token=token)
        assert_true(
            not any(step.get("type") == "data_seed" for step in desk_after_first_visitor.get("setup_steps", [])),
            "After the first visitor is saved, the desk should stop asking for seed data.",
        )
        calendar_after_first_visitor = desk_after_first_visitor.get("calendar_blocks") or []
        assert_true(
            calendar_after_first_visitor and calendar_after_first_visitor[0].get("title") == "Visitor follow-up window",
            "Calendar suggestions after a first visitor should be tied to visitor follow-up, not generic care-block copy.",
        )
        assert_true(
            "welcome draft" in (calendar_after_first_visitor[0].get("detail") or "").lower(),
            "The visitor calendar suggestion should explain the concrete welcome-draft follow-up.",
        )
        scheduling_reply = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a scheduling reply.", "mode": "live"},
            token,
        )
        assert_true(
            scheduling_reply.get("intent") == "scheduling_reply_drafted",
            "Suggested scheduling-reply prompts should prepare a focused scheduling draft, not generic ministry replies.",
        )
        assert_true(
            any(action.get("type") == "email_draft" and "scheduling" in (action.get("title") or "").lower() for action in scheduling_reply.get("actions", [])),
            "Scheduling-reply chat should return a reviewable email draft action.",
        )
        scheduling_actions = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        scheduling_action = next((action for action in scheduling_actions if action.get("title") == "Review scheduling reply"), None)
        scheduling_email = ((scheduling_action or {}).get("payload") or {}).get("email") or {}
        assert_true(scheduling_action is not None, "Scheduling-reply chat should persist the draft action.")
        assert_true("Thursdays are sermon prep" in scheduling_email.get("body", ""), "Scheduling reply should use the saved weekly rhythm.")
        assert_true(
            "welcome draft" in scheduling_email.get("body", "").lower()
            or "Talia Brooks" in scheduling_email.get("body", ""),
            "Scheduling reply should stay tied to the current ministry calendar priority.",
        )
        approval_question = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I approve first?", "mode": "live"},
            token,
        )
        assert_true(
            approval_question.get("intent") == "approval_queue_lookup",
            "Approval questions should summarize the queue, not approve an item.",
        )
        assert_true(
            "Review Visitor welcome" in approval_question.get("reply", ""),
            "Approval lookup should point to the queued visitor welcome draft.",
        )
        first_visitor_actions_after_question = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        talia_after_question = next(
            (
                action
                for action in first_visitor_actions_after_question
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Talia Brooks" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(
            talia_after_question and talia_after_question.get("status") == "pending",
            "Asking what to approve first must leave the visitor welcome draft pending.",
        )
        review_question = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I review first?", "mode": "live"},
            token,
        )
        assert_true(
            review_question.get("intent") == "approval_queue_lookup",
            "Review-first prompts from the frontend should summarize the queue, not fall through to generic chat.",
        )
        assert_true(
            "Review Visitor welcome" in review_question.get("reply", ""),
            "Review-first lookup should point to the queued visitor welcome draft.",
        )
        first_visitor_actions_after_review = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        talia_after_review = next(
            (
                action
                for action in first_visitor_actions_after_review
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Talia Brooks" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(
            talia_after_review and talia_after_review.get("status") == "pending",
            "Asking what to review first must leave the visitor welcome draft pending.",
        )
        approve_first_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Approve the visitor welcome draft.", "mode": "live"},
            token,
        )
        assert_true(
            approve_first_draft.get("intent") == "assistant_action_approved",
            "Explicit approval commands should still approve the matching draft.",
        )

        first_person = request(
            "POST",
            "/assistant/chat",
            {
                "message": "Help me add the first real person: Ruth Carter, ruth.carter@example.test. She is grieving her mother and asked for private prayer.",
                "mode": "live",
            },
            token,
        )
        first_person_reply = first_person.get("reply", "")
        assert_true(first_person.get("intent") == "member_logged", "Chat should create a clear first real person instead of only giving form guidance.")
        assert_true(first_person.get("saved"), "Chat-created first person should persist.")
        assert_true("Ruth Carter" in first_person_reply, "First-person chat reply should name the created person.")
        assert_true("private prayer" in first_person_reply.lower(), "First-person chat should preserve private prayer context.")
        assert_true(first_person.get("actions"), "Chat-created first person should return visible next-step cards.")
        first_person_prompts = " ".join(first_person.get("suggested_prompts") or [])
        assert_true("Ruth Carter" in first_person_prompts, "Chat-created first-person suggestions should name the person instead of using an unresolved pronoun.")
        assert_true("about them" not in first_person_prompts.lower(), "Chat-created first-person suggestions should not suggest unresolved pronoun lookups.")
        ruth_members = request("GET", "/members/?search=Ruth%20Carter&limit=10", token=token)
        assert_true(len(ruth_members) == 1, "Chat-created first person should appear in account-scoped people memory.")
        ruth_context = request(
            "POST",
            "/assistant/chat",
            {"message": "What do you know about Ruth Carter?", "mode": "live"},
            token,
        )
        assert_true(ruth_context.get("intent") == "person_context_lookup", "Chat-created first person should be retrievable by name.")
        assert_true("mother" in ruth_context.get("reply", "").lower(), "Retrieved first-person context should include saved grief/prayer details.")
        ruth_context_prompts = " ".join(ruth_context.get("suggested_prompts") or [])
        assert_true("Ruth Carter" in ruth_context_prompts, "Person-context suggestions should keep the named person in the next prompt.")
        assert_true("Log that I visited Ruth Carter today." in ruth_context_prompts, "Person-context suggestions should produce a contact-log prompt that can resolve the person.")

        unknown_context = request(
            "POST",
            "/assistant/chat",
            {"message": "What do you know about Marcus Reed?", "mode": "live"},
            token,
        )
        assert_true(unknown_context.get("intent") == "person_context_not_found", "Unknown named-person lookup should ask for safe person capture.")
        assert_true(
            "Help me add Marcus Reed as a person." in (unknown_context.get("suggested_prompts") or []),
            "Unknown named-person suggestions should carry the person name into the add-person prompt.",
        )
        generic_person_add = request(
            "POST",
            "/assistant/chat",
            {"message": "Add this person first.", "mode": "live"},
            token,
        )
        assert_true(
            generic_person_add.get("intent") == "person_capture_guidance",
            "Generic add-person prompts should explain what to capture instead of creating a placeholder person.",
        )
        assert_true("real name first" in generic_person_add.get("reply", "").lower(), "Person capture guidance should require a real name.")
        add_unknown_person = request(
            "POST",
            "/assistant/chat",
            {"message": "Help me add Marcus Reed as a person.", "mode": "live"},
            token,
        )
        assert_true(add_unknown_person.get("intent") == "member_logged", "Name-carrying add-person suggestions should save a local person.")
        assert_true(add_unknown_person.get("saved"), "Name-carrying add-person suggestions should persist the person.")
        assert_true("Marcus Reed" in add_unknown_person.get("reply", ""), "Saved add-person reply should name the person.")

        prayer_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a prayer follow-up for Ruth Carter.", "mode": "live"},
            token,
        )
        assert_true(prayer_draft.get("intent") == "draft_prayer_followup_queued", "Chat should queue a private prayer follow-up draft from saved prayer context.")
        assert_true(prayer_draft.get("actions"), "Prayer draft response should return a reviewable action card.")
        assert_true(
            "Ruth Carter" in " ".join(prayer_draft.get("suggested_prompts") or []),
            "Prayer draft suggestions should preserve the named pastoral context for the next chat turn.",
        )

        care_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a care follow-up for Ruth Carter.", "mode": "live"},
            token,
        )
        assert_true(care_draft.get("intent") == "draft_care_followup_queued", "Chat should queue a care follow-up draft from saved care context.")
        assert_true(care_draft.get("actions"), "Care draft response should return a reviewable action card.")
        assert_true(
            "Ruth Carter" in " ".join(care_draft.get("suggested_prompts") or []),
            "Care draft suggestions should preserve the named pastoral context for the next chat turn.",
        )
        draft_actions = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        ruth_draft_actions = [
            action
            for action in draft_actions
            if action.get("action_type") == "email_draft"
            and "Ruth Carter" in (action.get("title") or "")
        ]
        assert_true(len(ruth_draft_actions) >= 2, "Ruth's prayer and care follow-up drafts should be in the approval queue.")
        assert_true(
            any((action.get("payload") or {}).get("email", {}).get("body") for action in ruth_draft_actions),
            "Chat-queued follow-up draft actions should include reviewable body text.",
        )

        visitor = request(
            "POST",
            "/visitors/",
            {
                "first_name": "Jordan",
                "last_name": "Parker",
                "email": "jordan.parker@example.test",
                "visit_date": date.today().isoformat(),
                "source": "first-run smoke",
                "notes": "Visited worship and asked about small groups.",
            },
            token,
        )
        after_visitor_actions = request("GET", "/assistant/actions?status=all&limit=30", token=token)
        welcome_actions = [
            action
            for action in after_visitor_actions
            if action.get("action_type") == "email_draft"
            and action.get("related_type") == "visitor"
            and action.get("related_id") == visitor["id"]
        ]
        assert_true(welcome_actions, "Logging the first real visitor should queue a reviewable welcome draft.")
        welcome_email = (welcome_actions[0].get("payload") or {}).get("email") or {}
        assert_true(welcome_email.get("to") == "jordan.parker@example.test", "Visitor welcome draft should include the visitor email.")
        assert_true(bool(welcome_email.get("body")), "Visitor welcome draft should include a reviewable body.")
        visitor_draft = request("GET", f"/visitors/{visitor['id']}/draft?day=1", token=token)
        assert_true("Pastor First Run Smoke" in visitor_draft.get("draft", ""), "Visitor draft endpoint should use the workspace pastor name.")
        assert_true(f"First Run Smoke Church {suffix}" in visitor_draft.get("draft", ""), "Visitor draft endpoint should use the workspace church name.")

        chat_visitor = request(
            "POST",
            "/assistant/chat",
            {"message": "New visitor Morgan Lee came Sunday, morgan.lee@example.test, 555-0144, and asked about youth ministry.", "mode": "live"},
            token,
        )
        assert_true(chat_visitor.get("intent") == "visitor_logged", "Chat should log clear visitor updates.")
        assert_true(chat_visitor.get("saved"), "Chat visitor updates should persist.")
        assert_true("contact details" in chat_visitor.get("reply", "").lower(), "Chat visitor updates with email or phone should say contact details were saved.")
        after_visitor_history = request("GET", "/assistant/chat/history?limit=10", token=token)
        assert_true(
            any("morgan lee" in (message.get("content") or "").lower() for message in after_visitor_history),
            "Chat history should persist later pastoral updates too.",
        )
        cleared_history = request("DELETE", "/assistant/chat/history", token=token)
        assert_true(cleared_history.get("messages_deleted", 0) >= 2, "Clearing chat history should delete persisted transcript rows.")
        empty_history = request("GET", "/assistant/chat/history?limit=10", token=token)
        assert_true(empty_history == [], "Chat history should be empty after clearing it.")
        profile_after_clear = request("GET", "/assistant/profile", token=token)
        assert_true(profile_after_clear.get("completion_percent") == 100, "Clearing chat history should not delete saved profile context.")
        assert_true("visitor follow-up" in (profile_after_clear.get("followup_pain") or "").lower(), "Clearing chat history should preserve learned follow-up burden.")
        chat_visitor_actions = request("GET", "/assistant/actions?status=all&limit=50", token=token)
        chat_welcome_actions = [
            action
            for action in chat_visitor_actions
            if action.get("action_type") == "email_draft"
            and action.get("related_type") == "visitor"
            and "Morgan Lee" in (action.get("description") or "")
        ]
        assert_true(chat_welcome_actions, "A visitor logged through chat should also queue a reviewable welcome draft.")
        chat_welcome_email = (chat_welcome_actions[0].get("payload") or {}).get("email") or {}
        assert_true(chat_welcome_email.get("to") == "morgan.lee@example.test", "Visitor welcome drafts queued from chat should preserve the visitor email.")
        assert_true(bool(chat_welcome_email.get("body")), "Visitor welcome drafts queued from chat should include reviewable body text.")

        unnamed_visitor = request(
            "POST",
            "/assistant/chat",
            {"message": "A new visitor came Sunday and asked about youth ministry.", "mode": "live"},
            token,
        )
        assert_true(unnamed_visitor.get("intent") == "visitor_missing_name", "No-name visitor updates should ask for a name instead of saving a placeholder.")
        assert_true(not unnamed_visitor.get("saved"), "No-name visitor updates should not persist placeholder visitors.")
        visitors_after_unnamed = request("GET", "/visitors/?limit=30", token=token)
        assert_true(
            not any(visitor.get("full_name") == "Guest Guest" for visitor in visitors_after_unnamed),
            "Assistant chat should not create a generic Guest placeholder for no-name visitor updates.",
        )

        single_name_visitor = request(
            "POST",
            "/assistant/chat",
            {"message": "New visitor Talia came Sunday and asked about youth ministry.", "mode": "live"},
            token,
        )
        assert_true(single_name_visitor.get("intent") == "visitor_logged", "Single-name visitor updates should still log a real visitor.")
        single_name_visitors = request("GET", "/visitors/?limit=30", token=token)
        assert_true(
            any(visitor.get("full_name") == "Talia" for visitor in single_name_visitors),
            "Single-name visitor updates should preserve the real name without appending a fake last name.",
        )
        assert_true(
            not any(visitor.get("full_name") == "Talia Guest" for visitor in single_name_visitors),
            "Single-name visitor updates should not append a Guest placeholder last name.",
        )

        legacy_chat_visitor = request(
            "POST",
            "/chat/",
            {
                "message": "New visitor Naomi Grace came Sunday, naomi.grace@example.test, and asked about joining a small group.",
                "pastor_name": "Pastor First Run Smoke",
                "mode": "live",
            },
            token,
        )
        assert_true(legacy_chat_visitor.get("action") == "visitor_logged", "Legacy /chat/ should use connected assistant visitor logging.")
        assert_true(legacy_chat_visitor.get("saved"), "Legacy /chat/ visitor updates should persist.")
        legacy_visitors = request("GET", "/visitors/?limit=30", token=token)
        assert_true(
            any(visitor.get("full_name") == "Naomi Grace" for visitor in legacy_visitors),
            "Legacy /chat/ should save the named visitor instead of a placeholder guest.",
        )
        assert_true(
            not any(visitor.get("full_name") == "Guest Guest" for visitor in legacy_visitors),
            "Legacy /chat/ should not create a generic Guest placeholder.",
        )
        legacy_actions = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        assert_true(
            any(
                action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Naomi Grace" in (action.get("description") or "")
                and ((action.get("payload") or {}).get("email") or {}).get("to") == "naomi.grace@example.test"
                for action in legacy_actions
            ),
            "Legacy /chat/ visitor logging should queue a reviewable welcome draft with recipient.",
        )

        member = request(
            "POST",
            "/members/",
            {
                "first_name": "Janet",
                "last_name": "Ellis",
                "email": "janet.ellis@example.test",
                "last_attendance": (date.today() - timedelta(days=35)).isoformat(),
            },
            token,
        )
        request(
            "POST",
            f"/members/{member['id']}/notes",
            {"note_text": "Janet's brother died last month; she prefers phone calls over texts.", "context_tag": "grief"},
            token,
        )
        care_case = request(
            "POST",
            "/care/",
            {
                "member_id": member["id"],
                "category": "grief",
                "description": "Follow up after her brother's funeral.",
                "last_contact": (date.today() - timedelta(days=10)).isoformat(),
            },
            token,
        )
        request(
            "POST",
            "/care/prayers/",
            {
                "member_id": member["id"],
                "request_text": "Pray for peace and sleep after the funeral.",
                "is_private": True,
            },
            token,
        )
        care_draft_endpoint = request("GET", f"/members/{member['id']}/draft/care?situation=grief", token=token)
        assert_true("Pastor First Run Smoke" in care_draft_endpoint.get("draft", ""), "Member care draft endpoint should use the workspace pastor name.")
        assert_true("Pastor Pastor" not in care_draft_endpoint.get("draft", ""), "Member care draft endpoint should not duplicate the Pastor title.")
        absence_draft_endpoint = request("POST", "/drafts/", {"kind": "absence", "member_id": member["id"]}, token=token)
        assert_true("Pastor First Run Smoke" in absence_draft_endpoint.get("draft", ""), "Draft service should use the workspace pastor name.")
        assert_true("Pastor Pastor" not in absence_draft_endpoint.get("draft", ""), "Draft service should not duplicate the Pastor title when the saved name includes it.")
        prepared_after_care = request("POST", "/assistant/actions/prepare?mode=live&email_limit=20", token=token)
        care_action = next(
            (
                action for action in prepared_after_care
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "care"
                and action.get("related_id") == care_case["id"]
            ),
            None,
        )
        care_action_body = (((care_action or {}).get("payload") or {}).get("email") or {}).get("body", "")
        care_action_context = ((care_action or {}).get("payload") or {}).get("draft_context") or {}
        assert_true(care_action is not None, "Preparing actions should queue a care email draft from the live desk item.")
        assert_true("brother's funeral" in care_action_body, "Prepared care actions should use the actual care context in the server draft.")
        assert_true(care_action_context.get("drafting_voice") == "warm and brief", "Prepared care actions should carry saved drafting voice in review metadata.")
        assert_true("i have been thinking about you" not in care_action_body.lower(), "Prepared care actions should not use the old browser-only draft wording.")
        assert_true("Pastor Pastor" not in care_action_body, "Prepared care actions should not duplicate the Pastor title.")
        absence_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Who has been absent?", "mode": "live"},
            token,
        )
        assert_true(
            absence_context.get("intent") == "absence_context_lookup",
            "Rock/attendance suggested prompts should answer from live absence context, not generic chat.",
        )
        assert_true("Janet Ellis" in absence_context.get("reply", ""), "Absence lookup should name the absent member.")
        assert_true(
            any(action.get("type") == "absence" and action.get("source") == "attendance" for action in absence_context.get("actions", [])),
            "Absence lookup should attach absence follow-up action cards.",
        )
        absence_drafts = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft absence check-ins.", "mode": "live"},
            token,
        )
        absence_drafts_reply = absence_drafts.get("reply", "")
        assert_true(
            absence_drafts.get("intent") == "absence_drafts_queued",
            (
                "Suggested absence draft prompts should queue reviewable drafts instead of repeating lookup. "
                f"Got {absence_drafts.get('intent')}: {absence_drafts_reply}"
            ),
        )
        assert_true("queued" in absence_drafts_reply.lower() and "approval" in absence_drafts_reply.lower(), "Absence draft reply should keep review/approval boundaries visible.")
        assert_true(
            any(action.get("type") == "email_draft" and "Absence" in (action.get("title") or "") for action in absence_drafts.get("actions", [])),
            "Absence draft prompt should return reviewable email draft action cards.",
        )
        absence_draft_action_id = int(str((absence_drafts.get("actions") or [{}])[0].get("id", "action-0")).replace("action-", ""))
        absence_draft_action = request("GET", f"/assistant/actions/{absence_draft_action_id}", token=token)
        absence_draft_payload = absence_draft_action.get("payload") or {}
        absence_draft_email = absence_draft_payload.get("email") or {}
        absence_draft_context = absence_draft_payload.get("draft_context") or {}
        assert_true("Janet" in (absence_draft_email.get("body") or ""), "Absence draft body should name the absent member.")
        assert_true(absence_draft_context.get("drafting_voice") == "warm and brief", "Absence drafts should carry saved drafting voice in review metadata.")
        person_context = request(
            "POST",
            "/assistant/chat",
            {"message": "What do you know about Janet Ellis?", "mode": "live"},
            token,
        )
        person_reply = person_context.get("reply", "")
        assert_true(person_context.get("intent") == "person_context_lookup", "Chat should retrieve a named member's saved ministry context.")
        assert_true("Janet Ellis" in person_reply, "Person context should name the matched member.")
        assert_true("brother died" in person_reply, "Person context should include recent pastoral notes.")
        assert_true("Private" in person_reply or "private" in person_reply, "Person context should preserve private prayer context.")
        assert_true(person_context.get("actions"), "Person context should return actionable care/prayer/member items.")
        person_context_prompts = " ".join(person_context.get("suggested_prompts") or [])
        assert_true(
            "Draft a care follow-up for Janet Ellis." in person_context_prompts,
            "Person-context suggestions should name the member in draft prompts.",
        )
        assert_true(
            "Log that I visited Janet Ellis today." in person_context_prompts,
            "Person-context suggestions should name the member in contact-log prompts.",
        )

        prayer_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Who needs prayer follow-up?", "mode": "live"},
            token,
        )
        assert_true(prayer_context.get("intent") == "prayer_context_lookup", "Chat should answer prayer follow-up questions from saved records.")
        assert_true("Janet Ellis" in prayer_context.get("reply", ""), "Prayer lookup should include the saved private prayer request.")
        prayer_context_prompts = " ".join(prayer_context.get("suggested_prompts") or [])
        assert_true(
            "Draft a prayer follow-up for " in prayer_context_prompts
            and any(name in prayer_context_prompts for name in ["Ruth Carter", "Janet Ellis"]),
            "Prayer-context suggestions should name a real person from the prayer results.",
        )

        care_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Who needs care follow-up this week?", "mode": "live"},
            token,
        )
        assert_true(care_context.get("intent") == "care_context_lookup", "Chat should answer care follow-up questions from saved records.")
        assert_true("Janet Ellis" in care_context.get("reply", ""), "Care lookup should include active care cases.")
        care_context_prompts = " ".join(care_context.get("suggested_prompts") or [])
        assert_true(
            "Log that I visited " in care_context_prompts
            and any(name in care_context_prompts for name in ["Ruth Carter", "Janet Ellis"]),
            "Care-context suggestions should name a real person in contact-log prompts.",
        )
        generic_followup_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Who needs follow-up?", "mode": "live"},
            token,
        )
        assert_true(
            generic_followup_context.get("intent") == "prioritize_day",
            "Generic follow-up questions should prioritize across care, visitors, prayer, and absence instead of defaulting to care-only lookup.",
        )
        assert_true(
            any(action.get("type") in {"care", "visitor", "prayer", "absence"} for action in generic_followup_context.get("actions", [])),
            "Generic follow-up prioritization should attach pastoral priority cards.",
        )
        context_history = request("GET", "/assistant/chat/history?limit=8", token=token)
        latest_assistant = next((message for message in reversed(context_history) if message.get("role") == "assistant"), {})
        assert_true(latest_assistant.get("intent") == "prioritize_day", "Persisted chat history should preserve generic follow-up prioritization intent.")
        assert_true(latest_assistant.get("action_count", 0) > 0, "Persisted chat history should preserve returned action count.")
        assert_true(latest_assistant.get("actions"), "Persisted chat history should restore returned action cards.")
        assert_true(
            any("Janet Ellis" in (action.get("title") or "") for action in latest_assistant.get("actions", [])),
            "Restored chat action cards should keep a real follow-up person visible after reload.",
        )
        visit_plan = request(
            "POST",
            "/assistant/chat",
            {"message": "Where can I fit a visit with Janet Ellis?", "mode": "live"},
            token,
        )
        visit_plan_reply = visit_plan.get("reply", "")
        assert_true(
            visit_plan.get("intent") == "care_visit_plan_queued",
            "Named visit-planning prompts should queue a reviewable care visit block.",
        )
        assert_true("Janet Ellis" in visit_plan_reply, "Visit-planning reply should name the care person.")
        assert_true("approval" in visit_plan_reply.lower(), "Visit-planning reply should keep external calendar approval boundaries visible.")
        assert_true(
            "Tuesday" in visit_plan_reply or "Thursday" in visit_plan_reply or "Friday" in visit_plan_reply,
            "Visit planning should use the pastor's saved weekly rhythm.",
        )
        assert_true(
            any(action.get("type") == "calendar_block" and "Janet Ellis" in (action.get("title") or "") for action in visit_plan.get("actions", [])),
            "Visit planning should return a visible calendar-block action for the named person.",
        )
        generic_visit_plan = request(
            "POST",
            "/assistant/chat",
            {"message": "Where can I fit care follow-up?", "mode": "live"},
            token,
        )
        generic_visit_reply = generic_visit_plan.get("reply", "")
        assert_true(
            generic_visit_plan.get("intent") == "care_visit_plan_queued",
            "Suggested generic care follow-up scheduling prompts should queue a visit block instead of falling through.",
        )
        assert_true(
            any(name in generic_visit_reply for name in ["Janet Ellis", "Ruth Carter"]),
            "Generic care follow-up scheduling should choose a real active care case.",
        )
        assert_true(
            any(action.get("type") == "calendar_block" for action in generic_visit_plan.get("actions", [])),
            "Generic care follow-up scheduling should return a visible calendar-block action.",
        )
        defer_triage = request(
            "POST",
            "/assistant/chat",
            {"message": "What can wait until next week?", "mode": "live"},
            token,
        )
        defer_reply = defer_triage.get("reply", "")
        assert_true(defer_triage.get("intent") == "defer_triage", "Suggested deferral prompts should triage work instead of falling through.")
        assert_true("next week" in defer_reply.lower(), "Deferral triage should answer what can wait until next week.")
        assert_true("approval" in defer_reply.lower(), "Deferral triage should preserve external action approval boundaries.")
        assert_true(
            any(name in defer_reply for name in ["Janet Ellis", "Ruth Carter", "Talia Brooks", "Naomi Grace"]),
            "Deferral triage should stay grounded in real people or queued review items.",
        )
        assert_true(defer_triage.get("actions"), "Deferral triage should return visible work that should not be deferred.")
        next_action = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I handle next?", "mode": "live"},
            token,
        )
        next_action_reply = next_action.get("reply", "")
        assert_true(next_action.get("intent") == "next_action", "Suggested next-action prompts should return concrete work instead of generic chat.")
        assert_true(
            any(name in next_action_reply for name in ["Janet Ellis", "Ruth Carter", "Talia Brooks", "Naomi Grace", "Review Visitor welcome"]),
            "Next-action reply should stay grounded in real people or queued review work.",
        )
        assert_true(next_action.get("actions"), "Next-action reply should attach the concrete work card.")
        morning_briefing = request(
            "POST",
            "/assistant/chat",
            {"message": "What should we handle today?", "mode": "live"},
            token,
        )
        morning_reply = morning_briefing.get("reply", "")
        assert_true(morning_briefing.get("intent") == "morning_briefing", "Today briefing prompts should return the dedicated morning briefing.")
        assert_true(
            any(name in morning_reply for name in ["Janet Ellis", "Talia Brooks", "Naomi Grace", "Review Visitor welcome"]),
            "Morning briefing should stay grounded in real people or queued review work.",
        )
        assert_true("approval" in morning_reply.lower(), "Morning briefing should keep approval boundaries visible.")
        assert_true(morning_briefing.get("actions"), "Morning briefing should attach concrete desk or review cards.")
        contact_log = request(
            "POST",
            "/assistant/chat",
            {"message": "Log that I visited Janet Ellis today.", "mode": "live"},
            token,
        )
        assert_true(contact_log.get("intent") == "pastoral_contact_logged", "Named contact-log suggestions should save a pastoral contact.")
        assert_true("Janet Ellis" in contact_log.get("reply", ""), "Named contact-log chat should resolve the correct member.")
        assert_true(
            "Janet Ellis" in " ".join(contact_log.get("suggested_prompts") or []),
            "Contact-log follow-up suggestions should keep the member name visible.",
        )

        command_answer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Command Answer Smoke",
                "church_name": f"Command Answer Smoke Church {suffix}",
            },
        )
        command_answer_token = command_answer_signup["token"]
        command_answer_account_id = command_answer_signup["account_id"]
        command_answer_messages = [
            "I'm a solo pastor; we have 60 on Sundays.",
            "We are a rural church with young families and retired farmers.",
            "Our church tradition is Methodist, but use plain language with guests.",
            "Visitor follow-up falls through the cracks after Sunday.",
            "Help me close loops with first-time guests before Monday.",
            "Connect Planning Center and Gmail.",
            "Write in a warm and brief tone.",
            "Protect Fridays as my day off and Thursdays for sermon prep.",
            "Never send or change anything without my approval.",
        ]
        for message in command_answer_messages:
            request("POST", "/assistant/chat", {"message": message, "mode": "live"}, command_answer_token)
        command_answer_profile = request("GET", "/assistant/profile", token=command_answer_token)
        assert_true(command_answer_profile.get("completion_percent") == 100, "Command-like onboarding answers should still complete the ministry profile.")
        assert_true(
            "first-time guests" in (command_answer_profile.get("ministry_priorities") or "").lower(),
            "A 'Help me...' answer to the priority question should save ministry priorities instead of being skipped as a command.",
        )
        assert_true(
            command_answer_profile.get("tools_in_use") == "Planning Center, Gmail",
            "A 'Connect Planning Center and Gmail' answer to the tools question should save tools instead of being skipped as a command.",
        )
        assert_true(
            command_answer_profile.get("communication_style") == "warm and brief",
            "A 'Write in a warm and brief tone' answer should save drafting voice instead of being skipped as a command.",
        )
        assert_true(
            "Thursdays for sermon prep" in (command_answer_profile.get("weekly_rhythm") or ""),
            "Command-answer smoke should preserve the pastor's weekly rhythm.",
        )

        terse_answer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Terse Answer Smoke",
                "church_name": f"Terse Answer Smoke Church {suffix}",
            },
        )
        terse_answer_token = terse_answer_signup["token"]
        terse_answer_account_id = terse_answer_signup["account_id"]
        terse_answer_messages = [
            "Solo.",
            "72.",
            "Small college town church with a lot of new believers.",
            "Baptist roots; avoid insider language.",
            "Visitors and prayer cards.",
            "First-time guests before Monday.",
            "Planning Center and Gmail.",
            "Warm, brief, pastoral.",
            "Sermon prep Thursdays, Fridays off.",
            "Ask me before sending anything.",
        ]
        for message in terse_answer_messages:
            request("POST", "/assistant/chat", {"message": message, "mode": "live"}, terse_answer_token)
        terse_answer_profile = request("GET", "/assistant/profile", token=terse_answer_token)
        assert_true(terse_answer_profile.get("completion_percent") == 100, "Terse onboarding answers should still complete the ministry profile.")
        assert_true(terse_answer_profile.get("role_title") == "Solo Pastor", "Terse role answers should normalize to a pastoral role title.")
        assert_true(terse_answer_profile.get("congregation_size") == "72", "Terse numeric attendance should be normalized without punctuation.")
        assert_true("Baptist roots" in (terse_answer_profile.get("faith_tradition") or ""), "Terse church voice should preserve the stated tradition.")
        assert_true("avoid insider language" in (terse_answer_profile.get("faith_tradition") or "").lower(), "Terse church voice should preserve language boundaries.")
        assert_true("first-time guests" in (terse_answer_profile.get("ministry_priorities") or "").lower(), "Terse priority answers should not be mistaken for a fake visitor.")
        assert_true(terse_answer_profile.get("tools_in_use") == "Planning Center, Gmail", "Terse tool answers should be normalized to known church tools.")
        assert_true(
            terse_answer_profile.get("communication_style") == "warm and brief and pastoral",
            "Terse drafting-voice answers should be normalized from known voice words.",
        )

        prayer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Prayer Focus Smoke",
                "church_name": f"Prayer Focus Smoke Church {suffix}",
                "role_title": "Solo Pastor",
                "congregation_size": "45",
                "church_context": "A small rural church where private prayer needs often come through handwritten cards.",
                "faith_tradition": "Baptist roots; keep language gentle and discreet.",
                "followup_pain": "Private prayer requests fall through the cracks after Sunday.",
                "ministry_priorities": "Close loops with private prayer needs before people feel forgotten.",
                "tools_in_use": "Gmail",
                "communication_style": "warm, brief, pastoral",
                "weekly_rhythm": "Sermon prep Thursdays; prayer follow-up on Tuesday afternoons.",
                "guardrails": "Ask me before sharing private prayer details.",
            },
        )
        prayer_answer_token = prayer_signup["token"]
        prayer_answer_account_id = prayer_signup["account_id"]
        prayer_desk = request("GET", "/assistant/desk?mode=auto", token=prayer_answer_token)
        prayer_setup_steps = prayer_desk.get("setup_steps") or []
        assert_true(
            prayer_setup_steps and prayer_setup_steps[0].get("form") == "prayer",
            "Prayer-focused first-run profiles should route the first real record to prayer, not a generic person or visitor.",
        )
        assert_true(
            prayer_setup_steps[0].get("title") == "Add the first real prayer request",
            "Prayer-focused setup should name the first prayer request explicitly.",
        )
        private_prayer_coaching = request(
            "POST",
            "/assistant/chat",
            {"message": "How do you handle private prayer?", "mode": "live"},
            prayer_answer_token,
        )
        private_prayer_reply = private_prayer_coaching.get("reply", "")
        assert_true(
            private_prayer_coaching.get("intent") == "first_record_coaching",
            "Private-prayer coaching prompt should return first-record coaching.",
        )
        assert_true("private prayer" in private_prayer_reply.lower(), "Private-prayer coaching should name the private prayer boundary.")
        assert_true("public list" in private_prayer_reply.lower(), "Private-prayer coaching should explain that Marge will not expose private requests.")
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "prayer" for action in private_prayer_coaching.get("actions", [])),
            "Private-prayer coaching should keep the prayer data-seed card attached.",
        )
        anonymous_prayer_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "please pray for our school families dealing with job loss this week.", "mode": "live"},
            prayer_answer_token,
        )
        assert_true(anonymous_prayer_chat.get("intent") == "prayer_logged", "Unnamed private-prayer chat should still save a request.")
        assert_true(anonymous_prayer_chat.get("saved"), "Unnamed private-prayer chat should persist request-level prayer context.")
        assert_true("Pastor" not in anonymous_prayer_chat.get("reply", ""), "Unnamed private-prayer chat should not pretend Pastor is the request subject.")
        assert_true(
            "request-level follow-up" in anonymous_prayer_chat.get("reply", ""),
            "Unnamed private-prayer chat should explain it is tracking the request until a person is attached.",
        )
        assert_true(
            "Draft a private prayer follow-up from this request." in (anonymous_prayer_chat.get("suggested_prompts") or []),
            "Unnamed private-prayer chat should suggest a usable request-level draft prompt instead of a generic prayer draft.",
        )
        assert_true(
            "Draft a prayer follow-up." not in (anonymous_prayer_chat.get("suggested_prompts") or []),
            "Unnamed private-prayer chat should not surface dead-end generic draft prompts.",
        )
        anonymous_prayer_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "Who needs prayer follow-up?", "mode": "live"},
            prayer_answer_token,
        )
        assert_true(anonymous_prayer_lookup.get("intent") == "prayer_context_lookup", "Prayer lookup should include request-level private prayers.")
        assert_true("Pastor (" not in anonymous_prayer_lookup.get("reply", ""), "Prayer lookup should not label unnamed requests as Pastor.")
        assert_true("Private prayer request" in anonymous_prayer_lookup.get("reply", ""), "Prayer lookup should name unnamed requests as private prayer requests.")
        assert_true(
            "Draft a private prayer follow-up from this request." in (anonymous_prayer_lookup.get("suggested_prompts") or []),
            "Prayer lookup should offer a usable request-level draft prompt for unnamed prayer requests.",
        )
        anonymous_prayer_guidance = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I capture for private prayer?", "mode": "live"},
            prayer_answer_token,
        )
        assert_true(anonymous_prayer_guidance.get("intent") == "private_prayer_guidance", "Private-prayer capture prompt should remain useful after the first prayer exists.")
        assert_true("who may know" in anonymous_prayer_guidance.get("reply", "").lower(), "Private-prayer guidance should explain what details to capture.")
        assert_true(anonymous_prayer_guidance.get("actions"), "Private-prayer guidance should attach active prayer request cards when available.")
        anonymous_prayer_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a private prayer follow-up from this request.", "mode": "live"},
            prayer_answer_token,
        )
        assert_true(anonymous_prayer_draft.get("intent") == "draft_prayer_followup_queued", "Request-level private-prayer draft prompt should queue a reviewable draft.")
        assert_true("for Pastor" not in anonymous_prayer_draft.get("reply", ""), "Request-level private-prayer drafts should not use Pastor as a fake recipient.")
        assert_true("from this private prayer request" in anonymous_prayer_draft.get("reply", ""), "Request-level private-prayer draft should explain the source request.")
        anonymous_prayer_action_card = (anonymous_prayer_draft.get("actions") or [{}])[0]
        anonymous_prayer_action_id = int(str(anonymous_prayer_action_card.get("id", "action-0")).replace("action-", ""))
        anonymous_prayer_action = request("GET", f"/assistant/actions/{anonymous_prayer_action_id}", token=prayer_answer_token)
        anonymous_prayer_payload = anonymous_prayer_action.get("payload") or {}
        anonymous_prayer_email = anonymous_prayer_payload.get("email") or {}
        anonymous_prayer_context = anonymous_prayer_payload.get("draft_context") or {}
        assert_true(
            "job loss" in (anonymous_prayer_email.get("body") or "").lower(),
            "Request-level private-prayer draft should use the actual prayer context in the reviewable body.",
        )
        assert_true(
            anonymous_prayer_context.get("drafting_voice") == "warm, brief, pastoral",
            "Private-prayer drafts should carry the saved pastor drafting voice in review metadata.",
        )
        assert_true(
            "Baptist roots" in (anonymous_prayer_context.get("faith_tradition") or ""),
            "Private-prayer drafts should carry the saved church voice in review metadata.",
        )
        assert_true(
            "Ask me before sharing private prayer details" in (anonymous_prayer_context.get("guardrail") or ""),
            "Private-prayer drafts should carry the saved privacy guardrail in review metadata.",
        )
        prayers_before_generic_add = request("GET", "/care/prayers/?include_private=true&limit=20", token=prayer_answer_token)
        generic_prayer_add = request(
            "POST",
            "/assistant/chat",
            {"message": "Add a prayer request.", "mode": "live"},
            prayer_answer_token,
        )
        prayers_after_generic_add = request("GET", "/care/prayers/?include_private=true&limit=20", token=prayer_answer_token)
        assert_true(
            generic_prayer_add.get("intent") == "private_prayer_guidance",
            "Generic add-prayer prompts should guide capture instead of saving placeholder prayer text.",
        )
        assert_true(
            len(prayers_after_generic_add) == len(prayers_before_generic_add),
            "Generic add-prayer prompts should not create an empty prayer request.",
        )
        first_prayer_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Add the first prayer request: Naomi Grace asked for private prayer for her diagnosis.", "mode": "live"},
            prayer_answer_token,
        )
        assert_true(first_prayer_chat.get("intent") == "prayer_logged", "Concrete first-prayer chat should save a prayer request.")
        assert_true(first_prayer_chat.get("saved"), "Concrete first-prayer chat should persist the prayer request.")
        assert_true("Naomi Grace" in first_prayer_chat.get("reply", ""), "First-prayer chat should name the saved prayer request.")
        assert_true(
            "Naomi Grace" in " ".join(first_prayer_chat.get("suggested_prompts") or []),
            "First-prayer chat suggestions should carry the prayer person's name forward.",
        )
        prayer_pending = request("GET", "/assistant/actions?status=pending&limit=40", token=prayer_answer_token)
        assert_true(
            not any(action.get("action_type") == "data_seed" for action in prayer_pending),
            "After the first real prayer is saved, the prayer data-seed setup action should leave the pending queue.",
        )

        care_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Care Focus Smoke",
                "church_name": f"Care Focus Smoke Church {suffix}",
                "role_title": "Solo Pastor",
                "congregation_size": "70",
                "church_context": "A small town church with older members and a lot of hospital care.",
                "faith_tradition": "Methodist; use gentle plain language.",
                "followup_pain": "Hospital and grief follow-up fall through the cracks after the first visit.",
                "ministry_priorities": "Keep active hospital and grief care visible until someone checks back in.",
                "tools_in_use": "Gmail",
                "communication_style": "warm and brief",
                "weekly_rhythm": "Hospital visits Tuesday afternoons; sermon prep Thursday mornings.",
                "guardrails": "Ask me before sharing medical or grief details.",
            },
        )
        care_answer_token = care_signup["token"]
        care_answer_account_id = care_signup["account_id"]
        care_desk = request("GET", "/assistant/desk?mode=auto", token=care_answer_token)
        care_setup_steps = care_desk.get("setup_steps") or []
        assert_true(
            care_setup_steps and care_setup_steps[0].get("title") == "Add the first person needing care",
            "Care-focused first-run profiles should name the first care-oriented person instead of a generic person setup.",
        )
        assert_true(
            care_setup_steps[0].get("form") == "person",
            "Care-focused setup should still open the person-first form because care cases need a person record.",
        )
        care_coaching = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I record first?", "mode": "live"},
            care_answer_token,
        )
        care_coaching_reply = care_coaching.get("reply", "")
        assert_true(care_coaching.get("intent") == "first_record_coaching", "Care coaching prompt should return first-record coaching.")
        assert_true("care case" in care_coaching_reply.lower(), "Care coaching should name the care case.")
        assert_true("latest contact" in care_coaching_reply.lower(), "Care coaching should ask for latest contact context.")
        assert_true(
            any(action.get("type") == "data_seed" and "care" in (action.get("title") or "").lower() for action in care_coaching.get("actions", [])),
            "Care coaching should keep the care-oriented data-seed card attached.",
        )
        first_care_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Help me open the first care case: Ruth Carter is grieving her mother and needs a visit.", "mode": "live"},
            care_answer_token,
        )
        assert_true(first_care_chat.get("intent") == "care_case_logged", "Concrete first-care chat should save a care case.")
        assert_true(first_care_chat.get("saved"), "Concrete first-care chat should persist the care case.")
        assert_true("Ruth Carter" in first_care_chat.get("reply", ""), "First-care chat should name the care case person.")
        assert_true(
            "Ruth Carter" in " ".join(first_care_chat.get("suggested_prompts") or []),
            "First-care chat suggestions should carry the care person's name forward.",
        )
        assert_true(
            "Where can I fit a visit with Ruth Carter?" in " ".join(first_care_chat.get("suggested_prompts") or []),
            "First-care chat should suggest named visit planning instead of a generic visit prompt.",
        )
        care_followup_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a care follow-up for Ruth Carter.", "mode": "live"},
            care_answer_token,
        )
        assert_true(care_followup_draft.get("intent") == "draft_care_followup_queued", "Care follow-up draft prompts should queue a reviewable draft.")
        care_followup_action_card = (care_followup_draft.get("actions") or [{}])[0]
        care_followup_action_id = int(str(care_followup_action_card.get("id", "action-0")).replace("action-", ""))
        care_followup_action = request("GET", f"/assistant/actions/{care_followup_action_id}", token=care_answer_token)
        care_followup_payload = care_followup_action.get("payload") or {}
        care_followup_email = care_followup_payload.get("email") or {}
        care_followup_context = care_followup_payload.get("draft_context") or {}
        care_followup_body = care_followup_email.get("body") or ""
        assert_true(
            "mother" in care_followup_body.lower() and "visit" in care_followup_body.lower(),
            "Care follow-up drafts should use the actual care situation instead of generic grief copy.",
        )
        assert_true(
            care_followup_context.get("drafting_voice") == "warm and brief",
            "Care follow-up drafts should carry the saved pastor drafting voice in review metadata.",
        )
        assert_true(
            "Methodist" in (care_followup_context.get("faith_tradition") or ""),
            "Care follow-up drafts should carry the saved church voice in review metadata.",
        )
        assert_true(
            "Ask me before sharing medical or grief details" in (care_followup_context.get("guardrail") or ""),
            "Care follow-up drafts should carry the saved care privacy guardrail in review metadata.",
        )
        care_members = request("GET", "/members/?search=Ruth%20Carter&limit=10", token=care_answer_token)
        assert_true(len(care_members) == 1, "First-care chat should create local person memory when needed.")
        care_cases_before_generic_add = request("GET", "/care/?limit=20", token=care_answer_token)
        generic_care_add = request(
            "POST",
            "/assistant/chat",
            {"message": "Add a care case.", "mode": "live"},
            care_answer_token,
        )
        care_cases_after_generic_add = request("GET", "/care/?limit=20", token=care_answer_token)
        assert_true(
            generic_care_add.get("intent") == "care_case_guidance",
            "Generic add-care prompts should guide care capture instead of creating a placeholder case.",
        )
        assert_true(
            "real person first" in generic_care_add.get("reply", "").lower(),
            "Care-case guidance should explain that care cases need a real person.",
        )
        assert_true(generic_care_add.get("actions"), "Care-case guidance should attach active care cards when available.")
        assert_true(
            len(care_cases_after_generic_add) == len(care_cases_before_generic_add),
            "Generic add-care prompts should not create an empty care case.",
        )
        care_capture_help = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I capture for a care case?", "mode": "live"},
            care_answer_token,
        )
        assert_true(care_capture_help.get("intent") == "care_case_guidance", "Care capture prompts should stay useful after the first care case exists.")
        assert_true("latest contact" in care_capture_help.get("reply", "").lower(), "Care capture guidance should ask for latest contact context.")
        care_pending = request("GET", "/assistant/actions?status=pending&limit=40", token=care_answer_token)
        assert_true(
            not any(action.get("action_type") == "data_seed" for action in care_pending),
            "After the first real care case is saved, the care data-seed setup action should leave the pending queue.",
        )

        print("Marge first-run smoke passed.")
        print(json.dumps({
            "account_id": account_id,
            "profile_completion": profile.get("completion_percent"),
            "setup_steps": [step.get("title") for step in setup_steps],
            "action_types": sorted({action.get("action_type") for action in chat_visitor_actions}),
            "visitor_welcome_action": welcome_actions[0].get("title"),
            "chat_visitor_welcome_action": chat_welcome_actions[0].get("title"),
            "chat_history_messages_before_clear": len(after_visitor_history),
            "chat_history_messages_after_clear": len(empty_history),
        }, indent=2))
    finally:
        if care_answer_account_id is not None:
            cleanup_account(care_answer_account_id)
        if prayer_answer_account_id is not None:
            cleanup_account(prayer_answer_account_id)
        if terse_answer_account_id is not None:
            cleanup_account(terse_answer_account_id)
        if command_answer_account_id is not None:
            cleanup_account(command_answer_account_id)
        if identity_answer_account_id is not None:
            cleanup_account(identity_answer_account_id)
        if account_id is not None:
            cleanup_account(account_id)


if __name__ == "__main__":
    main()
