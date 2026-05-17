# Active Goal Audit

Last updated: 2026-05-17

This audit maps the active user goal to concrete evidence in the repository. It is intentionally stricter than "tests pass": local smoke coverage proves the MVP behavior, but a pilot pastor is not ready until production configuration and at least one live church-tool connector are verified.

## Restated Success Criteria

The active goal is achieved only when Marge can do all of the following for a first pastor signup:

1. Preserve the research and project vision for future LLM/Codex work.
2. Stop feeling like a static demo by keeping real workspaces separate from demo mode.
3. Make chat useful for saving and retrieving ministry context, not just returning canned replies.
4. Ask ministry-context questions that make Marge specific to the pastor and church.
5. Connect to church tools securely, with encrypted credentials and safe verification before sync.
6. Be proactive through a desk, approval queue, first-week plan, and reviewable drafts.
7. Keep the pastor in control before any external send or writeback.
8. Survive production readiness checks with real deployment config and at least one live external provider.

## Prompt-To-Artifact Checklist

| Requirement | Evidence | Current status |
| --- | --- | --- |
| Save the research | `docs/PASTOR_RESEARCH.md`, `docs/PASTORAL_SECRETARY_UX.md`, `docs/AI_AGENT_ROADMAP.md`, `AGENTS.md` | Implemented. These files preserve pastor pain research, product direction, agent roadmap, and repo-specific LLM guidance. |
| Give LLMs project understanding | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `BUILD_SUMMARY.md`, `docs/LAUNCH_PLAN.md`, `codex-task.md` | Implemented. `AGENTS.md` is canonical, the bridge docs point agents to it, stale launch/build docs now describe the current state, and the old task prompt is marked historical. |
| Reduce placeholder/demo behavior | Account-scoped live mode, first-record setup steps, demo/live gating in `/assistant/desk` and `/briefing/today` | Locally verified by `scripts/smoke_first_run.py` and browser checks. |
| Make first signup personal | First-run profile, contextual interview question, Assistant hero copy, contextual setup card, onboarding panel, suggested prompts | Locally verified by browser snapshot on 2026-05-17: first Assistant screen showed "Let's learn Header Smoke Church..." and asked the role question. `scripts/smoke_first_run.py` also asserts the first desk question and first setup card name the pastor's church. |
| Chat saves ministry context | `POST /assistant/chat`, account profile updates, persisted chat history | Locally verified by `scripts/smoke_first_run.py`. |
| Chat retrieves ministry memory | Person, visitor, care, prayer, connected-context lookup paths in `app/routers/assistant.py` | Locally verified by `scripts/smoke_first_run.py` and `scripts/smoke_connected_providers.py`. |
| Natural-language writes are safe | Visitor logging, single-name handling, no-name rejection, care/prayer/member note writes, queued action fallback | Locally verified by `scripts/smoke_first_run.py`. |
| Visitor follow-up is reviewable | `app/services/visitor_followup.py` and `AssistantAction` email drafts | Locally verified by `scripts/smoke_first_run.py` and `scripts/smoke_mcp_first_run.py`. |
| Drafts are server-backed, not browser placeholders | `POST /drafts/`, live detail panel draft flow, `app/services/marge.py` draft helpers, backend-prepared `email_draft` actions | Locally verified by browser check and smoke assertions that "Pastor Pastor" is not generated and prepared care actions use the server draft template instead of old browser-only wording. |
| Tool connections are secure | OAuth/API-key setup, encrypted `IntegrationCredential`, credential-scoped `verified_at`, safe verify endpoints, chat/MCP credential checks, sync rejection before verification, disconnect, metadata-only audit | Locally verified with mocked/local providers by `scripts/smoke_integrations.py` and `scripts/smoke_connected_providers.py`. |
| External writes require approval | `AssistantAction` approval queue, pastor/admin/owner-only action endpoints, approve/execute flow, and provider policy gates | Locally verified by `scripts/smoke_integrations.py` and `scripts/smoke_connected_providers.py`. |
| Pastoral data is role-gated | Pastor/admin/owner gates for briefing, drafts, member detail/notes, care, prayer, member/visitor writes, assistant actions, chat history, audit, connected context, and sync | Locally verified by `scripts/smoke_integrations.py`; staff tokens can read the live desk and basic member/visitor directories but cannot inspect approvals or mutate pastoral records. |
| MCP clients can use Marge | `mcp_server/server.py`, `mcp_server/README.md`, MCP first-run tools | Locally verified by `scripts/smoke_mcp_first_run.py`. |
| Production schema is managed | Alembic migrations and `scripts/verify_migrations.py` | Verified in `scripts/verify_pilot_readiness.py`: migration schema passed. |
| Production config is safe | `.env.production.example`, `app/runtime_config.py`, `scripts/verify_production_config.py` | Not complete in the local environment. The readiness gate fails until deployment secrets are provided, and production runtime enforcement now fails closed when guardrails are missing. |
| Public deployment bootstraps first-run app | `scripts/verify_deployment_bootstrap.py`, `/health`, `/assistant/config`, `/app` | Implemented. Local development can pass with `--allow-relaxed-account-tokens`; production/pilot readiness requires runtime strict workspace-token mode. |
| First-run workspace journey can be rehearsed | `scripts/verify_first_run_workspace.py` | Implemented and locally verified. The script creates a disposable workspace, exchanges a session token, completes onboarding through chat, verifies first-record setup, logs a visitor through chat, queues a welcome draft, and confirms visitor follow-up lookup recalls the saved visitor. Remote runs require `--allow-remote-write`. |
| At least one live external provider is verified | `scripts/verify_live_integrations.py --include-mcp --require-live-provider` | Not complete. Local MCP verified, but no Google Workspace, Microsoft 365, Planning Center, Breeze, or Rock RMS credential is live. |

