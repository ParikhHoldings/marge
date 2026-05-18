# Pilot Connector Runbook

This runbook covers the remaining gap between local smoke coverage and a pastor relying on Marge with real church tools. Use it for the first pilot workspace and keep the evidence with the deployment notes.

## Goal

Prove that at least one real external church-tool provider is connected to the pilot workspace, credential-checked without syncing ministry data, and ready for an explicit pastor/admin sync.

MCP/local API access does not count. OAuth callback completion does not count. A configured API key does not count. The proof is a successful no-sync credential check for Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS, followed by `scripts/verify_live_integrations.py --include-mcp --require-live-provider`.

## Prerequisites

- Production config verification passes:

```bash
.venv/bin/python scripts/verify_production_config.py --env-file .env.production.candidate
```

- Deployment bootstrap verification passes against the public API:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_APP_URL=https://marge.yourchurch.org/app \
.venv/bin/python scripts/verify_deployment_bootstrap.py
```

- Migrations are applied and startup schema creation is disabled:

```bash
alembic upgrade head
.venv/bin/python scripts/verify_migrations.py
```

- A pilot workspace exists and the operator has an owner/admin/pastor session token. Verify it before connector work:

```bash
curl -sS \
  -H "Accept: application/json" \
  -H "X-Marge-Account-Token: $MARGE_ACCOUNT_TOKEN" \
  "$MARGE_API_URL/assistant/account"
```

The response must identify the expected church and `current_role` must be `owner`, `admin`, or `pastor`.

## Choose The First Provider

Start with the provider that best matches the pastor's first stated follow-up burden:

- Visitor and family follow-up: Planning Center first, then Google Workspace or Microsoft 365.
- Email reply help: Google Workspace or Microsoft 365 first.
- Calendar/meeting prep: Google Workspace, Microsoft 365, or Planning Center.
- Attendance or absence follow-up: Rock RMS, Planning Center, or Breeze.
- Directory/care context: Planning Center, Breeze, or Rock RMS.

Prefer the tool the church already named during onboarding. Do not connect a provider just because it is easiest for the developer.

## Configure Provider Secrets

For OAuth providers, create the production OAuth app before the pastor starts setup:

- Planning Center redirect URI: `https://marge.yourchurch.org/assistant/integrations/planning_center/callback`
- Google Workspace redirect URI: `https://marge.yourchurch.org/assistant/integrations/google_workspace/callback`
- Microsoft 365 redirect URI: `https://marge.yourchurch.org/assistant/integrations/microsoft_365/callback`

The redirect URI origin must match `MARGE_APP_URL`'s origin. The verifier rejects placeholder, partial, non-HTTPS, or wrong-path callback config.

For API-key providers, prefer encrypted workspace credentials entered through Marge's integration setup UI. If server-wide credentials are used for Breeze or Rock RMS, the base URL must be public HTTPS and must not include usernames, passwords, query strings, fragments, localhost, or private-network hosts.

## Setup In The Workspace

1. Open `/app` as the pilot pastor/admin.
2. Go to Integrations or ask chat: `What should I connect first?`
3. Start the provider setup card for the chosen provider.
4. Complete OAuth consent or encrypted API-key credential entry.
5. Stop after setup. Do not run sync yet.

Expected state after setup:

- The provider card says it is connected/configured but still needs credential checking.
- Marge does not show synced people, inbox, calendar, attendance, care, or prayer data from this provider yet.
- No new assistant review actions are created by setup alone.

## No-Sync Credential Check

Run the product credential check from the UI or chat:

- UI: click `Check credentials`.
- Chat: `Check Planning Center credentials before syncing.` Replace the provider name as needed.
- API:

```bash
curl -sS -X POST \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "X-Marge-Account-Token: $MARGE_ACCOUNT_TOKEN" \
  "$MARGE_API_URL/assistant/integrations/planning_center/verify" \
  -d '{}'
```

Expected result:

- `verified_at` is set for the provider credential/status.
- The response includes affirmative, non-secret identity/config metadata.
- The response does not include token, secret, API-key, cookie, authorization, or password-shaped fields.
- No connected context, assistant actions, members, visitors, care cases, or prayer requests are created.

## Operator Verification

After the product credential check, run:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
.venv/bin/python scripts/verify_live_integrations.py \
  --include-mcp \
  --require-live-provider \
  --evidence-file artifacts/live-connector-verification.json
