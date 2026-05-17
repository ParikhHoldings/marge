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
        "Array.isArray(parsed.detail)" in script,
        "FastAPI validation errors should be translated into pastor-readable form feedback.",
    )
    assert_true(
        "Check credentials before syncing ministry data" in html
        and "This check did not sync people, email, or calendar data, and it did not queue any actions." in html,
        "Integration UI should preserve the no-sync credential-check boundary.",
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
        and "I visited Maria today" not in html,
        "Live first-run copy should not imply future-only tools, MCP-as-ChMS, or fake-person placeholders.",
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
        "Log that I visited Maria" not in html
        and "I called Tom today" not in html
        and "Draft a text for Tom" not in html,
        "Generic live prompts should not lean on fake named people when workspace records should provide names.",
    )
    assert_true(
        "liveFallbackPrompts(profile, setupSteps)" in script
        and 'desk.suggested_prompts || ["What needs my attention before noon?"' not in script,
        "Live assistant prompt fallback should derive from workspace context instead of hardcoded operational placeholder prompts.",
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