## Verification Run

Most recent local verification completed on 2026-05-17:

- `.venv/bin/python scripts/smoke_first_run.py`: passed.
- Signup specificity: passed. The first-run smoke now asserts `/assistant/signup` rejects a missing church name instead of creating a generic `New Church` workspace.
- First-run chat specificity: passed. The smoke now uses natural pastor wording for role, attendance, church context, church voice/tradition, first ministry priority, tools, follow-up pain, weekly rhythm, and approval guardrails; it also verifies command-like onboarding answers such as "Help me close loops...", "Connect Planning Center and Gmail", and "Write in a warm and brief tone" save the intended profile fields instead of being skipped as commands. It verifies terse answers such as "Solo.", "72.", "Planning Center and Gmail", "Warm, brief, pastoral", and "Baptist roots; avoid insider language" normalize into the right profile fields, and that "Pastor Ben." saves as `Pastor Ben` when Marge is asking for the pastor name. Generic plural guest language is not logged as a fake visitor. It also verifies "How will you use this context?" explains how saved context shapes priorities, drafts, secure setup, and approvals; "How do secure connections work?" explains no-passwords-in-chat, no-sync credential checks, and approval boundaries while attaching connector setup cards; "Why is this the next step?" explains the active setup step and keeps the first-record card attached; "Help me add a ministry update" guides concrete pastoral logging and points back to the first real record while `data_seed` is pending; "What should I do for first-time guests and private prayer needs?" answers from saved ministry priority context and the active first-record setup; named-church recap prompts such as "What do you know about First Run Smoke Church..." summarize saved ministry context instead of failing as a person lookup; saved-person, person-context, prayer-context, care-context, absence-context, and follow-up-draft suggested prompts carry the real person name forward instead of unresolved "them"/"Log a contact" prompts; unknown named-person prompts carry the name forward into a safe add-person suggestion and generic "Add this person first." returns capture guidance instead of creating a placeholder; request-level private prayer prompts do not label unnamed requests as `Pastor`, do not create fake people, can answer "What should I capture for private prayer?", can queue a reviewable draft from the private request, and generic "Add a prayer request." prompts do not create empty prayer records; generic "Add a care case." and "What should I capture for a care case?" prompts explain person/category/latest-contact requirements and do not create empty care cases; "Who has been absent?" answers from live attendance/absence desk items and attaches absence follow-up actions; "Log that I visited Janet Ellis today" saves a pastoral contact from a named prompt; "Where can I fit a visit with Janet Ellis?" queues a reviewable calendar block using saved care context, weekly rhythm, and approval/writeback boundaries; "Draft a scheduling reply" creates a focused reviewable scheduling draft from saved weekly rhythm and ministry calendar context instead of generic reply drafts; "What calendar details do you need?" explains title/date/start-time requirements, Google Workspace or Microsoft 365 setup/check cards, and approval/writeback boundaries without creating approval actions; "What can wait until next week?" triages real people/review items, explains deferrable work, and keeps approval boundaries visible; "What should I handle next?" returns concrete next work with cards instead of generic chat; "What should I review first?" and "What should I approve first?" summarize pending review work without mutating action status; "Open integrations" returns actionable saved-tool setup cards and explicitly excludes MCP from church-tool readiness; "Sync the connected tools" runs a credential-aware precheck, excludes MCP as a church tool, attaches saved connector setup cards, and does not default to syncing Google before verification; "show my setup steps" returns data-seed and connector cards; "what should I ask a new family?" returns first-record coaching with the visitor setup card; prayer-focused profiles get `Add the first real prayer request` plus private-prayer coaching and retire that setup action after a concrete prayer save; care/hospital/grief-focused profiles get `Add the first person needing care` with a person-first setup form and can create the person plus care case from a named chat prompt; "prepare my first-week plan" returns the reviewable first-week plan action with visitor/tool setup; "how can you help me this week?" routes through the saved ministry operating plan with the visitor setup card; and "what can you do before tools are connected?" plus "explain the approval rules" return concrete, non-placeholder guidance.
- First visitor draft personalization: passed. The first-run smoke asserts a visitor note such as "asked about kids ministry" appears in the queued welcome draft body, while the saved drafting voice, church voice/tradition, and approval guardrails stay in review metadata rather than provider-send payload text.
- Prayer and care draft personalization: passed. The first-run smoke asserts request-level private-prayer drafts include the actual prayer context, care drafts include the actual grief/visit context, and both action payloads carry saved drafting voice, church voice/tradition, and privacy/approval guardrails as review metadata.
- Chat morning briefing: passed. "Give me my morning briefing" returns a dedicated `morning_briefing` response; complete-but-empty workspaces get the first-real-record card instead of fake all-clear copy, and populated workspaces get named people/review work plus approval boundaries.
- Absence draft prompt: passed. The backend-suggested "Draft absence check-ins." prompt now queues reviewable absence `email_draft` actions from attendance context instead of repeating the absence lookup response; the smoke verifies saved pastor voice metadata is attached.
- `.venv/bin/python scripts/smoke_mcp_first_run.py`: passed.
- `.venv/bin/python scripts/smoke_connected_providers.py`: passed.
- `.venv/bin/python scripts/smoke_integrations.py`: passed.
- `.venv/bin/python scripts/smoke_frontend_static.py`: passed.
- `.venv/bin/python -m compileall app mcp_server scripts`: passed.
- `.venv/bin/python scripts/verify_deployment_bootstrap.py --allow-relaxed-account-tokens`: passed locally. This proves `/health`, `/assistant/config`, and `/app` are reachable in local dev; production readiness still requires the same script without relaxed token mode so `/assistant/config` reports `require_account_token=true`.
- `.venv/bin/python scripts/verify_first_run_workspace.py`: passed locally. It also caught and now guards against a real chat routing bug where "Show visitors needing follow-up." was incorrectly handled as a care lookup.
- `.venv/bin/python scripts/verify_pilot_readiness.py --include-first-run-workspace`: first-run workspace step passed locally, migration schema passed, and the gate still failed for expected production configuration, strict runtime token mode, and live external-provider blockers.
- Role gates for pastor-controlled surfaces: passed. `scripts/smoke_integrations.py` now asserts a staff token can read the live desk and basic member/visitor directories, while receiving `403` for ministry profile edits, approval actions, pastoral briefing, pastoral drafts, member detail/notes, care, prayer, member/visitor writes, and legacy `/chat/`.
- `.venv/bin/python scripts/verify_migrations.py`: passed.
- Runtime production guard: implemented in `app/runtime_config.py`; `MARGE_ENV=production` or `MARGE_ENFORCE_PRODUCTION_CONFIG=true` now makes startup fail closed if production guardrails, placeholder domains, partial connector config, or non-HTTPS OAuth redirect URIs are present.
- `.venv/bin/python scripts/verify_pilot_readiness.py`: failed for expected deployment/live-provider blockers and printed concrete next actions for the failed production config, deployment bootstrap, and live connector gates.
- `.venv/bin/python scripts/verify_live_integrations.py --include-mcp --json`: passed for local MCP and reported no connected-context or assistant-action side effects from verification.
- Synthetic production config verification: passed with an exact non-placeholder `MARGE_APP_URL=https://.../app` and origin-only `CORS_ORIGINS`; failed when `/app` was missing, CORS included a path, placeholder example domains were used, or OAuth redirect config was partial/non-HTTPS.
- Frontend inline script parse with Node: passed. The static smoke also asserts form failures surface backend-safe error detail, connector credential checks stay no-sync/no-action, live drafts keep pointing through server/workspace records, care-focused setup prompts open the first care case instead of a generic person prompt, and live first-run copy avoids database/demo wording.
- Frontend live prompt fallback: passed. The static smoke now asserts Assistant fallback chips derive from workspace context instead of hardcoded "before noon" / "draft replies" operational placeholders when `desk.suggested_prompts` is absent.
- `git diff --check`: passed.
- Browser check of live detail panel draft flow: passed.
- Browser check of first Assistant header after signup: passed.
- Browser check of pre-workspace live shell: passed. A local `/app/` load with legacy singleton rows present showed only workspace-required first-run copy, no legacy church interview question, no legacy chat, and no demo/dev wording before signup. The network list before signup contained only `/assistant/config` and `/assistant/sessions/current`, not unscoped actions, chat history, people, policies, audit, or connected-context calls. Creating "Scoped Network QA Church 20260517" through the visible form then exchanged the one-time token for an HttpOnly `marge_session`, kept `localStorage` empty, started scoped workspace data requests, and rendered the role question for the new church.
- Browser check of pre-workspace header actions: passed. The first viewport now shows `Create workspace` and `Learn plan` before signup instead of premature `Add Care` / `Add Person` write actions. After creating "Header Action QA Church 20260517", the same header actions switched back to `Add Care` and `Add Person` inside the scoped workspace.
- Browser check of pre-workspace secondary tabs: passed. People, Care Board, Visitors, and Prayer show `Create workspace` / `Learn plan` controls plus a private-workspace gate before signup, rather than write buttons that cannot save without an account.
- Browser check of pre-workspace page headers: passed. Fresh `/app/` with no cookie/localStorage rendered People, Plan, and Integrations with `Create a workspace first.` headers, `Setup` detail toggle text, and workspace/learning CTAs instead of search, approval queue, or connector workflow language. Console warnings/errors were `0`; screenshot saved at `/tmp/marge-preworkspace-headers.png`.
- Browser check of first-run connector guidance: passed. A temporary workspace named "Browser Audit Church 20260517" saved role, size, church context, follow-up burden, and tools through the visible chat UI; the desk then surfaced Google Workspace, Planning Center, and Breeze setup cards from those saved tools. Opening Breeze setup showed the encrypted-storage requirement before any API key could be entered.
- Browser check of connected-but-unverified integration state: passed. The Planning Center card showed **Check credentials before syncing ministry data** and the empty synced-context row showed **Check Planning Center** instead of **Sync**.
- Chat credential check: passed. `scripts/smoke_connected_providers.py` asserts "Check Planning Center credentials before syncing" verifies the connector, records `verified_at`, imports no connected context, and queues no actions. It also asserts a premature "Sync Planning Center" request performs only the no-sync credential check first and asks for an explicit follow-up sync, that "Sync the connected tools" syncs verified Planning Center, Microsoft 365, and Breeze without defaulting to Google, that "What meetings need prep?" lists synced calendar events without creating extra approval actions, and that incomplete Outlook calendar-write prompts route to "What calendar details do you need?" help without creating actions.
- Visitor follow-up chat routing: passed. "Show visitors needing follow-up." now routes to `visitor_context_lookup` and recalls the saved visitor instead of being intercepted by the generic care follow-up matcher.
- Generic follow-up prioritization: passed. "Who needs follow-up?" now routes to cross-domain `prioritize_day` with pastoral priority cards instead of defaulting to care-only lookup.
- Care follow-up scheduling prompt: passed. The backend-suggested "Where can I fit care follow-up?" now routes to `care_visit_plan_queued`, chooses a real active care case, returns a calendar-block review item, and keeps external calendar writes behind approval.
- MCP sync precheck: passed. `scripts/smoke_mcp_first_run.py` asserts the MCP `sync_integration` tool turns an unchecked-provider sync rejection into a no-sync credential verification, imports no ministry context, queues no actions, and asks for an explicit follow-up sync.
- Staff role gate check: passed. `scripts/smoke_integrations.py` asserts staff tokens cannot change profile/policies, inspect or prepare approval actions, read pastoral briefing/care/prayer/member detail, generate drafts, write member/visitor records, or bypass through legacy `/chat/`.
- LLM documentation cleanup: passed. `BUILD_SUMMARY.md` and `docs/LAUNCH_PLAN.md` now describe the 2026-05-17 state instead of the old April/Monday MVP, and `codex-task.md` is explicitly marked as historical prompt text.
- SQLite `PRAGMA foreign_key_check`: `0`.
- SQLite `PRAGMA integrity_check`: `ok`.
- Smoke/debug/browser workspace residue: `0`.

