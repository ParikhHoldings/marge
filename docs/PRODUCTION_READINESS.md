# Production Readiness

This is the handoff checklist for the remaining gap between the local Marge MVP and a real pastor using live tools. The local app now has secure connector plumbing, account-scoped chat, first-run onboarding, approval queues, and smoke coverage. A deployment is not ready for real pastoral data until this checklist passes against the actual production environment.

## Completion Gate

Do not treat Marge as production-ready until all of these are true:

- `scripts/verify_production_config.py` exits `0` against the deployment environment.
- `scripts/verify_deployment_bootstrap.py` exits `0` against the public deployment, proving `/health`, `/assistant/config`, and `/app` are reachable before signup.
- `alembic upgrade head` has run against the production database and `MARGE_AUTO_CREATE_SCHEMA=false`.
- `scripts/verify_first_run_workspace.py --allow-remote-write` exits `0`, proving a disposable pastor workspace can sign up, exchange the owner token for a session, complete onboarding through chat, get proactive first-record/setup steps, log a visitor through chat, and queue a reviewable welcome draft.
- At least one live connector used by the pilot church is configured, checked with `POST /assistant/integrations/{provider}/verify`, and verified by `scripts/verify_live_integrations.py` without syncing ministry data or changing connected context/actions.
- The first live sync is intentionally run by a pastor/admin after credential verification, not during OAuth callback.
- External writes are still blocked until the church enables writeback policy and approves the exact action.

## Prompt-To-Artifact Checklist

| Objective requirement | Concrete artifact | Verification |
| --- | --- | --- |
| Save the research | `docs/PASTOR_RESEARCH.md`, `docs/PASTORAL_SECRETARY_UX.md`, `docs/AI_AGENT_ROADMAP.md`, `AGENTS.md` | Read the files and confirm research links and product posture are present. |
| Stop being only placeholder/demo | Account workspaces, live mode gating, first-record setup, connector statuses, `ConnectedContextItem`, approval queue | `scripts/smoke_first_run.py`, `scripts/smoke_connected_providers.py` |
| Chat really works | `POST /assistant/chat`, persisted chat history, deterministic local writes, synced-context lookup, action approval/execution commands | `scripts/smoke_first_run.py`, `scripts/smoke_integrations.py`, `scripts/smoke_connected_providers.py` |
| Connect tools securely | OAuth/API-key setup endpoints, encrypted `IntegrationCredential`, safe verify endpoints, disconnect, metadata-only audit | `scripts/smoke_integrations.py`, `scripts/smoke_connected_providers.py`, `scripts/verify_live_integrations.py` |
| Ask ministry-context questions | First-run profile fields, `interview_question`, onboarding chat saves, tailored operating plan | `scripts/smoke_first_run.py` |
| Make first signup feel personal | `/app` first-run assistant, setup steps from saved pain/tools/rhythm/guardrails, "first real visitor/person" prompt | Browser check plus `scripts/smoke_first_run.py` |
| Be proactive | `GET /assistant/desk`, `POST /assistant/actions/prepare`, visitor welcome draft queue, meeting prep, inbox triage | Smoke tests and manual `/app` Plan/Today checks |
| Safe production exposure | Production env template, config verifier, migrations, HTTPS cookies, SMTP login links | `scripts/verify_production_config.py --env-file <candidate>` |
| Public app bootstrap | Public API health, public assistant config, `/app` static shell, strict runtime token mode | `scripts/verify_deployment_bootstrap.py` |
| First-run workspace journey | Signup, session exchange, onboarding chat saves, proactive first-record setup, visitor chat logging, welcome draft queue, visitor recall | `scripts/verify_first_run_workspace.py --allow-remote-write` |

Passing local smoke tests is necessary but not sufficient. The live credential checks and production config gate cover the parts that cannot be proven with mocked providers.

## Production Environment

Start from `.env.production.example` and move the values into the deployment platform's secret manager. Do not commit a filled production env file.

Required production settings:

- `MARGE_ENV=production`: enables startup safety checks so an unsafe deployment fails closed.
- `DATABASE_URL`: managed database URL, not SQLite.
- `MARGE_AUTO_CREATE_SCHEMA=false`: run Alembic migrations during deploy instead.
- `MARGE_REQUIRE_ACCOUNT_TOKEN=true`: prevents missing-token requests from reading shared legacy rows.
- `MARGE_ENCRYPTION_KEY`: stable Fernet key for OAuth/API-key ciphertext.
- `MARGE_SESSION_COOKIE_SECURE=true`: required behind HTTPS.
- `MARGE_APP_URL`: exact public `https://.../app` URL for invite/passwordless links.
- `CORS_ORIGINS`: exact HTTPS frontend origin list with no path, query, or wildcard.
- SMTP settings: required so invite/passwordless auth works for real churches.
- Provider OAuth credentials: Planning Center, Google Workspace, and/or Microsoft 365 as needed by the pilot church. If any OAuth provider is configured, client id, client secret, and HTTPS redirect URI must all be present and non-placeholder.
- Server-side Breeze and Rock settings are optional, but partial or placeholder values fail the production guard. Prefer encrypted workspace credentials for those API-key connectors when serving more than one church.

