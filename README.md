# Marge — AI Pastoral Secretary

> "Marge is the church secretary you can't afford — a warm, AI-powered assistant who briefs you every morning, drafts follow-up messages and ministry emails, helps with calendar follow-through, tracks care and prayer, and makes sure no one falls through the cracks."

## Quick Start

```bash
# From the repository root

# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env for local database, encryption, sessions, and optional connectors

# 3. Start the API server
uvicorn app.main:app --reload

# API docs available at:
#   http://localhost:8000/docs      (Swagger UI)
#   http://localhost:8000/redoc
# Product workspace:
#   http://localhost:8000/app
```

## Morning Briefing (Cron)

The product path is the account-scoped assistant workspace and `/briefing/today`.
For a cron-style text briefing, target a real workspace with
`MARGE_ACCOUNT_TOKEN`, `MARGE_ACCOUNT_ID`, or `MARGE_ACCOUNT_SLUG` so Marge does
not read legacy unscoped rows.

```bash
# Run manually
MARGE_ACCOUNT_TOKEN=marge_sess_... python3 scripts/morning_briefing.py

# Schedule for 7 AM daily (cron)
0 7 * * * cd /path/to/marge && MARGE_ACCOUNT_SLUG=your-church .venv/bin/python scripts/morning_briefing.py >> /var/log/marge_briefing.log 2>&1
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to receive briefings via Telegram.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Public app/bootstrap pointer with no workspace data |
| GET | `/health` | Health check |
| GET | `/briefing/today` | Today's full pastoral briefing |
| POST | `/visitors/` | Log a new visitor |
| GET | `/visitors/` | List visitors |
| GET | `/visitors/{id}/draft` | Get follow-up message draft |
| PATCH | `/visitors/{id}` | Mark follow-up sent, add notes |
| POST | `/members/` | Add a congregation member |
| GET | `/members/` | Search members |
| GET | `/members/{id}` | Member detail + notes |
| POST | `/members/{id}/notes` | Add pastoral note |
| GET | `/members/{id}/draft/care` | Draft a care message |
| POST | `/members/sync/rock` | Legacy Rock sync; delegates to verified assistant connector sync |
| POST | `/care/` | Open a care case |
| GET | `/care/` | List care cases |
| POST | `/care/{id}/resolve` | Resolve a care case |
| POST | `/care/{id}/contact` | Log a pastoral contact |
| POST | `/care/prayers/` | Submit a prayer request |
| GET | `/care/prayers/` | List prayer requests |
| PATCH | `/care/prayers/{id}` | Update prayer status |
| POST | `/drafts/` | Draft a pastoral message for care, visitors, prayer, birthdays, anniversaries, or absence |
| POST | `/chat/` | Legacy compatibility chat; delegates to account-scoped `/assistant/chat` |
| POST | `/assistant/signup` | Create a church workspace and account-scoped ministry profile |
| GET | `/assistant/account` | Get the current church workspace from `X-Marge-Account-Token` |
| POST | `/assistant/sessions` | Exchange a workspace user token for an expiring session token |
| POST | `/assistant/login-links/request` | Request a one-time passwordless sign-in link |
| POST | `/assistant/login-links/exchange` | Exchange a sign-in link for an expiring session token |
| GET | `/assistant/sessions/current` | Inspect the current token/session |
| DELETE | `/assistant/sessions/current` | Revoke the current session token |
| GET | `/assistant/users` | List role-scoped workspace users; owner/admin only |
| POST | `/assistant/users/invite` | Create a one-time role-scoped user token; owner/admin only |
| PATCH | `/assistant/users/{id}` | Update/deactivate a workspace user token; owner/admin only |
| GET | `/assistant/profile` | Get pastor/church onboarding profile |
| PATCH | `/assistant/profile` | Save ministry context Marge should use |
| GET | `/assistant/desk` | Get connected assistant desk from current data |
| GET | `/assistant/chat/history` | List recent persisted assistant conversation turns |
| DELETE | `/assistant/chat/history` | Clear persisted assistant conversation turns without deleting saved ministry records |
| POST | `/assistant/chat` | Chat with Marge using the profile and current desk context |
| GET | `/assistant/actions` | List Marge's prepared approval queue |
| POST | `/assistant/actions` | Create an assistant action for pastor review |
| POST | `/assistant/actions/prepare` | Queue today's proactive drafts, calendar blocks, and follow-ups |
| POST | `/assistant/actions/{id}/approve` | Approve an assistant action |
| POST | `/assistant/actions/{id}/execute` | Mark an approved action done |
| POST | `/assistant/actions/{id}/skip` | Skip an assistant action |
| GET | `/assistant/policies` | List connector read/write policies |
| PATCH | `/assistant/policies/{provider}` | Enable or disable connector writeback policy |
| GET | `/assistant/integrations` | List secure connector statuses |
| POST | `/assistant/integrations/{provider}/start` | Start secure OAuth/server-side connector setup |
| POST | `/assistant/integrations/{provider}/credentials` | Store encrypted workspace API-key credentials for API-key connectors |
| GET | `/assistant/integrations/{provider}/callback` | Complete OAuth callback and store provider tokens encrypted server-side |
| POST | `/assistant/integrations/{provider}/verify` | Verify connector credentials without syncing ministry data |
| POST | `/assistant/integrations/{provider}/sync` | Sync external provider context into Marge |
| DELETE | `/assistant/integrations/{provider}` | Disconnect the current user's OAuth credential and disable writeback if no credentials remain |
| GET | `/assistant/connected-items` | List normalized external connector context |
| GET | `/assistant/audit-log` | List security, connector, approval, and action audit events |

## Rock RMS Integration

Marge can sync members and attendance from Rock RMS automatically.

1. Add a Rock API key and HTTPS API base URL through secure connector setup, or set `ROCK_API_KEY` and `ROCK_BASE_URL` in `.env`
2. Call `POST /assistant/integrations/rock/sync` to trigger a sync; `POST /members/sync/rock` remains as a legacy compatibility route and uses the same verify-before-sync boundary
3. The app works fully standalone without Rock — the API key is optional

The assistant integration sync path also writes a compact Rock sync summary to `/assistant/connected-items` and queues absence follow-up actions when synced attendance shows people who need pastoral attention.

## Secure Connector Setup

OAuth connectors use server-side provider config and encrypted token storage. API-key connectors can use encrypted workspace credentials or server-side environment config.

1. Generate `MARGE_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Add provider client ID, client secret, and redirect URI to `.env`
3. Call `POST /assistant/integrations/{provider}/start`
4. Open the returned authorization URL
5. The provider redirects to `/assistant/integrations/{provider}/callback`, where Marge exchanges the code and stores the token payload encrypted

