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
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE_URL = os.getenv("MARGE_API_URL", "http://127.0.0.1:8000").rstrip("/")


def assert_empty_ai_briefing_prompt_is_honest() -> None:
    from app.services import marge as marge_service

    calls: list[str] = []
    original_call_llm = marge_service._call_llm

    def fake_call_llm(prompt: str, *, max_tokens: int = 400, temperature: float = 0.5):
        calls.append(prompt)
        if prompt == "Reply with the word ready.":
            return "ready", "test"
        return "Captured empty briefing.", "test"

    empty_briefing = {
        "birthdays_this_week": [],
        "anniversaries_this_week": [],
        "visitors_needing_followup": [],
        "active_care_cases": [],
        "absent_members": [],
        "unanswered_prayers": [],
        "nudges": [],
    }

    try:
        marge_service._call_llm = fake_call_llm
        marge_service.generate_ai_briefing(
            empty_briefing,
            pastor_name="Pastor First Run Smoke",
            church_name="First Run Smoke Church",
        )
    finally:
        marge_service._call_llm = original_call_llm

    briefing_prompts = [prompt for prompt in calls if "Here is today's pastoral context:" in prompt]
    assert_true(briefing_prompts, "AI briefing generation should send a briefing prompt when an LLM provider is ready.")
    prompt = briefing_prompts[-1]
    assert_true(
        "No urgent pastoral needs flagged today" not in prompt,
        "Empty AI briefing prompts should not claim no urgent needs were found before real context exists.",
    )
    assert_true(
        "does not yet have real" in prompt
        and "do not imply the flock has been fully checked" in prompt
        and "Never invent people" in prompt,
        "Empty AI briefing prompts should tell the LLM to be honest about missing real workspace context.",
    )


def assert_missing_name_briefing_fallbacks_are_pastoral() -> None:
    from app.services import marge as marge_service

    rendered = marge_service.render_briefing_text({
        "greeting": "Good morning, Pastor.",
        "birthdays_this_week": [],
        "anniversaries_this_week": [],
        "visitors_needing_followup": [],
        "active_care_cases": [{"member_name": None, "category": "hospital", "last_contact": None}],
        "absent_members": [],
        "unanswered_prayers": [{
            "submitted_by": None,
            "request_text": "Please pray for a private family need.",
            "created_at": datetime.utcnow(),
        }],
        "nudges": [],
    })
    assert_true(
        "Unknown" not in rendered and "Anonymous" not in rendered,
        "Rendered live briefings should not use placeholder names for unlinked care or prayer records.",
    )
    assert_true(
        "Name not linked" in rendered and "Name withheld" in rendered,
        "Rendered live briefings should describe missing names as incomplete or private context.",
    )

    calls: list[str] = []
    original_call_llm = marge_service._call_llm

    def fake_call_llm(prompt: str, *, max_tokens: int = 400, temperature: float = 0.5):
        calls.append(prompt)
        if prompt == "Reply with the word ready.":
            return "ready", "test"
        return "Captured missing-name briefing.", "test"

    try:
        marge_service._call_llm = fake_call_llm
        marge_service.generate_ai_briefing(
            {
                "birthdays_this_week": [],
                "anniversaries_this_week": [],
                "visitors_needing_followup": [],
                "active_care_cases": [
                    SimpleNamespace(member=None, category="hospital", last_contact=None, description="Hospital follow-up.")
                ],
                "absent_members": [],
                "unanswered_prayers": [
                    SimpleNamespace(member=None, submitted_by=None, request_text="Please pray for a private family need.")
                ],
                "nudges": [],
            },
            pastor_name="Pastor First Run Smoke",
            church_name="First Run Smoke Church",
        )
    finally:
        marge_service._call_llm = original_call_llm

    briefing_prompts = [prompt for prompt in calls if "Here is today's pastoral context:" in prompt]
    assert_true(briefing_prompts, "AI briefing generation should send a briefing prompt for incomplete-name context.")
    prompt = briefing_prompts[-1]
    assert_true(
        "Unknown" not in prompt and "Anonymous" not in prompt,
        "AI briefing prompts should not use placeholder names for unlinked care or prayer records.",
    )
    assert_true(
        "Name not linked" in prompt and "Name withheld" in prompt,
        "AI briefing prompts should preserve incomplete/private-name language for unlinked records.",
    )


def assert_connected_email_fallbacks_are_pastoral() -> None:
    from app.routers import assistant as assistant_router

    triage = assistant_router._email_triage_description({"snippet": "Could we talk about a care need?"})
    missing_preview_triage = assistant_router._email_triage_description({})
    assert_true(
        "Unknown sender" not in triage and "Sender not available" in triage,
        "Synced inbox triage should describe a missing sender instead of using an Unknown placeholder.",
    )
    assert_true(
        "No preview available" not in missing_preview_triage
        and "No preview was included by the provider" in missing_preview_triage,
        "Synced inbox triage should describe missing preview context as provider-incomplete context.",
    )
    assert_true(
        assistant_router._recipient_first_name("") == "there",
        "Connected email replies without a parsed sender should use a neutral greeting fallback.",
    )
    reply_body = assistant_router._connected_email_reply_body(
        SimpleNamespace(pastor_name="Pastor First Run Smoke", church_name="First Run Smoke Church"),
        "there",
        "Pastoral care",
        "Could we talk about a care need?",
    )
    assert_true(
        "Hi friend" not in reply_body and "Hi there" in reply_body,
        "Connected email reply drafts should not address missing sender context as friend.",
    )


def assert_connected_context_missing_payload_copy_is_pastoral() -> None:
    from app.routers import assistant as assistant_router

    care_case = SimpleNamespace(member=None, category="hospital", last_contact=None, description=None)
    care_line = assistant_router._care_line(care_case)
    care_summary = assistant_router._care_case_summary(care_case)
    assert_true(
        "Unknown person" not in care_line and "Name not linked" in care_line,
        "Care context lines should describe an unlinked person instead of using an Unknown placeholder.",
    )
    assert_true(
        "no description" not in care_line.lower()
        and "no description" not in care_summary.lower()
        and "description not attached yet" in care_line,
        "Care context lines should describe missing descriptions as incomplete context.",
    )

    profile = SimpleNamespace(
        pastor_name="Pastor First Run Smoke",
        church_name="First Run Smoke Church",
        church_context=None,
        faith_tradition=None,
        guardrails=None,
    )
    item = SimpleNamespace(title="Pastoral meeting", subtitle=None, snippet=None)
    prep = assistant_router._connected_meeting_prep_text(profile, item, {})
    assert_true(
        "time not listed" not in prep.lower()
        and "No description synced" not in prep
        and "Use the pastor's saved ministry context" not in prep
        and "Use the pastor's saved church voice" not in prep,
        "Connected meeting prep should avoid stub-like missing calendar/profile wording.",
    )
    assert_true(
        "Time was not included by the connected calendar" in prep
        and "No description was included by the connected calendar" in prep
        and "Ask the pastor for local ministry context" in prep,
        "Connected meeting prep should explain missing provider/profile context honestly.",
    )


def assert_backend_name_fallbacks_do_not_create_unknown_people() -> None:
    from app.database import SessionLocal
    from app.integrations import rock as rock_integration
    from app.models import Member
    from app.routers import assistant as assistant_router

    priority_items = assistant_router._priority_items({
        "active_care_cases": [{"id": 1, "member_name": None, "category": "hospital", "last_contact": None}],
        "visitors_needing_followup": [{"id": 2, "full_name": None, "visit_date": None, "notes": "Asked about kids ministry."}],
        "absent_members": [{"id": 3, "full_name": None, "last_attendance": None}],
    })
    assert_true(
        any(item.title == "Name not linked" for item in priority_items),
        "Priority care items should use incomplete-context language instead of Unknown placeholders.",
    )
    assert_true(
        any(item.title == "Visitor name not linked" for item in priority_items),
        "Priority visitor items should describe missing names as incomplete context.",
    )
    assert_true(
        any(item.title == "Member name not linked" for item in priority_items),
        "Priority absence items should describe missing names as incomplete context.",
    )

    first_name, last_name = assistant_router._person_names_from_payload({"first_name": "Avery"})
    assert_true(
        first_name == "Avery" and last_name == "",
        "Connected person imports with only a first name should not invent an Unknown last name.",
    )

    suffix = int(time.time() * 1000)
    no_name_id = f"smoke-no-name-{suffix}"
    last_only_id = f"smoke-last-only-{suffix}"
    original_fetch = rock_integration.fetch_active_members

    def fake_fetch_active_members(*, api_key: str | None = None, base_url: str | None = None):
        return [
            {"Id": no_name_id},
            {"Id": last_only_id, "LastName": "Linked"},
        ]

    db = SessionLocal()
    try:
        rock_integration.fetch_active_members = fake_fetch_active_members
        stats = rock_integration.sync_members_from_rock(
            db,
            account_id=None,
            api_key="rock-smoke",
            base_url="https://rock.example.test/api/v2",
        )
        linked_member = db.query(Member).filter(Member.rock_id == last_only_id).first()
        skipped_member = db.query(Member).filter(Member.rock_id == no_name_id).first()
        assert_true(
            stats["created"] == 1 and stats["skipped"] == 1,
            "Rock sync should skip no-name rows and only create usable person rows.",
        )
        assert_true(
            linked_member is not None
            and linked_member.first_name == ""
            and linked_member.last_name == "Linked",
            "Rock sync should preserve partial provider names without inventing Unknown.",
        )
        assert_true(skipped_member is None, "Rock sync should not create no-name placeholder people.")
    finally:
        rock_integration.fetch_active_members = original_fetch
        db.query(Member).filter(Member.rock_id.in_([no_name_id, last_only_id])).delete(synchronize_session=False)
        db.commit()
        db.close()


def assert_calendar_provider_not_ready_prompts_match_setup_state() -> None:
    from app.routers import assistant as assistant_router

    unconfigured_steps = assistant_router._calendar_write_setup_steps([
        assistant_router.IntegrationStatus(
            provider="google_workspace",
            display_name="Google Workspace",
            status="not_configured",
            auth_type="oauth",
            scopes=[],
            secure_note="Start OAuth setup.",
        ),
        assistant_router.IntegrationStatus(
            provider="microsoft_365",
            display_name="Microsoft 365",
            status="not_configured",
            auth_type="oauth",
            scopes=[],
            secure_note="Start OAuth setup.",
        ),
    ])
    unconfigured_prompts = assistant_router._connector_setup_or_check_prompts(unconfigured_steps)
    assert_true(
        unconfigured_steps and unconfigured_steps[0].action == "Start secure setup",
        "Calendar write setup should start with secure setup when no calendar connector is configured.",
    )
    assert_true(
        "Check Google Workspace credentials." not in unconfigured_prompts
        and any("Connect Google Workspace" in prompt for prompt in unconfigured_prompts),
        "Provider-not-ready calendar prompts should not suggest credential checks before setup exists.",
    )

    unchecked_steps = assistant_router._calendar_write_setup_steps([
        assistant_router.IntegrationStatus(
            provider="google_workspace",
            display_name="Google Workspace",
            status="connected",
            auth_type="oauth",
            scopes=[],
            secure_note="Google Workspace connected.",
        )
    ])
    unchecked_prompts = assistant_router._connector_setup_or_check_prompts(unchecked_steps)
    assert_true(
        unchecked_steps and unchecked_steps[0].action == "Check credentials",
        "Calendar write setup should ask for a credential check when the connector exists but is unchecked.",
    )
    assert_true(
        "Check Google Workspace credentials." in unchecked_prompts,
        "Provider-not-ready calendar prompts should suggest credential checks once setup exists.",
    )

    example_prompt = "Queue a calendar event for hospital follow-up on 2026-05-18 at 3pm."
    unconfigured_help_prompts = assistant_router._calendar_help_suggested_prompts(example_prompt, unconfigured_steps)
    unchecked_help_prompts = assistant_router._calendar_help_suggested_prompts(example_prompt, unchecked_steps)
    assert_true(
        unconfigured_help_prompts[0] == example_prompt,
        "Calendar details help should keep the concrete event example as the first prompt chip.",
    )
    assert_true(
        any("Connect Google Workspace" in prompt for prompt in unconfigured_help_prompts)
        and "Check Google Workspace credentials." not in unconfigured_help_prompts,
        "Calendar details help should suggest secure setup before credential checks when no calendar connector exists.",
    )
    assert_true(
        "Check Google Workspace credentials." in unchecked_help_prompts,
        "Calendar details help should suggest credential checks after calendar setup exists.",
    )


