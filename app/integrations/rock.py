"""
Rock RMS integration layer for Marge.

Syncs people and attendance records from a Rock RMS instance into
Marge's local database. The app functions fully standalone — if
ROCK_HALLMARK_API_KEY is not set, sync methods return empty lists
and log a warning rather than crashing.

Rock RMS API v2 reference:
  https://rock.hbcfw.org/api/v2/docs/index

Auth: Authorization-Token header (per-church API key)
Base: https://rock.hbcfw.org/api/v2   (overridable via ROCK_BASE_URL env var)
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

ROCK_BASE = os.getenv("ROCK_BASE_URL", "https://rock.hbcfw.org/api")
ROCK_API_KEY = os.getenv("ROCK_HALLMARK_API_KEY", "")

# Reasonable timeouts for API calls
REQUEST_TIMEOUT = int(os.getenv("ROCK_REQUEST_TIMEOUT", "15"))

# Rock Person record status IDs (active member)
ROCK_ACTIVE_RECORD_STATUS_ID = 3  # "Active" in Rock


# ── Internal helpers ───────────────────────────────────────────────────────────

def _headers() -> Dict[str, str]:
    """Return request headers with auth token."""
    return {
        "Authorization-Token": ROCK_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _is_configured() -> bool:
    """Return True if Rock API key is available."""
    if not ROCK_API_KEY:
        logger.warning(
            "ROCK_HALLMARK_API_KEY is not set — Rock RMS sync is disabled. "
            "Marge will operate in standalone mode."
        )
        return False
    return True


def _get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[List[dict]]:
    """
    Make a GET request to Rock RMS and return the JSON response.

    Returns None on any error (including missing API key) so callers
    can gracefully fall back to an empty result.
    """
    if not _is_configured():
        return None

    url = f"{ROCK_BASE}/{endpoint.lstrip('/')}"
    try:
        response = requests.get(
            url,
            headers=_headers(),
            params=params or {},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Rock RMS at %s — check ROCK_BASE_URL.", ROCK_BASE)
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

def fetch_active_members() -> List[dict]:
    """
    Fetch active people records from Rock RMS.

    Uses the /People endpoint filtered to active record status.
    Returns a list of Rock Person dicts, or [] if sync is disabled.

    Rock API reference:
      GET /api/v2/People?$filter=RecordStatusValueId eq 3&$select=Id,FirstName,LastName,Email,PhoneNumbers,BirthDay,BirthMonth,BirthYear,AnniversaryDate
    """
    params = {
        "$filter": (
            f"RecordStatusValueId eq {ROCK_ACTIVE_RECORD_STATUS_ID} "
            "and RecordTypeValueId eq 1 and IsDeceased eq false"
        ),
        "$select": (
            "Id,FirstName,NickName,LastName,Email,PhoneNumbers,"
            "BirthDay,BirthMonth,BirthYear,AnniversaryDate"
        ),
        "$top": 1000,
    }
    data = _get("People", params)
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("value", [])


def fetch_attendance_records(top: int = 500) -> List[dict]:
    """
    Fetch recent attendance records from Rock RMS.

    Returns a list of Attendance dicts, or [] if sync is disabled.

    Rock API reference:
      GET /api/v2/Attendances?$orderby=StartDateTime desc&$top=500
    """
    params = {
        "$orderby": "StartDateTime desc",
        "$top": top,
    }
    data = _get("Attendances", params)
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


def sync_members_from_rock(db: Session, church_id: str) -> Dict[str, int]:
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
    people = fetch_active_members()
    if not people:
        logger.info("No active people returned from Rock RMS (sync skipped or API unavailable).")
        return {"created": 0, "updated": 0, "skipped": 0}

    stats = {"created": 0, "updated": 0, "skipped": 0}

    for person in people:
        rock_id = str(person.get("Id", ""))
        if not rock_id:
            stats["skipped"] += 1
            continue

        existing = (
            db.query(Member)
            .filter(Member.rock_id == rock_id, Member.church_id == church_id)
            .first()
        )

        if existing:
            # Update fields that may have changed in Rock
            existing.first_name = person.get("FirstName", existing.first_name)
            existing.last_name = person.get("LastName", existing.last_name)
            existing.email = person.get("Email") or existing.email
            existing.phone = _parse_rock_phone(person) or existing.phone
            existing.birthday = _parse_rock_birthday(person) or existing.birthday
            existing.anniversary = _parse_rock_anniversary(person) or existing.anniversary
            stats["updated"] += 1
        else:
            first_name = person.get("FirstName") or person.get("NickName") or "Unknown"
            last_name = person.get("LastName") or ""
            member = Member(
                church_id=church_id,
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


def sync_attendance_from_rock(db: Session, church_id: str) -> Dict[str, int]:
    """
    Sync recent attendance records from Rock RMS and update Member.last_attendance.

    Rock attendance rows often only include `PersonAliasId`, so this step resolves
    aliases back to `PersonId` before updating local members.

    Args:
        db: SQLAlchemy session.

    Returns:
        {"updated": N, "not_found": N, "alias_resolved": N}
    """
    attendances = fetch_attendance_records()
    if not attendances:
        logger.info("No attendance records returned from Rock RMS.")
        return {"updated": 0, "not_found": 0, "alias_resolved": 0}

    alias_ids = sorted({record.get("PersonAliasId") for record in attendances if record.get("PersonAliasId")})
    alias_to_person: Dict[str, str] = {}
    alias_batches = 0

    for i in range(0, len(alias_ids), 20):
        batch = alias_ids[i:i + 20]
        filter_expr = " or ".join([f"Id eq {alias_id}" for alias_id in batch])
        data = _get("PersonAlias", {"$filter": filter_expr, "$top": len(batch)})
        if data is None:
            continue
        alias_batches += 1
        for alias in (data if isinstance(data, list) else data.get("value", [])):
            if alias.get("Id") and alias.get("PersonId"):
                alias_to_person[str(alias.get("Id"))] = str(alias.get("PersonId"))

    latest: Dict[str, date] = {}
    for record in attendances:
        rock_person_id = ""
        person_alias = record.get("PersonAlias") or {}
        if person_alias.get("PersonId"):
            rock_person_id = str(person_alias.get("PersonId", ""))
        elif record.get("PersonAliasId"):
            rock_person_id = alias_to_person.get(str(record.get("PersonAliasId")), "")
        raw_date = record.get("StartDateTime")
        if not rock_person_id or not raw_date:
            continue
        if record.get("DidAttend") is False:
            continue
        try:
            att_date = datetime.fromisoformat(raw_date[:10]).date()
        except (ValueError, TypeError):
            continue
        if rock_person_id not in latest or att_date > latest[rock_person_id]:
            latest[rock_person_id] = att_date

    stats = {"updated": 0, "not_found": 0, "alias_resolved": len(alias_to_person)}

    for rock_id, att_date in latest.items():
        member = db.query(Member).filter(Member.rock_id == rock_id, Member.church_id == church_id).first()
        if not member:
            stats["not_found"] += 1
            continue
        if member.last_attendance is None or att_date > member.last_attendance:
            member.last_attendance = att_date
            stats["updated"] += 1

    db.commit()
    logger.info(
        "Rock attendance sync: %d updated, %d not found in local DB, %d aliases resolved across %d batches.",
        stats["updated"], stats["not_found"], stats["alias_resolved"], alias_batches,
    )
    return stats


def run_full_sync(db: Session, church_id: str) -> dict:
    """
    Run a full Rock RMS sync: members + attendance.

    This is the function to call from a cron job or the /sync endpoint.
    Safe to call even without API key — returns zeroes gracefully.

    Args:
        db: SQLAlchemy session.

    Returns:
        Combined stats dict.
    """
    if not _is_configured():
        return {
            "rock_sync_enabled": False,
            "message": "ROCK_HALLMARK_API_KEY not set — running in standalone mode.",
        }

    member_stats = sync_members_from_rock(db, church_id=church_id)
    attendance_stats = sync_attendance_from_rock(db, church_id=church_id)

    return {
        "rock_sync_enabled": True,
        "members": member_stats,
        "attendance": attendance_stats,
    }