For API-key connectors such as Breeze or Rock, owner/admin users can submit the key and full public HTTPS base URL to `POST /assistant/integrations/{provider}/credentials`. Base URLs may include a normal path such as `/api/v2`, but not embedded usernames, passwords, query strings, fragments, localhost, or private-network hosts. Marge stores the key encrypted as a workspace credential, never returns it in status/audit/chat responses, and still requires a safe credential check before sync.

Once Google Workspace is connected, Marge can sync recent Gmail and Calendar context into `/assistant/connected-items`, prepare pastor-review actions from that context, and create Gmail drafts or Google Calendar events for approved actions when their payload includes complete `email` or `calendar_event` data. Prepared desk suggestions remain local approval items until they contain concrete send/create details.

Planning Center OAuth can sync read-side People and Calendar context into `/assistant/connected-items`. Marge uses that context to queue review items for new/visitor-like people and meeting prep for upcoming events. Planning Center remains read-only in this MVP; Marge does not write back to Planning Center.

Microsoft 365 OAuth can sync read-side Outlook mail and calendar context into `/assistant/connected-items`. Marge uses that context to queue inbox review and meeting-prep actions. Approved `email_draft` actions may create Outlook drafts, and approved `calendar_block` actions may create Outlook calendar events, only after Microsoft writeback policy and per-action approval. Marge still does not send Outlook mail.

Rock RMS uses the same assistant connector surface for read-side sync when a workspace API key and base URL are saved or `ROCK_API_KEY` plus `ROCK_BASE_URL` are configured server-side. Rock sync imports people and attendance, stores only a compact sync summary as connected context, and prepares pastor-review follow-up from absence signals. It does not write back to Rock.

Breeze uses encrypted workspace credentials or `BREEZE_API_KEY` and `BREEZE_BASE_URL` server-side for read-side people and event sync. Marge stores compact Breeze context, queues review for new/visitor-like people, and prepares meeting-prep actions for pastoral events. It does not write back to Breeze.

Approved `person_review` actions from Planning Center or Breeze can be executed to create or merge a local Marge person record, with a connector-import note and audit row. This is a local Marge memory write only; it does not write back to the source system.

