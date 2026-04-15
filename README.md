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

# 3. Apply DB migrations
alembic upgrade head

# 4. Start the API server
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
- `MARGE_LOCAL_DEV_CREATE_ALL` — Set `true` only for explicit local create_all bootstrap
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


## Database Migrations

Marge now uses Alembic for schema management.

```bash
# Apply all pending migrations
alembic upgrade head

# Show current DB revision
alembic current
```

For staging/production migration safety and rollback steps, see `docs/OPERATIONS.md`.
