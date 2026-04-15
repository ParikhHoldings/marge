# Deployment Security Profiles

This runbook defines secure defaults for Marge by environment so staging can stay realistic without inheriting production risk.

## Shared baseline (all environments)

- `APP_ENV` controls startup validation and default security posture.
- CORS, rate limiting, and abuse controls are environment-driven from `.env` / deployment variables.
- Startup now fails fast in `production` when required configuration is missing or insecure.

## Staging profile (recommended)

Use this profile for pilot rehearsals and release-candidate testing.

```env
APP_ENV=staging
DATABASE_URL=postgresql://...
PASTOR_NAME=Staging Pastor
CHURCH_NAME=Staging Church

CORS_ORIGINS=https://staging.yourchurch.org
CORS_METHODS=GET,POST,PATCH,DELETE,OPTIONS
CORS_HEADERS=Authorization,Content-Type
CORS_ALLOW_CREDENTIALS=false

RATE_LIMIT_ENABLED=true
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_CHAT_REQUESTS=45
RATE_LIMIT_WRITE_REQUESTS=180
ABUSE_MAX_BREACHES=6
ABUSE_BLOCK_SECONDS=300
```

### Why these defaults

- Staging keeps realistic CORS and rate limits so integration behavior matches production.
- Limits are intentionally slightly looser than production to reduce false positives during QA and demos.

## Production profile (required minimum)

```env
APP_ENV=production
DATABASE_URL=postgresql://...
PASTOR_NAME=<real pastor name>
CHURCH_NAME=<real church name>

CORS_ORIGINS=https://app.yourchurch.org
CORS_METHODS=GET,POST,PATCH,DELETE,OPTIONS
CORS_HEADERS=Authorization,Content-Type
CORS_ALLOW_CREDENTIALS=false

RATE_LIMIT_ENABLED=true
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_CHAT_REQUESTS=30
RATE_LIMIT_WRITE_REQUESTS=120
ABUSE_MAX_BREACHES=5
ABUSE_BLOCK_SECONDS=600
```

### Production fail-fast validation

When `APP_ENV=production`, Marge will refuse to boot if:

- `DATABASE_URL` is missing.
- `DATABASE_URL` points to SQLite.
- `CORS_ORIGINS` is missing or contains `*`.
- `PASTOR_NAME` or `CHURCH_NAME` is blank or left at known defaults.
- `CORS_ALLOW_CREDENTIALS=true` with wildcard origins.

## Credentials policy and auth strategy alignment

Current API auth is header/token-oriented (not cookie session auth). Default policy should therefore be:

- `CORS_ALLOW_CREDENTIALS=false`
- Explicit `Authorization` header allowlist via `CORS_HEADERS`

If cookie-based auth is introduced later, revisit this policy and update CORS plus CSRF protections together.

## Operational notes

- Current rate limiting is in-memory and per-process. For multi-instance production deployments, migrate to a shared store (e.g., Redis) for consistent global enforcement.
- Keep these defaults synchronized with deployment templates (Railway/Vercel/Terraform) before pilot expansion.
