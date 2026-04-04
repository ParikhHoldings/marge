# clawchief → Marge Adaptation Plan

## Why this matters

`clawchief` is not valuable because it is a founder OS.
It is valuable because it separates **policy**, **state**, and **execution**, then treats passive inputs as an **ingestion pipeline** that continuously produces ranked action.

That maps almost perfectly onto Marge.

Marge should not merely store congregation data.
Marge should continuously convert church signals into pastoral action.

---

## The core pattern we are adopting

From clawchief, Marge should inherit these architectural ideas:

1. **Policy files separate from runtime logic**
2. **One canonical live operational state**
3. **A heartbeat that maintains state quietly**
4. **Cron jobs for recurring orchestration**
5. **Ingestion of passive notes into active tasks**
6. **An MCP layer that exposes workflows, not raw CRUD**

We are not adapting founder workflows.
We are adapting the operating model.

---

## Domain translation

| clawchief concept | Marge equivalent |
|---|---|
| founder priorities | pastoral care priorities |
| business-development follow-up | visitor and member follow-up |
| meeting-note ingestion | pastoral note ingestion |
| canonical task system | canonical care queue |
| auto-resolver | care auto-resolution policy |
| executive assistant heartbeat | care heartbeat |
| inbox/calendar signal processing | attendance/prayer/note/care signal processing |

---

## Marge operating model

### Inputs

Marge should treat these as live signals:

- first-time visitor submissions
- attendance drop-offs and absence streaks
- birthdays and anniversaries
- prayer requests
- hospitalizations and crisis notes
- counseling scheduling activity
- pastoral notes from visits or conversations
- direct free-text inputs from pastor
- eventually SMS, email, church forms, and ChMS sync events

### Processor

Marge classifies each signal and decides:

- is this informational or actionable?
- should it become a care case?
- should it create a follow-up task?
- should it affect urgency?
- should it appear in tomorrow's morning briefing?
- should it create a nudge?
- can it be auto-resolved or does it need pastor confirmation?

### Outputs

- morning briefing sections
- active care queue
- drafted texts/emails/cards
- visitor follow-up steps
- prayer follow-up reminders
- pastoral nudges
- archived completions

---

## What we are creating in the repo

### 1. `policy/care-priority-map.md`
Defines what rises to the top of the morning briefing.

### 2. `policy/auto-resolution-policy.md`
Defines what Marge may do automatically, and what always requires confirmation.

### 3. `policy/briefing-policy.md`
Defines how many items show up, in what order, with what thresholds.

### 4. `policy/privacy-policy.md`
Defines how private, sensitive, and pastoral data may be surfaced.

These files become the governance layer for both:
- product behavior
- future LLM prompting / orchestration

---

## The canonical care queue

Marge needs one operational source of truth for active work.

Even if the backend stores different records in different tables, the product should think in terms of one queue made of:

- urgent care cases
- visitor follow-up items
- absence follow-ups
- prayer requests needing updates
- pastoral nudges
- birthdays and anniversaries requiring action

This is the pastoral equivalent of clawchief's canonical task state.

### Product implication

The current morning briefing is one output.
The next interface should be a **Care Queue** view that shows:
- due today
- overdue
- newly created
- resolved recently

---

## Pastoral note ingestion

This is one of the most important adaptations.

Clawchief treats meeting notes as signal, not as static documents.
Marge should do the same with pastoral notes.

Example:

> "Talked with David Park after service. Still unemployed. Wife is discouraged. Check in next week."

Marge should parse this into:
- member: David Park
- concern type: job / family stress
- urgency: medium
- follow-up due: next week
- prayer relevance: yes
- briefing inclusion: yes

This means Marge's future intelligence should be built around **note ingestion → care state mutation**.

---

## Heartbeat adaptation

The Marge heartbeat should not try to do everything.
It should maintain operational state by checking:

- visitor follow-up windows crossed
- absence thresholds crossed
- overdue care cases
- stale prayer requests
- birthdays and anniversaries entering the horizon
- note-derived nudges that have gone untouched
- stale items that can be auto-archived

The heartbeat should be quiet unless something meaningful changes.

---

## MCP adaptation

The MCP server should expose pastoral workflows rather than database-shaped tools.

Strong examples:

- `get_morning_briefing`
- `get_member_snapshot`
- `log_pastoral_note`
- `open_care_case`
- `mark_contacted`
- `advance_visitor_followup`
- `add_prayer_request`
- `draft_message`
- `tell_marge`

This makes Marge available in Claude / ChatGPT without flattening her into a generic CRM.

---

## Product roadmap implications

### Immediate
- formalize policy layer
- build unified care queue
- connect briefing actions to real backend mutations
- improve note ingestion pathway

### Near-term
- add real state transitions for visitor pipeline
- add note parsing and follow-up creation
- persist data in Postgres
- refine MCP tools around care workflows

### Longer-term
- support inbound SMS / email / form ingestion
- support denomination-specific tone and thresholds
- add role-based care visibility for small multi-staff churches

---

## Key principle

The thing to borrow from clawchief is this:

> Marge is not a dashboard with church records.
> Marge is a policy-driven pastoral operating system that converts relational signal into care action.

That is the adaptation.
