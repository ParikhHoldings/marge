#!/usr/bin/env python3
"""
Marge Morning Briefing — Standalone cron script.

Generates and delivers the daily pastoral briefing.

Usage:
  python3 scripts/morning_briefing.py

Environment variables required:
  DATABASE_URL      — SQLAlchemy connection string (default: sqlite:///./marge.db)
  PASTOR_NAME       — Pastor's first name (default: Nathan)
  CHURCH_NAME       — Church name (default: Hallmark Church)

Optional (Telegram delivery):
  TELEGRAM_BOT_TOKEN  — Bot token from BotFather
  TELEGRAM_CHAT_ID    — Chat/user ID to send the briefing to

Schedule via cron:
  0 7 * * * cd /root/marge && python3 scripts/morning_briefing.py >> /var/log/marge_briefing.log 2>&1
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
from app.services.marge import generate_morning_briefing, render_briefing_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("marge.briefing")


# ── Config ─────────────────────────────────────────────────────────────────────

PASTOR_NAME = os.getenv("PASTOR_NAME", "Nathan")
CHURCH_NAME = os.getenv("CHURCH_NAME", "Hallmark Church")
DEFAULT_CHURCH_ID = os.getenv("DEFAULT_CHURCH_ID", "default-church")
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

def main():
    logger.info("Marge morning briefing starting for %s at %s.", PASTOR_NAME, CHURCH_NAME)

    # Initialize DB (creates tables if they don't exist)
    init_db()

    db = SessionLocal()
    try:
        briefing = generate_morning_briefing(
            db,
            pastor_name=PASTOR_NAME,
            church_name=CHURCH_NAME,
            church_id=DEFAULT_CHURCH_ID,
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
