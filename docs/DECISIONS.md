# Decisions

## 2026-04-15 - Enforce secure-by-default runtime guards for production
### Decision
Production startup now performs fail-fast environment validation, CORS defaults were tightened to explicit allowlists/scoped methods-headers, and optional abuse protection/rate limiting was added for chat and write-heavy API paths.

### Why
Pilot-readiness requires preventing insecure deployment drift and reducing abuse risk on pastor-facing workflows without blocking staging velocity.

### Impact
Misconfigured production deployments fail immediately instead of running insecurely, and operators now have documented staging/production security profiles with optional traffic protection controls.

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
