# Decisions

## 2026-04-15 - Adopt explicit confidentiality classes and role-aware redaction across pastoral records
### Decision
Prayer requests, care cases, and member notes now carry a required `confidentiality_class` value aligned to policy (`public`, `private`, `sensitive`). API mappers and chat-derived action payloads now redact private/sensitive text for lower-privilege roles.

### Why
The prior `is_private` boolean on prayer requests was insufficiently expressive and left room for accidental data promotion in multi-output surfaces. We needed policy-aligned classes and consistent read/write/update behavior across care, prayer, notes, and chat workflows.

### Impact
Public-facing outputs (briefing public audience, prayer bulletin, export audience=public) are now guarded and only include explicitly public records. Conformance tests now cover privacy behavior for create/read/update flows and chat-derived actions.

## 2026-04-10 - Use durable PostgreSQL instead of SQLite for Railway environments
### Decision
Staging and production should both use PostgreSQL-backed `DATABASE_URL` values rather than SQLite on Railway.

### Why
SQLite resets on deploy created an unacceptable reliability problem for a product handling pastoral workflow state.

### Impact
Marge now has durable staging/production data rails and can move forward on pilot-readiness work without the previous infra blocker.

## 2026-04-10 - Emphasize workflow-first product experience over passive notes UX
### Decision
Marge should surface actionable workflows directly in the product experience rather than relying on a generic passive notes/chat model.

### Why
The core product risk was not missing backend logic alone; it was weak exposure of already valuable care/visitor/outreach workflows.

### Impact
Frontend and interaction design should continue emphasizing actionable Tell Marge, visitor sequences, care tracking, and outreach drafts.

## 2026-04-11 - Use Marge as the reference implementation for the autonomous OS standard
### Decision
Marge is the first repo to receive the full OpenClaw autonomous operating layer.

### Why
Marge is active, real, revenue-adjacent, and mature enough to validate the system cleanly.

### Impact
Lessons from this rollout should shape the standard before propagation to SundayEngine, Nexdo, and FDD Tracker.
