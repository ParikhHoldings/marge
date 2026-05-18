#!/usr/bin/env python3
"""
Marge Morning Briefing — Standalone cron script.

Generates and delivers the daily pastoral briefing.

Usage:
  python3 scripts/morning_briefing.py

Environment variables:
  DATABASE_URL      — SQLAlchemy connection string (default: sqlite:///./marge.db)
  MARGE_ACCOUNT_TOKEN, MARGE_ACCOUNT_ID, or MARGE_ACCOUNT_SLUG
                    — Optional workspace selector for account-scoped briefings
  PASTOR_NAME       — Legacy fallback pastor name (default: Pastor)
  CHURCH_NAME       — Legacy fallback church name (default: your church)

Optional (Telegram delivery):
  TELEGRAM_BOT_TOKEN  — Bot token from BotFather
  TELEGRAM_CHAT_ID    — Chat/user ID to send the briefing to

Schedule via cron:
  0 7 * * * cd /root/marge && MARGE_ACCOUNT_SLUG=your-church .venv/bin/python scripts/morning_briefing.py >> /var/log/marge_briefing.log 2>&1
"""

import os
import sys
import logging
from datetime import datetime

# Allow running from project root without installing
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from app.database import SessionLocal, init_db
from app.models import AccountPastorProfile, ChurchAccount
from app.services.marge import generate_morning_briefing, render_briefing_text
from app.services.accounts import account_access_from_token, account_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("marge.briefing")


# ── Config ─────────────────────────────────────────────────────────────────────

PASTOR_NAME = os.getenv("PASTOR_NAME", "").strip() or "Pastor"
CHURCH_NAME = os.getenv("CHURCH_NAME", "").strip() or "your church"
MARGE_ACCOUNT_TOKEN = os.getenv("MARGE_ACCOUNT_TOKEN", "")
MARGE_ACCOUNT_ID = os.getenv("MARGE_ACCOUNT_ID", "")
MARGE_ACCOUNT_SLUG = os.getenv("MARGE_ACCOUNT_SLUG", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Telegram delivery ──────────────────────────────────────────────────────────

def send_telegram(text: str, bot_token: str, chat_id: str) -> bool:
    """
    Send a message via Telegram Bot API.

    Splits messages longer than 4096 characters (Telegram's limit) into chunks.

    Args:
        text:       The message text (Markdown supported).
        bot_token:  Telegram bot token from BotFather.
        chat_id:    Target chat ID (user or group).

    Returns:
        True if all chunks sent successfully, False on error.
    """
    try:
        import requests
    except ImportError:
        logger.error("'requests' library not installed. Run: pip install requests")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    max_length = 4096

    # Split into chunks if needed
    chunks = [text[i : i + max_length] for i in range(0, len(text), max_length)]
    success = True

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
        }
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            logger.info("Telegram chunk %d/%d delivered.", i + 1, len(chunks))
        except Exception as exc:
            logger.error("Failed to send Telegram chunk %d: %s", i + 1, exc)
            success = False

    return success


# ── Main ───────────────────────────────────────────────────────────────────────

def _workspace_from_env(db):
    if MARGE_ACCOUNT_TOKEN:
        return account_access_from_token(db, MARGE_ACCOUNT_TOKEN).account
    if MARGE_ACCOUNT_ID:
        try:
            return db.get(ChurchAccount, int(MARGE_ACCOUNT_ID))
        except ValueError:
            raise SystemExit("MARGE_ACCOUNT_ID must be an integer.")
    if MARGE_ACCOUNT_SLUG:
        return db.query(ChurchAccount).filter(ChurchAccount.slug == MARGE_ACCOUNT_SLUG).one_or_none()
    return None


def _briefing_identity(db, account):
    if not account:
        return PASTOR_NAME, CHURCH_NAME
    profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
    pastor_name = (profile.pastor_name if profile else None) or account.pastor_name or PASTOR_NAME
    church_name = (profile.church_name if profile else None) or account.church_name or CHURCH_NAME
    return pastor_name, church_name


def main():
    # Initialize DB (creates tables if they don't exist)
    init_db()

    db = SessionLocal()
    try:
        account = _workspace_from_env(db)
        if any([MARGE_ACCOUNT_TOKEN, MARGE_ACCOUNT_ID, MARGE_ACCOUNT_SLUG]) and not account:
            raise SystemExit("Could not find the requested Marge workspace for this briefing.")
        pastor_name, church_name = _briefing_identity(db, account)
        logger.info("Marge morning briefing starting for %s at %s.", pastor_name, church_name)
        briefing = generate_morning_briefing(
            db,
            pastor_name=pastor_name,
            church_name=church_name,
            account_id=account_id(account),
        )
        text = render_briefing_text(briefing)
    finally:
        db.close()

    # Always print to stdout (useful for cron logs)
    print("\n" + "=" * 60)
    print(f"MARGE BRIEFING — {datetime.now().strftime('%A, %B %-d, %Y')}")
    print("=" * 60)
    print(text)
    print("=" * 60 + "\n")

    # Telegram delivery (optional)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        logger.info("Sending briefing to Telegram chat %s…", TELEGRAM_CHAT_ID)
        date_header = f"*{datetime.now().strftime('%A, %B %-d')}*\n\n"
        success = send_telegram(date_header + text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        if success:
            logger.info("Briefing delivered to Telegram successfully.")
        else:
            logger.error("Telegram delivery failed.")
            sys.exit(1)
    else:
        logger.info(
            "No Telegram credentials configured — briefing printed to stdout only. "
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable Telegram delivery."
        )

    logger.info("Briefing complete.")


if __name__ == "__main__":
    main()