Approved `email_triage` actions from synced inbox context can be executed to prepare a reviewable reply draft action. Google-sourced drafts may become Gmail draft writes only after Google writeback policy and per-action approval; Microsoft-sourced drafts may become Outlook draft writes after Microsoft writeback policy and per-action approval. Other read-only providers stay as local Marge drafts.

Generated reply draft bodies should contain only text that is safe for the recipient to see. Review metadata such as drafting voice, source message id, ministry context, and guardrails belongs in the action payload, not inside the sendable email body.

Connector setup, OAuth completion, sync, approvals, skips, and executions are written to `/assistant/audit-log` with metadata only. Do not store raw tokens, API keys, prayer text, or full provider payloads in audit events.

## First-Run Smoke Test

With the FastAPI server running locally, run:

```bash
.venv/bin/python scripts/smoke_first_run.py
```

The script creates a temporary church workspace, teaches Marge ministry context through `/assistant/chat`, verifies that chat history persists, verifies that church voice, first ministry priority, personal support style, weekly rhythm, and explicit guardrails are learned before setup work is queued, confirms `auto` mode stays live instead of showing demo people, checks that visitor-focused first-record setup and secure connector setup steps are queued, and removes the temporary local rows afterward.

For secure connector plumbing that does not require live Google credentials, run:

```bash
.venv/bin/python scripts/smoke_integrations.py
```

That script uses a mocked Google OAuth token exchange to verify account-scoped and user-scoped OAuth state, encrypted token storage, one-time state consumption, cross-account isolation, role-scoped user tokens, optional invite delivery, passwordless login links, revocable session tokens, HttpOnly session-cookie transport, revoked-token rejection, token redaction from API/audit responses, safe credential verification without sync, writeback disabled by default, and approved Google draft writeback after explicit policy.

For the other supported connected-tool paths, run:

```bash
.venv/bin/python scripts/smoke_connected_providers.py
```

That script mocks Planning Center, Microsoft 365, and Breeze provider responses while still exercising Marge's OAuth/API-key setup, safe verification, sync, connected-context storage, review-action queueing, generic sync provider selection, person-review execution into local memory, inbox-triage execution into a reviewable draft, approved Outlook draft writeback, and approved Outlook calendar writeback. It is the local coverage gate that proves those connectors are not only UI placeholders.

For external LLM/MCP clients that should drive the same first-run workflow, run:

```bash
.venv/bin/python scripts/smoke_mcp_first_run.py
```

That script creates a temporary workspace, sends onboarding context through the MCP `tell_marge` tool, verifies that MCP output preserves saved/intent/profile metadata plus action cards, and confirms the MCP desk exposes concrete first-record and connector setup steps.

For the combined pilot readiness gate planner, run:

```bash
.venv/bin/python scripts/smoke_pilot_readiness_gate.py
```

That smoke does not contact the network. It proves the combined gate refuses first-run writes and live connector checks until `MARGE_ACCOUNT_TOKEN` is present, and that non-local tokens must resolve to owner/admin/pastor access.

For the static workspace shell, run:

```bash
.venv/bin/python scripts/smoke_frontend_static.py
```

That script checks the inline frontend JavaScript parses, verifies form failures surface backend-safe detail messages, and keeps the no-sync credential-check and live-draft guardrails visible in the UI.

After sync, chat can answer prompts such as "Show Planning Center context" or "Review synced people" from `/assistant/connected-items` and return action cards that open the queued review item directly.

When a synced person has a queued review item, an explicit chat request such as "Add Nina Brooks to Marge" can approve and execute that local import into Marge's people memory. This still does not write back to Planning Center, Breeze, or another source system.

Chat prompts such as "Queue replies for these" or "Draft replies from synced inbox" create reviewable `email_draft` actions from synced inbox context. They stay in Marge's approval queue and are not sent automatically.

Approved Google Workspace and Microsoft 365 draft writebacks create provider drafts only. Marge does not send mail from these paths.

Generic mailbox prompts such as "Sync the inbox" or "Sync the mailbox again" choose the connected mail provider for that workspace instead of assuming Google.

Generic calendar prompts such as "Sync the calendar" or "Refresh the schedule" choose the connected calendar provider from the workspace and saved tools instead of assuming Google.

## Church Workspaces

