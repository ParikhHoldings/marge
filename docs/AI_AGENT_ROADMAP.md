# AI Agent Roadmap

Marge should serve human pastors today and AI agent users tomorrow. The agent plan is broader than pastoral care, but it must stay faithful to the product posture: Marge is an AI pastoral secretary and ministry staff helper, not a replacement church management system.

## Product Thesis

The pastor should be able to open Marge and say:

- "What needs my attention today?"
- "Draft replies to the three church emails that need pastoral judgment."
- "Find a time this week to visit Janet and protect sermon study time."
- "Who visited Sunday and has not received a note?"
- "Log that I visited Maria and prayed with her after surgery."
- "Check Planning Center for volunteers who have not served in 60 days."
- "Pull Rock notes for the people on today's care list."

The answer should be brief, pastoral, and grounded in Marge's records or connected systems. Marge can draft, summarize, prioritize, queue, and log. Sending messages, changing privacy, deleting records, or writing into external systems requires explicit confirmation.

## Why Chat First

Pastors do not primarily need another dashboard. They need help deciding what to do next across a fragmented day. Research and practitioner reports point to the same set of problems: stress, loneliness, overwork, time management, interruptions, fast email/text expectations, and costly context switching between finance, care, sermon, meetings, and emergencies.

The chat-first surface should be the home screen because it lets the pastor delegate in natural language, while deeper screens remain available for inspection:

- Assistant: primary place to ask, decide, draft, approve, and log.
- Today: prioritized people and care briefing.
- People: member memory and relational history.
- Care: active care board.
- Visitors: guest follow-up.
- Prayer: requests and follow-up.
- Email: ministry inbox triage and draft queue.
- Calendar: visit blocks, meeting prep, scheduling proposals.
- Integrations: Rock RMS, Planning Center, Breeze, Google/Outlook, and MCP.
- Agents: technical surface for external AI clients.

## Agent Architecture Principles

- Tool-first under the hood. Chat interprets intent, then calls explicit tools.
- Human approval for high-impact actions. Drafting and summarizing are safe; sending, deleting, public sharing, privacy changes, and external writeback need confirmation.
- Stable IDs in every actionable response. Agents need record IDs for follow-up.
- Privacy flags travel with the data. Private prayer and care context should never be stripped from downstream responses.
- Narrow tools beat broad database access. Avoid SQL-shaped agent tools.
- Deterministic fallbacks keep Marge useful without LLM keys.
- Every agent action should leave an audit trail with actor, source, proposed change, confirmation state, and external system target.

## Current Agent Surface

The repo includes `mcp_server/server.py`, a stdio MCP server that wraps the FastAPI app.

Current tools:

- `get_morning_briefing`
- `list_members`
- `log_visitor`
- `log_care_event`
- `list_care_cases`
- `mark_contacted`
- `add_prayer_request`
- `list_prayer_requests`
- `add_member_note`
- `draft_message`
- `tell_marge`

Current readiness:

- MCP member search aligns with backend parameters.
- Draft tools route through `/drafts/`.
- Care category labels are mapped into current backend enums.
- Tool responses include concise human summaries and IDs where useful.

## Long-Term Action Model

Marge should eventually treat work as an action queue instead of loose chat replies.

Action states:

- `drafted`: Marge prepared content or a plan.
- `needs_review`: pastor must inspect before it can happen.
- `approved`: pastor approved the exact action.
- `executed`: external send/writeback completed.
- `logged`: Marge saved a local pastoral record.
- `skipped`: pastor intentionally dismissed the action.
- `failed`: external system or validation blocked execution.

Action types:

- `message_draft`: email, text, or pastoral note draft.
- `calendar_proposal`: meeting, visit block, counseling appointment shell, or protected work block.
- `care_log`: local care contact or case update.
- `prayer_update`: prayer status, follow-up note, privacy-safe bulletin candidate.
- `visitor_followup`: guest email/text draft and follow-up state.
- `integration_read`: query external data from Rock, Planning Center, Breeze, Google, or Outlook.
- `integration_write`: confirmed writeback to external system.
- `daily_brief`: proactive summary generated on schedule.

Minimum action record fields:

- `id`
- `church_id`
- `created_by`
- `source` (`web`, `mcp`, `cron`, `email`, `calendar`, `integration`)
- `type`
- `status`
- `title`
- `summary`
- `payload`
- `privacy_level`
- `external_targets`
- `confirmation_required`
- `approved_by`
- `executed_at`
- `error`

