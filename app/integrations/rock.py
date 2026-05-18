"""
Rock RMS integration layer for Marge.

Syncs people and attendance records from a Rock RMS instance into
Marge's local database. The app functions fully standalone — if
ROCK_API_KEY and ROCK_BASE_URL are not set, sync methods return empty lists
and log a warning rather than crashing. ROCK_HALLMARK_API_KEY is still read as
a legacy fallback for old local environments, but new deployments should use
ROCK_API_KEY.

Rock RMS API v2 reference:
  https://community.rockrms.com/developer

Auth: Authorization-Token header (per-church API key)
Base: Set ROCK_BASE_URL to the church's Rock API v2 base URL.
"""

import os
import logging
from datetime import date, datetime
from typing import List, Optional, Dict, Any

import requests
from sqlalchemy.orm import Session

from app.models import Member

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

ROCK_API_KEY_ENV = "ROCK_API_KEY"
ROCK_LEGACY_API_KEY_ENV = "ROCK_HALLMARK_API_KEY"
ROCK_BASE_URL_ENV = "ROCK_BASE_URL"

# Reasonable timeouts for API calls
REQUEST_TIMEOUT = int(os.getenv("ROCK_REQUEST_TIMEOUT", "15"))

# Rock Person record status IDs (active member)
ROCK_ACTIVE_RECORD_STATUS_ID = 3  # "Active" in Rock


# ── Internal helpers ───────────────────────────────────────────────────────────

def _rock_api_key(api_key: Optional[str] = None) -> str:
    return api_key or os.getenv(ROCK_API_KEY_ENV, "") or os.getenv(ROCK_LEGACY_API_KEY_ENV, "")


def _rock_base_url(base_url: Optional[str] = None) -> str:
    return (base_url or os.getenv(ROCK_BASE_URL_ENV) or "").rstrip("/")