`POST /assistant/signup` requires a church name and valid owner email, creates a lightweight church workspace, and returns a one-time owner user token. Clients should exchange that token for a shorter-lived `marge_sess_...` token with `POST /assistant/sessions`. The static frontend performs that exchange immediately, avoids persisting the raw token in `localStorage`, and relies on the HttpOnly `marge_session` cookie by default. API/MCP clients may send a session token as `X-Marge-Account-Token`. The pastor profile, members, visitors, care cases, prayer requests, briefing data, assistant chat history, assistant actions, connected items, audit rows, integration status, OAuth state, encrypted credentials, sessions, and writeback policies are scoped to that church. Requests without a token use only legacy unscoped local rows; invalid tokens return `401` instead of falling back to shared data.

Workspace owners/admins can create additional role-scoped user tokens with `POST /assistant/users/invite` and deactivate them with `PATCH /assistant/users/{id}`. User roles are `owner`, `admin`, `pastor`, `staff`, and `viewer`. Owner/admin tokens are required for connector setup and writeback policy changes; pastor/admin/owner tokens are required for ministry profile edits, approval queue, execution, audit log, connected-context, sync, pastoral briefing, pastoral drafts, member detail/notes, care cases, prayer requests, and member/visitor write surfaces. Staff tokens can read the live desk plus basic member/visitor directories, but the staff desk hides approval-queue items and staff cannot mutate pastoral records. When SMTP is configured, Marge emails an invite link with the one-time token; otherwise the response still returns the token for trusted manual sharing. The frontend accepts `?invite_token=...`, exchanges it for an HttpOnly session cookie, and removes it from the address bar. Existing users can request a one-time passwordless link from the first-run screen or with `POST /assistant/login-links/request`; `POST /assistant/login-links/exchange` turns that short-lived single-use token into the same revocable session cookie. Legacy account tokens still resolve as owner for existing local workspaces, but new browser sessions should use user tokens or passwordless sessions. Marge blocks deactivating the final active workspace owner.

OAuth credentials are scoped to the workspace user who starts connector setup. A second pastor/admin user in the same church can see that a connector exists, but must authorize their own Google/Planning Center/Microsoft credential before syncing or writing through that provider. Legacy account-level credentials still work for old local workspaces, but new OAuth callbacks should store `IntegrationCredential.user_id`.

Use `POST /assistant/integrations/{provider}/verify` after OAuth callback or server-side API-key setup to prove credentials work before syncing live ministry data. Verification performs a minimal provider call, records an audit event and `verified_at`, and should not import people, messages, calendar events, or queue actions. Sync endpoints reject connectors that are connected/configured but not verified.

Use `DELETE /assistant/integrations/{provider}` to disconnect the current user's OAuth credential or remove a stored workspace API-key credential. Marge deletes the encrypted payload, writes a metadata-only audit event, and disables connector writeback if no OAuth credentials remain for that provider. If an API-key connector is configured only through environment variables, remove the server-side secret to disconnect it.

The `/app` Integrations screen exposes this as **Check credentials** and **Disconnect** on connector cards. Use credential checks after setup and before the first sync so the pastor can see that Marge is connected without pulling live ministry data yet. The browser only offers **Sync** after the checked credential status comes back.

Assistant chat and MCP clients can run the same safe check. A prompt such as "Check Planning Center credentials before syncing" verifies the connector, records the checked status, and explicitly avoids importing ministry context or queuing actions.

If a pastor asks chat to sync a connected provider before it has been checked, Marge verifies credentials first and stops there. The MCP `sync_integration` tool follows the same boundary: it runs the no-sync credential check, queues nothing, and asks for an explicit follow-up sync after verification.

To check a live workspace from the command line, set `MARGE_API_URL` and `MARGE_ACCOUNT_TOKEN`, then run:

```bash
.venv/bin/python scripts/verify_live_integrations.py
```

By default the script verifies only connectors that already look connected/configured for that workspace. Use explicit provider keys plus `--include-not-ready` when diagnosing setup, for example `.venv/bin/python scripts/verify_live_integrations.py google_workspace planning_center --include-not-ready`. This is a credential health check only; it does not sync ministry data, checks every page of connected context, assistant actions, members, visitors, care cases, and prayer requests, including private prayer requests, to prove verification leaves them unchanged, and refuses to count a provider if verification exposes token/API-key-shaped identity metadata or lacks affirmative non-secret identity/config metadata. Empty, false-only, zero-only, or negative numeric metadata does not count. `--require-live-provider` requires `MARGE_ACCOUNT_TOKEN` even against localhost, and non-local API checks always require it, so provider verification cannot run against an unscoped deployment. If the target API is unreachable, the script exits non-zero with a concise operator error instead of a traceback. When no external provider qualifies, human output includes **Next actions** and JSON output includes `next_actions` telling the operator to connect/configure a real provider, run Check credentials, rerun the verifier, and avoid first sync until it passes.