```

The verifier snapshots every page of workspace records before and after credential checks, including private prayer requests. It must report:

- at least one verified external provider,
- no-sync side effects are OK,
- no sensitive identity metadata keys,
- MCP is not counted as the live provider.

The evidence file is structured JSON and must not include workspace tokens, OAuth tokens, API keys, or provider secrets. Keep it with the pilot handoff notes.
The default local `artifacts/` directory is gitignored; do not commit live provider evidence, provider identity metadata, or workspace count reports to the repository.

The evidence JSON must prove all of these fields from the same verifier run:

- `api_url`: matches the deployment being handed to the pastor.
- `required_live_provider`: `true`.
- `generated_at`: valid timezone-bearing ISO timestamp from the fresh verifier run, no older than 24 hours when the gate validates it.
- `workspace`: includes the token-free `account_id`, `slug`, `church_name`, and `current_role`; the role must be `owner`, `admin`, or `pastor`.
- `external_provider_checks`: includes at least one verified Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS check.
- `local_bridge_checks`: may include MCP, but MCP is only local agent bridge evidence.
- `external_verified_count`: at least `1`.
- `live_provider_ready`: `true`.
- `no_sync_side_effect_check_passed`: `true`.
- `side_effect_check.collections`: passing entries for connected context, assistant actions, members, visitors, care cases, and private/public prayer requests.

Do not use the legacy `verified` array or `local_bridge_checks` as proof that a church-tool provider is ready.

The combined pilot gate also passes this evidence-file path to the live verifier by default:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=REPLACE_WITH_OWNER_ADMIN_PASTOR_SESSION \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

Use `--live-evidence-file <path>` on the combined gate if your deployment or CI system stores handoff artifacts somewhere else.
The combined gate removes any existing evidence file at that path before running the live verifier, then validates the fresh JSON before it can pass. If the path points to a directory, cannot be removed, has the wrong `api_url`, lacks workspace scope, reports a non-pastoral role, has a missing, stale, timezone-less, or future-dated `generated_at`, shows only MCP/local bridge checks, omits any required no-sync side-effect collection, or fails the no-sync side-effect check, the gate fails.

If it fails, do not sync. Follow the verifier's `Next actions`.

## First Sync

Only after the verifier passes:

1. Let the pastor/admin explicitly request sync from the UI or chat.
2. Confirm the provider is the intended first provider.
3. Run sync once.
4. Review connected context and queued assistant actions.

Expected state after first sync:

- Synced records are compact and pastor-purpose only.
- Review actions are queued instead of external writes.
- The assistant can answer from synced context.
- No email is sent and no external calendar or ChMS write occurs.

## Writeback

Leave writeback disabled until the pastor reviews the approval queue. To enable writeback later, require all of:

- connected user credential,
- credential verification still valid,
- provider policy `write_enabled=true`,
- the exact action type in `allowed_actions`,
- pastor/admin approval of the exact assistant action,
- execution audit with no secret payloads.

Marge may create Gmail/Outlook drafts or calendar events only through approved action execution and only when policy allows that action type. Marge must not send mail.

## Evidence To Save

Capture these artifacts for the pilot handoff:

- production config verifier output,
- deployment bootstrap verifier output,
- migration verifier output,
- first-run workspace verifier output,
- `verify_live_integrations.py --include-mcp --require-live-provider --evidence-file ...` output and JSON evidence file,
- the provider chosen and why it matches the pastor's first ministry burden,
- timestamp of the first credential check,
- timestamp of the first sync, if performed,
- any writeback policy changes.

Do not save raw OAuth tokens, API keys, provider payload dumps, private prayer text, or pastoral member exports.
Do not commit the generated evidence files. Store them in the deployment or pilot handoff system instead.

## Stop Conditions

Stop and fix the system before pilot use if any of these happen:

- production config verification fails,
- public `/assistant/config` reports `require_account_token=false`,
- the operator token is missing or not owner/admin/pastor,
- credential check imports data or queues actions,
- credential verification response exposes secret-shaped metadata,
- `verified_at` is set from empty or false-only provider metadata,
- MCP is the only verified provider,
- sync works before credential verification,
- setup, verify, sync, or writeback can run without a scoped workspace.
