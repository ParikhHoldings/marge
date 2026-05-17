# AGENTS.md

This is the canonical working context for Codex and other LLM agents in the Marge repo. Read this before changing code, copy, product docs, or deployment config.

## Project Identity

Marge is an AI pastoral assistant and pastoral secretary for solo and small-church pastors. The product promise is:

> Marge is the church secretary you cannot afford: a warm, proactive assistant who briefs the pastor every morning on who needs care today, drafts follow-up messages, tracks visitors, care cases, prayer requests, and makes sure no one falls through the cracks.

Marge is not a generic chatbot, dashboard, church-management database, sermon helper, or corporate executive assistant. She is the missing church secretary: institutional memory, relational radar, inbox/calendar helper, and practical ministry assistant who moves pastoral care and pastoral administration forward.

Primary user: a burned-out solo pastor leading roughly 50-150 weekly attendees with no full-time admin staff. The pastor has little margin, carries guilt about people being missed, and will only trust software that sounds like it understands ministry.

Core loop: the pastor opens Marge like a trusted assistant. Marge gives the day's briefing, answers plain-English questions, drafts the next emails or texts, proposes calendar blocks, and keeps people, care, visitors, prayer, and integrated church systems in context.

North star metric: important pastoral follow-through completed per pastor per week.

## Product Principles

- Proactive help beats passive reporting. Marge should bring the next pastoral or administrative action to the pastor.
- Pastoral care is a movement problem, not just an information problem. Avoid building prettier databases that still make the pastor do all the remembering.
- Pastoral administration is a trust problem, not just an automation problem. Draft, queue, summarize, and remind before sending or changing external systems.
- Specific names, dates, and context matter. "Tom's birthday is Thursday" is Marge. "Member milestone detected" is not.
- The pastor stays in control. Marge may draft, remind, and log, but she must not send outreach or share sensitive information without explicit pastor confirmation.
- One useful nudge is better than noisy lists. Prioritize without overwhelming.
- Privacy is product integrity. Treat care notes, prayer requests, counseling, family details, and medical context as sensitive pastoral data.
- Distinguish current MVP behavior from the long-term spec. The spec describes the destination; this repo currently contains a FastAPI/SQLite MVP plus static frontend, landing page, and MCP server.

## Marge's Voice

Marge should sound like a beloved church secretary who has served the church for decades: warm, reliable, direct, humble, and a little old-fashioned in the best way.

Use:

- "Good morning, Pastor Nathan. Here are your people for today."
- "Janet could really use a call this week."
- "Tom's birthday is Thursday. He would love a call."
- "Here's a draft for you."
- "Marge will note this and give it time before flagging them again."

Avoid:

- Corporate language: "engagement", "touchpoint", "workflow optimization", "member milestone event".
- Clinical language: "case flagged", "risk segment", "intervention required".
- AI disclaimers in product voice: "I am an AI", "based on the data".
- Judgmental reminders. If the pastor missed something, simply bring it back with care.

Tone test: read every notification aloud. If it sounds like it came from a database, rewrite it. If it sounds like a caring colleague briefing a pastor before a busy day, it fits.

## Scope Boundaries

Marge should focus on the work a small-church pastoral secretary would help with:

- Chat-first daily assistant experience.
- Morning briefing.
- Inbox triage and email draft preparation.
- Calendar awareness, meeting preparation, visit blocks, and scheduling drafts.
- Visitor follow-up.
- Care and crisis tracking.
- Prayer request tracking and follow-up.
- Member relationship notes.
- Absence detection.
- Outreach drafts.
- Pastoral nudges.
- Privacy-safe counseling scheduling only, without counseling content.
- Integration with existing church systems such as Rock RMS, Planning Center, Breeze, Google Workspace, Outlook, and calendar providers.
- Weekly bulletin/newsletter content assist from approved public information.

Marge should not expand into:

- Sermon preparation.
- Financial records, giving statements, or bookkeeping.
- Owning facility scheduling as a system of record.
- HR/personnel management.
- Owning volunteer scheduling as a system of record.
- Social media scheduling.
- Livestream/service production.
- Theological research/commentary.
- General email inbox management unrelated to church ministry.
- Children's/student ministry tracking for launch.

If a feature request crosses these boundaries, flag the product tradeoff before implementing.

The long-term product posture is "AI personnel hire for churches," not "new ChMS." Marge should integrate with systems that already hold church data, help the pastor use that data conversationally, and write back only after explicit approval.

## Current Repository Snapshot

Current stack:

- Backend: FastAPI, SQLAlchemy, Pydantic, SQLite by default, Postgres-compatible `DATABASE_URL`.
- AI calls: Anthropic preferred in `app/services/marge.py` when `ANTHROPIC_API_KEY` is set; OpenAI fallback when `OPENAI_API_KEY` is set. Chat extraction in `app/routers/chat.py` uses OpenAI when available with heuristic fallback.
- Frontend app: static HTML mounted at `/app` from `frontend/index.html`.
- Landing page: static Vercel landing page under `landing/` with Beehiiv waitlist API.
- MCP server: `mcp_server/server.py` exposes Marge REST API tools over stdio.
- Deployment: Railway config in `railway.toml`.

Important docs already in this repo:

- `spec.md`: long-term product vision, ICP, roadmap, GTM, pricing, security posture, target architecture.
- `README.md`: current quickstart, endpoints, structure, voice notes.
- `BUILD_SUMMARY.md`: current-state build summary, verification commands, and remaining pilot blockers.
- `docs/LAUNCH_PLAN.md`: first pastor pilot launch definition and demo flow.
- `docs/AI_AGENT_ROADMAP.md`: long-term agent safety, action model, MCP/API, permissions, secretary tooling, and integrations plan.
- `docs/PASTORAL_SECRETARY_UX.md`: researched product direction for Marge as a chat-first pastoral secretary.
- `docs/PASTOR_RESEARCH.md`: saved research on pastor pain points and first-run product implications.
- `docs/THREAD_GOAL_AUDIT.md`: current active-goal evidence, verification results, and remaining production/live-provider blockers.
- `mcp_server/README.md`: MCP server install and client setup.
- `codex-task.md`: historical build task. Do not treat its instructions as always-current.
- `requirements.txt` and `mcp_server/requirements.txt`: Python dependencies.

## Project Map

