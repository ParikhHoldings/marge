# Marge MVP — Build Summary

**Built:** April 1, 2026  
**Status:** ✅ Ready to run

---

## How to Run

```bash
cd /root/marge

# Install dependencies (first time only)
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit PASTOR_NAME, CHURCH_NAME, and optionally ROCK_HALLMARK_API_KEY

# Start the API server
uvicorn app.main:app --reload

# API docs: http://localhost:8000/docs
```

**Morning briefing (cron):**
```bash
python3 scripts/morning_briefing.py

# Schedule at 7 AM daily
0 7 * * * cd /root/marge && python3 scripts/morning_briefing.py >> /var/log/marge_briefing.log 2>&1
```

---

## Complete File Tree

```
/root/marge/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry point, lifespan, CORS, router registration
│   ├── database.py          # SQLAlchemy setup — SQLite (dev) / Postgres (prod)
│   ├── models.py            # ORM models: Member, Visitor, CareNote, PrayerRequest, MemberNote
│   ├── marge_voice.py       # Tone constants + all message templates (Marge's personality)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── briefing.py      # GET /briefing/today — full morning briefing
│   │   ├── visitors.py      # CRUD + /visitors/{id}/draft for follow-up messages
│   │   ├── members.py       # Member CRM + notes + /members/sync/rock
│   │   └── care.py          # Care cases + prayer requests (all CRUD + /resolve + /contact)
│   ├── services/
│   │   ├── __init__.py
│   │   └── marge.py         # Marge brain: briefing gen, nudge logic, all draft functions
│   └── integrations/
│       ├── __init__.py
│       └── rock.py          # Rock RMS sync: fetch_active_members, sync_attendance, run_full_sync
├── scripts/
│   └── morning_briefing.py  # Standalone cron script — stdout + optional Telegram delivery
├── requirements.txt         # fastapi, uvicorn, sqlalchemy, python-dotenv, requests, pydantic
├── .env.example             # All environment variables documented with defaults
├── README.md                # Quickstart, endpoint table, structure, voice guidelines
└── BUILD_SUMMARY.md         # This file
```

---

## What's Ready to Test Immediately

### ✅ API server
```bash
uvicorn app.main:app --reload
# → http://localhost:8000/docs
```

### ✅ Morning briefing endpoint
```
GET http://localhost:8000/briefing/today
```
Returns structured JSON with greeting, all care categories, nudges, and a `plain_text` field ready for Telegram/email.

### ✅ Visitor CRUD + follow-up drafts
```
POST /visitors/                    # Log a visitor
GET  /visitors/?needs_followup=true  # Who needs Day-1 follow-up
GET  /visitors/{id}/draft?day=1    # Get Day-1 text draft
GET  /visitors/{id}/draft?day=3    # Get Day-3 email draft
GET  /visitors/{id}/draft?day=14   # Get Week-2 invitation draft
PATCH /visitors/{id}               # Mark follow_up_day1_sent=true after sending
```

### ✅ Member CRM + pastoral notes
```
POST /members/                       # Add member manually
GET  /members/?q=Smith               # Search by name
GET  /members/{id}                   # Detail + all notes
POST /members/{id}/notes             # Add pastoral note (context_tag: job, health, etc.)
GET  /members/{id}/draft/care?situation=hospital  # Draft care message
POST /members/sync/rock              # Trigger Rock RMS sync
```

### ✅ Care cases + prayer requests
```
POST /care/                          # Open a care case
GET  /care/?status=active            # List active cases
POST /care/{id}/contact              # Log a contact, reset 7-day timer
POST /care/{id}/resolve              # Mark resolved
POST /care/prayers/                  # Submit prayer request
GET  /care/prayers/?status=active    # List active prayers
PATCH /care/prayers/{id}             # Mark answered/archived
```

### ✅ Morning briefing cron script
```bash
python3 scripts/morning_briefing.py
# Prints to stdout
# Sends to Telegram if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set
```

### ✅ Rock RMS sync (standalone-safe)
- Works with or without `ROCK_HALLMARK_API_KEY`
- Without key: returns `{"rock_sync_enabled": false}` gracefully
- With key: syncs people + attendance from `https://rock.hbcfw.org/api/v2`

---

## Spec Deviations & Notes

| Item | Decision | Why |
|------|----------|-----|
| **Stack** | FastAPI + SQLAlchemy + SQLite (not Next.js + Supabase) | Spec Phase 1 calls for Supabase as the production stack — this MVP uses FastAPI + SQLite for standalone operation and rapid iteration. Swap `DATABASE_URL` to Postgres when ready. |
| **AI drafts** | Template-based (no OpenAI API call) | Avoids requiring an OpenAI key for the MVP to work. Templates are in `marge_voice.py` and match Marge's voice exactly. Wire in GPT-4o later for dynamic voice matching. |
| **Telegram delivery** | requests library, direct Bot API | Simple and reliable. No third-party library needed beyond `requests`. |
| **Enum handling** | SQLAlchemy Enum with string values | Compatible with both SQLite and Postgres without migration issues. |
| **`full_name` property** | Added to Member + Visitor | Convenience property used throughout routers — cleaner than concatenating everywhere. |
| **`/care/prayers/` prefix** | Prayers nested under `/care/` | Logical grouping since prayer requests are a care function. Matches the router organization. |

---

## Next Steps

1. **Add `.env`** with PASTOR_NAME, CHURCH_NAME, and optionally ROCK_HALLMARK_API_KEY
2. **Seed data** via the API or directly in the SQLite DB for testing
3. **Wire up Telegram** for morning briefing delivery
4. **Test Rock sync** once API key is configured
5. **Replace template drafts with GPT-4o** — add `OPENAI_API_KEY` and update `services/marge.py`
6. **Deploy to Railway/VPS** — set `DATABASE_URL` to Postgres, add `gunicorn` or Railway start command

---

*Marge is ready. She knows your people.*
