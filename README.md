# Marge — AI Pastoral Assistant

> "Marge is the church secretary you can't afford — a warm, AI-powered assistant who briefs you every morning on who needs care today, drafts your follow-up messages, tracks every prayer request and visitor, and makes sure no one falls through the cracks."

## Quick Start

```bash
cd /root/marge

# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your values (PASTOR_NAME, CHURCH_NAME, etc.)

# 3. Start the API server
uvicorn app.main:app --reload

# API docs available at:
#   http://localhost:8000/docs      (Swagger UI)
#   http://localhost:8000/redoc
```

## Morning Briefing (Cron)

```bash
# Run manually
python3 scripts/morning_briefing.py

# Schedule for 7 AM daily (cron)
0 7 * * * cd /root/marge && python3 scripts/morning_briefing.py >> /var/log/marge_briefing.log 2>&1
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to receive briefings via Telegram.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/briefing/today` | Today's full pastoral briefing |
| POST | `/visitors/` | Log a new visitor |
| GET | `/visitors/` | List visitors |
| GET | `/visitors/{id}/draft` | Get follow-up message draft |
| PATCH | `/visitors/{id}` | Mark follow-up sent, add notes |
| POST | `/members/` | Add a congregation member |
| GET | `/members/` | Search members |
| GET | `/members/{id}` | Member detail + notes |
| POST | `/members/{id}/notes` | Add pastoral note |
| GET | `/members/{id}/draft/care` | Draft a care message |
| POST | `/members/sync/rock` | Sync from Rock RMS |
| POST | `/care/` | Open a care case |
| GET | `/care/` | List care cases |
| POST | `/care/{id}/resolve` | Resolve a care case |
| POST | `/care/{id}/contact` | Log a pastoral contact |
| POST | `/care/prayers/` | Submit a prayer request |
| GET | `/care/prayers/` | List prayer requests |
| PATCH | `/care/prayers/{id}` | Update prayer status |

## Authentication & Authorization

All API routes are protected by default. These endpoints remain public:
- `GET /`
- `GET /health`
- `/docs`, `/redoc`, `/openapi.json`

### Required headers

Use **one** of these authentication methods:

1) JWT bearer token:
```http
Authorization: Bearer <jwt>
```

2) Session token:
```http
X-Session-Token: <token>
```

### JWT claims

JWTs must include:
- `sub` (or `user_id`)
- `role`: one of `pastor`, `admin`, `staff`, `read-only`
- `church_id` (or `tenant`)

Optional verification controls:
- `AUTH_JWT_SECRET` (required for JWT auth)
- `AUTH_JWT_ALGORITHMS` (default: `HS256`)
- `AUTH_JWT_ISSUER` (optional)
- `AUTH_JWT_AUDIENCE` (optional)

### Session token format (local/dev)

Set `AUTH_SESSION_TOKENS` as comma-separated entries:
```bash
AUTH_SESSION_TOKENS="dev-pastor|pastor|hallmark|nathan,dev-staff|staff|hallmark|amy"
```

Each entry is:
`token|role|church_id|user_id`

### Role policy highlights

- `pastor` / `admin`: full access across routes in their church tenant.
- `staff`: standard read/write access, but cannot delete protected entities or access private prayer records.
- `read-only`: GET/list endpoints only; write attempts are rejected with `403`.
- Care endpoints are restricted to pastor/admin/staff only.
- Private prayer records are restricted to pastor/admin only, with explicit `403` responses for unauthorized attempts.

### Tenant scoping (`church_id`)

All domain tables are tenant-scoped:
- `members`
- `visitors`
- `care_notes`
- `prayer_requests`
- `member_notes`

Every query path applies `church_id` filtering to prevent cross-church access.

## Secret rotation guidance (production)

1. Use a secret manager (Railway/Render/Cloud provider secret store), not plaintext files.
2. Generate a new high-entropy `AUTH_JWT_SECRET`.
3. Deploy with dual-token overlap window:
   - begin issuing new JWTs with the new secret,
   - allow old sessions to expire quickly,
   - then remove the old secret.
4. Rotate `AUTH_SESSION_TOKENS` at the same time (or disable static sessions in production).
5. Set short JWT TTLs and re-authenticate privileged roles (`pastor`, `admin`) aggressively.
6. Audit logs for 401/403 spikes during rollout.

## Rock RMS Integration

Marge can sync members and attendance from Rock RMS automatically.

1. Add `ROCK_HALLMARK_API_KEY` to your `.env`
2. Call `POST /members/sync/rock` to trigger a sync
3. The app works fully standalone without Rock — the API key is optional

## Environment Variables

See `.env.example` for all available configuration options.

Key variables:
- `PASTOR_NAME` — Pastor's first name (appears in all drafts)
- `CHURCH_NAME` — Church name (appears in visitor messages)
- `DATABASE_URL` — SQLite for dev, Postgres for production
- `ROCK_HALLMARK_API_KEY` — Optional Rock RMS API key
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — Optional Telegram delivery

## Project Structure

```
/root/marge/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── database.py          # SQLAlchemy setup
│   ├── models.py            # ORM models
│   ├── marge_voice.py       # Tone constants + message templates
│   ├── routers/
│   │   ├── briefing.py      # GET /briefing/today
│   │   ├── visitors.py      # Visitor CRUD + follow-up
│   │   ├── members.py       # Member CRM + notes
│   │   └── care.py          # Care cases + prayer requests
│   ├── services/
│   │   └── marge.py         # Marge brain: briefing, nudges, drafts
│   └── integrations/
│       └── rock.py          # Rock RMS sync layer
├── scripts/
│   └── morning_briefing.py  # Standalone cron script
├── requirements.txt
├── .env.example
└── README.md
```

## Marge's Voice

Marge is the beloved church secretary who's been at the church 30 years. She's warm, reliable, and never sounds like a database.

**She never says:**
- "Member milestone event detected"
- "Follow-up touchpoint scheduled"
- "I am an AI"

**She always says:**
- "Tom's birthday is Thursday — he'd love a call"
- "Janet could really use a call this week"
- "Good morning, Pastor Nathan. Here are your people for today."

---

*Built for Nathan Parikh — Hallmark Church, Fort Worth TX. April 2026.*