- `app/main.py`: FastAPI app, startup DB init, CORS, router registration, static `/app` mount.
- `app/database.py`: SQLAlchemy engine/session setup and `init_db()`.
- `app/models.py`: ORM models for members, visitors, care notes, prayer requests, member notes, church accounts, account-scoped pastor profiles, integration status, OAuth state, encrypted integration credentials, the assistant action approval queue, persisted assistant chat history, normalized connected context, and audit events.
- `migrations/`: Alembic migration environment and versioned schema history. Keep migrations static and reviewable; do not rely on `Base.metadata.create_all()` as the production migration path.
- `app/marge_voice.py`: voice constants and template drafts. Prefer updating these for template copy changes.
- `app/services/marge.py`: briefing generation, nudge logic, draft generation, LLM wrappers, text rendering.
- `app/services/demo_data.py`: demo-mode briefing payload for empty/live-less environments.
- `app/services/secure_tokens.py`: Fernet encryption helpers for OAuth token payloads. Requires `MARGE_ENCRYPTION_KEY`.
- `app/services/invitations.py`: optional SMTP/file-outbox workspace invite and login-link delivery. It may handle raw one-time tokens only for email delivery; never audit or persist them.
- `app/services/visitor_followup.py`: shared visitor welcome action queueing for form/API, chat, MCP, and future connector-created visitor records.
- `app/routers/briefing.py`: `GET /briefing/today`, demo/live selection, response schemas.
- `app/routers/visitors.py`: visitor CRUD and follow-up drafts.
- `app/routers/members.py`: member CRUD, pastoral notes, care drafts, Rock sync endpoint.
- `app/routers/care.py`: care cases and prayer request lifecycle.
- `app/routers/drafts.py`: unified draft endpoint for frontend and agent clients.
- `app/routers/assistant.py`: signup/workspace token flow, connected assistant desk, pastor profile onboarding/chat capture, persisted assistant chat history, persisted assistant action approval queue, connected context sync/cache, audit log, secure integration setup/status, OAuth callback/token storage, and assistant chat.
- `app/routers/chat.py`: plain-English "Tell Marge" endpoint and lightweight action persistence.
- `app/integrations/rock.py`: Rock RMS member and attendance sync. Safe no-op when no API key is configured.
- `scripts/morning_briefing.py`: standalone cron script for briefing output and optional Telegram delivery.
- `scripts/seed_demo_data.py`: local demo data with narrative story threads.
- `scripts/smoke_first_run.py`: local first-run pastor journey smoke test against a running FastAPI server.
- `scripts/smoke_integrations.py`: in-process OAuth/security smoke test with a mocked Google token exchange.
- `scripts/verify_production_config.py`: offline deployment readiness gate for token scoping, encryption, HTTPS cookies, CORS, migration settings, invite/login email delivery, and OAuth config shape.
- `scripts/verify_migrations.py`: runs Alembic against a temporary SQLite database and checks schema drift against SQLAlchemy metadata.
- `frontend/index.html`: static chat-first assistant workspace with demo/live mode, secretary desk surfaces, deeper care data views, draft modal, care contact action, and chat input.
- `landing/index.html`: marketing waitlist page.
- `landing/api/subscribe.js`: Beehiiv waitlist subscription endpoint.
- `mcp_server/server.py`: MCP stdio server wrapping the Marge REST API.

## Local Development

Use Python 3.11+ if possible.

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Useful URLs:

- API health: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`
- Frontend app: `http://localhost:8000/app`
- Briefing endpoint: `http://localhost:8000/briefing/today`
- Demo briefing: `http://localhost:8000/briefing/today?mode=demo`

Useful scripts:

```bash
python3 scripts/seed_demo_data.py
python3 scripts/seed_demo_data.py --force
python3 scripts/morning_briefing.py
.venv/bin/python scripts/smoke_first_run.py
.venv/bin/python scripts/smoke_integrations.py
.venv/bin/python scripts/smoke_connected_providers.py
.venv/bin/python scripts/verify_deployment_bootstrap.py
.venv/bin/python scripts/verify_first_run_workspace.py
.venv/bin/python scripts/verify_live_integrations.py
.venv/bin/python scripts/verify_pilot_readiness.py
```

For production or pilot handoff, use the combined readiness gate when possible. It runs production config checks, migration drift verification, and live connector verification with `--require-live-provider` so a green MCP check cannot hide that no real church-tool connector is verified:

```bash
MARGE_API_URL=https://marge.yourchurch.org MARGE_ACCOUNT_TOKEN=marge_sess_... .venv/bin/python scripts/verify_pilot_readiness.py --env-file .env.production.candidate
```

MCP server:

```bash
pip install -r mcp_server/requirements.txt
MARGE_API_URL=http://localhost:8000 python mcp_server/server.py
```

Deployment:

- Railway uses `railway.toml`.
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Health check path: `/health`.

## Environment Variables

Common local settings are documented in `.env.example`.

