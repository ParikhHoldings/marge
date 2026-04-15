# Operations Runbook

## Database migrations policy

- **Production/staging startup does not run `create_all` anymore.**
- Runtime startup now verifies the schema is already on Alembic head.
- If schema is behind, startup fails fast with instructions to run migrations first.
- `create_all` is only allowed when `MARGE_LOCAL_DEV_CREATE_ALL=true` (local-only bootstrap mode).

## Backup-before-migration procedure

Always take a database backup immediately before running migrations in staging or production.

### PostgreSQL (recommended)

1. Export connection variables in your shell:
   - `PGHOST`
   - `PGPORT`
   - `PGUSER`
   - `PGDATABASE`
2. Run a compressed backup:

```bash
pg_dump --format=custom --file "backup-pre-migration-$(date +%Y%m%d-%H%M%S).dump" "$DATABASE_URL"
```

3. Verify backup integrity:

```bash
pg_restore --list "backup-pre-migration-YYYYMMDD-HHMMSS.dump" >/dev/null
```

4. Store backup in your approved secure backup location before applying migrations.

## Migration deployment procedure

1. Confirm backup exists and is verified.
2. Apply migrations:

```bash
alembic upgrade head
```

3. Confirm DB revision:

```bash
alembic current
alembic heads
```

4. Start app process and verify health endpoint responds.

## Rollback procedure

### Schema rollback (recent migration issue)

1. Put the application into maintenance mode / stop write traffic.
2. Roll back one migration:

```bash
alembic downgrade -1
```

3. Validate application health and critical workflows.

### Full restore (data or multi-step issue)

1. Stop app writers.
2. Restore from the pre-migration backup:

```bash
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" "backup-pre-migration-YYYYMMDD-HHMMSS.dump"
```

3. Re-run smoke checks and bring app traffic back.

## Local development bootstrap options

- Preferred local flow:

```bash
alembic upgrade head
```

- Explicit local-only bootstrap fallback (no migration run):

```bash
MARGE_LOCAL_DEV_CREATE_ALL=true uvicorn app.main:app --reload
```

Use this only for throwaway local databases; do not use this setting in staging/production.
