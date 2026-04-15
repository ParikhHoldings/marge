# Marge Operations Runbook

## 1) Recovery objectives by environment

| Environment | RPO target | RTO target | Notes |
|---|---:|---:|---|
| Pilot / staging | 24 hours | 4 hours | Daily automated backup is acceptable while pilots are supervised closely. |
| Production | 1 hour | 60 minutes | Hourly snapshots + tested restore path for church-facing reliability. |

**Definitions**
- **RPO** (Recovery Point Objective): max tolerated data loss window.
- **RTO** (Recovery Time Objective): max tolerated service recovery duration.

## 2) Automated DB backups with retention policy

### Backup command
```bash
python3 scripts/db_backup.py --environment pilot
# or
python3 scripts/db_backup.py --environment production
```

### Policy
- Pilot retention: `DB_BACKUP_RETENTION_DAYS_PILOT=14`
- Production retention: `DB_BACKUP_RETENTION_DAYS_PRODUCTION=35`
- Minimum artifacts retained regardless of age: `DB_BACKUP_KEEP_MIN=10`
- Backup storage root: `DB_BACKUP_DIR` (default: `./backups`)

### Suggested schedules
- Pilot: daily at 02:00 UTC
  - `0 2 * * * cd /workspace/marge && python3 scripts/db_backup.py --environment pilot >> /var/log/marge_backup.log 2>&1`
- Production: hourly
  - `5 * * * * cd /workspace/marge && python3 scripts/db_backup.py --environment production >> /var/log/marge_backup.log 2>&1`

## 3) Restore procedure (staging rehearsal)

### Restore command
```bash
python3 scripts/db_restore.py \
  --backup-file <backup artifact path> \
  --database-url <target DATABASE_URL> \
  --force
```

### Rehearsal command
```bash
python3 scripts/rehearse_restore.py --environment pilot
```

### Most recent rehearsal result
- Rehearsal date (UTC): **2026-04-15T04:29:54Z**
- Backup time: **0.154s**
- Restore time: **0.139s**
- Data integrity: **PASS** (`PRAGMA integrity_check = ok`)
- Critical-table row-count parity: **PASS**
  - `members: 1`
  - `visitors: 1`
  - `care_notes: 1`
  - `prayer_requests: 1`
  - `member_notes: 1`

### Data integrity verification steps
1. Run restore into staging target DB.
2. Confirm physical/engine consistency check:
   - SQLite: `PRAGMA integrity_check;` must return `ok`.
   - Postgres: run `ANALYZE` and validate no restore errors in `pg_restore` output.
3. Compare row counts for critical pastoral tables:
   - `members`, `visitors`, `care_notes`, `prayer_requests`, `member_notes`.
4. Perform one API-level smoke test after restore:
   - `GET /health`
   - `GET /briefing/today`

## 4) Encryption, secrets, and key rotation expectations

### Encryption at rest
- **Database volumes and backup storage must use provider-managed encryption at rest** (AES-256 or equivalent).
- Backup artifacts stored outside managed encrypted volumes must be encrypted before upload/transfer.
- Disk-level encryption responsibility:
  - **Pilot**: Engineering owner verifies provider default encryption settings.
  - **Production**: Platform owner verifies and documents settings quarterly.

### Encryption in transit
- All app and API traffic must use TLS 1.2+.
- Database connections in production must require SSL/TLS (`sslmode=require` or provider equivalent).
- Administrative restore access must use secure channels (SSH tunnel, private network, or provider console).

### Secret management
- No secrets in repo; all secrets loaded via environment variables / platform secret manager.
- Required sensitive secrets include DB credentials, bot tokens, API keys, and signing keys.
- Access policy: least privilege, named ownership, and removal of stale credentials.

### Key and credential rotation responsibilities
- **Engineering lead** (owner): rotate application/API secrets every 90 days or upon suspected exposure.
- **Platform owner** (owner): rotate DB credentials every 90 days and after team-role changes.
- **Release manager** (owner): confirm post-rotation health checks and morning briefing smoke test.

## 5) Quarterly restore drill checklist

**Cadence:** once per quarter (Q1/Q2/Q3/Q4), plus after any major DB engine/storage migration.

**Primary owner:** Platform owner.  
**Backup owner:** Engineering lead.  
**Approver:** Product/operations lead.

### Checklist
- [ ] Confirm current RPO/RTO targets are still correct for pilot and production.
- [ ] Trigger fresh backup in source environment.
- [ ] Restore into isolated staging database.
- [ ] Measure and record backup and restore durations.
- [ ] Run integrity checks (engine check + critical row counts).
- [ ] Run API smoke checks (`/health`, `/briefing/today`).
- [ ] Verify retention pruning happened as expected.
- [ ] Capture drill output in `docs/DAILY_DIGEST.md` and any follow-up tasks in `docs/BACKLOG.md`.
- [ ] Escalate if RPO/RTO miss or integrity check fails.