Run:

```bash
.venv/bin/python scripts/verify_production_config.py --env-file .env.production.candidate
```

The command must exit `0` before real pastoral data is exposed.

## First Deployment Flow

1. Provision the database and apply migrations:

```bash
alembic upgrade head
.venv/bin/python scripts/verify_migrations.py
```

2. Start the app with production secrets and confirm:

```bash
curl -sf https://marge.yourchurch.org/health
.venv/bin/python scripts/verify_production_config.py
MARGE_API_URL=https://marge.yourchurch.org MARGE_APP_URL=https://marge.yourchurch.org/app .venv/bin/python scripts/verify_deployment_bootstrap.py
```

3. Create the first pastor workspace through `/app`, then verify session behavior:

- Signup returns a one-time owner user token.
- The frontend exchanges it for an HttpOnly `marge_session` cookie.
- The address bar does not retain invite or login tokens.
- Reloading `/app` stays in the same workspace without `localStorage` account tokens.

Run the API-level first-run rehearsal as well:

```bash
MARGE_API_URL=https://marge.yourchurch.org .venv/bin/python scripts/verify_first_run_workspace.py --allow-remote-write
```

This creates a disposable workspace on the target deployment. Remove that workspace manually if you do not want to keep verification data.

4. Connect a provider from the Integrations screen.

5. Click **Check credentials** before syncing. This calls `/assistant/integrations/{provider}/verify`, records `verified_at`, and must not import people, email, or events. Sync endpoints should reject connectors that are only connected/configured but not verified.

6. Run the operator verifier:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=marge_sess_REPLACE_WITH_OPERATOR_SESSION \
.venv/bin/python scripts/verify_live_integrations.py --include-mcp --require-live-provider
```

The verifier snapshots `/assistant/connected-items` and `/assistant/actions` before and after credential checks. It should report an OK no-sync side-effect check; any connected context or assistant action change means verification is importing or queueing work and must be fixed before pilot use.

7. After verification, let the pastor/admin explicitly run the first sync.

8. Leave writeback disabled until the pastor has reviewed the approval queue. Enable only the action classes the church wants, such as `email_draft` or `calendar_block`.

## Live Connector Notes

- Google Workspace: read Gmail and Calendar; approved actions can create Gmail drafts and Google Calendar events after policy allows it. Marge does not send Gmail.
- Microsoft 365: read Outlook mail and calendar; approved actions can create Outlook drafts and Outlook calendar events after policy allows it. Marge does not send Outlook mail.
- Planning Center: OAuth read-side People and Calendar context in this MVP.
- Breeze: encrypted workspace API key or server-side key/base URL, read-side people and events.
- Rock RMS: encrypted workspace API key or server-side key, read-side people/attendance sync.

OAuth consent does not imply writeback permission. External writes require:

- a connected user credential,
- provider policy with `write_enabled=true`,
- the relevant action type in `allowed_actions`,
- an approved assistant action with concrete payload,
- successful execution audit.

## Final Audit Before A Pilot Pastor

Before handing Marge to a pilot pastor, run the single readiness gate and capture its output:

```bash
MARGE_API_URL=https://marge.yourchurch.org MARGE_ACCOUNT_TOKEN=marge_sess_... .venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

The gate runs `scripts/verify_production_config.py`, `scripts/verify_deployment_bootstrap.py`, `scripts/verify_migrations.py`, and `scripts/verify_live_integrations.py --include-mcp --require-live-provider`. Add `--include-first-run-workspace` when you want the gate to also create a disposable workspace and rehearse the first-run chat journey. It must fail if the deployment only verifies MCP/local access and no external church-tool provider is live.

When the gate fails, it prints a **Next actions** section. Treat that as the operator punch list: fix production config first, then apply migrations, then verify at least one live provider with the no-sync credential check before any first sync.

Then manually verify `/app`:

- New workspace signup asks or reflects pastor/church/ministry context.
- The Assistant screen does not show demo people in a real workspace.
- The next setup step names a concrete first record or secure connector.
- Chat can save a real visitor or care note and recall it after reload.
- Integrations show credential checks and sync/writeback guardrails.
- Approval queue shows no external action as sent or written before approval.