## Secretary Capabilities

### Email

Marge should not become a full email client. She should become the pastor's ministry-aware draft desk.

Early capabilities:

- Surface ministry emails that need a reply.
- Summarize threads into pastor-relevant context.
- Draft replies in Marge's pastoral voice.
- Connect emails to people, care cases, visitors, or prayer requests.
- Queue drafts for review.
- Log meaningful pastoral follow-up after approval.

Later capabilities:

- Gmail/Outlook OAuth.
- Label-based ministry inbox scanning.
- Approved-send flow.
- Attachment and newsletter-safe public content handling.
- Routing rules for "pastor handles," "admin handles," and "ignore."

Hard rule: Marge may draft email without confirmation, but she must not send email until the pastor approves the exact text and recipient.

### Calendar

Marge should protect time, propose visits, and make scheduling less manual.

Early capabilities:

- Show today's pastoral schedule.
- Suggest care visit blocks.
- Prepare meeting briefs from member/care context.
- Draft "could we meet Tuesday?" messages.
- Flag overfull days and unplanned care commitments.

Later capabilities:

- Google Calendar / Outlook calendar read.
- Event creation after confirmation.
- Counseling appointment shells without session notes.
- Smart blocks for sermon prep, hospital visits, rest, and admin catch-up.
- Calendar-to-care logging prompts after events.

Hard rule: counseling content is not stored; only scheduling and minimal follow-up metadata are allowed.

### Church System Integrations

Marge should integrate with existing church systems rather than replace them.

Rock RMS:

- Read members, families, attendance, groups, notes where permitted, and care-relevant attributes.
- Write back confirmed care contacts or notes if configured.
- Use Rock's API resources rather than scraping.

Planning Center:

- Use the REST/JSON API.
- Start with People, Calendar, Groups, and Services contexts.
- Read volunteer/service participation for pastoral awareness.
- Avoid becoming a replacement scheduler; propose and draft instead.

Breeze and other ChMS:

- Treat as pluggable connectors behind a common people/attendance/events interface.
- Normalize only what Marge needs: person identity, contact info, household, attendance, groups, events, and ministry role.

Integration posture:

- External systems remain systems of record.
- Marge maintains local memory only for assistant-specific context, drafts, actions, audit, and pastoral notes that a church chooses to store in Marge.
- Every writeback is opt-in per church and per action class.

## API Roadmap

Near-term REST/MCP resources:

- `/briefing/today`
- `/members`
- `/members/{id}`
- `/members/{id}/notes`
- `/care`
- `/care/{id}/contact`
- `/care/prayers`
- `/visitors`
- `/drafts`
- `/chat`

Next REST/MCP resources:

- `/assistant/desk`
- `/assistant/signup`
- `/assistant/account`
- `/assistant/profile`
- `/assistant/actions`
- `/assistant/actions/{id}/approve`
- `/assistant/actions/{id}/execute`
- `/assistant/actions/{id}/skip`
- `/assistant/policies`
- `/assistant/policies/{provider}`
- `/email/threads`
- `/email/drafts`
- `/calendar/events`
- `/calendar/proposals`
- `/integrations`
- `/integrations/{provider}/start`
- `/integrations/{provider}/callback`
- `/integrations/{provider}/sync`
- `/assistant/connected-items`
- `/audit-log`

Tool contracts should stay boring and predictable. Marge's warmth belongs in summaries, drafts, and briefings; the API should be explicit and stable.

## Permissions And Safety

Roles:

- `owner`: church account owner and billing/admin.
- `lead_pastor`: full pastoral access.
- `pastor`: pastoral access scoped by role.
- `care_team`: care records where assigned.
- `admin`: email/calendar/admin workflows without private prayer/counseling content unless granted.
- `viewer`: read-only limited access.

Policy checks:

- Private prayer requires pastoral permission.
- Counseling appointments show scheduling metadata only.
- Public bulletin generation uses public-only prayer/care content.
- Email/calendar connectors require provider OAuth and per-user consent.
- OAuth state must be stored server-side, consumed once, and paired with encrypted token storage.
- OAuth consent does not imply writeback permission; church connector policy must allow writes.
- External writeback requires church-level provider permission and per-action approval.

Audit events:

- Action drafted.
- Action approved.
- Email sent.
- Calendar event created.
- External record written.
- Privacy changed.
- Record deleted or resolved.
- Sensitive record viewed by agent.

