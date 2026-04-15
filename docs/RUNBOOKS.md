# Incident Runbooks

This document covers first-response playbooks for the most likely launch incidents.
All incident responses should preserve pastoral trust, avoid leaking sensitive care data,
and restore reliable care workflow operation quickly.

## 1) Database outage

### Triggers
- `/health` failing with DB connection errors
- Spiking 5xx on CRUD routes (`/care`, `/members`, `/visitors`)
- Elevated `care_prayer_crud_failures_total`

### Immediate response (0–10 min)
1. Acknowledge incident in internal ops channel with timestamp.
2. Confirm blast radius: all routes vs specific workflow.
3. Validate DB reachability from app environment (connection string, network, credentials).
4. If possible, fail over to known-good standby / managed backup.

### Mitigation (10–30 min)
1. Restart app pods/workers after DB connectivity is restored.
2. Run smoke checks:
   - `GET /health`
   - `GET /briefing/today`
   - `POST /care/` with non-sensitive test data in staging only
3. Monitor availability + CRUD failure metric trend for 15 minutes.

### Communication
- Internal update every 15 minutes until resolved.
- If user-facing impact > 15 minutes, send pilot-facing status note:
  - what happened
  - current mitigation
  - next update ETA

### Post-incident follow-up
- Document root cause and duration.
- Add preventive action (connection pool, failover drills, alert tuning).

---

## 2) LLM provider error (chat fallback degradation)

### Triggers
- Increased chat fallback failures
- Provider 429/5xx errors
- User reports that chat replies fail or are generic

### Immediate response (0–10 min)
1. Verify provider status page and quota usage.
2. Confirm API key validity and environment config.
3. Switch to deterministic local-path behavior only (structured actions still available).

### Mitigation (10–30 min)
1. Rate-limit non-critical LLM calls.
2. Keep pastoral workflows operational via:
   - manual care/prayer CRUD
   - morning briefing generation
   - prebuilt template drafts
3. Track `chat_action_total{status=failure}` until stabilized.

### Communication
- Explain that freeform conversational drafting may be degraded.
- Confirm that care tracking and briefings remain functional.

### Post-incident follow-up
- Add retry/backoff improvements.
- Reassess provider limits and fallback policy.

---

## 3) Rock sync degradation

### Triggers
- Consecutive `rock_sync_outcome_total{status=degraded}`
- Missing/updating fewer members than expected
- Timeout/connection errors to Rock endpoint

### Immediate response (0–10 min)
1. Confirm Rock API base URL and auth token validity.
2. Check timeout/network path between app and Rock.
3. Determine if degradation is:
   - full failure (no data)
   - partial failure (members only / attendance only)

### Mitigation (10–45 min)
1. Retry sync manually once connection/auth is corrected.
2. If still degraded, disable automated sync noise and use last-known-good local data.
3. Prioritize continuity of pastor workflows with cached member records.

### Communication
- Notify internal team of sync staleness window.
- If >24h stale, notify pilot churches that imports are delayed but local updates continue.

### Post-incident follow-up
- Update thresholds and expected row-change baselines.
- Add provider-specific retry and timeout tuning.

---

## 4) Privacy incident response

### Triggers
- Any suspicion of sensitive pastoral data exposure
- Wrong-tenant/church data visibility
- Logs containing care/prayer message text or PII

### Immediate response (0–15 min)
1. Declare **privacy incident** and assign incident commander.
2. Contain exposure:
   - disable affected endpoint/feature
   - rotate compromised credentials/tokens
3. Preserve forensic evidence (logs, request IDs, timestamps).

### Assessment (15–60 min)
1. Determine data classes involved (care notes, prayer requests, contact info).
2. Determine tenant/church scope and time window.
3. Confirm whether data was viewed, exported, or only theoretically exposed.

### Notification & remediation
1. Escalate to founder/leadership immediately.
2. Draft church-facing notification with:
   - what data category was affected
   - time window
   - containment actions already completed
   - expected next update timing
3. Implement remediation before re-enabling feature.

### Post-incident follow-up
- Record a decision log entry with corrective actions.
- Add regression checks to prevent repeated exposure.
- Re-audit observability to ensure sensitive content is not logged.
