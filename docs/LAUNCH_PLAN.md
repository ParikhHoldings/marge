# Marge Launch Plan

Last updated: 2026-05-17

This plan supersedes the older Monday demo checklist. The target is now a first pastor pilot where Marge feels like a real pastoral secretary, not a static demo.

## Pilot Launch Definition

Marge is ready for a real pastor only when these are true:

- A pastor can sign up at `/app`, reload, and stay inside a private workspace.
- Marge asks ministry-context questions that adapt to the pastor's role, church, follow-up pain, first priority, personal support style, tools, voice, weekly rhythm, and guardrails.
- The Assistant screen does not show fake people in a live workspace.
- The first setup step names concrete work, such as logging the first real visitor or connecting the tool the pastor already uses.
- Chat can save real ministry updates and later answer from saved context.
- The first-run workspace verifier can create a disposable workspace, complete onboarding through chat, log a visitor, and queue the welcome draft.
- At least one live church-tool connector is verified without syncing ministry data first.
- Sync happens only after credential verification and an explicit pastor/admin request.
- External email/calendar writes remain disabled until connector policy, pastor approval, and explicit execution allow them.
- The public deployment bootstrap check passes for `/`, `/health`, `/assistant/config`, `/app`, and strict workspace-token mode.
- The strict pilot gate passes:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=marge_sess_... \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

## First Pastor Demo Flow

1. Create a fresh workspace through `/app`.
2. Answer the onboarding questions with the pastor's actual ministry context.
3. Show that Marge changes her next question and setup steps based on that context.
4. Log the first real visitor, care note, person, or prayer request through chat.
5. Open the approval queue and show the reviewable welcome draft or follow-up action.
6. Start secure setup for the pastor's real tool, such as Planning Center, Google Workspace, Microsoft 365, Breeze, or Rock.
7. Run **Check credentials** and confirm no people, email, calendar, or attendance data was imported.
8. Run the first sync only after the pastor agrees.
9. Show synced context and the pastor-review actions it creates.
10. Leave writeback disabled unless the pastor explicitly opts into a narrow action type.

## What Must Not Happen

- Do not use demo rows in a real workspace.
- Do not create `Guest` placeholder people or visitors when chat lacks a real name.
- Do not ask pastors to paste OAuth secrets, API keys, passwords, or refresh tokens into chat.
- Do not sync provider data during OAuth callback or API-key save.
- Do not send email or create calendar events just because a draft/action exists.
- Do not mark the pilot ready because local mocked-provider tests pass or because MCP verifies without a live church-tool provider.

## Current Local Verification

Use these as local gates while building:

```bash
.venv/bin/python scripts/smoke_first_run.py
.venv/bin/python scripts/smoke_integrations.py
.venv/bin/python scripts/smoke_connected_providers.py
.venv/bin/python scripts/smoke_mcp_first_run.py
.venv/bin/python scripts/smoke_pilot_readiness_gate.py
.venv/bin/python scripts/verify_deployment_bootstrap.py --allow-relaxed-account-tokens
.venv/bin/python scripts/verify_first_run_workspace.py
.venv/bin/python scripts/verify_migrations.py
git diff --check
```

These tests prove the local experience and mocked connector behavior. They do not replace production config checks or live connector verification.

## Handoff Artifacts

- `AGENTS.md`: canonical context for Codex and other LLM agents.
- `docs/THREAD_GOAL_AUDIT.md`: prompt-to-artifact checklist and latest gate results.
- `docs/PRODUCTION_READINESS.md`: production environment and live-connector handoff.
- `.env.production.example`: deployment secret template.
- `scripts/verify_deployment_bootstrap.py`: no-write public app bootstrap check.
- `scripts/verify_first_run_workspace.py`: disposable first-run workspace journey check.
- `scripts/verify_pilot_readiness.py`: one-command pilot readiness gate; includes the disposable first-run workspace journey by default.
