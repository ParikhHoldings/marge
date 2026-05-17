# Marge Build Summary

Last updated: 2026-05-17

This file is a current-state summary for agents and maintainers. Older build-task details have been superseded by `AGENTS.md`, `README.md`, `docs/THREAD_GOAL_AUDIT.md`, and `docs/PRODUCTION_READINESS.md`.

## Current Status

Marge is now a FastAPI/SQLite local MVP with a static `/app` workspace, account-scoped first-run onboarding, persisted assistant chat, reviewable assistant actions, secure connector setup, MCP tooling, and Alembic migrations.

The local product is useful enough to exercise the first pastor journey with smoke tests, but it is not pilot-ready until production secrets are configured and at least one live external church-tool provider verifies successfully without syncing ministry data.

## What Works Locally

- Pastor signup creates a private church workspace and owner token.
- Browser sessions exchange tokens for an HttpOnly `marge_session` cookie.
- First-run onboarding captures pastor/church context, church voice/tradition, first ministry priority, follow-up pain, tools, drafting voice, rhythm, and guardrails.
- `/assistant/chat` saves and retrieves ministry context, logs visitors/care/prayer/person updates, remembers chat history, and answers from synced context.
- `/assistant/desk` drives proactive setup steps, first-week plan, approvals, connector status, and suggested next prompts.
- Visitor follow-up drafts are queued as `AssistantAction` records for pastor review.
- Pastor/admin/owner role gates protect pastoral briefing, drafts, member details/notes, care, prayer, approval actions, chat history, connected context, audit, sync, and write surfaces; staff can read the live desk and basic member/visitor directories without seeing the approval queue.
- Google Workspace, Microsoft 365, Planning Center, Breeze, and Rock have secure setup/status/sync surfaces.
- OAuth/API-key credentials are encrypted, workspace-scoped, and require a no-sync verification step before sync.
- External writes require connector policy, per-action approval, and explicit execution.
- `MARGE_ENV=production` enables startup safety checks so unsafe deployments, placeholder domains, partial connector config, and non-HTTPS OAuth redirects fail closed instead of serving real pastoral data.
- MCP clients can use Marge as an assistant through `mcp_server/server.py`.

## Main Entry Points

- App: `frontend/index.html`, mounted at `/app`.
- API: `app/main.py`.
- Assistant and onboarding: `app/routers/assistant.py`.
- Account/session helpers: `app/services/accounts.py`.
- Encryption helpers: `app/services/secure_tokens.py`.
- Visitor follow-up queueing: `app/services/visitor_followup.py`.
- MCP server: `mcp_server/server.py`.
- Schema history: `migrations/versions/`.

## Verification Commands

Run these against the local app when changing first-run, chat, connector, approval, migration, or MCP behavior:

```bash
.venv/bin/python scripts/smoke_first_run.py
.venv/bin/python scripts/smoke_integrations.py
.venv/bin/python scripts/smoke_connected_providers.py
.venv/bin/python scripts/smoke_mcp_first_run.py
.venv/bin/python scripts/smoke_frontend_static.py
.venv/bin/python scripts/verify_deployment_bootstrap.py --allow-relaxed-account-tokens
.venv/bin/python scripts/verify_first_run_workspace.py
.venv/bin/python scripts/verify_migrations.py
git diff --check
```

Run the strict pilot gate before any live pastoral-data handoff:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=marge_sess_... \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

That gate must fail unless production config is safe, the public app bootstrap path is reachable with strict workspace-token mode, migrations are current, and at least one live external provider verifies. MCP alone is not enough.

When the gate fails, it prints a `Next actions` section that names the exact production config, migration, or live connector work still blocking a pilot.

## Known Remaining Blockers

- Production `DATABASE_URL`, session-cookie, CORS, app URL, SMTP, account-token enforcement, and Fernet encryption settings must be supplied in the deployment environment.
- At least one real provider tenant, such as Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS, must be connected and verified with the no-sync credential check. The live verifier also checks that verification did not change synced context or assistant actions.
- The first real sync should happen only after the pastor/admin explicitly requests it after credential verification.

## Historical Context

The original April MVP implemented a morning briefing API, CRUD routes, draft templates, and a basic MCP server. The current repo has moved beyond that baseline. Treat `spec.md` as the long-term product vision, `codex-task.md` as a historical prompt, and `docs/THREAD_GOAL_AUDIT.md` as the current active-goal evidence map.