def assert_generic_email_calendar_setup_phrasing_routes_to_connector_setup() -> None:
    from app.routers import assistant as assistant_router

    for phrase in [
        "connect my email",
        "set up my mail",
        "connect my mailbox",
        "set up my inbox",
        "connect my calendar",
        "set up my schedule",
    ]:
        assert_true(
            assistant_router._connector_setup_requested(phrase),
            f"Generic setup phrase should route to secure connector setup: {phrase}",
        )
        assert_true(
            assistant_router._provider_from_chat(phrase) is None,
            f"Generic setup phrase should leave provider choice to saved ministry tools: {phrase}",
        )

    unconfigured_integrations = [
        assistant_router.IntegrationStatus(
            provider="google_workspace",
            display_name="Google Workspace",
            status="not_configured",
            auth_type="oauth",
            scopes=[],
            secure_note="Start OAuth setup.",
        ),
        assistant_router.IntegrationStatus(
            provider="microsoft_365",
            display_name="Microsoft 365",
            status="not_configured",
            auth_type="oauth",
            scopes=[],
            secure_note="Start OAuth setup.",
        ),
    ]
    gmail_profile = SimpleNamespace(
        tools_in_use="Gmail",
        followup_pain="Visitor follow-up happens through email.",
        ministry_priorities="Close loops with first-time guests.",
        church_context="Neighborhood church.",
    )
    outlook_profile = SimpleNamespace(
        tools_in_use="Outlook calendar",
        followup_pain="Scheduling care visits is hard to keep current.",
        ministry_priorities="Protect care appointments.",
        church_context="Multi-site church.",
    )
    assert_true(
        assistant_router._next_setup_provider(gmail_profile, unconfigured_integrations) == "google_workspace",
        "Generic email setup should use saved Gmail context to recommend Google Workspace.",
    )
    assert_true(
        assistant_router._next_setup_provider(outlook_profile, unconfigured_integrations) == "microsoft_365",
        "Generic calendar setup should use saved Outlook context to recommend Microsoft 365.",
    )

    complete_profile = SimpleNamespace(
        pastor_name="Pastor Smoke",
        church_name="Smoke Church",
        role_title="Lead pastor",
        congregation_size="100",
        church_context="Neighborhood church.",
        faith_tradition="Baptist roots.",
        followup_pain="Care follow-up.",
        ministry_priorities="Protect care.",
        support_preferences="Nudge gently.",
        tools_in_use="Not sure which system to connect first.",
        communication_style="Warm and brief.",
        weekly_rhythm="Staff meeting Monday.",
        guardrails="Ask before sending.",
    )
    first_tool_steps = assistant_router._setup_steps(complete_profile, unconfigured_integrations)
    assert_true(
        first_tool_steps
        and first_tool_steps[0].title == "Connect the first ministry tool"
        and "Microsoft 365" in first_tool_steps[0].subtitle,
        "Generic first-tool setup should include Microsoft 365 alongside other supported live providers.",
    )
    tools_question = next(item for item in assistant_router.ONBOARDING_QUESTIONS if item["id"] == "tools_in_use")
    assert_true(
        "Gmail/Google Workspace" in tools_question["placeholder"]
        and "Outlook/Microsoft 365" in tools_question["placeholder"],
        "Tools onboarding placeholder should name the actual Google Workspace and Microsoft 365 connectors while preserving pastor-friendly aliases.",
    )
    assert_true(
        assistant_router._extract_known_tools("we use gmail and google workspace") == "Gmail/Google Workspace",
        "Tool extraction should not duplicate Gmail and Google Workspace as separate saved tools.",
    )
    assert_true(
        assistant_router._extract_known_tools("we use outlook and microsoft 365") == "Outlook/Microsoft 365",
        "Tool extraction should not duplicate Outlook and Microsoft 365 as separate saved tools.",
    )
    assert_true(
        assistant_router._extract_known_tools("planning center and gmail")
        == "Planning Center, Gmail/Google Workspace",
        "Tool extraction should preserve Planning Center while canonicalizing Gmail to its connector.",
    )
    assert_true(
        assistant_router._ministry_learning_gaps_requested("what should i include for tools?"),
        "Neutral onboarding guidance prompts should route to the context-question explanation branch.",
    )


def assert_seed_context_uses_current_user_for_connector_state() -> None:
    from app.routers import assistant as assistant_router

    profile = SimpleNamespace(
        pastor_name=None,
        church_name="First Run Smoke Church",
        role_title=None,
        congregation_size=None,
        church_context=None,
        faith_tradition=None,
        followup_pain=None,
        ministry_priorities=None,
        support_preferences=None,
        tools_in_use=None,
        communication_style=None,
        weekly_rhythm=None,
        guardrails=None,
    )
    user = SimpleNamespace(id=4321)
    calls: list[Any] = []
    original_integration_statuses = assistant_router._integration_statuses
    original_seed_context_step = assistant_router._seed_context_step

    def fake_integration_statuses(db, account=None, current_user=None):
        calls.append(current_user)
        return []

    try:
        assistant_router._integration_statuses = fake_integration_statuses
        assistant_router._seed_context_step(SimpleNamespace(), None, profile, "demo", user)
        assert_true(
            calls and calls[-1] is user,
            "Seed-context setup guidance should compute connector state for the current workspace user.",
        )
    finally:
        assistant_router._integration_statuses = original_integration_statuses

    seed_users: list[Any] = []

    def fake_seed_context_step(db, account, current_profile, effective_mode, current_user=None):
        seed_users.append(current_user)
        return assistant_router.DeskItem(
            id="setup-seed-first-people",
            type="data_seed",
            title="Log the first real visitor",
            subtitle="Add one real visitor.",
            priority="high",
            action="Log the first real visitor",
            source="seed",
            form="visitor",
        )

    try:
        assistant_router._seed_context_step = fake_seed_context_step
        response = assistant_router._missing_visitor_name_response(SimpleNamespace(), None, profile, "live", user)
        assert_true(
            seed_users and seed_users[-1] is user,
            "Missing-visitor-name guidance should preserve the current user when attaching setup cards.",
        )
        assert_true(
            response.get("actions") and response["actions"][0].form == "visitor",
            "Missing-visitor-name guidance should still attach the visitor setup card.",
        )
    finally:
        assistant_router._seed_context_step = original_seed_context_step


def assert_pastoral_reminder_chat_queues_local_action() -> None:
    from app.database import SessionLocal
    from app.models import AssistantAction, Member
    from app.routers import assistant as assistant_router

    suffix = int(time.time() * 1000)
    db = SessionLocal()
    account_id: int | None = None
    try:
        account, _token, profile, user = assistant_router._create_account(
            db,
            assistant_router.AccountSignupRequest(
                pastor_name="Pastor Reminder Smoke",
                church_name=f"Reminder Smoke Church {suffix}",
                email=f"reminder-smoke-{suffix}@example.test",
            ),
        )
        db.commit()
        db.refresh(account)
        db.refresh(profile)
        db.refresh(user)
        account_id = account.id
        profile.role_title = "Solo Pastor"
        profile.congregation_size = "85"
        profile.church_context = "Neighborhood church with young families."
        profile.faith_tradition = "Non-denominational; plain language."
        profile.followup_pain = "Care follow-up can go quiet."
        profile.ministry_priorities = "Keep active care visible."
        profile.support_preferences = "Nudge me gently and protect my rest."
        profile.tools_in_use = "Planning Center, Gmail"
        profile.communication_style = "warm and brief"
        profile.weekly_rhythm = "Thursdays are sermon prep."
        profile.guardrails = "Ask me before sending anything."
        profile.onboarding_complete = True
        db.commit()
        member = Member(
            account_id=account.id,
            first_name="Janet",
            last_name="Ellis",
            email="janet.ellis@example.test",
        )
        db.add(member)
        db.commit()
        response = assistant_router._pastoral_reminder_chat_response(
            db,
            profile,
            account,
            "Remind me to call Janet Ellis tomorrow.",
            "remind me to call janet ellis tomorrow.",
            "live",
        )
        assert_true(
            response.intent == "pastoral_reminder_queued" and response.saved,
            "Reminder chat should queue a saved local assistant action.",
        )
        assert_true(
            "Nothing was sent, synced, or written" in response.reply,
            "Reminder chat should preserve the no-external-write boundary.",
        )
        assert_true(
            response.actions and response.actions[0].type == "pastoral_reminder",
            "Reminder chat should return a visible pastoral reminder card.",
        )
        action = db.query(AssistantAction).filter(
            AssistantAction.account_id == account.id,
            AssistantAction.action_type == "pastoral_reminder",
        ).one()
        assert_true(action.status == "pending", "Pastoral reminders should remain reviewable until the pastor marks them done.")
        assert_true(action.external_provider is None, "Pastoral reminders should not target an external provider.")
        payload = json.loads(action.payload_json or "{}")
        reminder = payload.get("reminder") or {}
        assert_true(
            reminder.get("member_id") == member.id
            and reminder.get("person_name") == "Janet Ellis"
            and reminder.get("due") == "tomorrow",
            "Pastoral reminder payload should link known local people and preserve timing context.",
        )
        lookup = assistant_router._pastoral_reminder_lookup_response(db, profile, account, "live")
        assert_true(
            lookup.intent == "pastoral_reminder_lookup"
            and "call Janet Ellis" in lookup.reply
            and "local Marge memory" in lookup.reply
            and lookup.actions
            and lookup.actions[0].type == "pastoral_reminder",
            "Reminder lookup should list pending local pastoral reminders with visible action cards.",
        )
        command = assistant_router._maybe_handle_action_command(
            db,
            account,
            user,
            profile,
            [],
            "Mark Janet reminder done.",
            "mark janet reminder done.",
            "live",
        )
        db.refresh(action)
        payload = json.loads(action.payload_json or "{}")
        execution = payload.get("execution") or {}
        assert_true(
            command is not None
            and command.intent == "assistant_action_executed"
            and action.status == "executed"
            and execution.get("kind") == "pastoral_reminder_completed",
            "Reminder completion from chat should mark local pastoral reminders done without requiring a separate approval.",
        )
        empty_lookup = assistant_router._pastoral_reminder_lookup_response(db, profile, account, "live")
        assert_true(
            "do not see any pending local pastoral reminders" in empty_lookup.reply,
            "Completed pastoral reminders should leave the pending reminder lookup.",
        )
        cancel_response = assistant_router._pastoral_reminder_chat_response(
            db,
            profile,
            account,
            "Remind me to text Janet Ellis next week.",
            "remind me to text janet ellis next week.",
            "live",
        )
        assert_true(cancel_response.intent == "pastoral_reminder_queued", "Reminder smoke should queue a second local reminder for cancel coverage.")
        cancel_action = db.query(AssistantAction).filter(
            AssistantAction.account_id == account.id,
            AssistantAction.action_type == "pastoral_reminder",
            AssistantAction.status == "pending",
        ).one()
        reschedule_command = assistant_router._maybe_handle_action_command(
            db,
            account,
            user,
            profile,
            [],
            "Move Janet reminder to Friday.",
            "move janet reminder to friday.",
            "live",
        )
        db.refresh(cancel_action)
        rescheduled_payload = json.loads(cancel_action.payload_json or "{}")
        assert_true(
            reschedule_command is not None
            and reschedule_command.intent == "pastoral_reminder_rescheduled"
            and (rescheduled_payload.get("reminder") or {}).get("due") == "Friday"
            and "Nothing was sent, synced, or written externally" in reschedule_command.reply,
            "Reminder reschedule chat should update local reminder timing without writing externally.",
        )
        snooze_command = assistant_router._maybe_handle_action_command(
            db,
            account,
            user,
            profile,
            [],
            "Snooze it in two weeks.",
            "snooze it in two weeks.",
            "live",
        )
        db.refresh(cancel_action)
        snoozed_payload = json.loads(cancel_action.payload_json or "{}")
        assert_true(
            snooze_command is not None
            and snooze_command.intent == "pastoral_reminder_rescheduled"
            and (snoozed_payload.get("reminder") or {}).get("due") == "in two weeks",
            "Generic reminder snooze phrasing should update the selected local reminder timing.",
        )
        cancel_command = assistant_router._maybe_handle_action_command(
            db,
            account,
            user,
            profile,
            [],
            "Cancel Janet reminder.",
            "cancel janet reminder.",
            "live",
        )
        db.refresh(cancel_action)
        assert_true(
            cancel_command is not None
            and cancel_command.intent == "assistant_action_skipped"
            and cancel_action.status == "skipped",
            "Reminder cancel chat should skip the matching local pastoral reminder.",
        )
    finally:
        db.close()
        if account_id is not None:
            cleanup_account(account_id)


