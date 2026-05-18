# Marge Build Summary

Last updated: 2026-05-17

This file is a current-state summary for agents and maintainers. Older build-task details have been superseded by `AGENTS.md`, `README.md`, `docs/THREAD_GOAL_AUDIT.md`, and `docs/PRODUCTION_READINESS.md`.

## Current Status

Marge is now a FastAPI/SQLite local MVP with a static `/app` workspace, account-scoped first-run onboarding, persisted assistant chat, reviewable assistant actions, secure connector setup, MCP tooling, and Alembic migrations.

The local product is useful enough to exercise the first pastor journey with smoke tests, but it is not pilot-ready until production secrets are configured and at least one live external church-tool provider verifies successfully without syncing ministry data.

## What Works Locally

- Pastor signup requires a church name and valid owner email, then creates a private church workspace and owner token.
- Browser sessions exchange tokens for an HttpOnly `marge_session` cookie.
- First-run onboarding captures pastor/church context, church voice/tradition, first ministry priority, personal support style, follow-up pain, tools, drafting voice, rhythm, and guardrails.
- `/assistant/chat` saves and retrieves ministry context, logs visitors/care/prayer/person updates, remembers chat history, and answers from synced context.
- Visitor, person, care, and calendar capture guidance asks for real names/context and avoids fake example people or `.test` email addresses in live pastor-facing help.
- Setup reasons, empty live morning briefings, and person-capture guidance avoid prototype/placeholder wording and keep the pastor oriented around first real records, secure setup, and approval boundaries.
- "How will you support me?" is a real support-style chat path: while that field is missing, Marge asks the contextual support question without saving the question as the answer; after it is saved, Marge answers from the pastor's support preference.
- Broad weekly help prompts such as "How will you help me this week?" route to the saved ministry operating plan and first-record setup, not a generic explainer.
- "What should I connect first?" starts the next secure connector setup and explains the recommendation from the pastor's saved tools, follow-up burden, and remaining connector order; communication-heavy profiles start with mail/calendar, while care/attendance-heavy profiles start with the people system.
- Natural onboarding answers such as "Planning Center for kids check-in and Gmail" save as tools context; explicit credential, connection, verify, test, health-check, or short `Check {provider}` language routes chat to connector verification.
- The proactive desk summary carries the pastor's saved support preference once onboarding is complete, so the first screen can reflect how he asked Marge to nudge, protect, and surface work.
- The first-week launch plan review card and payload carry the pastor's saved support preference, not only tasks, connectors, and approval rules.
- Morning briefing chat carries the pastor's saved support preference in both empty and populated live workspaces while staying grounded in real records or the first-record setup card.
- "What should I handle next?" returns real people/review work while preserving the pastor's saved support preference and weekly rhythm.
- "What can wait until next week?" triages real people/review work, keeps approval boundaries visible, and preserves the pastor's saved support preference.
- `/assistant/desk` drives proactive setup steps, first-week plan, approvals, connector status, and suggested next prompts.
- Visitor follow-up drafts are queued as `AssistantAction` records for pastor review.
- Pastor/admin/owner role gates protect pastoral briefing, drafts, member details/notes, care, prayer, approval actions, chat history, connected context, audit, sync, and write surfaces; staff can read the live desk and basic member/visitor directories without seeing the approval queue.
- Google Workspace, Microsoft 365, Planning Center, Breeze, and Rock have secure setup/status/sync surfaces.
- OAuth/API-key credentials are encrypted, workspace-scoped, and require a no-sync verification step before sync.
- The live connector verifier snapshots every page of connected context, assistant actions, members, visitors, care cases, and private/public prayer requests before and after Check credentials, so pilot evidence cannot miss imports or queued work hidden beyond the first page.
- OAuth callback and integration status copy keep the "Check credentials before syncing ministry data" boundary visible while `verified_at` is still empty.
- Chat-triggered connector setup short-circuits connected-but-unchecked tools to a `Check credentials` card instead of starting a fresh OAuth setup or suggesting sync too early; following that suggested prompt runs a no-sync credential check rather than saving the phrase as onboarding/profile context.
- Calendar write help and chat-queued calendar events require a credential-checked Google Workspace or Microsoft 365 OAuth connection; connected-but-unverified calendars get credential-check setup cards instead of write-ready copy or generic missing-details replies, and calendar help examples use real workspace context or neutral wording instead of fake names/emails.
- The frontend labels unchecked connector credentials as "Needs check", routes setup/chat `Check credentials` cards to the no-sync verifier, hides Sync/writeback controls until `verified_at` exists, keeps writeback locked until credentials pass Check credentials, and excludes MCP from pastor-facing church-tool readiness summaries.
- Breeze/Rock API-key setup cards and chat replies distinguish missing encrypted storage from pastor-actionable setup: when `MARGE_ENCRYPTION_KEY` is present they prompt admins to add encrypted workspace credentials; when it is missing they keep the pastor at the encrypted-storage/server-config boundary.
- Frontend API-key setup requires and labels the public HTTPS base URL for Breeze and Rock before encrypted credential submission, matching backend validation.
- Proactive setup/action preparation evaluates user-scoped OAuth readiness for the current pastor/admin user, so another workspace user's verified credential is not inherited when preparing connector setup work.
- External writes require checked credentials, connector policy, per-action approval, and explicit execution.
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
.venv/bin/python scripts/smoke_pilot_readiness_gate.py
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
MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

