# Pilot Runbook: Church Activation (Day 0–14)

## Purpose

This runbook gives church staff a single, repeatable activation flow to get Marge from setup to a verified first care outcome and verified first visitor follow-up sequence within 14 days.

It is designed for consistent execution across churches in pilot mode and includes:
- onboarding steps
- required data imports
- first briefing verification
- first care workflow completion
- first visitor sequence completion
- milestone evidence requirements (Day 1, 3, 7, 14)
- a docs-backed checklist
- setup blocker escalation matrix

---

## 1) Roles and ownership

Assign one owner before kickoff:

- **Pilot Owner (required):** pastoral admin or executive pastor responsible for checklist completion and evidence collection.
- **Pastor Champion (required):** pastor who validates briefing usefulness and uses message drafts.
- **Technical Contact (recommended):** staff member who can validate auth/integration details.

If one person wears multiple hats, keep responsibilities explicit in the checklist.

---

## 2) Onboarding steps (Day 0 / kickoff)

### Step 2.1 — Environment + app health
1. Confirm app starts and returns healthy:
   - `GET /health`
   - success criteria: `{"status":"healthy"}`
2. Confirm API docs are available for staff walkthrough:
   - `GET /docs` in browser

### Step 2.2 — Church context variables
Set and verify:
- `PASTOR_NAME`
- `CHURCH_NAME`
- `CORS_ORIGINS` (staging-safe value)

Validation check:
- `GET /` and confirm greeting reflects configured pastor/church values.

### Step 2.3 — Access + workflow orientation
In a 30-minute onboarding session:
- walk through `/briefing/today`
- walk through member profile and notes flow (`/members`, `/members/{id}/notes`)
- walk through care case flow (`/care`, `/care/{id}/contact`, `/care/{id}/resolve`)
- walk through visitor follow-up flow (`/visitors/{id}/draft`, follow-up status flags)

Deliverable:
- named Pilot Owner + Pastor Champion recorded in internal pilot notes.

---

## 3) Required data imports (must be complete before Day 1 sign-off)

## 3.1 Minimum required data

### Members
Import/create enough member records to support real briefing quality.

Required fields per member (minimum):
- `first_name`
- `last_name`

Recommended fields for high-quality care surfacing:
- `email`
- `phone`
- `birthday`
- `anniversary`
- `last_attendance`

Endpoints:
- `POST /members/` (manual)
- `GET /members/` (audit list)
- `POST /members/sync/rock` (if Rock RMS is enabled)

### Visitors
Import/create recent visitors to test real follow-up workflow.

Required fields per visitor (minimum):
- `first_name`
- `last_name`
- `visit_date`

Recommended fields:
- `email`
- `phone`
- `source`

Endpoints:
- `POST /visitors/`
- `GET /visitors/`

### Care + prayer baseline (recommended for briefing realism)
- at least 1 active care case
- at least 1 prayer request (private or non-private as appropriate)

Endpoints:
- `POST /care/`
- `POST /care/prayers/`

## 3.2 Data quality acceptance checks
Before Day 1 sign-off, run:
- `GET /members/?limit=200`
- `GET /visitors/?limit=200`
- `GET /care/?status=active`
- `GET /care/prayers/?status=active&include_private=true`

Accept only if:
1. no obvious duplicate people in sampled records
2. at least one valid contact method (`email` or `phone`) for priority follow-up people
3. dates are plausible (`visit_date`, `last_attendance`, `last_contact` not malformed)
4. private prayer requests are handled intentionally (`is_private` set correctly)

---

## 4) First briefing verification (Day 1 required)

Goal: prove briefing output is trustworthy enough for daily use.

### Procedure
1. Call `GET /briefing/today`.
2. Review these sections with Pastor Champion:
   - `visitors_needing_followup`
   - `active_care_cases`
   - `absent_members`
   - `unanswered_prayers`
   - `nudges`
3. Confirm plain text output (`plain_text`) is pastor-usable without heavy editing.
4. Cross-check at least 3 surfaced items against known church reality.

### Day 1 pass criteria
- briefing returns successfully
- pastor confirms at least 1 surfaced item is actionable the same day
- no severe trust issue found (wrong person, unsafe privacy exposure, clearly stale/invalid core item)

### Evidence to capture
- timestamped `GET /briefing/today` response snapshot
- short pastoral validation note: “What felt accurate/useful? What was off?”
- list of remediation tasks for any misses

---

## 5) First care workflow completion (Day 1–3 required)

Goal: complete one end-to-end care cycle inside Marge.

### Workflow steps
1. Identify a real or pilot-safe care need.
2. Confirm person exists (`GET /members/?q=<name>`), or create via `POST /members/`.
3. Open care case via `POST /care/` with category and description.
4. Generate outreach draft:
   - `GET /members/{member_id}/draft/care?situation=<context>`
5. After pastor sends message externally, log follow-up in app:
   - `POST /care/{care_id}/contact` with `contact_date` and optional note.
6. If care situation is complete, resolve:
   - `POST /care/{care_id}/resolve`

### Completion criteria
- one care case opened
- one care draft generated and reviewed by pastor
- one contact logged
- case left active with rationale OR resolved

### Evidence to capture
- `care_id`
- copy of draft used (or short summary if privacy-sensitive)
- logged contact timestamp
- status after follow-up (`active` or `resolved`) with brief rationale

---

## 6) First visitor sequence completion (Day 1–14 required)

Goal: execute and track one full visitor sequence (Day 1, Day 3, Week 2).

### Workflow steps
1. Confirm visitor record exists (`GET /visitors/?limit=50`) or create via `POST /visitors/`.
2. Generate Day 1 draft:
   - `GET /visitors/{visitor_id}/draft?day=1`
