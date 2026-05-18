#!/usr/bin/env python3
"""
Static smoke checks for the single-file Marge frontend.

This catches JavaScript syntax errors and a few first-run UX invariants that
are easy to regress without a full browser session.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "index.html"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert_true(len(scripts) == 1, "Frontend should keep a single inline app script.")
    script = scripts[0]

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        temp_path = handle.name
    try:
        subprocess.run(["node", "--check", temp_path], cwd=ROOT, check=True)
    finally:
        os.remove(temp_path)

    assert_true(
        "toast(errorMessage(error, submitErrorFallback(type)))" in script,
        "Form failures should show backend-safe error details instead of a generic save failure.",
    )
    assert_true(
        'type === "integration-credential"' in script
        and "Check encrypted storage, provider URL, and your workspace role" in script,
        "Connector credential failures should point pastors/admins to encrypted storage, URL, and role issues.",
    )
    assert_true(
        'const baseRequired = ["breeze", "rock"].includes(provider);' in script
        and 'provider === "rock" ? "Rock base URL"' in script
        and "https://rock.yourchurch.org/api/v2" in script,
        "API-key connector setup should require and label Breeze/Rock public HTTPS base URLs before credential submission.",
    )
    assert_true(
        'const canAddWorkspaceApiKey = ["api_key", "env_api_key"].includes(setup.setup_type) && !missing.includes("MARGE_ENCRYPTION_KEY");' in script
        and '"Add encrypted credentials"' in script,
        "API-key connector setup cards should point to encrypted workspace credentials when storage is ready.",
    )
    assert_true(
        'missing.includes("MARGE_ENCRYPTION_KEY")' in script
        and '"Review encrypted storage"' in script,
        "API-key connector setup cards should distinguish missing encrypted storage from generic server config.",
    )
    assert_true(
        "Array.isArray(parsed.detail)" in script,
        "FastAPI validation errors should be translated into pastor-readable form feedback.",
    )
    assert_true(
        "Check credentials before syncing ministry data" in html
        and "This check did not sync people, email, or calendar data, and it did not queue any actions." in html,
        "Integration UI should preserve the no-sync credential-check boundary.",
    )
    assert_true(
        "function unsafeIdentityKey(key)" in script
        and "function unsafeIdentityValue(value)" in script
        and "function redactSecretText(value)" in script
        and "escapeHtml(redactSecretText(result.message" in script
        and "return redactSecretText(parsed.detail)" in script
        and "Some verification metadata was hidden because it looked like a secret." in script
        and "verificationIdentityHtml(result.identity || {})" in script,
        "Connector verification UI should hide or redact token-shaped metadata/messages even if a future API response regresses.",
    )
    assert_true(
        "access[_\\-.]?token" in script
        and "refresh[_\\-.]?token" in script
        and "client[_\\-.]?secret" in script
        and "api[_-]?key" in script
        and "marge_sess_" in script
        and "ya29\\." in script,
        "Frontend identity metadata filter should recognize common OAuth/API/session token shapes.",
    )
    assert_true(
        "function safeExternalHref(value)" in script
        and "unsafeIdentityValue(href)" in script
        and "parsed.protocol === \"https:\"" in script
        and "escapeHtml(redactSecretText(execution.provider_id))" in script
        and "toast(redactSecretText(result.message || \"Assistant chat history cleared.\"))" in script,
        "Execution detail links and server-provided toast messages should avoid rendering token-shaped provider values.",
    )
    assert_true(
        "Connect Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS, check credentials, then sync" in html,
        "Synced-context empty state should list all live providers and keep credential checks before sync.",
    )
    assert_true(
        "Gmail/Google Workspace" in html
        and "Outlook/Microsoft 365" in html
        and "Google, and Outlook can feed Marge" not in html
        and "Google, and Outlook remain the systems of record" not in html,
        "Visible connector copy should use pastor-friendly aliases while naming the actual Google Workspace and Microsoft 365 connectors.",
    )
    assert_true(
        'if (connectorNeedsVerification(item)) return "Needs check";' in script
        and "const canToggleWriteback = writebackAllowedActions(item.provider).length > 0 && canSync;" in script
        and "Writeback: locked until credentials pass Check credentials." in script,
        "Integration cards should not present unchecked credentials as ready for sync/writeback.",
    )
    assert_true(
        'if (["profile", "seed", "integrations"].includes(item.source || "")) return setupStepButton(item);' in script
        and 'return /check/i.test(`${step.action || ""} ${step.title || ""}`) ? "integration-verify" : "integration-start";' in script
        and 'if (action === "integration-verify") await verifyIntegration(actionEl.dataset.provider);' in script,
        "Setup and chat Check credentials cards should call the no-sync credential verifier, not restart setup or dead-end.",
    )
    assert_true(
        'const detailIntegrations = integrations.filter(item => item.provider !== "mcp");' in script
        and "detailIntegrations.length ? detailIntegrations.slice(0, 4).map" in script
        and "integrationStatusLabel(item)" in script,
        "Today detail connector health should use verified church-tool readiness labels and exclude MCP.",
    )
    assert_true(
        "Live drafts are prepared from workspace records through Marge's draft service" in html,
        "Live draft UI should not imply browser-only templates are approval-ready.",
    )
    assert_true(
        'name="ministry_priorities"' in html
        and "What would make Marge genuinely helpful in the first month?" in html
        and "Tell Marge what should get better first." in html,
        "Profile UI should let a pastor name the first ministry priority Marge should help move.",
    )
    assert_true(
        'name="support_preferences"' in html
        and "How to support you" in html
        and "Tell Marge how to nudge, protect, and support you personally." in html,
        "Profile UI should capture how the pastor personally wants Marge to support him.",
    )
    assert_true(
        'name="pastor_name" required' in html
        and 'name="email" type="email" required' in html
        and 'name="church_name" required' in html,
        "Visible first-pastor workspace signup should require pastor name, email, and church name.",
    )
    assert_true(
        'name="faith_tradition"' in html
        and "What tradition, denomination, or ministry language should Marge respect?" in html
        and "Tell Marge the tradition and language boundaries to respect." in html,
        "Profile UI should capture the pastor's church voice and tradition for draft fit.",
    )
    assert_true(
        'const churchToolIntegrations = integrations.filter(item => item.provider !== "mcp")' in script
        and "churchToolIntegrations.map" in script
        and "churchToolIntegrations.filter(canSyncIntegration)" in script,
        "Pastor-facing connector posture should separate MCP from external church-tool connectors.",
    )
    assert_true(
        'connectedCount || "Soon"' not in script
        and "MCP should feed Marge" not in html
        and "MCP stays under Agent Tools" not in html
        and "MCP-capable assistants should act through" not in html
        and "I visited Maria today" not in html,
        "Live first-run copy should not imply future-only tools, MCP-as-ChMS, protocol-first navigation, or fake-person placeholders.",
    )
    assert_true(
        "Agent access stays separate." in html
        and "Connect Marge to approved AI assistants without giving up pastoral safety." in html,
        "Pastor-facing navigation should keep agent access separate without leading with MCP protocol language.",
    )
    assert_true(
        "Live data" not in html
        and "current Marge database" not in html
        and "demo data" not in html
        and "browser-only demo" not in html
        and "Marge is reading this workspace's people, care, visitor, prayer, and approval history." in html,
        "Pastor-facing live workspace copy should avoid database/demo wording.",
    )
    assert_true(
        "actions: message.actions || []" in script
        and "prompts: message.suggested_prompts || []" in script
        and "chatActionsHtml(message)" in script
        and "chatPromptsHtml(message)" in script
        and "const enhancement = chatEnhancement(result);" in script
        and "attachChatEnhancement(result.reply, enhancement);" in script
        and "function normalizeChatText(value)" in script
        and "normalizeChatText(message.text) === normalizedReply" in script,
        "Assistant chat should keep returned actions/prompts visible after the post-chat data refresh.",
    )
    assert_true(
        "state.chat.slice(-4).map(tellChatBubbleHtml).join(\"\")" in script
        and "function tellChatBubbleHtml(message)" in script
        and "chatMessageBodyHtml(message)" in script
        and ".bubble.has-actions" in html,
        "Secondary-view Tell Marge panels should render returned chat action cards and prompts, not text-only bubbles.",
    )
    assert_true(
        'action.action_type === "pastoral_reminder" && action.status === "pending"' in script
        and 'data-action="action-complete"' in script
        and "completeAssistantAction(actionId)" in script
        and "Reminder marked done." in script
        and "This is local Marge memory only" in script,
        "Local pastoral reminders should be mark-done actions, not generic external approval items.",
    )
    assert_true(
        'const preferenceNotes = notes.filter(n => n.context_tag === "preference");' in script
        and 'const otherNotes = notes.filter(n => n.context_tag !== "preference");' in script
        and "Preferences To Respect" in script
        and "No preferences saved yet." in script
        and "Recent Notes" in script,
        "Person detail should surface remembered preferences separately from generic notes.",
    )
    assert_true(
        "const draftContext = action.payload?.draft_context || action.payload?.review_context || {};" in script
        and "const memberPreferences = Array.isArray(draftContext.member_preferences) ? draftContext.member_preferences : [];" in script
        and "const memberContext = Array.isArray(draftContext.member_context) ? draftContext.member_context : [];" in script
        and "Preferences to respect for" in script
        and "People context" in script
        and "memberPreferenceTexts.map" in script,
        "Assistant approval modal should show remembered member preferences from draft or meeting review metadata.",
    )
    assert_true(
        "const draftPrompt = `Draft reply to synced email: ${item.title}`;" in script
        and 'data-prompt="${escapeHtml(draftPrompt)}">Draft reply' in script
        and "const prepPrompt = `Prepare meeting brief for ${item.title}`;" in script
        and 'data-prompt="${escapeHtml(prepPrompt)}">Prepare brief' in script,
        "Synced inbox/calendar rows without queued actions should prompt Marge to create reviewable work from the real connected item.",
    )
    assert_true(
        "Log that I visited Maria" not in html
        and "I called Tom today" not in html
        and "Draft a text for Tom" not in html
        and "Pastor Ben" not in html
        and "Grace Community Church" not in html
        and "const answer = onboardingAnswerExample(interviewQuestion, currentProfile());" in script
        and "pastor_name: pastorName ? `Call me ${pastorName}.` : \"\"" in script
        and "church_name: churchName ? `I serve at ${churchName}.` : \"\"" in script,
        "Generic live prompts should not lean on fake named people when workspace records should provide names.",
    )
    assert_true(
        'tools_in_use: ""' in script
        and "We use Planning Center and Gmail/Google Workspace." not in html,
        "Tools onboarding should not offer a canned provider-stack answer that can save the wrong connectors.",
    )
    assert_true(
        'role_title: ""' in script
        and 'congregation_size: ""' in script
        and 'church_context: ""' in script
        and 'followup_pain: ""' in script
        and 'ministry_priorities: ""' in script
        and 'support_preferences: ""' in script
        and 'communication_style: ""' in script
        and 'weekly_rhythm: ""' in script
        and 'guardrails: ""' in script
        and "I am a solo pastor and we average about 85 people each week." not in html
        and "Visitor follow-up after Sunday is where things slip through the cracks." not in html
        and "Do not send emails or write to external systems without my approval." not in html,
        "First-run answer chips should not save canned ministry context for role, size, burden, rhythm, or guardrails.",
    )
    assert_true(
        'prompts.push({ label: "What to include", prompt: `What should I include for ${interviewQuestion.label.toLowerCase()}?` });' in script,
        "First-run onboarding should offer neutral guidance instead of only canned saveable answers.",
    )
    assert_true(
        "pastor@example.com" not in html
        and "you@yourchurch.org" in html,
        "Visible signup/login email placeholders should feel church-specific, not generic example data.",
    )
    assert_true(
        "liveFallbackPrompts(profile, setupSteps)" in script
        and 'desk.suggested_prompts || ["What needs my attention before noon?"' not in script,
        "Live assistant prompt fallback should derive from workspace context instead of hardcoded operational placeholder prompts.",
    )
    assert_true(
        "No urgent care gaps" not in script
        and "No urgent follow-up gaps" not in script
        and "No urgent situation logged" not in script
        and ' : "Clear")' not in script
        and "No current follow-up items visible" in script
        and "No drafts queued from current workspace context" in script
        and "None visible" in script
        and "No current situation is attached to this record yet." in script,
        "Live empty states should avoid placeholder certainty and stay scoped to current workspace context.",
    )
    assert_true(
        '"Unknown"' not in script
        and '"Anonymous"' not in script
        and '"friend"' not in script
        and '|| "Person")' not in script
        and 'execution.member_name || "Member"' not in script
        and "Sender not listed" not in script
        and "No preview synced." not in script
        and "Name not linked" in script
        and "Name withheld" in script,
        "Live row fallbacks should describe missing context instead of using placeholder names.",
    )
    assert_true(
        '"Here are your people for today"' not in script
        and "Marge is reading this workspace." in script
        and "real people, care, visitor, prayer, and approval work from this church workspace" in script,
        "Live Today header fallback should not claim Marge has sorted people before real context is available.",
    )
    assert_true(
        "/care|hospital|grief|crisis/i.test" in script
        and "Help me open the first care case." in script,
        "Care-focused first-run setup should prompt chat to open the first care case instead of falling back to a generic person prompt.",
    )
    assert_true(
        "const interview = needsWorkspace ? null : state.desk?.interview_question" in script
        and "state.mode === \"live\" && !hasWorkspace" in script,
        "Pre-workspace live mode should not render unscoped legacy interview, chat, or people data.",
    )
    assert_true(
        "state.mode === \"live\" && !hasWorkspaceSession" in script
        and 'hasWorkspaceSession ? api("/assistant/actions?status=all&limit=50") : Promise.resolve([])' in script,
        "Pre-workspace live mode should not request scoped workspace data before a session-backed workspace exists.",
    )
    assert_true(
        "if (needsWorkspaceForChat()) {" in script
        and 'return state.mode !== "demo" && !hasWorkspaceContext();' in script
        and 'state.view === "assistant" && needsWorkspaceForChat()' not in script,
        "Pre-workspace live chat composers on every tab should stay local instead of calling scoped assistant chat.",
    )
    assert_true(
        "state.chat.push(preWorkspaceChatMessage(message));" in script
        and "function preWorkspaceActionCard()" in script
        and 'form: "account"' in script
        and 'if (item.form === "account") return `<button class="btn primary" data-action="open-form" data-form="account"' in script
        and "function preWorkspacePrompts(message)" in script,
        "Pre-workspace local chat replies should include a visible Create workspace card and setup prompt chips.",
    )
    assert_true(
        'id="topPrimaryAction" data-action="open-form" data-form="account">Create workspace' in html
        and 'id="topSecondaryAction" data-prompt="What will you learn about my ministry?">Learn plan' in html
        and "configureTopActions(needsWorkspace)" in script,
        "Pre-workspace header actions should start workspace setup, not invite unsaved care/person writes.",
    )
    assert_true(
        "workspaceSetupToolbar()" in script
        and "quickActionCards(needsWorkspace)" in script
        and "Start the private church workspace before Marge saves people, care, visitors, prayer, or notes." in script,
        "Pre-workspace secondary views should show workspace setup actions before write buttons.",
    )
    assert_true(
        "function headerTitles(needsWorkspace, assistantHeader, todayHeader)" in script
        and "Create a private church workspace before Marge saves pastoral data, connects tools, or queues approvals." in script
        and 'detailToggle.textContent = needsWorkspace ? "Setup"' in script,
        "Pre-workspace page headers should not advertise save/log/approval workflows before a workspace exists.",
    )
    print("Marge frontend static smoke passed.")


if __name__ == "__main__":
    main()
