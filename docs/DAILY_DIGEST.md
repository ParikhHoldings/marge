# Daily Digest

## 2026-04-11
### Shipped
- Added the full autonomous operating layer reference doc set to Marge as the first reference implementation.
- Created repo-local `AGENTS.md` with Marge-specific mission, ICP, priorities, commands, and execution rules.
- Added canonical product docs for vision, roadmap, backlog, decisions, metrics, marketing, research, and daily digest.

### In progress
- Aligning Marge’s active pilot-readiness work with the new autonomous operating standard.
- Preparing the repo for issue/template/PR standardization.

### Blocked
- No hard blocker on the operating-layer rollout itself.
- Production-promotion decision still depends on review and pilot-readiness doc alignment.

### Approvals needed
- Production promotion if and when staging is approved for `main`.

### Recommended next focus
- Add GitHub templates into Marge and align current active work with issue-based execution.

## 2026-04-15
### Shipped
- Initialized Alembic with a baseline migration for current SQLAlchemy models.
- Replaced startup auto-create behavior with migration-head verification in normal runtime.
- Added CI migration smoke checks (upgrade/downgrade/re-upgrade) against ephemeral Postgres.
- Added operations runbook for backup-before-migration and rollback steps.

### In progress
- Tightening migration ergonomics for local developer setup and drift detection.

### Blocked
- No blockers currently.

### Approvals needed
- None for staging rollout; production promotion still follows existing approval boundary.

### Recommended next focus
- Add migration autogenerate guardrails and a schema drift check in CI.
