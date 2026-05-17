# Pastoral Secretary UX

Marge should feel less like "church software" and more like a trusted ministry assistant sitting beside the pastor at the start of the day.

## Research Signals

The current product direction is supported by pastor pain points that show up repeatedly in research and practitioner discussion:

- Lifeway's Greatest Needs of Pastors study found time management, work/home balance, avoiding over-commitment, and consistent rest are major self-care challenges for pastors. Source: https://research.lifeway.com/wp-content/uploads/2022/04/The-Greatest-Needs-of-Pastors-Phase-2-Quantitative-Report-Release-5.pdf
- Barna reported in March 2022 that 42% of pastors had considered quitting full-time ministry in the prior year, with stress, loneliness/isolation, and political division rising to the surface. Source: https://www.barna.com/research/pastors-quitting-ministry/
- A Wesleyan workload report describes pastoral work as costly context switching with few easy hand-offs, unexpected events, fast email/call expectations, and a "daily onslaught of emails." Source: https://cdn.resources.wesleyan.org/wesleyanrc/wp-content/uploads/FIM-Report-Workload.pdf
- Planning Center's public API docs confirm it exposes a REST API for church account data, which supports Marge's "integrate, do not replace" posture. Source: https://api.planningcenteronline.com/docs/overview/getting-started
- Rock RMS exposes API resources for integration, supporting the same connector posture. Source: https://community.rockrms.com/api-docs/
- Planning Center's integration directory already positions integrations as a way to fill gaps and keep systems in sync. Source: https://www.planningcenter.com/integrations

## Product Answer

Yes, the primary interface should be chat-first.

The pastor's problem is not that they lack a place to click. It is that ministry work arrives as fragments: a text about a hospital visit, a visitor card, an email from a worried parent, a meeting request, a prayer update, a ChMS attendance pattern, and a Sunday deadline. A chat-first assistant lets the pastor delegate the human-shaped problem first:

> "Marge, what needs my attention before staff meeting?"

The deeper screens still matter, but they should be secondary:

- Chat is the command center.
- Today is the prioritized briefing.
- People is the memory.
- Care is the board.
- Visitors is follow-up.
- Prayer is the spiritual care queue.
- Email is the draft desk.
- Calendar is the schedule and meeting-prep desk.
- Integrations is the connector health and system-of-record map.

## Use Cases

### Morning Desk

Marge opens with:

- A short pastoral greeting.
- Today's three to five priorities.
- Email drafts needing approval.
- Calendar conflicts or suggested visit blocks.
- People who need care, prayer, or follow-up.
- Integration status.

The screen should answer, "What do I need to do next, and what can Marge do for me?"

### Ministry Inbox

Pastor intent:

- "Draft a reply to the visitor who emailed after Sunday."
- "Which emails need my actual attention?"
- "Turn this email into a care note."
- "Draft a kind no for this facility request."

Marge behavior:

- Summarize the thread.
- Link it to a person, visitor, care case, or prayer request when possible.
- Draft a reply.
- Keep a review/approval queue.
- Log pastoral context after approval.

### Calendar And Visits

Pastor intent:

- "Find time to visit Janet this week."
- "Protect sermon prep on Thursday."
- "Prepare me for the Smith counseling appointment without showing counseling content."
- "Schedule a coffee with the new visitor."

Marge behavior:

- Suggest blocks, not force bookings.
- Draft scheduling messages.
- Create events only after approval.
- Prompt after a visit: "Should I log this as a care contact?"

### Care And Prayer

Pastor intent:

- "Who has not been checked on?"
- "Show unanswered private requests older than two weeks."
- "Draft a prayer follow-up."
- "Mark David contacted after the hospital visit."

Marge behavior:

- Keep care visible without shaming the pastor.
- Preserve privacy.
- Draft warm messages.
- Save factual notes and contact logs.

### ChMS-Aware Assistant

Pastor intent:

- "Who has missed three Sundays?"
- "Which volunteers have not served in 60 days?"
- "Show first-time visitors from Planning Center."
- "Pull Rock family context before I call."

Marge behavior:

- Read from Rock RMS, Planning Center, Breeze, and similar systems.
- Normalize only the fields Marge needs.
- Treat external systems as source of truth.
- Write back only after church-level configuration and action-level approval.

## UI Model

### Primary Layout

The ideal Marge home screen has three zones:

- Left: small navigation rail for Assistant, Today, People, Care, Visitors, Prayer, Email, Calendar, Integrations, Agents, and Plan.
- Center: chat-first assistant thread, daily briefing, and suggested actions.
- Right: "Today's Desk" with email drafts, calendar suggestions, follow-ups, approval queue, and connector health.

### Interaction Rules

- The pastor can type anything into the assistant composer.
- Suggested prompts are concrete and ministry-shaped.
- Any item Marge proposes can become an approval action.
- Deeper screens are one click away for inspection or manual edits.
- The right desk should feel like the secretary's stack of folders, not analytics.

### Copy Rules

Use:

- "I can draft that for you."
- "This needs your review before anything is sent."
- "Janet has not had a contact logged since May 2."
- "Planning Center has three new people from Sunday."

Avoid:

- "Workflow execution pending."
- "Engagement touchpoint."
- "Contact object synced."
- "Automated pastoral intervention."

## Launchable UI Slice

The current MVP should show the direction even before real email/calendar connectors exist:

- Assistant as the default view.
- Today remains the care briefing.
- Email view shows a review queue and approved-send posture.
- Calendar view shows visit blocks, meeting prep, and scheduling proposals.
- Integrations view explains Rock RMS, Planning Center, Breeze, Google Workspace, Outlook, and MCP.
- Existing People, Care, Visitors, and Prayer views continue to work.

This is the correct direction because it keeps Marge out of the "another database" trap. The database views are useful, but the product promise is that Marge helps the pastor move through the day.