## Readiness Gate Result

`scripts/verify_pilot_readiness.py` still fails locally for the right reasons:

- Missing production `DATABASE_URL`.
- `MARGE_ENV=production` is not set in the local environment.
- `MARGE_AUTO_CREATE_SCHEMA=false` is not set in the local environment.
- `MARGE_REQUIRE_ACCOUNT_TOKEN` is not set to `true` in the local environment.
- `MARGE_ENCRYPTION_KEY` is not a valid production Fernet key in the local environment.
- Secure session cookies, exact public `/app` URL, origin-only CORS, and SMTP invite/login email delivery are not configured locally.
- The local runtime reports `require_account_token=false`; production must report `true` through `/assistant/config`.
- No live external church-tool provider is verified. MCP alone does not satisfy the live-provider requirement.

The gate now prints a **Next actions** section that tells the operator to fill the deployment environment, rerun production config verification, connect a real provider, run the no-sync credential check, and rerun live connector verification with an owner/admin/pastor session token.

## Remaining Work

The goal is not complete until a real deployment provides production secrets, applies migrations, and verifies at least one live provider without syncing ministry data first. Use:

```bash
MARGE_API_URL=https://marge.yourchurch.org \
MARGE_ACCOUNT_TOKEN=marge_sess_... \
.venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

Only after that passes should a pilot pastor import or sync real pastoral data.