- `DATABASE_URL`: defaults to `sqlite:///./marge.db`.
- `PASTOR_NAME`: used in greetings and drafts.
- `CHURCH_NAME`: used in greetings and drafts.
- `OPENAI_API_KEY` / `OPENAI_MODEL`: used by chat extraction and optionally Marge draft/briefing generation.
- `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`: used by `app/services/marge.py` before OpenAI when available.
- `ROCK_HALLMARK_API_KEY`: optional server-wide Rock RMS key. Prefer encrypted workspace credentials for multi-church use.
- `ROCK_BASE_URL`: optional Rock API base override.
- `MARGE_ENCRYPTION_KEY`: Fernet key required before OAuth connector tokens can be stored.
- `MARGE_REQUIRE_ACCOUNT_TOKEN`: set true before non-local exposure so scoped routes reject missing workspace tokens.
- `MARGE_AUTO_CREATE_SCHEMA`: local dev may leave true; production should run `alembic upgrade head` and set false.
- `MARGE_ENV=production` or `MARGE_ENFORCE_PRODUCTION_CONFIG=true`: enables startup safety checks that fail closed if production guardrails, HTTPS OAuth redirect URIs, or non-placeholder connector config are missing.
- `MARGE_SESSION_COOKIE_NAME` / `MARGE_SESSION_COOKIE_SECURE` / `MARGE_SESSION_COOKIE_SAMESITE`: HttpOnly browser session cookie settings for `POST /assistant/sessions`.
- `MARGE_APP_URL`, `MARGE_INVITE_EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`: optional workspace invite email delivery.
- `PLANNING_CENTER_CLIENT_ID` / `PLANNING_CENTER_CLIENT_SECRET` / `PLANNING_CENTER_REDIRECT_URI`: Planning Center OAuth.
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI`: Google Workspace OAuth.
- `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_REDIRECT_URI`: Microsoft 365 OAuth.
- `BREEZE_API_KEY` / `BREEZE_BASE_URL`: optional server-wide Breeze API key and account URL. Prefer encrypted workspace credentials for multi-church use.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: optional morning briefing delivery.
- `CORS_ORIGINS`: comma-separated allowed origins. Defaults to `*` for local dev.
- Tuning knobs: `ABSENCE_THRESHOLD_DAYS`, `CARE_OVERDUE_DAYS`, `PRAYER_OVERDUE_DAYS`, `VISITOR_FOLLOWUP_DELAY_HOURS`, `BIRTHDAY_LOOKAHEAD_DAYS`, `NUDGE_LOOKBACK_DAYS`.
- Landing waitlist: `BEEHIIV_API_KEY`, `BEEHIIV_PUB_ID`, `BEEHIIV_PRODUCT_FIELD_ID`.
- MCP: `MARGE_API_URL` and optional `MARGE_ACCOUNT_TOKEN`.

Never commit real `.env`, API keys, local DB files, or pastoral/member data exports.

## Data And Privacy Rules

- Treat all member, visitor, care, prayer, and counseling-related data as sensitive.
- Treat OAuth tokens, refresh tokens, API keys, and connector payloads as secrets. They must never be returned to the browser, chat, frontend logs, or normal API responses.
- Private prayer requests must stay private unless the pastor explicitly marks them public.
- Counseling content should not be stored. The long-term spec only allows scheduling/frequency metadata.
- Do not add logging that prints care descriptions, prayer text, API keys, raw Rock payloads, or personal contact details unless it is explicitly debug-only and safe.
- Do not send messages, emails, SMS, or public prayer content automatically. Drafts require pastor review and confirmation.
- Do not assume a member's spiritual state. Drafts can be caring without overclaiming.

## Known Implementation Notes And Footguns

- The long-term spec calls for Next.js, Supabase, Stripe, Resend, and Vercel cron. The current MVP is FastAPI plus SQLite/Postgres and static HTML. Do not silently migrate stacks unless the user asks.
- `codex-task.md` describes a completed/historical staging task. Use it for context only.
- Member search supports `q` and a `search` alias; keep frontend, MCP, and docs aligned if this changes.
- Drafting should go through `POST /drafts/` when possible so frontend and agent clients do not reimplement message templates.
- Backend-prepared `email_draft` actions should use the shared draft helpers in `app/services/marge.py`; the live frontend should not queue browser-only draft bodies.
- Proactive work should become `AssistantAction` rows before it is treated as approval-ready. Do not bypass `/assistant/actions/{id}/approve` for external writes.
- `POST /assistant/actions/{id}/execute` can call Google Workspace only for approved actions with complete `email` or `calendar_event` payloads and an encrypted Google OAuth token. Keep incomplete desk suggestions local.
- `POST /assistant/integrations/google_workspace/sync` reads recent Gmail and Calendar context into `ConnectedContextItem` rows and queues review actions. Keep synced payloads compact and pastoral-purpose only.
- `POST /assistant/integrations/planning_center/sync` reads Planning Center People and Calendar context via OAuth, stores compact connected context, and queues review actions for new/visitor-like people and pastoral event prep. It is read-side only in this MVP.
- `POST /assistant/integrations/microsoft_365/sync` reads Outlook mail and calendar context via Microsoft Graph OAuth, stores compact connected context, and queues inbox review plus pastoral meeting-prep actions. Approved `email_draft` actions may create Outlook drafts and approved `calendar_block` actions may create Outlook calendar events only after Microsoft writeback policy allows the exact action type; Marge must not send Outlook mail.
- `POST /assistant/integrations/{provider}/credentials` stores encrypted workspace API-key credentials for API-key connectors such as Breeze and Rock. Owner/admin only; never return the submitted key in status, chat, audit, or frontend logs.
- `POST /assistant/integrations/breeze/sync` reads Breeze people and event context via encrypted workspace credentials or server-side API key/base URL, stores compact connected context, and queues review actions for new/visitor-like people and pastoral event prep. It is read-side only in this MVP.
- `DELETE /assistant/integrations/{provider}` removes the current user's encrypted OAuth credential or a stored workspace API-key credential, expires unconsumed setup states for that user when OAuth is involved, and turns writeback off when no credentials remain. Do not log or return deleted token/API-key payloads. Env-only API-key connectors are disconnected by removing their server-side environment secrets.
- `scripts/smoke_connected_providers.py` is the local non-live coverage gate for Planning Center, Microsoft 365, and Breeze. It should keep proving OAuth/API-key setup, encrypted workspace API-key storage, safe verification, synced context, queued review actions, person-review execution into local memory, and inbox-triage execution into reviewable drafts.
- Chat prompts like "Show Planning Center context", "Show Breeze context", and "Review synced people" should answer from synced `ConnectedContextItem` rows, not fall back to generic connector status. Cards for synced items with queued actions should open the corresponding approval item directly.
- Explicit chat requests like "Add Nina Brooks to Marge" should execute the matching queued `person_review` action into local people memory when the synced person can be matched confidently. This is a local Marge write only; the reply should say it did not write back to the source system.
- Approved `person_review` actions from Planning Center or Breeze can be executed to create or merge a local Marge `Member` plus a connector-import note. This is local Marge memory only; it must not write back to the source connector.
- Approved `email_triage` actions from synced inbox items should execute into a separate `email_draft` review action. Google-sourced drafts still need Google writeback policy plus per-action approval before Gmail draft creation. Microsoft-sourced drafts may create Outlook drafts only after Microsoft writeback policy plus per-action approval. Approved Microsoft `calendar_block` actions may create Outlook calendar events only after calendar writeback is explicitly allowed. Other read-only sources stay local.
- Chat prompts like "Queue replies for these" or "Draft replies from synced inbox" should create reviewable `email_draft` actions from synced inbox context. They must remain drafts in the approval queue and must not send mail or write externally.
- Generic chat prompts like "Sync the inbox" or "Sync the mailbox again" should select the connected mail provider from the pastor's workspace and saved tools. Do not default to Google if Microsoft 365 is the connected mailbox.
- Generic chat prompts like "Sync the calendar" or "Refresh the schedule" should select the connected calendar-capable provider from the pastor's workspace and saved tools. Prefer Planning Center when the church named it, then Microsoft 365, Google Workspace, or Breeze based on actual connected/configured status; do not default to Google.
- Suggested prompts like "Open integrations" should return pastor-facing church-tool status with setup/check cards attached. Exclude local MCP from connected church-tool readiness, and keep the setup/check/sync order visible.
- Suggested prompts like "Sync the connected tools" must use actual church-tool connector status, exclude local MCP from pastor-facing readiness, and stop at setup/credential-check guidance when no external provider is verified. If providers are connected but unchecked, run the no-sync credential check first and ask for a follow-up sync. Only sync providers that have completed credential verification.
- Keep sendable email draft bodies clean. Internal review notes, drafting voice, source ids, and guardrails belong in action payload metadata such as `draft_context`, not in `payload.email.body`.
- `POST /assistant/integrations/rock/sync` calls the Rock RMS sync layer, imports people/attendance, stores a compact sync summary in `ConnectedContextItem`, and queues absence follow-up actions from attendance signals. It must remain read-side only.
- Rock RMS person IDs are account-scoped. The same external Rock ID may appear in two different church workspaces, but duplicates inside one workspace should be rejected or updated in place.
- `GET /assistant/audit-log` exposes security and action metadata. Audit rows must never include secrets, raw provider payloads, or sensitive pastoral text.
- Assistant chat should prefer synced context when the pastor asks about inbox, Gmail, messages, calendar, or meetings. Connected email reply drafts and meeting briefs should become approval-queue actions, not direct sends/writes.
- Synced Google Calendar `meeting_prep` actions should include a `brief` payload using the saved ministry context and guardrails. Do not leave synced calendar actions as raw event references only.
- Meeting lookup prompts such as "What meetings need prep?" should list synced calendar events and existing meeting-prep review cards without creating new approval actions. Explicit commands such as "Prepare my next meeting" may upsert/open a reviewable `meeting_prep` action.
- Calendar write prompts that are missing details should not dead-end. The suggested "What calendar details do you need?" prompt must explain the required title, `YYYY-MM-DD` date, start time, optional duration/location/attendees, Google Workspace or Microsoft 365 setup/check path, and the approval/writeback boundary without creating approval actions.
- Assistant chat also performs deterministic local writes from natural language. It can save ministry profile context, visitor records, private prayer requests, care cases, care contact logs, and member notes. If a person cannot be matched safely, queue an assistant action instead of writing to the wrong record.
- Assistant chat must retrieve local ministry memory, not just save it. Questions like "What do you know about Janet?", "Who needs prayer follow-up?", "Who needs care this week?", and "Show visitors needing follow-up" should answer from account-scoped members, visitors, care cases, prayer requests, and pastoral notes.
- `POST /assistant/chat` should persist the pastor turn and Marge reply into account-scoped `AssistantChatMessage` rows. `GET /assistant/chat/history` powers frontend reload continuity and MCP/LLM clients that need to know what the pastor already taught Marge. `DELETE /assistant/chat/history` clears transcript rows only; it must not delete saved profile fields, members, visitors, care, prayer, connected context, or approval actions.
- Visitor records created from any path should queue the same reviewable welcome draft through `app/services/visitor_followup.py`. Do not add a new visitor creation path that silently bypasses pastor-review follow-up. If the update includes contact details, preserve email and phone so the queued welcome draft has a usable recipient.
- MCP `log_visitor` should accept contact details such as email, phone, and source so external LLM clients can queue the same useful welcome draft as the web app. The response should say the draft is queued for review and that nothing was sent.
- First-run ministry profile saves should reflect back the actual context Marge learned and explain how she will use it. Avoid generic "saved" replies when the pastor gives unique role, church, follow-up, tool, voice, or guardrail context.
- Signup must require a real church name. Do not silently create generic workspaces such as `New Church`; the first screen and first setup card should be anchored to the pastor's actual church.
- First-run completion should require the pastor's size, church voice/tradition, first ministry priority, rhythm, and guardrail context, not just a default approval sentence. Marge should not declare the profile complete or prepare launch/setup actions until she has learned what language to respect, what the pastor wants moved first, the weekly ministry rhythm, and explicit boundaries.
- First-run setup prompts in chat should stay concrete. If the top setup step is `data_seed`, chat should guide the pastor toward the relevant first record type instead of falling through to a generic assistant reply.
- First-run priority and draft prompts should respect `data_seed`. If a complete live workspace has no people, visitors, care cases, or prayer requests yet, "what needs attention?" and "draft replies" should ask for the first relevant real record instead of saying the desk is clear.
- The chat turn that completes first-run onboarding should return setup-aware suggested prompts, not generic "before noon" or "draft replies" prompts. The visible next prompt should match the first concrete setup action, such as "Help me log the first real visitor."
- Suggested first-run prompts must be real chat behaviors too. "How will you use this context?" should explain how saved ministry context shapes priorities, drafts, secure setup, and approvals. "Why is this the next step?" should explain the active setup step in terms of the pastor's saved follow-up burden, tools, or missing context and attach the same setup card. "What should I do for {saved ministry priority}?" should answer from `profile.ministry_priorities` plus active setup/priority records, not generic fallback. "Show my setup steps" should return concrete setup cards, and coaching prompts such as "What should I ask a new family?", "What should I record first?", or "How do you handle private prayer?" should explain what to capture and keep the relevant `data_seed` card attached.
- Suggested prompts after saving or retrieving a person, prayer, care case, or draft should carry the real name forward whenever Marge knows it. Avoid prompts like "What do you know about them?", "Log a contact.", or "Draft a care follow-up." when Marge can instead suggest a prompt with the actual person name.
- Named visit-planning prompts such as "Where can I fit a visit with Janet Ellis?" should use saved care context and weekly rhythm, then queue a reviewable `calendar_block`. They must not create or change an external calendar event without checked credentials, writeback policy, and pastor approval.
- Deferral prompts such as "What can wait until next week?" should triage real work: name what should not be deferred, explain what can wait, attach reviewable actions, and keep external sends/calendar writes/system changes behind approval.
- Next-action prompts such as "What should I handle next?" should return the concrete next setup, pastoral priority, approval, or protected block with action cards attached. Do not answer them with a generic assistant fallback.
- Scheduling prompts such as "Draft a scheduling reply" should create a focused reviewable `email_draft` from saved weekly rhythm and current ministry calendar context. Do not let them fall into generic care/visitor/prayer reply drafting.
- Review lookup prompts such as "What should I review first?" and approval lookup prompts such as "What should I approve first?" should summarize the approval queue without changing action status. Reserve status changes for explicit commands like "Approve the visitor welcome draft."
- If the frontend ever lacks `desk.suggested_prompts`, live fallback chips should derive from saved ministry priorities, active approvals, verified tools, or setup state. Do not fall back to canned operational prompts that make a fresh workspace feel like a placeholder demo.
- First-run chat must not overwrite a saved church name with descriptive context. Sentences like "Grace Church is a neighborhood church with many young families" should preserve `church_name` and save the description as `church_context`.
- The frontend assistant conversation should render returned chat `actions` and `suggested_prompts` as visible next steps on the Marge reply. Do not discard them after `loadData()`, or chat will feel like plain text instead of a working secretary.
- Chat action cards for setup steps should use their concrete setup CTA before generic approval opening. A `seed` card should open the right first-record form directly, and an `integrations` card with a provider should start secure setup directly.
- Visible fallback prompts such as "Help me add a ministry update" should guide concrete pastoral logging, not generic onboarding. If the workspace still needs a `data_seed`, point back to the first real record. Otherwise explain how to phrase visitor, care, prayer, and member-note updates, and queue for review rather than writing to the wrong person when matching is uncertain.
- Pastor-facing connector readiness should count external church tools only: Google Workspace, Microsoft 365, Planning Center, Breeze, and Rock RMS. MCP is an LLM/developer bridge, not a church data provider; keep it out of "ready church tools" counts and connector-status replies unless the user is explicitly working on MCP.
- Chat prompts like "What can you do before tools are connected?" should answer with useful local secretary work: learn ministry context, keep local people/visitor/care/prayer memory, draft reviewable follow-up from local details, prepare first-week setup, and then explain secure connector setup/check/sync order. Do not collapse this into generic connector status.
- Rock/attendance follow-up prompts such as "Who has been absent?" should answer from live absence desk items and attach absence check-in actions. Do not leave backend-suggested Rock prompts as generic chat fallbacks.
- Suggested absence prompts such as "Draft absence check-ins." should prepare reviewable `email_draft` actions from live attendance/absence context, not repeat the lookup response. Drafts must remain approval-gated and carry saved pastor voice metadata.
- "How do secure connections work?" should explain the connector safety model directly: private workspace first, provider OAuth or encrypted API-key setup, server-side credential storage, no passwords in chat, no-sync credential checks before sync, and approval/policy gates before external writes. If the pastor has named tools, return connector setup cards with that explanation.
- Chat prompts like "Explain the approval rules" should explain the writeback boundary: credentials checked, church writeback policy allows the action type, and the pastor approves the exact item before external sends/calendar writes/system changes. Do not answer these prompts as a plain approval-queue lookup.
- Broad pastoral help questions such as "How can you help me this week?" should route through the saved ministry operating plan and return concrete setup/action cards. Do not let these fall through to a generic empty-desk answer while the first-run workspace still needs a real person, visitor, prayer, care, or connector step. Church recap prompts such as "What do you know about {church name}?" should summarize the saved ministry profile and setup plan, not run a failed person lookup against the church name.
- Chat prompts like "Prepare my first-week plan" should return the reviewable `first_week_plan` action and summarize the pastor's first real record, recommended connectors, rhythm, voice, and approval guardrails. Do not let a prompt Marge suggests fall through to the generic assistant reply.
- Frontend labels on connector trust surfaces should preserve common acronyms such as OAuth, API, MCP, and RMS. Avoid rendering "Oauth" or "Api" in setup/verification modals.
- `AssistantChatMessage.response_json` stores compact assistant response metadata so action cards and suggested prompts can rehydrate after browser reloads. Keep it account-scoped, compact, and free of secrets; production databases need a real migration for this additive column.
- The legacy `/chat/` route is still mounted for older clients and should delegate to the connected `/assistant/chat` behavior. Keep it account-scoped with `X-Marge-Account-Token`; do not let it reintroduce old heuristic writes or generic `Guest` placeholders.
- The static frontend should send all workspace chat composer messages to `/assistant/chat`, even outside the Assistant tab, so Marge returns persisted history, action cards, suggested prompts, first-run setup guidance, and visitor welcome draft actions consistently. Keep `/chat/` only as a backward-compatible API route for older clients.
- `POST /drafts/` is account-scoped too. Do not draft from another church's member, visitor, care, or prayer IDs; return 404 under the current token if the target record belongs elsewhere.
- Legacy resource draft endpoints such as `GET /visitors/{id}/draft` and `GET /members/{id}/draft/care` must also use the current workspace pastor/church profile, not global `PASTOR_NAME`/`CHURCH_NAME` env values, when `X-Marge-Account-Token` is present.
- `GET /assistant/desk` includes `setup_steps`, `interview_question`, and `operating_plan`. These drive first-run onboarding from missing profile fields, saved ministry context, and the pastor's tools. Keep them concrete and route connector work through secure integration setup, not chat-secrets.
- `interview_question` should use saved ministry context when available. If Marge knows follow-up pain, role, church name, tools, or rhythm, the next question should reference that context instead of falling back to generic form wording.
- Complete live workspaces with no people/care/prayer data should get a `data_seed` setup action tied to the pastor's saved follow-up burden, but the pastor-facing title should name the concrete next record, such as "Log the first real visitor." This is the first real-person prompt that replaces fake demo names. Include the most relevant first form in the desk item when possible, for example `visitor` for new-family/guest pain and `prayer` for prayer follow-up pain.
- Prayer-focused first-run profiles should route the first real record to `Add the first real prayer request`, keep private-prayer coaching attached to the prayer `data_seed` card, and retire that setup action after the first concrete prayer request is saved.
- Care-, hospital-, or grief-focused first-run profiles should route the first real record to `Add the first person needing care`, keep the setup form person-first because care cases need a person record, and let a named chat prompt such as "Help me open the first care case: Ruth Carter is grieving..." create the person, open the care case, and retire the `data_seed` action.
- In the frontend Assistant tab, `data_seed` should stay visually ahead of generic approval queue items while the workspace has no real people/care/prayer data. Do not let `first_week_plan` or another setup approval displace "Log the first real visitor" from First Pass For Today.
- Once real ministry data exists, calendar suggestions should name the actual pastoral use case. Do not render generic "care block" or "protect time" copy when the priority is a visitor welcome, prayer follow-up, or absence check-in.
- Clear first-person chat messages such as "Help me add the first real person: Ruth Carter..." should create account-scoped `Member` memory directly. If the message includes normal pastoral language like grieving, death, prayer, or contact info, Marge should also create the appropriate care case, private prayer request, contact fields, and note instead of sending the pastor to a blank form.
- First-run church-context answers may mention guests, visitors, or new families as ministry patterns. Do not log those as a specific visitor unless the pastor gives a clear individual visitor update such as "New visitor Morgan Lee came Sunday..."
- First-run follow-up burden extraction should understand natural pastor wording such as "Visitor follow-up and prayer follow-up fall through the cracks," not just form-like "follow-up pain is..." phrasing.
- First-run profile extraction should handle natural pastor wording, not only form-like answers: "I'm a bi-vocational solo pastor," "we have 85 on Sundays," "our church tradition is non-denominational with Baptist roots," "my first priority this month is closing loops with first-time guests," "our stack is Planning Center and Gmail," and "ask me before sending" should save role, size, church voice, ministry priorities, tools, and guardrails without contaminating weekly rhythm or logging generic guest language as a specific visitor.
- First-run profile extraction should also accept command-like answers when Marge is clearly asking that field. For example, "Help me close loops with first-time guests" should save ministry priorities, "Connect Planning Center and Gmail" should save tools, and "Write in a warm and brief tone" should save drafting voice during onboarding instead of being skipped as commands.
- First-run profile extraction should normalize terse one-question answers. For example, "Solo." should save `Solo Pastor`, "72." should save weekly attendance as `72`, "Planning Center and Gmail" should save canonical tools, "Warm, brief, pastoral" should save drafting voice, and "Baptist roots; avoid insider language" should preserve both the tradition and language boundary.
- First-run identity answers should normalize the current question too. For example, if signup only has the church name and Marge asks for the pastor's name, "Pastor Ben." should save `Pastor Ben` without trailing punctuation.
- If the pastor follows a first-run prompt with a concrete visitor, such as "Log the first visitor: Talia Brooks came Sunday...", chat should save the visitor and queue the welcome draft immediately. Keep generic no-name prompts as `data_seed_guidance`; do not create a `Guest` placeholder.
- First visitor welcome drafts should use the actual visitor note when present, such as "asked about kids ministry", and should carry saved drafting voice, church voice/tradition, and approval guardrails in review metadata. Do not leak that metadata into provider MIME/send payloads.
- If chat detects a visitor update but cannot find a real visitor name, it should ask for the name with `intent=visitor_missing_name` and `saved=false`. Do not save `Guest`, `Guest Guest`, or other placeholder visitor records.
- If chat only has a real first name for a visitor or person, keep that name as-is rather than appending a fake last name such as `Guest`.
- Unknown named-person lookup prompts should carry the name forward into the next suggested action, for example `Help me add Marcus Reed as a person.` Do not suggest vague follow-ups such as `Add this person first.` unless the next route only explains what details to capture and does not create a placeholder person.
- If chat saves a private prayer request without a person name, keep it as a request-level private prayer item. Do not label the request as `Pastor`, do not create a fake person, and do not surface dead-end prompts like "Draft a prayer follow-up."; request-level prompts should draft from the private request, explain what to capture, and preserve review/approval boundaries.
- Generic prayer setup prompts such as "Add a prayer request." should return capture guidance or the active prayer `data_seed`; they must not be saved as an empty/private prayer request just because the text contains "prayer request."
- Generic care setup prompts such as "Add a care case." or "Open a care case." should explain that care cases need a real person, category, latest contact, and next pastoral step. Do not create empty care cases or fake people from setup-only prompts.
- Once the first real visitor, member, prayer request, or care case is saved, retire pending `data_seed` setup actions by marking them `executed`. The pastor should not keep seeing "Log the first real visitor" after doing it, but related welcome/follow-up draft actions must remain pending for review.
- Chat prompts such as "Give me my morning briefing" or "What should we handle today?" should route to a dedicated live morning briefing. If the workspace is complete but empty, the briefing should ask for the first real ministry record instead of pretending the desk is clear. If real context exists, it should name the people/review items and keep approval boundaries visible.
- MCP `tell_marge` must preserve that same first-visitor behavior for external LLM clients: concrete first-visitor chat should return `intent=visitor_logged`, saved metadata, action cards, and a reviewable welcome draft.
- Chat draft requests like "Draft a prayer follow-up for Ruth Carter" or "Draft a care follow-up" should create reviewable `email_draft` assistant actions from account-scoped prayer/care memory. They must stay in the approval queue with draft body text and must not send or write externally.
- Prayer and care follow-up drafts should use the actual request/care situation in the body when safe, and should carry saved drafting voice, church voice/tradition, and privacy/approval guardrails in review metadata. Do not reduce a grief, diagnosis, job-loss, or hospital note to generic pastoral copy.
- Approval questions and approval commands are different. "What should I approve first?" should summarize the review queue and leave action statuses unchanged; only explicit commands such as "Approve this draft" should move an action to `approved`.
- Integration setup desk items should include the provider key when it is known. The frontend should start the secure connector setup flow directly from that step instead of forcing a generic navigation detour.
- Chat-triggered connector setup actions should include a provider-aware `setup_step` in the action payload, not just raw connector instructions. The approval modal uses that metadata to show the same secure setup CTA as the first-run desk.
- Setup actions shown from the approval queue should still expose the concrete setup CTA. Do not make first-run setup actions rely only on generic approve/done buttons.
- `first_week_plan` actions should preserve the pastor's actual first-run context: first-record form, recommended connectors/providers, weekly rhythm, drafting voice, and explicit guardrails. Do not collapse this into generic launch-plan copy.
- Frontend draft previews, chat avatars, sidebar identity, and legacy chat payloads should use the current workspace pastor/church context. Hardcoded demo names are acceptable only inside explicit demo-mode sample data or documentation examples.
- Live person/detail panels should not render browser-only draft text as if it is ready ministry work. Use `POST /drafts/` or a reviewable assistant action for live drafts; keep local template previews limited to explicit demo mode.
- Briefings, assistant greetings, and message drafts should avoid doubled titles. Draft templates expect `pastor_display_name(...)` from `app/services/marge.py`; greetings that include `"Pastor {name}"` should strip a saved leading `"Pastor "` before formatting.
- Draft desk approval buttons should create or open real `email_draft` assistant actions with the exact draft body. Avoid approval-looking buttons that only toast or preview.
- Logging a live visitor should queue a real `email_draft` welcome action for pastor review when possible. The action must include recipient/body payload and remain unsent until approved/executed through the action model.
- Email/inbox UI should reflect actual connector state and synced `ConnectedContextItem` email rows. Avoid copy that says Gmail/Outlook are only future work when the backend has secure setup, sync, and review actions.
- Calendar UI should reflect actual connector state and synced `ConnectedContextItem` calendar rows. Synced meetings should open their meeting-prep/review actions instead of remaining hidden behind generic calendar guidance. Live scheduling proposals should queue or open real `calendar_block` assistant actions; do not use browser-only scheduling draft previews for approval-looking work.
- Integrations UI should expose safe credential verification before sync. `Check credentials` must call `/assistant/integrations/{provider}/verify`, show only non-secret identity/status fields, and make clear that it did not sync ministry data or queue actions. Backend sync must also reject unverified connectors; do not treat OAuth callback or API-key save as sync-ready until `verified_at` is present on the current credential/status.
- Assistant chat should also understand natural-language credential checks such as "Check Planning Center credentials before syncing." This must call the same no-sync verification path, report that no people/email/calendar/attendance data was imported, and avoid queuing review actions.
- If a pastor asks chat to sync a connected but unverified provider, Marge should run the safe credential check first and stop there. The reply should say nothing was imported or queued yet and ask for an explicit follow-up sync request after verification.
- MCP `sync_integration` should preserve that same boundary for external LLM clients. If the backend rejects sync because credentials have not been checked, the MCP tool should call the no-sync verification endpoint, import nothing, queue nothing, and ask for an explicit follow-up sync.
- API-key connector setup must not invite pastors/admins to paste keys until `MARGE_ENCRYPTION_KEY` is present and valid. Invalid encryption config should appear as missing secure token storage, and the credential endpoint should reject saves instead of failing after a secret is submitted.
- `POST /assistant/actions/prepare` should queue setup work too. Complete profiles get connector setup plus a `first_week_plan`; incomplete profiles get a `profile_question` action. Keep these account-scoped and non-destructive.
- `mcp_server/server.py` sends `MARGE_ACCOUNT_TOKEN` as `X-Marge-Account-Token` when configured. Keep all MCP tools account-scoped; do not add MCP writes that bypass the assistant/account boundary.
- The MCP server exposes assistant-native tools for the same first-run workflow as `/app`: assistant desk, persisted chat history, ministry profile, connector status/setup/sync, connected context, approval queue, prepare/approve/execute/skip, and chat. Prefer these tools over direct CRUD when an LLM client needs to act like Marge rather than merely edit records.
- MCP `tell_marge` responses should preserve the assistant reply plus intent/saved/mode metadata, profile completion, returned action cards, and suggested prompts. If an LLM client loses those cards, first-run setup feels like generic chat instead of a working secretary.
- `scripts/smoke_mcp_first_run.py` is the smoke gate for external LLM clients driving first-run onboarding through MCP. It should stay aligned with `scripts/smoke_first_run.py` but focus on what the MCP client can see and act on.
- OAuth consent and writeback permission are separate. External writes require `/assistant/policies/{provider}.write_enabled`, per-action approval, and an allowed action type.
- `POST /assistant/signup` returns a one-time owner user token. `POST /assistant/sessions` exchanges an active user token for a shorter-lived revocable session token and sets it as an HttpOnly browser cookie. The static frontend should immediately make that exchange, remove any legacy `margeAccountToken` from `localStorage`, and rely on the cookie for same-origin API calls. API/MCP clients may also send that session token as `X-Marge-Account-Token`. Core API routes scope pastor profiles, members, visitors, care cases, prayer requests, briefing data, assistant chat history, assistant actions, connected context, audit rows, integration status, OAuth state, encrypted credentials, connector policies, sessions, and workspace users to that church when present. Requests without a token use legacy unscoped rows only; invalid tokens must not fall back to singleton/shared data.
- Workspace user roles are `owner`, `admin`, `pastor`, `staff`, and `viewer`. Legacy account tokens resolve as `owner` for old local workspaces. Require owner/admin for connector setup, user invites/user deactivation, and writeback policy changes; require pastor/admin/owner for approval, execution, audit log, synced-context, provider sync, pastoral briefing, pastoral drafts, member detail/notes, care cases, prayer requests, and member/visitor write surfaces. Staff tokens may read the live desk and basic member/visitor directories, but the desk must hide approval-queue items for staff and staff must not mutate pastoral records or execute external writes. Do not allow the final active workspace owner to be deactivated or demoted.
- The ministry profile controls pastor/church identity, drafting voice, weekly rhythm, guardrails, and first-run personalization. Keep profile updates restricted to pastor/admin/owner; staff/viewer tokens may not change the pastor's ministry context.
- Assistant action queue endpoints can contain sensitive draft, synced inbox, calendar, and pastoral follow-up payloads. Keep list/get/create/prepare/approve/execute/skip restricted to pastor/admin/owner tokens; staff/viewer tokens may read the live desk but must not inspect or mutate the approval queue directly.
- `POST /assistant/users/invite` may send a one-time invite link by SMTP when configured, or write to `MARGE_INVITE_EMAIL_OUTBOX` in tests. The raw token may appear in the immediate API response for local/manual sharing and in the delivered invite, but it must never be stored, logged, or included in audit payloads.
- `POST /assistant/login-links/request` and `/assistant/login-links/exchange` provide passwordless sign-in for existing active workspace users. The first-run frontend should expose this as a returning-user sign-in link form before a workspace session exists. Login-link tokens are short-lived, single-use, hashed at rest, exchanged into normal revocable sessions, and must never appear in audit payloads or persisted chat. Keep request responses generic and preserve the resend cooldown so the endpoint does not reveal account existence or flood inboxes.
- OAuth state and encrypted OAuth credentials should be user-scoped when setup starts from a user token. `IntegrationOAuthState.user_id` and `IntegrationCredential.user_id` prevent one pastor/admin user from syncing or writing through another user's Google/Planning Center/Microsoft consent. Legacy account-level credentials with `user_id=NULL` remain valid for old local workspaces only.
- `POST /assistant/integrations/{provider}/verify` should make only a minimal provider identity/config call, audit `integration.verified`, and never import ministry data or queue actions. Use it to prove credentials before sync.
- `scripts/verify_live_integrations.py` calls the live verify endpoint against `MARGE_API_URL` using `MARGE_ACCOUNT_TOKEN`. It is the operator-facing credential health check and should remain read-only/no-sync. It snapshots connected context and assistant actions before/after verification so credential checks cannot quietly import data or queue work. Production/pilot checks should include `--require-live-provider`; MCP alone is not proof that a pastor's live tools are connected.
- `scripts/verify_production_config.py` should pass before exposing real pastoral data. It accepts `--env-file` for checking a candidate production env file without replacing local `.env`. It does not contact providers; it catches unsafe deployment settings that would undermine secure connectors or workspace isolation.
- `scripts/verify_deployment_bootstrap.py` checks the public no-write bootstrap path: `/health`, `/assistant/config`, `/app`, first-run workspace copy, and runtime strict workspace-token mode. Use `--allow-relaxed-account-tokens` only for local development.
- `scripts/verify_first_run_workspace.py` creates a disposable workspace, exchanges a session token, completes onboarding through chat, verifies first-record setup, logs a visitor through chat, queues a welcome draft, and confirms visitor follow-up lookup routes correctly. It writes data; local runs clean up automatically, while remote runs require `--allow-remote-write`.
- `scripts/verify_pilot_readiness.py` is the one-command pilot gate. It runs production config, public deployment bootstrap, migration drift, and no-sync live connector verification; it should fail unless at least one external provider verifies successfully.
- `.env.production.example` and `docs/PRODUCTION_READINESS.md` are the production handoff artifacts. Keep them aligned with connector requirements, OAuth redirect paths, migration expectations, and the live no-sync credential verification flow.
- `MARGE_REQUIRE_ACCOUNT_TOKEN=true` changes missing-token behavior for scoped API routes from legacy unscoped access to `401`. Keep this enabled for any non-local exposure; `/assistant/signup` and OAuth callbacks are the intended unauthenticated exceptions.
- `GET /assistant/config` is intentionally public and tells the frontend whether account tokens are required. Use it for first-run bootstrapping only; never include church data or secrets in that response.
- Before a workspace exists, frontend explainer prompts such as secure connections, learning plan, and after-signup should be answered locally. Do not send unauthenticated first-run chat to protected assistant routes.
- Before a workspace exists, the live frontend must not request or render legacy singleton profile, chat, people, action, audit, policy, or connected-context rows. The pre-signup shell should never show an old church name or legacy interview question such as a seeded/demo church context; scoped workspace requests should start only after signup/login establishes a session-backed account.
- Before a workspace exists, top-level live header actions should invite workspace setup and learning, not unsaved care/person writes. Switch back to `Add Care` and `Add Person` only after a real workspace or explicit demo context exists.
- Before a workspace exists, live page headers and the detail toggle should stay in setup-first language across People, Care Board, Visitors, Prayer, Email, Calendar, Integrations, Agent Tools, and Plan. Do not show save/log/search/approval workflow copy until a real workspace or explicit demo context exists.
- Before a workspace exists, live People, Care Board, Visitors, Prayer, and Today quick actions should show workspace setup/learning CTAs instead of write buttons such as `Add Person`, `Open Care Case`, `Log Visitor`, or `Add Prayer Request`. Those write actions can appear after workspace creation or in explicit demo mode.
- Account-scoped `auto` mode must not fall back to demo people. Once a real workspace token is present, empty live data should produce live empty states plus setup/proactive actions; demo stories require explicit `mode=demo`.
- Care category enums in `app/models.py` are `hospital`, `crisis`, `grief`, and `general`. Map agent-friendly labels like `counseling`, `financial`, or `other` before writing care cases unless the model is expanded.
- `ai_provider_name()` makes a real LLM call. Avoid calling it repeatedly in hot paths if latency or cost matters.
- Demo mode returns dict-shaped items; live mode returns ORM-derived schemas. Keep helper functions tolerant of both if shared with rendering code.
- SQLite DB files (`marge.db`, `*.db`) are ignored and should stay local.

## Engineering Guidelines

- Keep Marge's product identity intact. A technically correct change that makes her feel like generic CRM software, a generic executive assistant, or a replacement ChMS is a product regression.
- Prefer small, explicit changes over broad refactors. This repo is still early and easy to over-abstract.
- Update `app/marge_voice.py` for reusable voice/template copy instead of scattering tone strings.
- Keep API responses practical for the static frontend and MCP server. Backward compatibility matters because LLM clients may depend on field names.
- When adding endpoints, register routers in `app/main.py` and include clear Pydantic request/response models.
- When changing schema models, add an Alembic migration under `migrations/versions/` and verify it against SQLite locally. Consider Postgres compatibility because `DATABASE_URL` may point at Postgres in production.
- `app/database.py` still has local startup table creation and a small SQLite compatibility patch for old local databases. Keep that as dev convenience only; production should run `alembic upgrade head` with `MARGE_AUTO_CREATE_SCHEMA=false`.
- When adding AI behavior, provide a deterministic fallback so Marge remains usable without API keys.
- For frontend changes, maintain the warm, quiet, pastoral feel. Avoid loud dashboards, corporate SaaS phrasing, and visual clutter.
- For landing copy, preserve the "built by a pastor for pastors" credibility and keep the primary promise concrete.
- When touching MCP, test against the running FastAPI app and keep tool responses concise, pastoral, and action-oriented.

## Validation Checklist

For backend changes:

```bash
python -m compileall app scripts mcp_server
uvicorn app.main:app --reload
curl http://localhost:8000/health
curl "http://localhost:8000/briefing/today?mode=demo"
.venv/bin/python scripts/smoke_first_run.py
```

For data/briefing changes:

```bash
python3 scripts/seed_demo_data.py --force
python3 scripts/morning_briefing.py
```

For frontend changes:

- Run `.venv/bin/python scripts/smoke_frontend_static.py`.
- Run the FastAPI app.
- Open `http://localhost:8000/app`.
- Check demo mode and live mode.
- Check mobile width.
- Verify text does not overlap or get clipped.