def assert_remembered_member_preferences_save_local_memory() -> None:
    from app.database import SessionLocal
    from app.models import CareNote, Member, MemberNote
    from app.routers import assistant as assistant_router

    suffix = int(time.time() * 1000)
    db = SessionLocal()
    account_id: int | None = None
    try:
        account, _token, profile, user = assistant_router._create_account(
            db,
            assistant_router.AccountSignupRequest(
                pastor_name="Pastor Memory Smoke",
                church_name=f"Memory Smoke Church {suffix}",
                email=f"memory-smoke-{suffix}@example.test",
            ),
        )
        db.commit()
        db.refresh(account)
        db.refresh(profile)
        db.refresh(user)
        account_id = account.id
        profile.role_title = "Solo Pastor"
        profile.congregation_size = "85"
        profile.church_context = "Neighborhood church with young families."
        profile.faith_tradition = "Non-denominational; plain language."
        profile.followup_pain = "Care follow-up can go quiet."
        profile.ministry_priorities = "Keep active care visible."
        profile.support_preferences = "Nudge me gently and protect my rest."
        profile.tools_in_use = "Planning Center, Gmail"
        profile.communication_style = "warm and brief"
        profile.weekly_rhythm = "Thursdays are sermon prep."
        profile.guardrails = "Ask me before sending anything."
        profile.onboarding_complete = True
        db.commit()
        member = Member(
            account_id=account.id,
            first_name="Janet",
            last_name="Ellis",
            email="janet.ellis@example.test",
        )
        db.add(member)
        db.commit()
        message = "Remember that Janet Ellis prefers phone calls over texts."
        result = assistant_router._maybe_save_pastoral_update(
            db,
            profile,
            account,
            user,
            message,
            message.lower(),
            "live",
        )
        assert_true(
            result and result.get("intent") == "member_note_logged" and result.get("saved"),
            "Remember-that preference phrasing should save a local member note instead of falling through.",
        )
        note = db.query(MemberNote).filter(MemberNote.account_id == account.id, MemberNote.member_id == member.id).one()
        assert_true(
            note.context_tag == "preference" and "phone calls over texts" in note.note_text,
            "Remembered preferences should be tagged and preserved in local Marge memory.",
        )
        assert_true(
            "Janet Ellis" in result.get("reply", "") and result.get("actions"),
            "Remembered preference replies should name the real local person and return a visible note card.",
        )
        context = assistant_router._person_context_chat_response(
            db,
            profile,
            account,
            "What do you know about Janet Ellis?",
            "what do you know about janet ellis?",
            "live",
        )
        assert_true(
            context.intent == "person_context_lookup"
            and "Preferences to respect" in context.reply
            and "phone calls over texts" in context.reply,
            "Person context lookups should surface remembered preferences distinctly.",
        )
        care = CareNote(
            account_id=account.id,
            member_id=member.id,
            category="general",
            description="Pastoral check-in after a difficult month.",
            status="active",
        )
        db.add(care)
        db.commit()
        db.refresh(care)
        natural_contact = assistant_router.assistant_chat(
            assistant_router.AssistantChatRequest(
                message="I just got back from visiting Janet Ellis at home.",
                mode="live",
            ),
            x_marge_account_token=_token,
            db=db,
        )
        assert_true(
            natural_contact.intent == "pastoral_contact_logged"
            and natural_contact.saved
            and "Janet Ellis" in natural_contact.reply
            and "reset the care follow-up timer" in natural_contact.reply,
            "Natural post-visit language should save a pastoral contact through chat.",
        )
        assert_true(
            any(action.type == "care" and action.title == "Janet Ellis" for action in natural_contact.actions),
            "Natural post-visit contact logging should return the linked care card.",
        )
        contact_result = assistant_router._save_contact_from_chat(
            db,
            account,
            profile,
            "Log that I visited Janet Ellis today.",
            "Janet Ellis",
        )
        contact_prompts = contact_result.get("suggested_prompts") or []
        assert_true(
            contact_result.get("intent") == "pastoral_contact_logged"
            and "local reminder" in contact_result.get("reply", "").lower()
            and f"Remind me to check on Janet Ellis next week." in contact_prompts,
            "Contact logging should proactively suggest a local next-check-in reminder.",
        )
        last_visit = assistant_router.assistant_chat(
            assistant_router.AssistantChatRequest(
                message="When did I last visit Janet Ellis?",
                mode="live",
            ),
            x_marge_account_token=_token,
            db=db,
        )
        ruth = Member(
            account_id=account.id,
            first_name="Ruth",
            last_name="Carter",
            email="ruth.carter@example.test",
        )
        db.add(ruth)
        db.commit()
        db.add(CareNote(
            account_id=account.id,
            member_id=ruth.id,
            category="grief",
            description="Recent grief follow-up with no latest contact logged.",
            status="active",
        ))
        db.commit()
        assert_true(
            last_visit.intent == "person_context_lookup"
            and "Active care" in last_visit.reply
            and "last contact" in last_visit.reply
            and (
                "Log that I visited Janet Ellis today" in last_visit.reply
                or "I just got back from visiting Janet Ellis" in last_visit.reply
            ),
            "Last-visit questions should answer from person/care memory instead of generic calendar planning.",
        )
        check_next = assistant_router.assistant_chat(
            assistant_router.AssistantChatRequest(
                message="Who should I check on next?",
                mode="live",
            ),
            x_marge_account_token=_token,
            db=db,
        )
        assert_true(
            check_next.intent == "next_action"
            and "Janet Ellis" in check_next.reply
            and any(action.title == "Janet Ellis" for action in check_next.actions),
            "Natural who-to-check-on questions should route to next-action triage from ministry context.",
        )
        assert_true(
            "Draft a care follow-up for Janet Ellis." in check_next.suggested_prompts
            and "Remind me to check on Janet Ellis next week." in check_next.suggested_prompts,
            "Who-to-check-on follow-up prompts should carry the named person forward.",
        )
        draft_action = assistant_router._prepare_single_followup_draft_action(
            db,
            profile,
            account,
            assistant_router._care_desk_item(care),
            "live",
        )
        draft_payload = assistant_router._json_loads(draft_action.payload_json)
        draft_context = draft_payload.get("draft_context") or {}
        draft_preferences = draft_context.get("member_preferences") or []
        draft_member_context = draft_context.get("member_context") or []
        draft_body = ((draft_payload.get("email") or {}).get("body") or "").lower()
        assert_true(
            draft_context.get("member_name") == "Janet Ellis"
            and any("phone calls over texts" in (preference.get("text") or "") for preference in draft_preferences)
            and "Pastor-only review context" in (draft_context.get("member_preference_guardrail") or ""),
            "Care follow-up drafts should carry remembered member preferences in review metadata.",
        )
        assert_true(
            "phone calls over texts" not in draft_body,
            "Remembered preferences should not be pasted into sendable draft bodies by default.",
        )
        assert_true(
            any(
                row.get("member_name") == "Janet Ellis"
                and any("difficult month" in text for text in row.get("active_care") or [])
                for row in draft_member_context
            ),
            "Care follow-up drafts should carry active local care context in review metadata.",
        )
        connected_item, _created = assistant_router._upsert_connected_item(
            db,
            provider="google_workspace",
            item_type="email",
            external_id=f"preference-email-{suffix}",
            thread_id=f"preference-thread-{suffix}",
            title="Prayer update",
            subtitle='"Janet Ellis" <janet.ellis@example.test>',
            snippet="Could use a quick pastoral follow-up.",
            occurred_at=datetime.utcnow(),
            payload={
                "email": {
                    "id": f"preference-email-{suffix}",
                    "thread_id": f"preference-thread-{suffix}",
                    "from": '"Janet Ellis" <janet.ellis@example.test>',
                    "subject": "Prayer update",
                    "snippet": "Could use a quick pastoral follow-up.",
                }
            },
            account=account,
        )
        connected_draft = assistant_router._prepare_email_reply_from_connected_item(
            db,
            profile,
            connected_item,
            account,
        )
        connected_payload = assistant_router._json_loads(connected_draft.payload_json)
        connected_context = connected_payload.get("draft_context") or {}
        connected_preferences = connected_context.get("member_preferences") or []
        connected_member_context = connected_context.get("member_context") or []
        connected_body = ((connected_payload.get("email") or {}).get("body") or "").lower()
        assert_true(
            connected_context.get("member_name") == "Janet Ellis"
            and any("phone calls over texts" in (preference.get("text") or "") for preference in connected_preferences),
            "Connected inbox reply drafts should attach local member preferences when the sender matches Marge people memory.",
        )
        assert_true(
            "phone calls over texts" not in connected_body,
            "Connected inbox reply bodies should not expose remembered preferences by default.",
        )
        assert_true(
            any(
                row.get("member_name") == "Janet Ellis"
                and any("difficult month" in text for text in row.get("active_care") or [])
                for row in connected_member_context
            ),
            "Connected inbox reply drafts should attach active local care context when the sender matches Marge people memory.",
        )
        calendar_item, _created = assistant_router._upsert_connected_item(
            db,
            provider="google_workspace",
            item_type="calendar_event",
            external_id=f"preference-calendar-{suffix}",
            thread_id=None,
            title="Care check-in with Janet Ellis",
            subtitle="Tomorrow at 10:00 AM",
            snippet="Pastoral care conversation.",
            occurred_at=datetime.utcnow(),
            payload={
                "calendar_event": {
                    "id": f"preference-calendar-{suffix}",
                    "summary": "Care check-in with Janet Ellis",
                    "when": "Tomorrow at 10:00 AM",
                    "description": "Pastoral care conversation.",
                    "attendees": [{"displayName": "Janet Ellis", "email": "janet.ellis@example.test"}],
                }
            },
            account=account,
        )
        meeting_action = assistant_router._prepare_connected_meeting_prep(
            db,
            profile,
            "prepare care check-in",
            account,
        )
        meeting_payload = assistant_router._json_loads(meeting_action.payload_json)
        meeting_context = meeting_payload.get("review_context") or {}
        meeting_preferences = meeting_context.get("member_preferences") or []
        meeting_member_context = meeting_context.get("member_context") or []
        assert_true(
            meeting_payload.get("connected_item_id") == calendar_item.id
            and meeting_context.get("member_name") == "Janet Ellis"
            and any("phone calls over texts" in (preference.get("text") or "") for preference in meeting_preferences),
            "Connected meeting prep should attach local member preferences when attendees match Marge people memory.",
        )
        assert_true(
            any(
                row.get("member_name") == "Janet Ellis"
                and any("difficult month" in text for text in row.get("active_care") or [])
                for row in meeting_member_context
            ),
            "Connected meeting prep should attach active local care context for matched attendees.",
        )
        pre_meeting_context = assistant_router._maybe_answer_ministry_context(
            db,
            profile,
            account,
            "What should I know before meeting with Janet Ellis?",
            "what should i know before meeting with janet ellis?",
            "live",
        )
        assert_true(
            pre_meeting_context
            and pre_meeting_context.intent == "person_context_lookup"
            and "Synced calendar" in pre_meeting_context.reply
            and "Care check-in with Janet Ellis" in pre_meeting_context.reply
            and any(action.type == "synced_calendar" for action in pre_meeting_context.actions),
            "Pre-meeting context questions should combine local people memory with synced calendar context.",
        )
    finally:
        db.close()
        if account_id is not None:
            cleanup_account(account_id)