3. After sending externally, mark sent:
   - `PATCH /visitors/{visitor_id}` with `follow_up_day1_sent=true`
4. Generate Day 3 draft:
   - `GET /visitors/{visitor_id}/draft?day=3`
5. Mark Day 3 sent:
   - `PATCH /visitors/{visitor_id}` with `follow_up_day3_sent=true`
6. Generate Week 2 draft:
   - `GET /visitors/{visitor_id}/draft?day=14`
7. Mark Week 2 sent:
   - `PATCH /visitors/{visitor_id}` with `follow_up_week2_sent=true`

### Completion criteria
- all three drafts generated
- all three sent flags updated in system
- pastor confirms tone is acceptable for church voice

### Evidence to capture
- `visitor_id`
- dates each sequence touchpoint was sent
- final visitor record showing all follow-up flags true

---

## 7) Activation milestones and required evidence

| Milestone | Target day | Required outcome | Required evidence |
|---|---:|---|---|
| **M1: Setup Complete** | Day 1 | Core setup and minimum data are ready; first briefing verified | Health check result, onboarding checklist complete, member/visitor import counts, one `GET /briefing/today` snapshot with pastor validation note |
| **M2: Early Workflow Confidence** | Day 3 | First end-to-end care workflow completed | `care_id`, draft evidence, contact log evidence, resulting care status |
| **M3: Consistent Daily Use** | Day 7 | Briefing reviewed on multiple days and acted on | At least 3 dated briefing reviews, action notes for surfaced items, unresolved data quality items tracked |
| **M4: Visitor Follow-up Reliability** | Day 14 | First full visitor sequence completed and tracked | `visitor_id`, day1/day3/day14 draft and sent evidence, final visitor follow-up flags all true |

Milestone failure rule:
- If evidence is missing, milestone is **not complete** even if work was verbally reported.

---

## 8) Docs-backed checklist for staff execution (cross-church standard)

Use this checklist as the required operating artifact for every pilot church.

## 8.1 Setup checklist (Pilot Owner)
- [ ] Confirm `GET /health` returns healthy.
- [ ] Confirm `GET /` reflects correct pastor/church values.
- [ ] Confirm `/docs` is reachable for staff.
- [ ] Assign Pilot Owner + Pastor Champion.
- [ ] Record kickoff date and target Day 14 date.

## 8.2 Data checklist (Pilot Owner + Technical Contact)
- [ ] Members imported/created and audited via `GET /members/`.
- [ ] Visitors imported/created and audited via `GET /visitors/`.
- [ ] Optional Rock sync tested via `POST /members/sync/rock` (if enabled).
- [ ] At least one active care case exists (`GET /care/?status=active`).
- [ ] Prayer request visibility reviewed (`GET /care/prayers/?include_private=true`).

## 8.3 Workflow checklist (Pastor Champion)
- [ ] First briefing verified via `GET /briefing/today`.
- [ ] One care workflow completed (open → draft → contact log → status update).
- [ ] One visitor sequence completed (day1/day3/day14 + sent flags).
- [ ] Any trust or safety concerns logged to escalation matrix.

## 8.4 Milestone evidence checklist (Pilot Owner)
- [ ] Day 1 evidence packet complete.
- [ ] Day 3 evidence packet complete.
- [ ] Day 7 evidence packet complete.
- [ ] Day 14 evidence packet complete.

Operational rule:
- Do not mark a stage complete without attached evidence links/snapshots.

---

## 9) Setup blocker support escalation matrix

Use this matrix for setup blockers across all pilots.

| Blocker type | Typical symptoms | First responder | Escalate to | Target first response SLA | Escalation trigger |
|---|---|---|---|---|---|
| **Integration** (Rock sync/API dependency) | Sync failures, missing imported fields, connector errors | Technical Contact | Engineering owner | 4 business hours | Blocks member/attendance import for >1 business day |
| **Authentication / Access** | Staff cannot log in/access endpoints/docs, invalid credentials, environment mismatch | Pilot Owner | Engineering owner | 2 business hours | Any role blocked from required Day 1 tasks |
| **Data Quality** | Duplicate people, missing contact info, stale attendance, unusable dates | Pilot Owner + Pastor Champion | Data steward / Engineering owner | 1 business day | Briefing trust compromised or workflow cannot proceed safely |
| **Privacy / Safety** | Private prayer/care info exposed incorrectly, unclear consent boundaries, sensitive data handling concern | Pilot Owner | Product + Privacy/Policy owner | 1 hour | Any potential privacy breach or pastoral confidentiality risk |

### Severity guidance
- **SEV-1 (Critical):** privacy/safety risk, halt pilot actions touching affected data until contained.
- **SEV-2 (High):** core workflow blocker (cannot run briefing/care/visitor flow).
- **SEV-3 (Medium):** workaround exists but reliability/trust degraded.
- **SEV-4 (Low):** cosmetic/docs issue with no immediate workflow risk.

### Escalation procedure
1. Log blocker with timestamp, church, severity, and affected workflow step.
2. Assign owner and expected next update time.
3. Communicate workaround (if any) to Pastor Champion.
4. Re-verify affected milestone evidence after resolution.

---

## 10) Completion definition for pilot activation

Pilot activation is complete only when all are true:
1. Day 1, 3, 7, and 14 milestones are met with evidence.
2. One care workflow and one full visitor sequence are completed in-system.
3. Briefing has been validated by Pastor Champion as useful and trustworthy enough for daily use.
4. Open blockers have owners, severities, and follow-up dates.