## Milestones

### Phase 1: Reliable Assistant MVP

- Chat-first `/app` home screen.
- Daily desk: care, visitors, prayer, email drafts, calendar blocks, approval queue.
- Existing CRUD and draft flows stay working.
- MCP tools match backend parameters.
- Deterministic local behavior in demo mode.

### Phase 2: Agent-Safe Work Queue

- Add persisted assistant actions.
- Add preview/approve/execute endpoints.
- Add audit log.
- Route all draft/send/write flows through the action model.
- Add per-action privacy level and external target metadata.

Current MVP note: Marge now has persisted assistant actions plus approve, execute, and skip endpoints. Google Workspace sync stores recent Gmail/Calendar context in normalized connected items, Microsoft 365 sync stores Outlook mail/calendar context, synced inbox triage can execute into a separate reply-draft review action, Google Workspace execution exists for approved actions with complete Gmail draft or Calendar event payloads, Microsoft 365 execution exists for approved Outlook draft and Outlook calendar-event creation, `/assistant/audit-log` records connector/action metadata, `/assistant/policies` separates OAuth consent from writeback permission, and pastor/admin/owner role gates are locally smoke-covered for approval, sync, audit, connected-context, pastoral briefing, and write surfaces. Production writeback still needs a real HTTPS deployment, live credential verification against at least one provider tenant, and broader provider-specific write execution beyond the current Google/Microsoft draft and calendar paths.

Chat grounding note: inbox/calendar chat now reads from synced connected items. Follow-up commands such as drafting a reply to a synced email or preparing a synced meeting create approval-queue actions.

Local memory note: assistant chat now saves structured pastoral updates from natural language and retrieves them later. It can save ministry profile context, visitor records, private prayer requests, care cases, care contacts, and member notes. It can also answer named-person, prayer follow-up, care follow-up, and visitor follow-up questions from account-scoped local memory. When Marge understands an update but cannot safely match the person, she queues a review action instead of writing to a potentially wrong record.

Onboarding plan note: `/assistant/desk` now returns `setup_steps`, which turn missing profile fields and saved tool names into the next best setup actions. The frontend renders these as the "Next Best Setup" path so first-run pastors see what Marge needs and which existing systems to connect next.

Connector setup note: setup steps now include provider keys for known tools so first-run buttons can open secure setup directly instead of sending the pastor through a generic integrations detour.

Chat connector setup note: when chat queues connector setup, the action payload should include both `integration_setup` details and a provider-aware `setup_step`. This keeps the approval queue actionable instead of becoming a static instruction card.

Approval modal note: setup actions surfaced in the approval queue should keep their concrete CTA visible. Data-seed actions should open the first relevant record form, and connector setup actions should start secure setup, rather than only asking for approve/done.

First-run chat note: suggested setup prompts should be action-specific. When the next setup step is first-record setup, chat should explain which first record to add and why, using the pastor's stated follow-up burden.

First-run priority note: a complete live workspace with no people/care/prayer data should route priority and ministry-draft questions into `data_seed_guidance`. Marge should ask for the first real visitor, prayer request, or person before claiming no one needs attention.

Frontend personalization note: any frontend-generated draft preview or legacy chat payload should use the current workspace pastor/church context. Demo names should not leak into live first-run workspaces.

Draft approval note: the email draft desk should queue real `email_draft` assistant actions with the exact draft body when the pastor asks to review/approve. Approval language must correspond to an inspectable approval queue item.

Synced inbox UI note: the Email screen should show synced `ConnectedContextItem` email rows and their review actions when present, plus secure setup/sync CTAs from connector status when absent. Do not leave the email surface as roadmap-only copy.

Calendar approval UI note: live scheduling proposals should create or open reviewable `calendar_block` assistant actions with the concrete recommendation and approval guardrail. Browser-only scheduling draft previews are acceptable only as explicit demo previews, not as live approval-looking work.

Synced calendar UI note: the Calendar screen should show synced `ConnectedContextItem` calendar rows and open their meeting-prep/review actions when present, plus secure setup/sync CTAs from connector status when absent.

Ministry memory note: assistant chat now reflects saved first-run context back to the pastor instead of only saying a field was saved. Replies name the role/church, follow-up pressure, first priority, personal support style, tools, voice, and guardrails Marge heard, then connect that memory to secure setup, drafting, prioritization, proactive nudges, and approvals.