For production handoff, require at least one real church-tool connector to verify successfully:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
.venv/bin/python scripts/verify_live_integrations.py \
  --include-mcp \
  --require-live-provider \
  --evidence-file artifacts/live-connector-verification.json
```

`--include-mcp` can confirm the local/LLM bridge, but MCP alone does not prove Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS are connected.
For readiness decisions, use the typed evidence fields: `required_live_provider` must be `true`, `generated_at` must be a valid timezone-bearing timestamp from the fresh verifier run and no older than 24 hours at gate time, `workspace` must identify the expected account with an owner/admin/pastor role, `external_provider_checks` must include at least one verified supported church-tool provider, `local_bridge_checks` may include MCP but cannot satisfy readiness, both `live_provider_ready` and `no_sync_side_effect_check_passed` must be `true`, and `side_effect_check.collections` must include passing connected-context, assistant-action, member, visitor, care, and prayer checks. The legacy `verified` array is for backward-compatible readers only.

For pilot handoff, use the combined gate so production config, public bootstrap, migrations, the disposable first-run workspace journey, and live no-sync connector verification are checked together:

```bash
MARGE_API_URL=https://marge.yourchurch.org MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION .venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

The combined gate also checks the public deployment bootstrap path. To run that no-write check by itself:

```bash
MARGE_API_URL=https://marge.yourchurch.org MARGE_APP_URL=https://marge.yourchurch.org/app .venv/bin/python scripts/verify_deployment_bootstrap.py
```

That check verifies `/`, `/health`, `/app`, and `/assistant/config`. The public root and config responses must stay limited to first-run bootstrap fields, so they cannot accidentally expose workspace, connector, credential, or pastor data before signup.

For local development, add `--allow-relaxed-account-tokens` because local routes usually allow legacy unscoped rows.

To rehearse the first pastor journey against a target API, run:

```bash
MARGE_API_URL=https://marge.yourchurch.org .venv/bin/python scripts/verify_first_run_workspace.py --allow-remote-write
```

That script creates a disposable workspace, completes onboarding through chat, verifies the proactive first-record setup, logs a visitor through chat, queues a welcome draft, and confirms visitor follow-up lookup recalls the saved visitor. Local runs clean up automatically; remote verification workspaces should be removed manually if you do not want to keep them.

The combined pilot gate runs that same first-run workspace rehearsal by default. It requires `MARGE_ACCOUNT_TOKEN` before first-run writes or live connector checks; for non-local API URLs it preflights the token against `/assistant/account`, so missing, invalid, or underprivileged operator tokens fail without creating disposable remote data. Use `--skip-first-run-workspace` only for targeted troubleshooting when you need a no-write gate run. Its summary **Next actions** include copy-pasteable rerun commands for production config and live-provider failures.
Before its live-connector step, the combined gate removes any previous evidence file at the selected path, runs `verify_live_integrations.py --include-mcp --require-live-provider --evidence-file ...`, and validates that the fresh JSON matches the current `MARGE_API_URL`, has workspace scope, proves no-sync side-effect safety for each required workspace collection, and includes a verified supported external provider. Stale evidence, MCP-only evidence, unsupported providers, staff/viewer tokens, missing side-effect collection details, and legacy `verified` entries cannot pass the gate.

If a candidate env file leaves operator-only values blank, such as `MARGE_ACCOUNT_TOKEN=`, the combined gate preserves non-empty `MARGE_ACCOUNT_TOKEN` and `MARGE_API_URL` values already supplied by the shell. Explicit `--token` and `--api-url` flags still take precedence.

Before exposing a deployment to real pastoral data, run:

```bash
.venv/bin/python scripts/verify_production_config.py
```

That readiness check fails when production-critical settings are unsafe: missing production runtime enforcement, missing account-token enforcement, invalid connector credential encryption key, insecure session cookies, wildcard/non-HTTPS CORS, CORS that omits the app origin, SQLite production database, startup schema creation, non-HTTPS app URL, missing invite/login email delivery, partial OAuth provider config, OAuth redirect URIs on the wrong origin or callback path, or unsafe Breeze/Rock server-side connector base URLs. It warns, but does not fail, when no live connector server config is present yet. When `--env-file` is used, checked production keys are read from that candidate file rather than being silently filled by the shell environment. Failure output includes **Next actions** plus JSON `next_actions` so operators know which deployment setting to fix next.