For landing changes:

- Open `landing/index.html` directly for static rendering.
- If testing subscription behavior, deploy/run the Vercel function with Beehiiv env vars. Do not use real lists unintentionally.

For MCP changes:

- Start the FastAPI app.
- Run `MARGE_API_URL=http://localhost:8000 python mcp_server/server.py` from an MCP client or a small local harness.
- Verify `get_morning_briefing`, member search, note creation, prayer creation, and contact logging against expected backend parameters.

If you cannot run a validation step, say exactly what was not run and why.

## Future Direction From The Spec

Near-term product priorities from `spec.md`:

- Prove the morning briefing loop.
- Get 10 paying pastors.
- Build Rock RMS import first.
- Add member database, birthday/anniversary tracking, visitor follow-up, absence detection, outreach drafts, prayer intake, onboarding, billing, and voice calibration.
- Keep launch pricing around Solo `$29/month` and Team `$49/month`.
- Distribution is relationship-led: Nathan's pastoral network, conferences, denominational contexts, and small-church communities.

Long-term moat:

- Relationship graph.
- Pastor-specific voice.
- Proactive intelligence.
- Deep pastoral care history.
- Low-friction pricing for solo pastors.

Before building a feature, ask whether it strengthens that moat and helps a pastor care for a real person this week.