That gate must fail unless production config is safe, the public app bootstrap path is reachable with strict workspace-token mode, migrations are current, the live evidence file was freshly generated for the current `MARGE_API_URL`, and at least one supported live external provider verifies. The evidence `generated_at` must be timezone-bearing, no older than 24 hours, and not future-dated beyond clock skew. MCP alone is not enough.

When the gate fails, it prints a `Next actions` section that names the exact production config, migration, operator token, live connector, or evidence-validation work still blocking a pilot. `scripts/verify_production_config.py` also prints `Next actions` and JSON `next_actions` for missing deployment settings. The no-network gate smoke covers that first-run writes and live connector checks require `MARGE_ACCOUNT_TOKEN`, that a failed live connector step keeps pilot readiness failed, that the gate clears stale evidence before running the live verifier, and that the generated JSON must include `required_live_provider=true`, fresh timezone-bearing `generated_at`, `workspace`, `external_provider_checks`, `live_provider_ready=true`, `no_sync_side_effect_check_passed=true`, complete passing `side_effect_check.collections`, and a verified supported external provider. Candidate production env checks do not let ambient shell values mask missing production-critical keys, `.env.production.example` is checked against the production verifier's required key list, a complete candidate env file still passes when all required values are present in the file, app/CORS origin mismatches fail, OAuth callback origin/path mismatches fail, and unsafe Breeze/Rock server-side base URLs fail both the verifier and runtime guard, including localhost/private-network hosts. Candidate env files may leave operator-only values such as `MARGE_ACCOUNT_TOKEN=` blank; the pilot gate preserves non-empty shell-provided `MARGE_ACCOUNT_TOKEN` and `MARGE_API_URL` values, while explicit `--token` and `--api-url` flags still win.

## Known Remaining Blockers

- Production `DATABASE_URL`, session-cookie, CORS, app URL, SMTP, account-token enforcement, and Fernet encryption settings must be supplied in the deployment environment.
- At least one real provider tenant, such as Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS, must be connected and verified with the no-sync credential check. The live verifier also checks that verification did not change synced context or assistant actions, and that the provider returned affirmative non-secret identity/config metadata rather than empty, false-only, zero-only, or negative numeric metadata.
- If the live-provider verifier fails, its human and JSON output now include `Next actions`/`next_actions` that tell the operator to connect a real external provider, run Check credentials, rerun the verifier with `--include-mcp --require-live-provider --evidence-file artifacts/live-connector-verification.json`, and avoid first sync until the check passes.
- The first real sync should happen only after the pastor/admin explicitly requests it after credential verification.

## Historical Context

The original April MVP implemented a morning briefing API, CRUD routes, draft templates, and a basic MCP server. The current repo has moved beyond that baseline. Treat `spec.md` as the long-term product vision, `codex-task.md` as a historical prompt, and `docs/THREAD_GOAL_AUDIT.md` as the current active-goal evidence map.