Approval queue note: `/assistant/actions/prepare` now uses setup steps too. Complete profiles queue connector setup and a first-week launch plan; incomplete profiles queue a profile-question action. This gives brand-new accounts useful work even before real people data is imported.

### Phase 3: Email And Calendar

- OAuth start/callback plumbing and encrypted token storage.
- Gmail read + draft creation.
- Google Calendar read + proposed event creation.
- Outlook mail/calendar read, approved Outlook draft creation, and approved Outlook calendar-event creation.
- Human approval before send/create.
- Post-event "should I log this care contact?" prompts.

### Phase 4: Integration Layer

- Rock RMS read sync, then opt-in writeback.
- Planning Center People and Calendar read sync.
- Planning Center Services/Groups awareness.
- Breeze connector.
- Connector health, sync logs, and field mapping UI.

### Phase 5: Multi-Church, Multi-Staff

- Tenant/church identity.
- Role-based permissions.
- Per-user OAuth connectors.
- Staff handoff and assignment.
- Church-level policy configuration.

Current MVP note: signup now creates a lightweight church workspace, an owner user token, and a token-scoped pastor profile. Role-scoped user tokens can be created and deactivated for owner/admin/pastor/staff/viewer access, with a guard against removing the final active owner. Active user tokens can mint shorter-lived revocable session tokens for ongoing API use; browser sessions also receive the session token as an HttpOnly same-origin cookie. Existing active users can request passwordless login links that are short-lived, single-use, hashed at rest, and exchanged into normal revocable sessions. Tokens scope members, visitors, care cases, prayer requests, live briefings, assistant actions, connected context, audit rows, integration status, OAuth state, encrypted credentials, writeback policies, sessions, and workspace users. Connector setup and policy changes are owner/admin-only; approval/execution/sync/audit/connected-context surfaces require pastor/admin/owner. OAuth state and encrypted OAuth credentials are user-scoped for new user-token setup, so a second pastor/admin must grant their own Google/Planning Center/Microsoft consent before syncing or writing through that provider. API-key connectors such as Breeze and Rock can use encrypted workspace credentials instead of only server-wide environment secrets. A connector verify endpoint now proves credentials with minimal provider calls before syncing ministry data, and local readiness checks reject MCP-only, unverified, or secret-leaking verification results. Local smoke coverage now exercises Google Workspace plus Planning Center, Microsoft 365, and Breeze with mocked provider responses, including encrypted workspace API-key storage, synced context, review-action creation, approved Outlook draft writeback, and approved Outlook calendar writeback. Alembic now tracks the baseline schema so production deploys can run migrations instead of relying on startup table creation. Workspace invites can be delivered by SMTP or a file outbox in tests, and invite/login links are exchanged into session cookies by the frontend. The pilot readiness gate now includes production config, public bootstrap, migration drift, a disposable first-run workspace rehearsal, and no-sync live connector verification. Production still needs OIDC identity, broader per-user OAuth UX, a real HTTPS deployment with strict workspace-token mode, SMTP delivery, and live credential verification against at least one actual provider tenant.

Real workspace note: account-scoped `auto` mode now stays live even when there is no people data. Demo people should not appear or be queued inside a pastor's real workspace unless the pastor explicitly switches to demo mode.

Live-empty setup note: a complete profile with no people/care/prayer records should produce a `data_seed` setup action. Marge should ask for one real person tied to the pastor's stated follow-up burden before pretending the desk is clear, and route the first action toward the relevant record type when the burden names visitors, new families, or prayer.

Setup completion note: when the first real visitor, member, prayer request, or care case is saved through chat, CRUD, or MCP, pending `data_seed` actions should be marked `executed`. Only the setup prompt retires; welcome and follow-up drafts stay pending for pastor review.

### Phase 6: Proactive Ministry Intelligence

- Weekly care digest.
- Missed follow-up recovery.
- Volunteer and group participation signals from integrations.
- Burnout-aware workload nudges for the pastor.
- Public-only bulletin/newsletter assistance.

## Non-Goals

- Do not replace Rock RMS, Planning Center, Breeze, or other ChMS products.
- Do not build finance, giving, bookkeeping, or HR systems.
- Do not become sermon research or theological commentary software.
- Do not automate pastoral judgment.
- Do not send ministry communication without explicit approval.
- Do not store counseling content.

The long-term version of Marge should feel like hiring a competent pastoral secretary who knows the church, remembers the people, keeps the day organized, drafts the hard first pass, and waits for the pastor's judgment before acting.