def assert_backend_empty_proactive_copy_is_honest() -> None:
    from app.routers import assistant as assistant_router

    profile = SimpleNamespace(
        pastor_name="Pastor First Run Smoke",
        church_name="First Run Smoke Church",
        role_title="Solo Pastor",
        congregation_size="85",
        church_context="Neighborhood church with young families.",
        faith_tradition="Non-denominational; plain language.",
        followup_pain="Visitor follow-up can go quiet.",
        ministry_priorities="Close loops with first-time guests.",
        support_preferences="Nudge me gently and protect my rest.",
        tools_in_use="Planning Center, Gmail",
        communication_style="warm and brief",
        weekly_rhythm="Thursdays are sermon prep.",
        guardrails="Ask me before sending anything.",
    )
    summary = assistant_router._proactive_summary(profile, [], [], [], [])
    assert_true(
        "No urgent people are flagged" not in summary,
        "Backend proactive summaries should not imply Marge has proven no people need care.",
    )
    assert_true(
        "No current follow-up items are visible in this workspace" in summary
        and "Thursdays are sermon prep" in summary
        and "Nudge me gently" in summary,
        "Backend proactive summaries should be honest about current workspace visibility while preserving saved rhythm and support style.",
    )


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
    assert_empty_ai_briefing_prompt_is_honest()
    assert_missing_name_briefing_fallbacks_are_pastoral()
    assert_connected_email_fallbacks_are_pastoral()
    assert_connected_context_missing_payload_copy_is_pastoral()
    assert_backend_name_fallbacks_do_not_create_unknown_people()
    assert_calendar_provider_not_ready_prompts_match_setup_state()
    assert_generic_email_calendar_setup_phrasing_routes_to_connector_setup()
    assert_seed_context_uses_current_user_for_connector_state()
    assert_pastoral_reminder_chat_queues_local_action()
    assert_remembered_member_preferences_save_local_memory()
    assert_backend_empty_proactive_copy_is_honest()

    suffix = int(time.time())
    church_name = f"First Run Smoke Church {suffix}"
    account_id = None
    identity_answer_account_id = None
    command_answer_account_id = None
    terse_answer_account_id = None
    prayer_answer_account_id = None
    care_answer_account_id = None
    care_connect_account_id = None
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
        missing_email_status, missing_email_body = request_status(
            "POST",
            "/assistant/signup",
            {"pastor_name": "Pastor Missing Email", "church_name": f"Missing Email Smoke Church {suffix}"},
        )
        assert_true(missing_email_status == 422, "Signup should reject blank owner email addresses so workspaces are recoverable.")
        assert_true(
            "email" in json.dumps(missing_email_body).lower(),
            "Blank email signup rejection should explain that email is required.",
        )
        invalid_email_status, invalid_email_body = request_status(
            "POST",
            "/assistant/signup",
            {"pastor_name": "Pastor Invalid Email", "church_name": f"Invalid Email Smoke Church {suffix}", "email": "not-an-email"},
        )
        assert_true(invalid_email_status == 422, "Signup should reject invalid owner email addresses.")
        assert_true(
            "valid email" in json.dumps(invalid_email_body).lower(),
            "Invalid email signup rejection should explain that a valid email is required.",
        )

        identity_answer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "church_name": f"Identity Answer Smoke Church {suffix}",
                "email": f"identity-answer-{suffix}@example.test",
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
                "email": f"first-run-smoke-{suffix}@example.test",
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
            "Nudge me gently, protect my rest, and surface the people I am most likely to miss.",
            "Our stack is Planning Center for kids check-in and Gmail.",
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
            if index == 4:
                support_desk = request("GET", "/assistant/desk?mode=auto", token=token)
                support_question = support_desk.get("interview_question") or {}
                assert_true(support_question.get("field") == "support_preferences", "After first priority is learned, the next question should ask how to support the pastor personally.")
                assert_true(
                    "first-time guests" in (support_question.get("question") or "").lower()
                    or "private prayer" in (support_question.get("question") or "").lower(),
                    "The support-style question should be contextual to the pastor's stated first priority.",
                )
                missing_support_chat = request(
                    "POST",
                    "/assistant/chat",
                    {"message": "How will you support me?", "mode": "live"},
                    token,
                )
                assert_true(
                    missing_support_chat.get("intent") == "support_style_guidance",
                    "Support-style prompts should have a dedicated response before the support preference is saved.",
                )
                assert_true(
                    "how should marge support you personally" in missing_support_chat.get("reply", "").lower(),
                    "When support style is missing, Marge should ask the pastor the contextual support question instead of guessing.",
                )
                assert_true(
                    any(action.get("type") == "profile_setup" and action.get("id") == "setup-profile-support_preferences" for action in missing_support_chat.get("actions", [])),
                    "Missing support-style replies should attach the support-preference setup card.",
                )
            if index == 5:
                support_chat = request(
                    "POST",
                    "/assistant/chat",
                    {"message": "How will you support me?", "mode": "live"},
                    token,
                )
                assert_true(support_chat.get("intent") == "support_style_guidance", "Support-style prompts should have a dedicated chat response.")
                assert_true(
                    "nudge me gently" in support_chat.get("reply", "").lower(),
                    "Support-style replies should reflect the pastor's saved personal support preference.",
                )
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
        assert_true(
            "Here are your people for today" not in (briefing.get("greeting") or ""),
            "Live briefing should not greet a first-run pastor as if real people data is already sorted.",
        )
        assert_true(
            "well-tended" not in (briefing.get("plain_text") or "").lower()
            and "current workspace" in (briefing.get("plain_text") or "").lower(),
            "Empty live briefing text should ask for real workspace context instead of claiming the flock is already handled.",
        )
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
        assert_true("Nudge me gently" in (profile.get("support_preferences") or ""), "First-run chat should save the pastor's personal support style.")
        assert_true("protect my rest" in (profile.get("support_preferences") or ""), "Support preferences should preserve rest/protection context.")
        assert_true(profile.get("tools_in_use") == "Planning Center, Gmail/Google Workspace", "Known tools should be extracted even when the pastor mentions Planning Center check-in.")
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
        secret_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Should I paste my Gmail password or API key here?", "mode": "live"},
            token,
        )
        secret_chat_reply = secret_chat.get("reply", "")
        assert_true(
            secret_chat.get("intent") == "secure_connections_explained",
            "Secret-pasting questions should route to secure connector guidance.",
        )
        assert_true(
            "Do not paste" in secret_chat_reply and "API keys" in secret_chat_reply,
            "Secret-pasting guidance should explicitly reject API keys and passwords in chat.",
        )
        assert_true(
            "encrypted credential form" in secret_chat_reply,
            "Secret-pasting guidance should direct API-key connectors to encrypted credential setup.",
        )

        proactive_summary = desk.get("proactive_summary", "")
        assert_true("log the first real visitor" in proactive_summary.lower(), "Proactive first-run summary should name the concrete next ministry setup action.")
        assert_true("visitor follow-up" in proactive_summary.lower(), "Proactive first-run summary should explain the next setup step from the pastor's follow-up pain.")
        assert_true("first-time guests" in proactive_summary.lower(), "Proactive first-run summary should carry the pastor's stated first ministry priority.")
        assert_true("nudge me gently" in proactive_summary.lower(), "Proactive first-run summary should remember the pastor's support style.")
        assert_true("protect my rest" in proactive_summary.lower(), "Proactive first-run summary should preserve the pastor's rest/protection preference.")
        assert_true("seed marge" not in proactive_summary.lower(), "Proactive first-run summary should not expose internal setup labels.")
        completed_setup_prompts = " ".join(desk.get("suggested_prompts") or []).lower()
        assert_true("log the first real visitor" in completed_setup_prompts, "Complete but empty first-run prompts should point to the first real ministry record.")
        assert_true("draft" not in completed_setup_prompts and "before noon" not in completed_setup_prompts, "Complete but empty first-run prompts should not imply drafts or priorities already exist.")

        off_script_empty_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Can you be my secretary?", "mode": "live"},
            token,
        )
        off_script_reply = off_script_empty_chat.get("reply", "")
        off_script_prompts = " ".join(off_script_empty_chat.get("suggested_prompts") or []).lower()
        assert_true(
            off_script_empty_chat.get("intent") == "general_assistant",
            "Off-script first-run chat should still use the assistant fallback.",
        )
        assert_true(
            "first real visitor" in off_script_reply.lower(),
            "Off-script first-run fallback should stay anchored to the concrete first-record setup step.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in off_script_empty_chat.get("actions", [])),
            "Off-script first-run fallback should attach the active first-record setup card.",
        )
        assert_true(
            "log the first real visitor" in off_script_prompts,
            "Off-script first-run fallback should keep setup-aware prompt chips visible.",
        )

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
            "placeholder" not in setup_reason_reply.lower(),
            "Setup-reason replies should not describe the live pastor experience as placeholder work.",
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
        connect_first_reply = connect_first.get("reply", "")
        assert_true(connect_first.get("intent") == "integration_setup_started", "A generic connect-first prompt should start secure setup, not only list connector status.")
        assert_true(connect_first.get("actions"), "Connect-first chat should return a setup action card.")
        assert_true(
            connect_first["actions"][0].get("provider") == "google_workspace",
            "Connect-first chat should choose the first relevant saved tool and preserve its provider key.",
        )
        assert_true(
            "gmail" in connect_first_reply.lower() and "visitor follow-up" in connect_first_reply.lower(),
            "Connect-first chat should explain the recommendation from saved tools and the pastor's follow-up burden.",
        )
        assert_true(
            "planning center" in connect_first_reply.lower(),
            "Connect-first chat should name the next saved connector after the first recommendation.",
        )
        assert_true(
            "secure" in connect_first_reply.lower() and ("tokens" in connect_first_reply.lower() or "secrets" in connect_first_reply.lower()),
            "Connect-first chat should keep secure setup boundaries visible.",
        )
        assert_true(
            "Sync Google Workspace." not in (connect_first.get("suggested_prompts") or []),
            "Connect-first chat should not suggest syncing before connector setup and credential checks are complete.",
        )

        start_google_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Start Google Workspace setup.", "mode": "live"},
            token,
        )
        assert_true(
            start_google_setup.get("intent") == "integration_setup_started",
            "Suggested start-setup prompts should route to secure connector setup.",
        )
        assert_true(
            any(action.get("provider") == "google_workspace" for action in start_google_setup.get("actions", [])),
            "Start Google Workspace setup should attach the Google setup card.",
        )
        assert_true(
            "Sync Google Workspace." not in (start_google_setup.get("suggested_prompts") or []),
            "Start-setup prompts should not suggest syncing before setup and credential checks.",
        )
        care_connect_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Care Connector",
                "church_name": f"Care Connector Smoke Church {suffix}",
                "email": f"care-connector-{suffix}@example.test",
                "role_title": "Solo pastor",
                "congregation_size": "90",
                "church_context": "Older congregation with active hospital visits and a small care team.",
                "faith_tradition": "Methodist; keep language gentle and plain.",
                "followup_pain": "Hospital and grief follow-up fall through the cracks after the first visit.",
                "ministry_priorities": "Keep active care cases visible until someone checks back in.",
                "support_preferences": "Surface the people I am likely to miss without overwhelming me.",
                "tools_in_use": "Planning Center and Gmail",
                "communication_style": "warm and brief",
                "weekly_rhythm": "Hospital visits Tuesday afternoons; sermon prep Thursday mornings.",
                "guardrails": "Ask me before sending or sharing medical details.",
            },
        )
        care_connect_token = care_connect_signup["token"]
        care_connect_account_id = care_connect_signup["account_id"]
        care_connect_first = request(
            "POST",
            "/assistant/chat",
            {"message": "Which tool should I connect first?", "mode": "live"},
            care_connect_token,
        )
        care_connect_reply = care_connect_first.get("reply", "")
        assert_true(
            care_connect_first.get("intent") == "integration_setup_started",
            "People/care-heavy connect-first prompts should still start secure setup.",
        )
        assert_true(care_connect_first.get("actions"), "People/care-heavy connect-first prompts should attach setup.")
        assert_true(
            care_connect_first["actions"][0].get("provider") == "planning_center",
            "People/care-heavy connect-first prompts should prioritize the saved people system over Gmail.",
        )
        assert_true(
            "hospital" in care_connect_reply.lower() or "grief" in care_connect_reply.lower(),
            "People/care-heavy connect-first prompts should explain the recommendation from the saved care burden.",
        )
        assert_true(
            "google workspace" in care_connect_reply.lower(),
            "People/care-heavy connect-first prompts should still name the next saved communication connector.",
        )

        assert_true(first_week_action is not None, "Profile completion should queue a first-week launch plan.")
        first_week_description = first_week_action.get("description") or ""
        assert_true(
            "Nudge me gently" in first_week_description and "protect my rest" in first_week_description,
            "First-week plan review card should carry the pastor's saved support style.",
        )
        first_week_plan = (first_week_action.get("payload") or {}).get("plan") or []
        first_week_text = json.dumps(first_week_plan)
        first_week_titles = {item.get("title") for item in first_week_plan}
        assert_true("Log the first real visitor" in first_week_titles, "First-week plan should start with the first relevant real ministry record.")
        assert_true("Connect Google Workspace" in first_week_titles, "First-week plan should include Google Workspace setup.")
        assert_true("Connect Planning Center" in first_week_titles, "First-week plan should include Planning Center setup.")
        assert_true("Nudge me gently" in first_week_text, "First-week plan should include the pastor's support style as a plan item.")
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
        assert_true(
            "Sync the connected tools." not in (open_integrations.get("suggested_prompts") or []),
            "Open integrations should not suggest syncing connected tools before setup and credential checks.",
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

        pre_setup_provider_syncs = [
            ("Sync Planning Center.", "planning_center", "sync_planning_center_not_connected"),
            ("Sync Google Workspace.", "google_workspace", "sync_google_workspace_not_connected"),
            ("Sync Rock RMS.", "rock", "sync_rock_rms_not_connected"),
        ]
        for sync_message, expected_provider, expected_intent in pre_setup_provider_syncs:
            provider_sync = request(
                "POST",
                "/assistant/chat",
                {"message": sync_message, "mode": "live"},
                token,
            )
            provider_sync_reply = provider_sync.get("reply", "")
            provider_sync_prompts = " ".join(provider_sync.get("suggested_prompts") or []).lower()
            assert_true(
                provider_sync.get("intent") == expected_intent,
                f"{sync_message} before setup should stop at a credential-aware setup route.",
            )
            assert_true(
                "secure setup" in provider_sync_reply.lower()
                and "no-sync credential check" in provider_sync_reply.lower(),
                f"{sync_message} before setup should explain setup/check/sync order.",
            )
            assert_true(
                any(action.get("provider") == expected_provider for action in provider_sync.get("actions", [])),
                f"{sync_message} before setup should attach the requested provider setup/check card.",
            )
            assert_true(
                "sync" not in provider_sync_prompts,
                f"{sync_message} before setup should not return sync prompt chips.",
            )

        empty_planning_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Show Planning Center context.", "mode": "live"},
            token,
        )
        empty_planning_context_reply = empty_planning_context.get("reply", "")
        empty_planning_context_providers = {action.get("provider") for action in empty_planning_context.get("actions", [])}
        empty_planning_context_prompts = empty_planning_context.get("suggested_prompts") or []
        assert_true(
            empty_planning_context.get("intent") == "connected_context_lookup",
            "Empty connected-context lookups should use the connected-context route.",
        )
        assert_true(
            "secure setup" in empty_planning_context_reply.lower() and "no-sync credential check" in empty_planning_context_reply.lower(),
            "Empty connected-context lookup should explain setup/check/sync order before context exists.",
        )
        assert_true(
            "planning_center" in empty_planning_context_providers,
            "Empty connected-context lookup should attach the requested provider setup card.",
        )
        assert_true(
            "Sync Planning Center." not in empty_planning_context_prompts and "Sync the connected tools." not in empty_planning_context_prompts,
            "Empty connected-context lookup should not suggest sync before secure setup and credential checks.",
        )

        meeting_prep_before_calendar_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "What meetings need prep?", "mode": "live"},
            token,
        )
        meeting_prep_before_calendar_reply = meeting_prep_before_calendar_setup.get("reply", "")
        meeting_prep_before_calendar_prompts = meeting_prep_before_calendar_setup.get("suggested_prompts") or []
        meeting_prep_before_calendar_providers = {
            action.get("provider") for action in meeting_prep_before_calendar_setup.get("actions", [])
        }
        assert_true(
            meeting_prep_before_calendar_setup.get("intent") == "meeting_prep_lookup",
            "Meeting-prep lookup before calendar setup should use a credential-aware empty route.",
        )
        assert_true(
            "secure setup" in meeting_prep_before_calendar_reply.lower()
            and "no-sync credential check" in meeting_prep_before_calendar_reply.lower(),
            "Meeting-prep empty state should explain setup/check/sync order before calendar context exists.",
        )
        assert_true(
            {"google_workspace", "planning_center"}.issubset(meeting_prep_before_calendar_providers),
            "Meeting-prep empty state should attach saved calendar setup/check cards.",
        )
        assert_true(
            "Sync the calendar again." not in meeting_prep_before_calendar_prompts,
            "Meeting-prep empty prompts should not suggest calendar sync before credentials are verified.",
        )
        prepare_meeting_before_calendar_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Prepare my next meeting.", "mode": "live"},
            token,
        )
        prepare_meeting_before_calendar_reply = prepare_meeting_before_calendar_setup.get("reply", "")
        prepare_meeting_before_calendar_prompts = prepare_meeting_before_calendar_setup.get("suggested_prompts") or []
        prepare_meeting_before_calendar_providers = {
            action.get("provider") for action in prepare_meeting_before_calendar_setup.get("actions", [])
        }
        assert_true(
            prepare_meeting_before_calendar_setup.get("intent") == "meeting_prep_lookup",
            "Prepare-meeting prompts before calendar setup should reuse the credential-aware empty meeting-prep route.",
        )
        assert_true(
            "secure setup" in prepare_meeting_before_calendar_reply.lower()
            and "no-sync credential check" in prepare_meeting_before_calendar_reply.lower(),
            "Prepare-meeting empty state should explain setup/check/sync order before calendar context exists.",
        )
        assert_true(
            {"google_workspace", "planning_center"}.issubset(prepare_meeting_before_calendar_providers),
            "Prepare-meeting empty state should attach saved calendar setup/check cards.",
        )
        assert_true(
            "Sync the calendar again." not in prepare_meeting_before_calendar_prompts,
            "Prepare-meeting empty prompts should not suggest calendar sync before credentials are verified.",
        )

        sync_calendar_before_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Sync the calendar.", "mode": "live"},
            token,
        )
        sync_calendar_before_setup_reply = sync_calendar_before_setup.get("reply", "")
        sync_calendar_before_setup_prompts = " ".join(sync_calendar_before_setup.get("suggested_prompts") or []).lower()
        sync_calendar_before_setup_providers = {
            action.get("provider") for action in sync_calendar_before_setup.get("actions", [])
        }
        assert_true(
            sync_calendar_before_setup.get("intent") == "sync_calendar_not_connected",
            "Explicit calendar sync before setup should stop at a credential-aware setup route.",
        )
        assert_true(
            "secure setup" in sync_calendar_before_setup_reply.lower()
            and "no-sync credential check" in sync_calendar_before_setup_reply.lower(),
            "Explicit calendar sync before setup should explain setup/check/sync order.",
        )
        assert_true(
            {"google_workspace", "planning_center"}.issubset(sync_calendar_before_setup_providers),
            "Explicit calendar sync before setup should attach saved calendar setup/check cards.",
        )
        assert_true(
            "sync" not in sync_calendar_before_setup_prompts,
            "Explicit calendar sync before setup should not return sync prompt chips.",
        )

        missing_synced_person_import = request(
            "POST",
            "/assistant/chat",
            {"message": "Add Marcus Reed from Planning Center to Marge.", "mode": "live"},
            token,
        )
        missing_synced_person_reply = missing_synced_person_import.get("reply", "")
        missing_synced_person_prompts = missing_synced_person_import.get("suggested_prompts") or []
        assert_true(
            missing_synced_person_import.get("intent") == "connected_person_import_not_found",
            "Importing a synced person before sync should return a connector-aware not-found response.",
        )
        assert_true(
            "secure setup" in missing_synced_person_reply.lower() and "no-sync credential check" in missing_synced_person_reply.lower(),
            "Missing synced-person import should explain setup/check/sync order instead of saying to sync first.",
        )
        assert_true(
            any(action.get("provider") == "planning_center" for action in missing_synced_person_import.get("actions", [])),
            "Missing synced-person import should attach the requested provider setup card.",
        )
        assert_true(
            "Sync Planning Center." not in missing_synced_person_prompts and "Sync Breeze." not in missing_synced_person_prompts,
            "Missing synced-person import should not suggest provider sync before setup and credential checks.",
        )

        inbox_before_email_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "What is in my inbox?", "mode": "live"},
            token,
        )
        inbox_before_email_setup_reply = inbox_before_email_setup.get("reply", "")
        inbox_before_email_setup_actions = inbox_before_email_setup.get("actions", [])
        inbox_before_email_setup_providers = {action.get("provider") for action in inbox_before_email_setup_actions}
        inbox_before_email_setup_prompts = " ".join(inbox_before_email_setup.get("suggested_prompts") or []).lower()
        assert_true(
            inbox_before_email_setup.get("intent") == "synced_inbox_empty",
            "Inbox lookup before mail setup should use a credential-aware empty route.",
        )
        assert_true(
            "no google workspace or microsoft 365 mailbox has completed secure setup" in inbox_before_email_setup_reply.lower(),
            "Inbox empty state should explain that secure mailbox setup is required before inbox context exists.",
        )
        assert_true(
            "no-sync credential check" in inbox_before_email_setup_reply.lower(),
            "Inbox empty state should require credential checks before suggesting sync.",
        )
        assert_true(
            {"google_workspace", "microsoft_365"}.issubset(inbox_before_email_setup_providers),
            "Inbox empty state should attach mail setup/check cards instead of returning no actions.",
        )
        assert_true(
            "sync the inbox" not in inbox_before_email_setup_prompts,
            "Inbox empty prompts should not suggest syncing before credentials are verified.",
        )

        sync_inbox_before_email_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Sync the inbox.", "mode": "live"},
            token,
        )
        sync_inbox_before_email_setup_reply = sync_inbox_before_email_setup.get("reply", "")
        sync_inbox_before_email_setup_prompts = " ".join(sync_inbox_before_email_setup.get("suggested_prompts") or []).lower()
        sync_inbox_before_email_setup_providers = {
            action.get("provider") for action in sync_inbox_before_email_setup.get("actions", [])
        }
        assert_true(
            sync_inbox_before_email_setup.get("intent") == "sync_mailbox_not_connected",
            "Explicit inbox sync before setup should stop at a credential-aware setup route.",
        )
        assert_true(
            "secure setup" in sync_inbox_before_email_setup_reply.lower()
            and "no-sync credential check" in sync_inbox_before_email_setup_reply.lower(),
            "Explicit inbox sync before setup should explain setup/check/sync order.",
        )
        assert_true(
            {"google_workspace", "microsoft_365"}.issubset(sync_inbox_before_email_setup_providers),
            "Explicit inbox sync before setup should attach mail setup/check cards.",
        )
        assert_true(
            "sync" not in sync_inbox_before_email_setup_prompts,
            "Explicit inbox sync before setup should not return sync prompt chips.",
        )

        queued_replies_before_email_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Queue replies for these.", "mode": "live"},
            token,
        )
        queued_replies_before_email_setup_reply = queued_replies_before_email_setup.get("reply", "")
        queued_replies_before_email_setup_prompts = " ".join(queued_replies_before_email_setup.get("suggested_prompts") or []).lower()
        queued_replies_before_email_setup_providers = {
            action.get("provider") for action in queued_replies_before_email_setup.get("actions", [])
        }
        assert_true(
            queued_replies_before_email_setup.get("intent") == "draft_synced_email_replies_empty",
            "Queued inbox replies before mail setup should use a credential-aware empty route.",
        )
        assert_true(
            "no google workspace or microsoft 365 mailbox has completed secure setup" in queued_replies_before_email_setup_reply.lower(),
            "Queued inbox replies should explain that no connected mailbox exists yet.",
        )
        assert_true(
            {"google_workspace", "microsoft_365"}.issubset(queued_replies_before_email_setup_providers),
            "Queued inbox replies should attach mail setup/check cards before any sync is possible.",
        )
        assert_true(
            "sync the inbox" not in queued_replies_before_email_setup_prompts,
            "Queued inbox reply prompts should not suggest syncing before credentials are verified.",
        )

        care_visit_before_context = request(
            "POST",
            "/assistant/chat",
            {"message": "Where can I fit care follow-up?", "mode": "live"},
            token,
        )
        care_visit_before_context_reply = care_visit_before_context.get("reply", "")
        assert_true(
            care_visit_before_context.get("intent") == "care_case_guidance",
            "Care visit planning before a real care person exists should ask for care context instead of proposing a generic calendar block.",
        )
        assert_true(
            "real person" in care_visit_before_context_reply.lower(),
            "Care visit planning before real context should require the person or care case first.",
        )
        assert_true(
            "protected ministry work" not in care_visit_before_context_reply.lower(),
            "Care visit planning before real context should not fall back to a generic protected-work block.",
        )
        assert_true(
            any(action.get("type") == "data_seed" for action in care_visit_before_context.get("actions", [])),
            "Care visit planning before real context should keep the first-record setup card attached.",
        )

        absence_before_attendance_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Who has been absent?", "mode": "live"},
            token,
        )
        absence_before_attendance_reply = absence_before_attendance_setup.get("reply", "")
        absence_before_attendance_prompts = absence_before_attendance_setup.get("suggested_prompts") or []
        assert_true(
            absence_before_attendance_setup.get("intent") == "absence_context_lookup",
            "Absence lookup before attendance setup should use a credential-aware empty route.",
        )
        assert_true(
            "secure setup" in absence_before_attendance_reply.lower()
            and "no-sync credential check" in absence_before_attendance_reply.lower(),
            "Absence empty state should explain setup/check/sync order before attendance context exists.",
        )
        assert_true(
            any(action.get("provider") == "rock" for action in absence_before_attendance_setup.get("actions", [])),
            "Absence empty state should attach the Rock RMS setup/check card.",
        )
        assert_true(
            "Sync Rock RMS." not in absence_before_attendance_prompts,
            "Absence empty prompts should not suggest Rock sync before credentials are verified.",
        )

        absence_drafts_before_attendance_setup = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft absence check-ins.", "mode": "live"},
            token,
        )
        assert_true(
            absence_drafts_before_attendance_setup.get("intent") == "absence_drafts_empty",
            "Absence drafts before attendance setup should use a credential-aware empty route.",
        )
        assert_true(
            "Sync Rock RMS." not in (absence_drafts_before_attendance_setup.get("suggested_prompts") or []),
            "Absence draft empty prompts should not suggest Rock sync before credentials are verified.",
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
        calendar_details_no_provider_text = json.dumps(calendar_details_no_provider).lower()
        assert_true(
            "marcus" not in calendar_details_no_provider_text and "example.test" not in calendar_details_no_provider_text,
            "Calendar details help should not teach first-run pastors with fake person names or test email addresses.",
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
        assert_true(
            any("Connect Google Workspace" in prompt for prompt in (calendar_details_no_provider.get("suggested_prompts") or []))
            and "Check Google Workspace credentials." not in (calendar_details_no_provider.get("suggested_prompts") or []),
            "Calendar details help should suggest setup before credential checks when no calendar connector exists.",
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
        approval_rule_prompts = " ".join(approval_rules.get("suggested_prompts") or []).lower()
        assert_true(
            "before noon" not in approval_rule_prompts and "draft" not in approval_rule_prompts,
            "Approval-rule prompts should stay setup-aware while no real draft work is ready.",
        )

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
        assert_true("warm and brief" in memory_reply.lower(), "Ministry recap should include the saved drafting voice.")
        assert_true("Thursdays are sermon prep" in memory_reply, "Ministry recap should include weekly rhythm.")
        assert_true("ask me before" in memory_reply.lower(), "Ministry recap should include saved approval guardrails.")

        learning_gaps = request(
            "POST",
            "/assistant/chat",
            {"message": "What else do you need from me?", "mode": "live"},
            token,
        )
        learning_gaps_reply = learning_gaps.get("reply", "")
        assert_true(
            learning_gaps.get("intent") == "ministry_learning_gaps",
            "Completed-profile learning-gap prompts should not fall back to a generic onboarding recap.",
        )
        assert_true(
            "core ministry profile" in learning_gaps_reply.lower()
            and "one real ministry record" in learning_gaps_reply.lower(),
            "Learning-gap replies should distinguish learned profile context from the next missing real ministry record.",
        )
        assert_true(
            "setup, no-sync credential check, then sync only when you ask" in learning_gaps_reply,
            "Learning-gap replies should keep secure tool setup/check/sync order visible.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in learning_gaps.get("actions", [])),
            "Learning-gap replies should attach the concrete first-real-record setup card.",
        )

        guardrail_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "What are my guardrails?", "mode": "live"},
            token,
        )
        guardrail_lookup_reply = guardrail_lookup.get("reply", "")
        assert_true(guardrail_lookup.get("intent") == "profile_guardrails_lookup", "Guardrail lookup should answer from the saved ministry profile.")
        assert_true("Ask me before sending" in guardrail_lookup_reply, "Guardrail lookup should include the pastor's saved guardrail.")
        assert_true("checked credentials" in guardrail_lookup_reply and "writeback policy" in guardrail_lookup_reply, "Guardrail lookup should preserve external-write safety boundaries.")

        drafting_voice_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "How should you sound when drafting?", "mode": "live"},
            token,
        )
        drafting_voice_reply = drafting_voice_lookup.get("reply", "")
        assert_true(drafting_voice_lookup.get("intent") == "profile_drafting_voice_lookup", "Drafting-voice lookup should answer from the saved ministry profile.")
        assert_true("warm and brief" in drafting_voice_reply.lower(), "Drafting-voice lookup should include the saved communication style.")
        assert_true("non-denominational" in drafting_voice_reply.lower(), "Drafting-voice lookup should include the saved church voice/tradition.")

        rhythm_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "What rhythm should you protect?", "mode": "live"},
            token,
        )
        rhythm_reply = rhythm_lookup.get("reply", "")
        assert_true(rhythm_lookup.get("intent") == "profile_weekly_rhythm_lookup", "Weekly-rhythm lookup should answer from the saved ministry profile.")
        assert_true("Thursdays are sermon prep" in rhythm_reply, "Weekly-rhythm lookup should include the saved rhythm.")
        assert_true("external calendar" in rhythm_reply.lower() and "approval" in rhythm_reply.lower(), "Weekly-rhythm lookup should keep calendar write boundaries visible.")

        tools_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "What tools do you remember we use?", "mode": "live"},
            token,
        )
        tools_reply = tools_lookup.get("reply", "")
        assert_true(tools_lookup.get("intent") == "profile_tools_lookup", "Tools lookup should answer from the saved ministry profile.")
        assert_true("Planning Center" in tools_reply and "Gmail" in tools_reply, "Tools lookup should include saved church tools.")
        assert_true("no-sync credential check" in tools_reply and "sync only when you ask" in tools_reply, "Tools lookup should keep secure setup/check/sync order visible.")
        assert_true(
            any(action.get("type") in {"integration_setup", "integration_check"} for action in tools_lookup.get("actions", [])),
            "Tools lookup should attach relevant connector setup/check cards.",
        )

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
        weekly_help = request(
            "POST",
            "/assistant/chat",
            {"message": "How will you help me this week?", "mode": "live"},
            token,
        )
        weekly_help_reply = weekly_help.get("reply", "")
        assert_true(
            weekly_help.get("intent") == "ministry_operating_plan",
            "Natural weekly help questions should use the saved ministry operating plan.",
        )
        assert_true(
            "visitor follow-up" in weekly_help_reply.lower() and "first-time guests" in weekly_help_reply.lower(),
            "Weekly help should stay anchored in the pastor's follow-up burden and first priority.",
        )
        profile_recap = request(
            "POST",
            "/assistant/chat",
            {"message": "Open the ministry profile.", "mode": "live"},
            token,
        )
        profile_recap_prompts = profile_recap.get("suggested_prompts") or []
        assert_true(
            profile_recap.get("intent") == "onboarding",
            "Suggested ministry-profile prompts should route to the profile recap branch.",
        )
        assert_true(
            "I have the core ministry profile" in profile_recap.get("reply", ""),
            "Ministry-profile recap should acknowledge the saved profile instead of asking a generic setup question.",
        )
        assert_true(
            "Open the ministry profile." not in profile_recap_prompts,
            "Ministry-profile recap should not suggest the same prompt again.",
        )
        assert_true(
            {"What do you know about my church?", "How will you use this context?"}.issubset(set(profile_recap_prompts)),
            "Ministry-profile recap should suggest useful follow-up questions.",
        )

        pastoral_pressure = request(
            "POST",
            "/assistant/chat",
            {"message": "I am overwhelmed by all the follow-up today.", "mode": "live"},
            token,
        )
        pastoral_pressure_reply = pastoral_pressure.get("reply", "")
        assert_true(
            pastoral_pressure.get("intent") == "pastor_support",
            "Pastoral pressure prompts should use the saved support style instead of generic chat.",
        )
        assert_true(
            "Nudge me gently" in pastoral_pressure_reply and "protect my rest" in pastoral_pressure_reply,
            "Pastoral pressure replies should preserve the pastor's saved support preference.",
        )
        assert_true(
            "Fridays are my day off" in pastoral_pressure_reply or "Thursdays are sermon prep" in pastoral_pressure_reply,
            "Pastoral pressure replies should keep the saved weekly rhythm in view.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in pastoral_pressure.get("actions", [])),
            "Pastoral pressure replies should attach the smallest concrete next setup item while the workspace is still empty.",
        )
        plate_support = request(
            "POST",
            "/assistant/chat",
            {"message": "What can you take off my plate today?", "mode": "live"},
            token,
        )
        plate_support_reply = plate_support.get("reply", "")
        assert_true(
            plate_support.get("intent") == "pastor_support",
            "Take-off-my-plate prompts should route to pastoral support instead of generic chat.",
        )
        assert_true(
            "Nudge me gently" in plate_support_reply and "protect my rest" in plate_support_reply,
            "Take-off-my-plate replies should preserve the pastor's saved support style.",
        )
        assert_true(
            "approval" in plate_support_reply.lower() and "draft" in plate_support_reply.lower(),
            "Take-off-my-plate replies should explain reviewable delegation instead of implying autonomous sends.",
        )
        assert_true(
            any(action.get("type") == "data_seed" and action.get("form") == "visitor" for action in plate_support.get("actions", [])),
            "Take-off-my-plate replies should attach the smallest concrete next setup item while the workspace is still empty.",
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
        missing_draft_approval = request(
            "POST",
            "/assistant/chat",
            {"message": "Approve the welcome draft.", "mode": "live"},
            token,
        )
        assert_true(
            missing_draft_approval.get("intent") == "assistant_action_not_found",
            "Approval commands should not fall back to setup items when the named draft does not exist yet.",
        )
        assert_true(
            "real visitor" in missing_draft_approval.get("reply", "").lower(),
            "Missing-draft approval should point back to first real context instead of approving the wrong setup item.",
        )
        assert_true(
            any(action.get("form") == "visitor" for action in missing_draft_approval.get("actions", [])),
            "Missing-draft approval should attach the first real visitor setup card.",
        )
        empty_approval_seed = request(
            "POST",
            "/assistant/chat",
            {"message": "What should I approve first?", "mode": "live"},
            token,
        )
        assert_true(
            empty_approval_seed.get("intent") == "approval_queue_lookup",
            "Empty approval questions should still use the approval lookup path.",
        )
        assert_true(
            "real visitor" in empty_approval_seed.get("reply", "").lower(),
            "An empty first-run approval queue should point back to the first real visitor instead of generic draft work.",
        )
        assert_true(
            any(action.get("form") == "visitor" for action in empty_approval_seed.get("actions", [])),
            "Empty approval guidance should attach the first real visitor setup card.",
        )
        empty_approval_prompts = " ".join(empty_approval_seed.get("suggested_prompts") or []).lower()
        assert_true(
            "before noon" not in empty_approval_prompts and "draft" not in empty_approval_prompts,
            "Empty approval guidance prompts should not imply placeholder operational work is ready.",
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
            "nudge me gently" in empty_morning_reply.lower() and "protect my rest" in empty_morning_reply.lower(),
            "Empty live morning briefings should reflect the pastor's saved support style.",
        )
        assert_true(
            "placeholder" not in empty_morning_reply.lower(),
            "Empty live morning briefings should avoid prototype/placeholder wording.",
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
        off_script_with_person = request(
            "POST",
            "/assistant/chat",
            {"message": "Can you be my secretary?", "mode": "live"},
            token,
        )
        assert_true(
            off_script_with_person.get("intent") == "general_assistant",
            "Off-script chat with real ministry context should still use the assistant fallback.",
        )
        assert_true(
            "Talia Brooks" in off_script_with_person.get("reply", ""),
            "Off-script fallback should lead with real people once Marge has ministry context.",
        )
        assert_true(
            any(action.get("title") == "Talia Brooks" for action in off_script_with_person.get("actions", [])),
            "Off-script fallback should return the real visitor card before connector setup cards.",
        )
        connector_setup_reason = request(
            "POST",
            "/assistant/chat",
            {"message": "Why is this the next step?", "mode": "live"},
            token,
        )
        connector_setup_reply = connector_setup_reason.get("reply", "")
        assert_true(
            connector_setup_reason.get("intent") == "setup_step_reason",
            "After the first real record, setup-reason prompts should explain the next connector step.",
        )
        assert_true(
            "secure setup" in connector_setup_reply.lower() and "real ministry context" in connector_setup_reply.lower(),
            "Connector setup reasons should explain how saved tools lead to real ministry context.",
        )
        assert_true(
            "placeholder" not in connector_setup_reply.lower(),
            "Connector setup reasons should not describe the pastor experience as placeholder guidance.",
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
        wrong_named_approval = request(
            "POST",
            "/assistant/chat",
            {"message": "Approve the Marcus welcome draft.", "mode": "live"},
            token,
        )
        assert_true(
            wrong_named_approval.get("intent") == "assistant_action_not_found",
            "Named approval commands should not approve a different pending draft when the named person does not match.",
        )
        first_visitor_actions_after_wrong_approval = request("GET", "/assistant/actions?status=all&limit=80", token=token)
        talia_after_wrong_approval = next(
            (
                action
                for action in first_visitor_actions_after_wrong_approval
                if action.get("action_type") == "email_draft"
                and action.get("related_type") == "visitor"
                and "Talia Brooks" in (action.get("description") or "")
            ),
            None,
        )
        assert_true(
            talia_after_wrong_approval and talia_after_wrong_approval.get("status") == "pending",
            "A mismatched approval command must leave the actual visitor welcome draft pending.",
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
        generic_person_reply = generic_person_add.get("reply", "")
        assert_true("real name first" in generic_person_reply.lower(), "Person capture guidance should require a real name.")
        assert_true(
            "ruth carter" not in generic_person_reply.lower() and "example.test" not in generic_person_reply.lower(),
            "Person capture guidance should not teach live pastors with fake names or test email addresses.",
        )
        assert_true(
            "placeholder" not in generic_person_reply.lower(),
            "Person capture guidance should avoid prototype/placeholder wording.",
        )
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
        unnamed_visitor_reply = unnamed_visitor.get("reply", "")
        assert_true(
            "naomi grace" not in unnamed_visitor_reply.lower() and "example.test" not in unnamed_visitor_reply.lower(),
            "No-name visitor guidance should not use fake people or test emails.",
        )
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
        legacy_chat_history = request("GET", "/assistant/chat/history?limit=20", token=token)
        assert_true(
            any(
                history_item.get("role") == "user"
                and "naomi grace" in (history_item.get("content") or "").lower()
                for history_item in legacy_chat_history
            ),
            "Legacy /chat/ should persist the pastor turn into connected assistant chat history.",
        )
        assert_true(
            any(
                history_item.get("role") == "assistant"
                and history_item.get("intent") == "visitor_logged"
                and history_item.get("saved")
                for history_item in legacy_chat_history
            ),
            "Legacy /chat/ should persist the connected assistant reply metadata.",
        )
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
        pastoral_reminder = request(
            "POST",
            "/assistant/chat",
            {"message": "Remind me to call Janet Ellis tomorrow.", "mode": "live"},
            token,
        )
        reminder_reply = pastoral_reminder.get("reply", "")
        assert_true(
            pastoral_reminder.get("intent") == "pastoral_reminder_queued",
            "Natural reminder prompts should queue a local pastoral reminder instead of falling through.",
        )
        assert_true(
            pastoral_reminder.get("saved") and "Janet Ellis" in reminder_reply and "tomorrow" in reminder_reply.lower(),
            "Pastoral reminder chat should link the known person and preserve timing language.",
        )
        assert_true(
            "Nothing was sent, synced, or written" in reminder_reply,
            "Pastoral reminder chat should preserve the no-external-write boundary.",
        )
        assert_true(
            any(action.get("type") == "pastoral_reminder" and "call Janet Ellis" in (action.get("detail") or "") for action in pastoral_reminder.get("actions", [])),
            "Pastoral reminder chat should return a visible reminder action card.",
        )
        reminder_actions = request("GET", "/assistant/actions?status=pending&limit=80", token=token)
        janet_reminder = next(
            (
                action
                for action in reminder_actions
                if action.get("action_type") == "pastoral_reminder"
                and action.get("related_type") == "member"
                and not action.get("external_provider")
                and ((action.get("payload") or {}).get("reminder") or {}).get("person_name") == "Janet Ellis"
            ),
            None,
        )
        assert_true(
            janet_reminder is not None,
            "Pastoral reminder actions should stay local, pending, and linked to the resolved member.",
        )
        reminder_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "What reminders do I have?", "mode": "live"},
            token,
        )
        assert_true(
            reminder_lookup.get("intent") == "pastoral_reminder_lookup"
            and "call Janet Ellis" in reminder_lookup.get("reply", "")
            and "local Marge memory" in reminder_lookup.get("reply", "")
            and any(action.get("type") == "pastoral_reminder" for action in reminder_lookup.get("actions", [])),
            "Reminder lookup chat should return pending local reminders instead of generic approval copy.",
        )
        completed_reminder = request("POST", f"/assistant/actions/{janet_reminder['id']}/execute", token=token)
        completed_payload = completed_reminder.get("payload") or {}
        assert_true(
            completed_reminder.get("status") == "executed"
            and completed_reminder.get("action_type") == "pastoral_reminder"
            and ((completed_payload.get("execution") or {}).get("kind") == "pastoral_reminder_completed"),
            "The Mark done API path should complete a pending local pastoral reminder without a separate approval.",
        )
        second_reminder = request(
            "POST",
            "/assistant/chat",
            {"message": "Remind me to text Janet Ellis next week.", "mode": "live"},
            token,
        )
        assert_true(
            second_reminder.get("intent") == "pastoral_reminder_queued",
            "A second reminder should be queued so chat completion can target a pending local item.",
        )
        mark_reminder_done = request(
            "POST",
            "/assistant/chat",
            {"message": "Mark Janet reminder done.", "mode": "live"},
            token,
        )
        assert_true(
            mark_reminder_done.get("intent") == "assistant_action_executed"
            and "Marked done" in mark_reminder_done.get("reply", "")
            and any(
                action.get("type") == "pastoral_reminder" and action.get("subtitle") == "executed"
                for action in mark_reminder_done.get("actions", [])
            ),
            "Natural reminder completion chat should mark the local pastoral reminder done instead of asking for approval.",
        )
        cancel_reminder = request(
            "POST",
            "/assistant/chat",
            {"message": "Remind me to check on Janet Ellis in two weeks.", "mode": "live"},
            token,
        )
        assert_true(
            cancel_reminder.get("intent") == "pastoral_reminder_queued",
            "A reminder should be queued so natural cancel phrasing can target it.",
        )
        reschedule_reminder_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Move Janet reminder to Friday.", "mode": "live"},
            token,
        )
        assert_true(
            reschedule_reminder_chat.get("intent") == "pastoral_reminder_rescheduled"
            and "Friday" in reschedule_reminder_chat.get("reply", "")
            and "Nothing was sent, synced, or written externally" in reschedule_reminder_chat.get("reply", "")
            and any(
                action.get("type") == "pastoral_reminder"
                and action.get("subtitle") == "pending"
                and "Timing: Friday" in (action.get("detail") or "")
                for action in reschedule_reminder_chat.get("actions", [])
            ),
            "Natural reminder reschedule chat should update local reminder timing without external writes.",
        )
        snooze_reminder_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Snooze it in two weeks.", "mode": "live"},
            token,
        )
        assert_true(
            snooze_reminder_chat.get("intent") == "pastoral_reminder_rescheduled"
            and "in two weeks" in snooze_reminder_chat.get("reply", "")
            and any(
                action.get("type") == "pastoral_reminder"
                and "Timing: in two weeks" in (action.get("detail") or "")
                for action in snooze_reminder_chat.get("actions", [])
            ),
            "Generic reminder snooze chat should update local reminder timing.",
        )
        cancel_reminder_chat = request(
            "POST",
            "/assistant/chat",
            {"message": "Cancel Janet reminder.", "mode": "live"},
            token,
        )
        assert_true(
            cancel_reminder_chat.get("intent") == "assistant_action_skipped"
            and "Skipped" in cancel_reminder_chat.get("reply", "")
            and any(
                action.get("type") == "pastoral_reminder" and action.get("subtitle") == "skipped"
                for action in cancel_reminder_chat.get("actions", [])
            ),
            "Natural reminder cancel chat should skip the matching local pastoral reminder.",
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
        assert_true("nudge me gently" in defer_reply.lower(), "Deferral triage should preserve the pastor's support style.")
        assert_true(
            any(name in defer_reply for name in ["Janet Ellis", "Ruth Carter", "Talia Brooks", "Naomi Grace"]),
            "Deferral triage should stay grounded in real people or queued review items.",
        )
        assert_true(
            "admin cleanup" not in defer_reply.lower(),
            "Deferral triage should not fall back to generic admin-cleanup language.",
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
        assert_true("nudge me gently" in next_action_reply.lower(), "Next-action reply should preserve the pastor's support style.")
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
        assert_true("nudge me gently" in morning_reply.lower(), "Populated morning briefing should preserve the pastor's support style.")
        assert_true("approval" in morning_reply.lower(), "Morning briefing should keep approval boundaries visible.")
        assert_true(morning_briefing.get("actions"), "Morning briefing should attach concrete desk or review cards.")
        staff_meeting_briefing = request(
            "POST",
            "/assistant/chat",
            {"message": "What needs my attention before staff meeting?", "mode": "live"},
            token,
        )
        staff_meeting_reply = staff_meeting_briefing.get("reply", "")
        assert_true(
            staff_meeting_briefing.get("intent") == "morning_briefing",
            "Staff-meeting attention prompts should return the concrete morning briefing.",
        )
        assert_true(
            any(name in staff_meeting_reply for name in ["Janet Ellis", "Talia Brooks", "Naomi Grace", "Review Visitor welcome"]),
            "Staff-meeting briefing should stay grounded in real people or queued review work.",
        )
        assert_true("approval" in staff_meeting_reply.lower(), "Staff-meeting briefing should keep approval boundaries visible.")
        assert_true("nudge me gently" in staff_meeting_reply.lower(), "Staff-meeting briefing should preserve the pastor's support style.")
        assert_true(staff_meeting_briefing.get("actions"), "Staff-meeting briefing should attach concrete desk or review cards.")
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
        assert_true(
            "local reminder" in contact_log.get("reply", "").lower()
            and "Remind me to check on Janet Ellis next week." in (contact_log.get("suggested_prompts") or []),
            "Contact-log chat should proactively suggest a local next-check-in reminder.",
        )
        last_visit_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "When did I last visit Janet Ellis?", "mode": "live"},
            token,
        )
        assert_true(
            last_visit_lookup.get("intent") == "person_context_lookup"
            and "Active care" in last_visit_lookup.get("reply", "")
            and "last contact" in last_visit_lookup.get("reply", "")
            and "Log that I visited Janet Ellis today" in last_visit_lookup.get("reply", ""),
            "Last-visit chat should answer from pastoral memory instead of generic calendar planning.",
        )
        check_next_lookup = request(
            "POST",
            "/assistant/chat",
            {"message": "Who should I check on next?", "mode": "live"},
            token,
        )
        assert_true(
            check_next_lookup.get("intent") == "next_action"
            and "Janet Ellis" in check_next_lookup.get("reply", "")
            and any(action.get("title") == "Janet Ellis" for action in check_next_lookup.get("actions", [])),
            "Who-to-check-on chat should route to next-action triage from ministry context.",
        )
        assert_true(
            "Draft a care follow-up for Janet Ellis." in (check_next_lookup.get("suggested_prompts") or [])
            and "Remind me to check on Janet Ellis next week." in (check_next_lookup.get("suggested_prompts") or []),
            "Who-to-check-on route prompts should carry the named person into the next action.",
        )
        remembered_preference = request(
            "POST",
            "/assistant/chat",
            {"message": "Remember that Janet Ellis prefers phone calls over texts.", "mode": "live"},
            token,
        )
        assert_true(
            remembered_preference.get("intent") == "member_note_logged" and remembered_preference.get("saved"),
            "Remember-that preference prompts should save local member memory through the chat route.",
        )
        assert_true(
            "Janet Ellis" in remembered_preference.get("reply", ""),
            "Remembered preference replies should name the resolved local person.",
        )
        assert_true(
            any(action.get("type") == "member_note" and action.get("source") == "member_note" for action in remembered_preference.get("actions", [])),
            "Remembered preferences should return a visible member-note card.",
        )
        preference_context = request(
            "POST",
            "/assistant/chat",
            {"message": "What do you know about Janet Ellis?", "mode": "live"},
            token,
        )
        assert_true(
            preference_context.get("intent") == "person_context_lookup"
            and "Preferences to respect" in preference_context.get("reply", "")
            and "phone calls over texts" in preference_context.get("reply", ""),
            "Person context lookup should retrieve remembered preferences distinctly.",
        )
        preference_draft = request(
            "POST",
            "/assistant/chat",
            {"message": "Draft a care follow-up for Janet Ellis.", "mode": "live"},
            token,
        )
        assert_true(
            preference_draft.get("intent") == "draft_care_followup_queued",
            "Care follow-up drafts should still queue after a remembered preference is saved.",
        )
        preference_draft_card = (preference_draft.get("actions") or [{}])[0]
        preference_draft_action_id = int(str(preference_draft_card.get("id", "action-0")).replace("action-", ""))
        preference_draft_action = request("GET", f"/assistant/actions/{preference_draft_action_id}", token=token)
        preference_draft_payload = preference_draft_action.get("payload") or {}
        preference_draft_context = preference_draft_payload.get("draft_context") or {}
        preference_draft_preferences = preference_draft_context.get("member_preferences") or []
        preference_draft_body = ((preference_draft_payload.get("email") or {}).get("body") or "").lower()
        assert_true(
            preference_draft_context.get("member_name") == "Janet Ellis"
            and any("phone calls over texts" in (preference.get("text") or "") for preference in preference_draft_preferences),
            "Reviewable care drafts should carry remembered member preferences in draft metadata.",
        )
        assert_true(
            "phone calls over texts" not in preference_draft_body,
            "Remembered preferences should stay out of sendable draft bodies unless the pastor edits them in.",
        )

        command_answer_signup = request(
            "POST",
            "/assistant/signup",
            {
                "pastor_name": "Pastor Command Answer Smoke",
                "church_name": f"Command Answer Smoke Church {suffix}",
                "email": f"command-answer-{suffix}@example.test",
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
            "Nudge me gently and surface only what I am likely to miss.",
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
            command_answer_profile.get("tools_in_use") == "Planning Center, Gmail/Google Workspace",
            "A 'Connect Planning Center and Gmail' answer to the tools question should save tools instead of being skipped as a command.",
        )
        assert_true(
            "Nudge me gently" in (command_answer_profile.get("support_preferences") or ""),
            "A support-style answer should be saved before tool setup starts.",
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
                "email": f"terse-answer-{suffix}@example.test",
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
            "Nudge me gently.",
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
        assert_true("Nudge me gently" in (terse_answer_profile.get("support_preferences") or ""), "Terse support-style answers should be saved.")
        assert_true(terse_answer_profile.get("tools_in_use") == "Planning Center, Gmail/Google Workspace", "Terse tool answers should be normalized to known church tools.")
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
                "email": f"prayer-focus-{suffix}@example.test",
                "role_title": "Solo Pastor",
                "congregation_size": "45",
                "church_context": "A small rural church where private prayer needs often come through handwritten cards.",
                "faith_tradition": "Baptist roots; keep language gentle and discreet.",
                "followup_pain": "Private prayer requests fall through the cracks after Sunday.",
                "ministry_priorities": "Close loops with private prayer needs before people feel forgotten.",
                "support_preferences": "Nudge me gently and help me protect confidential prayer follow-up.",
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
                "email": f"care-focus-{suffix}@example.test",
                "role_title": "Solo Pastor",
                "congregation_size": "70",
                "church_context": "A small town church with older members and a lot of hospital care.",
                "faith_tradition": "Methodist; use gentle plain language.",
                "followup_pain": "Hospital and grief follow-up fall through the cracks after the first visit.",
                "ministry_priorities": "Keep active hospital and grief care visible until someone checks back in.",
                "support_preferences": "Surface the care cases I am likely to miss and do not overwhelm me.",
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
        care_capture_reply = care_capture_help.get("reply", "")
        assert_true("latest contact" in care_capture_reply.lower(), "Care capture guidance should ask for latest contact context.")
        assert_true(
            "ruth carter" not in care_capture_reply.lower() and "example.test" not in care_capture_reply.lower(),
            "Care capture guidance should not rely on fake names or test email addresses.",
        )
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
        if care_connect_account_id is not None:
            cleanup_account(care_connect_account_id)
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