For a deployment handoff, start from `.env.production.example`, then use:

```bash
.venv/bin/python scripts/verify_production_config.py --env-file .env.production.candidate
```

See `docs/PRODUCTION_READINESS.md` for the exact go-live checklist, including the safe live-connector verification flow that checks credentials before syncing ministry data.

## Database Migrations

Marge uses Alembic for trackable schema changes:

```bash
alembic upgrade head
.venv/bin/python scripts/verify_migrations.py
```

Local development still auto-creates missing tables by default so `uvicorn app.main:app --reload` works on a fresh SQLite database. For production, run migrations during deploy, set `MARGE_ENV=production`, and set `MARGE_AUTO_CREATE_SCHEMA=false` so startup fails closed instead of masking missing migration work. Existing databases created before Alembic should be inspected/backed up and then stamped to the baseline with `alembic stamp head` before future migrations are applied.

Set `MARGE_REQUIRE_ACCOUNT_TOKEN=true` before exposing Marge beyond local demo use. In that mode, scoped API routes reject missing `X-Marge-Account-Token` requests with `401` instead of reading or writing legacy unscoped rows. `/assistant/signup` remains open so a pastor can create the first workspace, and OAuth callbacks still complete through their server-side state rows.

`GET /assistant/config` is intentionally public and returns whether account tokens are required. The static frontend uses it to show the first-run workspace creation screen before making protected data requests.

Before a workspace exists, the frontend answers the public first-run explainer prompts locally. Questions such as "How do secure connections work?" should not call protected chat routes until a church workspace token exists.

The MVP stores only token hashes server-side. Session tokens are revocable, expiring, and available through an HttpOnly same-origin cookie. Optional SMTP invite delivery and passwordless sign-in links avoid asking pastors or admins to copy long-lived tokens manually. Passwordless login requests are accepted with a generic response and duplicate sends are throttled for a short cooldown so the endpoint does not reveal account existence or flood inboxes. Production auth still needs OIDC identity and hardened HTTPS cookie configuration.

Account-scoped requests in `auto` mode should stay live even when the account has no people data yet. Demo stories are only for unauthenticated empty-state previews or explicit `mode=demo`; do not queue or display fake people inside a real church workspace.

External person IDs are scoped to the church workspace. For example, two churches can both have a Rock RMS person `Id` of `123`, but Marge treats those as separate people and rejects only duplicate Rock IDs inside the same workspace.

When a real workspace has a complete ministry profile but no members, visitors, care cases, or prayer requests yet, Marge should proactively ask the pastor to add the first real ministry record tied to the follow-up burden they named. That setup action is intentionally separate from demo data and connector setup, and should route to the most relevant first form when possible, such as visitor follow-up for new-family/guest pain or prayer intake for prayer follow-up pain.

OAuth connection does not enable external writes by itself. Each connector also has a church-level writeback policy. Google Workspace writes are disabled by default and require both:

- `write_enabled: true` on `/assistant/policies/google_workspace`
- an approved assistant action

Assistant chat can now act on synced context:

- "What is in my inbox?" summarizes synced Gmail context.
- "Draft a reply to the hospital visit email" creates a reviewable Gmail draft action from the synced email.
- "What meetings are coming up?" summarizes synced Calendar events.
- "Prepare my next meeting" creates a meeting-prep action from the synced calendar item. Google Calendar sync should also include a reviewable prep brief, not just a raw event reference.
- "Sync Planning Center" pulls People and Calendar context into Marge's review queue when OAuth is connected.
- "Sync Outlook" pulls Microsoft 365 mail and calendar context into Marge's review queue when OAuth is connected.
- "Sync Breeze" pulls Breeze people and event context into Marge's review queue when encrypted workspace credentials or server-side config are available.
- "Sync the calendar" selects the connected calendar-capable provider that best matches the saved ministry tools, such as Planning Center for churches using Planning Center.
- "Sync Rock RMS" imports people and attendance when encrypted workspace credentials or a server-side Rock key are available.

Assistant chat can also save local pastoral context from natural language:

- Ministry profile updates such as role, weekly attendance, church voice/tradition, first ministry priority, tools in use, follow-up burden, drafting voice, and guardrails.
- Visitor notes such as "New visitor Sarah Kim came Sunday..." and immediately queues a reviewable welcome draft.
- Private prayer requests.
- Care cases for hospital, surgery, grief, crisis, and similar needs.
- Contact logs and member notes when the person can be matched to an account-scoped member.
- Review-queue actions when Marge understands the update but cannot safely match the person.

Assistant chat history is persisted account-side through `GET /assistant/chat/history`, so the first-run conversation survives reloads and MCP/LLM clients can see what the pastor has already taught Marge without asking him to repeat it. `DELETE /assistant/chat/history` clears the transcript only; saved profile context, people, care, prayer, and approval records remain.

When Marge saves first-run ministry context, she should reflect back the specific church, role, church voice/tradition, first ministry priority, follow-up burden, personal support style, tools, voice, and guardrails she understood, then name how she will use that memory for secure setup, drafting, prioritization, proactive nudges, and approvals. This is intentional product behavior: first-run chat should not feel like a generic form save.

First-run setup prompts in chat should be concrete. For example, a visitor-related `data_seed` step should prompt and answer around logging the first real visitor, not "help me with setup."

`POST /drafts/` also honors the workspace token, so generated visitor, care, prayer, birthday, anniversary, and absence drafts can only use records visible to the current church workspace.

The assistant desk includes `setup_steps`, `interview_question`, and `operating_plan`. Together they form the first-run onboarding path: Marge asks the next missing ministry-context question, recommends connecting tools the pastor said the church already uses, and turns saved context into a practical plan for church voice, first priorities, follow-up, secure connectors, calendar protection, drafting voice, and approval guardrails.

Interview questions should become more specific as Marge learns context. For example, after the pastor names visitor follow-up as the burden, the next tools question should ask what systems the church uses for that follow-up rather than asking a generic tools question.

Integration setup steps include the provider key when Marge knows which tool to connect, so the frontend can start `/assistant/integrations/{provider}/start` directly instead of sending the pastor through a generic settings detour. Setup actions in the approval modal should also expose the concrete next action, such as opening the first visitor/prayer/person form or starting secure connector setup, instead of only showing generic approve/done controls.

`POST /assistant/actions/prepare` also uses those setup steps. For a new account with a complete ministry profile, Marge queues connector setup actions plus a first-week launch plan. For an incomplete profile, she queues the next profile question instead of pretending she knows enough.

The first-week launch plan should preserve the pastor's actual context: the first real person, visitor, or prayer record, each recommended connector, weekly rhythm, drafting voice, and explicit approval guardrail. Avoid generic launch-plan tasks when that context is available.

MCP clients should set `MARGE_ACCOUNT_TOKEN` alongside `MARGE_API_URL`. Use an owner/pastor session token for assistant work when available; a user token also works for local bootstrap. The MCP server sends that token as `X-Marge-Account-Token` on every tool call so Claude, ChatGPT Desktop, or another MCP client only sees the pastor's current church workspace and role-allowed surfaces.

Supported OAuth provider keys:

- `planning_center`
- `google_workspace`
- `microsoft_365`

API-key providers such as Rock RMS and Breeze can be configured through encrypted workspace credentials or server-side environment variables. Do not paste API keys into chat.

## Product Direction

Marge is not trying to become church management software. The long-term direction is an AI personnel hire for churches: a chat-first pastoral secretary that reads from tools like Rock RMS, Planning Center, Breeze, Gmail, Outlook, and calendars, then drafts, queues, reminds, and writes back only after approval.

See:

- `docs/PASTORAL_SECRETARY_UX.md`
- `docs/AI_AGENT_ROADMAP.md`

## Environment Variables

See `.env.example` for all available configuration options. The template also documents the local connector boundary: env placeholders alone do not prove live tools are connected, MCP/local API access is not a live provider, and Check credentials must run before the first sync.