def _headers(api_key: Optional[str] = None) -> Dict[str, str]:
    """Return request headers with auth token."""
    return {
        "Authorization-Token": _rock_api_key(api_key),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _is_configured(api_key: Optional[str] = None, base_url: Optional[str] = None) -> bool:
    """Return True if Rock API key is available."""
    missing = []
    if not _rock_api_key(api_key):
        missing.append(ROCK_API_KEY_ENV)
    if not _rock_base_url(base_url):
        missing.append(ROCK_BASE_URL_ENV)
    if missing:
        logger.warning(
            "Rock RMS sync is disabled because %s is not set. "
            "Marge will operate in standalone mode.",
            ", ".join(missing),
        )
        return False
    return True


def _get(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Optional[List[dict]]:
    """
    Make a GET request to Rock RMS and return the JSON response.

    Returns None on any error (including missing API key) so callers
    can gracefully fall back to an empty result.
    """
    if not _is_configured(api_key, base_url):
        return None

    rock_base = _rock_base_url(base_url)
    url = f"{rock_base}/{endpoint.lstrip('/')}"
    try:
        response = requests.get(
            url,
            headers=_headers(api_key),
            params=params or {},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Rock RMS at %s — check ROCK_BASE_URL.", rock_base)
        return None
    except requests.exceptions.Timeout:
        logger.error("Rock RMS request timed out after %ds.", REQUEST_TIMEOUT)
        return None
    except requests.exceptions.HTTPError as exc:
        logger.error("Rock RMS HTTP error: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Unexpected error calling Rock RMS: %s", exc)
        return None


# ── People Sync ────────────────────────────────────────────────────────────────

def fetch_active_members(api_key: Optional[str] = None, base_url: Optional[str] = None) -> List[dict]:
    """
    Fetch active people records from Rock RMS.

    Uses the /People endpoint filtered to active record status.
    Returns a list of Rock Person dicts, or [] if sync is disabled.

    Rock API reference:
      GET /api/v2/People?$filter=RecordStatusValueId eq 3&$select=Id,FirstName,LastName,Email,PhoneNumbers,BirthDay,BirthMonth,BirthYear,AnniversaryDate
    """
    params = {
        "$filter": f"RecordStatusValueId eq {ROCK_ACTIVE_RECORD_STATUS_ID}",
        "$select": (
            "Id,FirstName,LastName,Email,PhoneNumbers,"
            "BirthDay,BirthMonth,BirthYear,AnniversaryDate"
        ),
        "$top": 1000,
    }
    data = _get("People", params, api_key=api_key, base_url=base_url)
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("value", [])


def fetch_attendance_records(top: int = 500, api_key: Optional[str] = None, base_url: Optional[str] = None) -> List[dict]:
    """
    Fetch recent attendance records from Rock RMS.

    Returns a list of Attendance dicts, or [] if sync is disabled.

    Rock API reference:
      GET /api/v2/Attendances?$orderby=StartDateTime desc&$top=500
    """
    params = {
        "$orderby": "StartDateTime desc",
        "$top": top,
        "$select": "PersonAlias/PersonId,StartDateTime",
        "$expand": "PersonAlias",
    }
    data = _get("Attendances", params, api_key=api_key, base_url=base_url)
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("value", [])


# ── Sync to Local DB ───────────────────────────────────────────────────────────

def _parse_rock_birthday(person: dict) -> Optional[date]:
    """Parse Rock's split BirthYear/BirthMonth/BirthDay fields into a date."""
    year = person.get("BirthYear")
    month = person.get("BirthMonth")
    day = person.get("BirthDay")
    if year and month and day:
        try:
            return date(int(year), int(month), int(day))
        except (ValueError, TypeError):
            return None
    return None


def _parse_rock_anniversary(person: dict) -> Optional[date]:
    """Parse Rock's AnniversaryDate ISO string into a date."""
    raw = person.get("AnniversaryDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except (ValueError, TypeError):
        return None


def _parse_rock_phone(person: dict) -> Optional[str]:
    """Extract the first mobile or home phone number from Rock's PhoneNumbers list."""
    phones = person.get("PhoneNumbers") or []
    for phone in phones:
        number = phone.get("NumberFormatted") or phone.get("Number")
        if number:
            return number
    return None


def sync_members_from_rock(
    db: Session,
    account_id: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, int]:
    """
    Sync active member records from Rock RMS into Marge's local Member table.

    Creates new Member rows for people not yet in Marge.
    Updates existing rows (matched by rock_id) with fresh data.
    Does NOT delete members that are no longer in Rock (soft approach).

    Args:
        db: SQLAlchemy session.

    Returns:
        {"created": N, "updated": N, "skipped": N}
    """
    people = fetch_active_members(api_key=api_key, base_url=base_url)
    if not people:
        logger.info("No active people returned from Rock RMS (sync skipped or API unavailable).")
        return {"created": 0, "updated": 0, "skipped": 0}

    stats = {"created": 0, "updated": 0, "skipped": 0}

    seen_rock_ids: set[str] = set()
    for person in people:
        rock_id = str(person.get("Id", "")).strip()
        if not rock_id:
            stats["skipped"] += 1
            continue
        if rock_id in seen_rock_ids:
            stats["skipped"] += 1
            continue
        seen_rock_ids.add(rock_id)
        first_name = (person.get("FirstName") or "").strip()
        last_name = (person.get("LastName") or "").strip()
        if not first_name and not last_name:
            stats["skipped"] += 1
            continue

        existing = db.query(Member).filter(Member.rock_id == rock_id, Member.account_id == account_id).first()

        if existing:
            # Update fields that may have changed in Rock
            existing.first_name = first_name or existing.first_name
            existing.last_name = last_name or existing.last_name
            existing.email = person.get("Email") or existing.email
            existing.phone = _parse_rock_phone(person) or existing.phone
            existing.birthday = _parse_rock_birthday(person) or existing.birthday
            existing.anniversary = _parse_rock_anniversary(person) or existing.anniversary
            stats["updated"] += 1
        else:
            member = Member(
                account_id=account_id,
                rock_id=rock_id,
                first_name=first_name,
                last_name=last_name,
                email=person.get("Email"),
                phone=_parse_rock_phone(person),
                birthday=_parse_rock_birthday(person),
                anniversary=_parse_rock_anniversary(person),
            )
            db.add(member)
            stats["created"] += 1

    db.commit()
    logger.info(
        "Rock RMS sync complete: %d created, %d updated, %d skipped.",
        stats["created"], stats["updated"], stats["skipped"],
    )
    return stats


def sync_attendance_from_rock(
    db: Session,
    account_id: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, int]:
    """
    Sync recent attendance records from Rock RMS and update Member.last_attendance.

    Finds the most recent attendance date per person and writes it back to
    the Member row. Only updates if the Rock date is more recent than what's stored.

    Args:
        db: SQLAlchemy session.

    Returns:
        {"updated": N, "not_found": N}
    """
    attendances = fetch_attendance_records(api_key=api_key, base_url=base_url)
    if not attendances:
        logger.info("No attendance records returned from Rock RMS.")
        return {"updated": 0, "not_found": 0}

    # Build a dict: rock_person_id → most recent attendance date
    latest: Dict[str, date] = {}
    for record in attendances:
        person_alias = record.get("PersonAlias") or {}
        rock_person_id = str(person_alias.get("PersonId", ""))
        raw_date = record.get("StartDateTime")
        if not rock_person_id or not raw_date:
            continue
        try:
            att_date = datetime.fromisoformat(raw_date[:10]).date()
        except (ValueError, TypeError):
            continue
        if rock_person_id not in latest or att_date > latest[rock_person_id]:
            latest[rock_person_id] = att_date

    stats = {"updated": 0, "not_found": 0}

    for rock_id, att_date in latest.items():
        member = db.query(Member).filter(Member.rock_id == rock_id, Member.account_id == account_id).first()
        if not member:
            stats["not_found"] += 1
            continue
        if member.last_attendance is None or att_date > member.last_attendance:
            member.last_attendance = att_date
            stats["updated"] += 1

    db.commit()
    logger.info(
        "Rock attendance sync: %d updated, %d not found in local DB.",
        stats["updated"], stats["not_found"],
    )
    return stats


def run_full_sync(
    db: Session,
    account_id: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict:
    """
    Run a full Rock RMS sync: members + attendance.

    This is the function to call from a cron job or the /sync endpoint.
    Safe to call even without API key — returns zeroes gracefully.

    Args:
        db: SQLAlchemy session.

    Returns:
        Combined stats dict.
    """
    if not _is_configured(api_key, base_url):
        return {
            "rock_sync_enabled": False,
            "message": "ROCK_API_KEY and ROCK_BASE_URL are not configured; running in standalone mode.",
        }

    member_stats = sync_members_from_rock(db, account_id=account_id, api_key=api_key, base_url=base_url)
    attendance_stats = sync_attendance_from_rock(db, account_id=account_id, api_key=api_key, base_url=base_url)

    return {
        "rock_sync_enabled": True,
        "members": member_stats,
        "attendance": attendance_stats,
    }
