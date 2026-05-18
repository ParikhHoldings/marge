# Codex Handoff

## Current Objective

Make Marge feel like a real pastoral secretary for a first pastor signup: scoped workspace, useful chat, pastor-specific onboarding, secure tool connections, proactive reviewable work, and no placeholder/demo behavior in live flows.

## Completed Changes

- Added and expanded project guidance in `AGENTS.md` so future agents understand Marge's vision, architecture, security boundaries, first-run workflow, connector rules, and verification commands.
- Built account-scoped first-run/chat/desk behavior with persistent ministry profile, saved chat history, support preferences, setup steps, approval actions, and non-placeholder empty states.
- Hardened secure connector flows for Google Workspace, Microsoft 365, Planning Center, Breeze, Rock RMS, and MCP: setup/check/sync order, encrypted credentials, verified-before-sync gating, writeback approval boundaries, and live no-sync evidence.
- Added production/pilot readiness docs and gates so MCP/local checks cannot count as a live church-tool provider.
- Tightened backend, frontend, live verifier, pilot gate, and MCP redaction so token/API-key/session/JWT-shaped values are not returned in evidence, chat, frontend UI, or external LLM tool output.

## Files Changed

- Core app: `app/routers/assistant.py`, `app/runtime_config.py`, `app/main.py`, `app/models.py`, `app/services/*`, connector modules.
- Frontend: `frontend/index.html`.
- MCP: `mcp_server/server.py`, `mcp_server/README.md`.
- Verification: `scripts/smoke_first_run.py`, `scripts/smoke_integrations.py`, `scripts/smoke_connected_providers.py`, `scripts/smoke_frontend_static.py`, `scripts/smoke_mcp_first_run.py`, `scripts/smoke_pilot_readiness_gate.py`, `scripts/verify_live_integrations.py`, `scripts/verify_pilot_readiness.py`, `scripts/verify_production_config.py`, `scripts/verify_deployment_bootstrap.py`, `scripts/verify_first_run_workspace.py`.
- Docs/config: `AGENTS.md`, `README.md`, `BUILD_SUMMARY.md`, `.env.example`, `.env.production.example`, `.gitignore`, `docs/PRODUCTION_READINESS.md`, `docs/THREAD_GOAL_AUDIT.md`, `docs/PILOT_CONNECTOR_RUNBOOK.md`, `docs/AI_AGENT_ROADMAP.md`, `docs/LAUNCH_PLAN.md`.

## Known Issues

- The active goal is not complete until a real deployment is configured, migrations are applied there, and at least one real external provider verifies with no-sync side-effect evidence.
- Local runtime is intentionally not strict production mode: missing production `DATABASE_URL`, strict token enforcement, production Fernet key, HTTPS app/CORS/session-cookie settings, and SMTP delivery.
- No live Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS credential has been verified in this workspace.
- The worktree is dirty across many files from prior work. Do not assume all changes are from the current turn.

## Next Safest Steps

1. Run focused local checks after any touched area, not the whole suite by reflex.
2. For pilot readiness, configure a candidate production env and run:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

3. If the gate fails, follow its `Next actions` exactly: production config, deployment bootstrap, migrations, owner/admin/pastor token, live provider setup, no-sync credential check, and fresh evidence validation.
4. Only run first sync after the no-sync live verifier passes and the pastor/admin explicitly asks to sync.

## Tests Run

- `.venv/bin/python -m compileall mcp_server/server.py scripts/smoke_mcp_first_run.py`
- Focused no-network MCP redaction helper covering API error detail and rendered MCP tool output redaction.
- `git diff --check -- AGENTS.md docs/CODEX_HANDOFF.md mcp_server/server.py scripts/smoke_mcp_first_run.py docs/THREAD_GOAL_AUDIT.md`

Earlier passing checks are recorded in `docs/THREAD_GOAL_AUDIT.md` and `BUILD_SUMMARY.md`.

## Tests Still Needed

- Full local smoke suite after broader integration changes.
- `scripts/verify_pilot_readiness.py` against a real production candidate environment.
- Live no-sync connector verification with at least one real external provider and fresh evidence JSON.
- Production migration verification against the deployed database.

## Avoid Doing

- Do not mark the active goal complete from local smokes, MCP status, OAuth callback completion, or configured API keys.
- Do not sync/import real ministry data before a provider passes Check credentials and live no-sync verification.
- Do not echo raw provider errors, identity metadata, token-like text, or evidence payloads into chat, frontend UI, MCP output, docs, or git.
- Do not revert unrelated dirty files or user changes.
- Do not treat MCP as a live church-tool provider.