Key variables:
- `PASTOR_NAME` — Legacy fallback pastor name when no workspace profile is selected
- `CHURCH_NAME` — Legacy fallback church name when no workspace profile is selected
- `MARGE_ACCOUNT_TOKEN` / `MARGE_ACCOUNT_ID` / `MARGE_ACCOUNT_SLUG` — Optional workspace selector for CLI briefing scripts
- `DATABASE_URL` — SQLite for dev, Postgres for production
- `MARGE_ENV` — Set `production` in deployments so startup safety checks fail closed
- `MARGE_AUTO_CREATE_SCHEMA` — Keep true for local dev; set false in production after running Alembic migrations
- `MARGE_ENCRYPTION_KEY` — Fernet key required before OAuth tokens or workspace API-key connector credentials can be stored
- `MARGE_REQUIRE_ACCOUNT_TOKEN` — Set true before non-local exposure so scoped routes reject missing workspace tokens
- `MARGE_SESSION_COOKIE_NAME` / `MARGE_SESSION_COOKIE_SECURE` / `MARGE_SESSION_COOKIE_SAMESITE` — HttpOnly browser session cookie settings for `POST /assistant/sessions`
- `MARGE_APP_URL` — Exact public HTTPS `/app` URL used in invite/passwordless links
- `MARGE_INVITE_EMAIL_FROM` / `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_STARTTLS` — Optional workspace invite email delivery
- `CORS_ORIGINS` — Exact HTTPS frontend origin(s) with no path or wildcard; include the origin from `MARGE_APP_URL`
- `ROCK_API_KEY` / `ROCK_BASE_URL` — Optional server-wide Rock RMS API key and public HTTPS API v2 base URL with no username/password/query/fragment or localhost/private host; workspace API-key setup is preferred for multi-church use
- `PLANNING_CENTER_CLIENT_ID` / `PLANNING_CENTER_CLIENT_SECRET` / `PLANNING_CENTER_REDIRECT_URI` — Planning Center OAuth; redirect URI must use the `MARGE_APP_URL` origin and `/assistant/integrations/planning_center/callback`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` — Google Workspace OAuth; redirect URI must use the `MARGE_APP_URL` origin and `/assistant/integrations/google_workspace/callback`
- `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_REDIRECT_URI` — Microsoft 365 OAuth; redirect URI must use the `MARGE_APP_URL` origin and `/assistant/integrations/microsoft_365/callback`
- `BREEZE_API_KEY` / `BREEZE_BASE_URL` — Optional server-wide Breeze API key and public HTTPS account URL with no username/password/query/fragment or localhost/private host; workspace API-key setup is preferred for multi-church use
- `MARGE_API_URL` / `MARGE_ACCOUNT_TOKEN` — API origin and workspace token for MCP clients and connector verification; `MARGE_ACCOUNT_TOKEN` is required for `--require-live-provider` and non-local live verification
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — Optional Telegram delivery

## Project Structure

```
marge/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── database.py          # SQLAlchemy setup
│   ├── models.py            # ORM models, church accounts, profile, integrations, action queue, connected context cache
│   ├── marge_voice.py       # Tone constants + message templates
│   ├── routers/
│   │   ├── briefing.py      # GET /briefing/today
│   │   ├── visitors.py      # Visitor CRUD + follow-up
│   │   ├── members.py       # Member CRM + notes
│   │   ├── care.py          # Care cases + prayer requests
│   │   ├── drafts.py        # Unified pastoral message drafts
│   │   ├── assistant.py     # Connected assistant desk, profile, and integrations
│   │   └── chat.py          # Tell Marge endpoint
│   ├── services/
│   │   └── marge.py         # Marge brain: briefing, nudges, drafts
│   └── integrations/
│       └── rock.py          # Rock RMS sync layer
├── scripts/
│   ├── morning_briefing.py  # Standalone cron script
│   ├── smoke_mcp_first_run.py # MCP first-run workflow smoke
│   └── smoke_pilot_readiness_gate.py # No-network pilot gate planner smoke
├── frontend/
│   └── index.html           # Product workspace mounted at /app
├── docs/
│   ├── LAUNCH_PLAN.md
│   ├── PASTOR_RESEARCH.md
│   ├── AI_AGENT_ROADMAP.md
│   ├── PRODUCTION_READINESS.md
│   └── PASTORAL_SECRETARY_UX.md
├── migrations/              # Alembic migrations for production schema changes
├── requirements.txt
├── .env.example
├── .env.production.example
└── README.md
```

## Marge's Voice

Marge is the beloved church secretary who's been at the church 30 years. She's warm, reliable, and never sounds like a database.

**She never says:**
- "Member milestone event detected"
- "Follow-up touchpoint scheduled"
- "I am an AI"

**She always says:**
- "Tom's birthday is Thursday — he'd love a call"
- "Janet could really use a call this week"
- "Good morning, Pastor. Here is the ministry context I can see today."

---
