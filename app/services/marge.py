"""
Marge's brain — briefing generation, nudge logic, and draft generation.

All the pastoral intelligence lives here. The routers call these functions;
the functions pull from the DB and return clean dicts or strings that the
API layer serializes and delivers.
"""

import os
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Member, Visitor, CareNote, PrayerRequest, MemberNote
from app import marge_voice as voice


# ── Config defaults ───────────────────────────────────────────────────────────

ABSENCE_THRESHOLD_DAYS = int(os.getenv("ABSENCE_THRESHOLD_DAYS", "21"))
CARE_OVERDUE_DAYS = int(os.getenv("CARE_OVERDUE_DAYS", "7"))
PRAYER_OVERDUE_DAYS = int(os.getenv("PRAYER_OVERDUE_DAYS", "14"))
VISITOR_FOLLOWUP_DELAY_HOURS = int(os.getenv("VISITOR_FOLLOWUP_DELAY_HOURS", "48"))
BIRTHDAY_LOOKAHEAD_DAYS = int(os.getenv("BIRTHDAY_LOOKAHEAD_DAYS", "7"))
NUDGE_LOOKBACK_DAYS = int(os.getenv("NUDGE_LOOKBACK_DAYS", "14"))


# ── Morning Briefing ──────────────────────────────────────────────────────────

def generate_morning_briefing(db: Session, pastor_name: str, church_name: str, church_id: str) -> dict:
    """
    Generate today's pastoral briefing.

    Queries the database for every category of pastoral attention and returns
    a structured dict ready for rendering in email, Telegram, or the web app.

    Returns:
        {
            "greeting": str,
            "pastor_name": str,
            "church_name": str,
            "generated_at": str (ISO datetime),
            "birthdays_this_week": [Member, ...],
            "anniversaries_this_week": [Member, ...],
            "visitors_needing_followup": [Visitor, ...],
            "active_care_cases": [CareNote, ...],
            "absent_members": [Member, ...],
            "unanswered_prayers": [PrayerRequest, ...],
            "nudges": [str, ...],
        }
    """
    today = date.today()
    week_end = today + timedelta(days=BIRTHDAY_LOOKAHEAD_DAYS)

    briefing = {
        "greeting": voice.PASTOR_GREETING.format(pastor_name=pastor_name),
        "pastor_name": pastor_name,
        "church_name": church_name,
        "generated_at": datetime.utcnow().isoformat(),
        "birthdays_this_week": _get_birthdays_this_week(db, today, week_end, church_id),
        "anniversaries_this_week": _get_anniversaries_this_week(db, today, week_end, church_id),
        "visitors_needing_followup": _get_visitors_needing_followup(db, today, church_id),
        "active_care_cases": _get_active_care_cases(db, today, church_id),
        "absent_members": _get_absent_members(db, today, church_id),
        "unanswered_prayers": _get_unanswered_prayers(db, today, church_id),
        "nudges": get_nudges(db, church_id),
    }

    return briefing


def _get_birthdays_this_week(db: Session, today: date, week_end: date, church_id: str) -> List[Member]:
    """
    Return Members whose birthday falls within the next BIRTHDAY_LOOKAHEAD_DAYS days.

    Compares month+day only (year-agnostic).
    """
    members = db.query(Member).filter(Member.church_id == church_id, Member.birthday.isnot(None)).all()
    result = []
    for m in members:
        bday = m.birthday
        # Construct this year's birthday for comparison
        try:
            this_year_bday = bday.replace(year=today.year)
        except ValueError:
            # Feb 29 on non-leap year
            this_year_bday = bday.replace(year=today.year, day=28)
        if today <= this_year_bday <= week_end:
            result.append(m)
        else:
            # Also check if birthday is early in the year and we're near year-end
            try:
                next_year_bday = bday.replace(year=today.year + 1)
            except ValueError:
                next_year_bday = bday.replace(year=today.year + 1, day=28)
            if today <= next_year_bday <= week_end:
                result.append(m)
    return result


def _get_anniversaries_this_week(db: Session, today: date, week_end: date, church_id: str) -> List[Member]:
    """
    Return Members whose wedding anniversary falls within the next BIRTHDAY_LOOKAHEAD_DAYS days.
    """
    members = db.query(Member).filter(Member.church_id == church_id, Member.anniversary.isnot(None)).all()
    result = []
    for m in members:
        ann = m.anniversary
        try:
            this_year_ann = ann.replace(year=today.year)
        except ValueError:
            this_year_ann = ann.replace(year=today.year, day=28)
        if today <= this_year_ann <= week_end:
            result.append(m)
    return result


def _get_visitors_needing_followup(db: Session, today: date, church_id: str) -> List[Visitor]:
    """
    Return Visitors where:
      - visit_date was at least VISITOR_FOLLOWUP_DELAY_HOURS ago
      - Day-1 follow-up has NOT been sent yet
    """
    cutoff = today - timedelta(hours=VISITOR_FOLLOWUP_DELAY_HOURS / 24)
    return (
        db.query(Visitor)
        .filter(Visitor.church_id == church_id)
        .filter(Visitor.visit_date <= cutoff)
        .filter(Visitor.follow_up_day1_sent == False)  # noqa: E712
        .order_by(Visitor.visit_date)
        .all()
    )


def _get_active_care_cases(db: Session, today: date, church_id: str) -> List[CareNote]:
    """
    Return CareNotes where:
      - status = 'active'
      - last_contact is NULL or was more than CARE_OVERDUE_DAYS ago
    """
    cutoff = today - timedelta(days=CARE_OVERDUE_DAYS)
    return (
        db.query(CareNote)
        .filter(CareNote.church_id == church_id)
        .filter(CareNote.status == "active")
        .filter(
            (CareNote.last_contact == None) |  # noqa: E711
            (CareNote.last_contact < cutoff)
        )
        .order_by(CareNote.last_contact.asc().nullsfirst())
        .all()
    )


def _get_absent_members(db: Session, today: date, church_id: str) -> List[Member]:
    """
    Return Members where last_attendance was more than ABSENCE_THRESHOLD_DAYS ago.

    Members with no attendance record at all are not surfaced (they may be new
    additions without a history yet). Only those who have attended before and
    then went absent are flagged.
    """
    cutoff = today - timedelta(days=ABSENCE_THRESHOLD_DAYS)
    return (
        db.query(Member)
        .filter(Member.church_id == church_id)
        .filter(Member.last_attendance.isnot(None))
        .filter(Member.last_attendance < cutoff)
        .order_by(Member.last_attendance.asc())
        .all()
    )


def _get_unanswered_prayers(db: Session, today: date, church_id: str) -> List[PrayerRequest]:
    """
    Return PrayerRequests that are:
      - status = 'active'
      - created_at more than PRAYER_OVERDUE_DAYS ago with no update
    """
    cutoff = datetime.utcnow() - timedelta(days=PRAYER_OVERDUE_DAYS)
    return (
        db.query(PrayerRequest)
        .filter(PrayerRequest.church_id == church_id)
        .filter(PrayerRequest.status == "active")
        .filter(PrayerRequest.created_at < cutoff)
        .order_by(PrayerRequest.created_at.asc())
        .all()
    )


# ── Visitor Follow-Up Drafts ──────────────────────────────────────────────────

def draft_visitor_followup(
    visitor: Visitor,
    day: int,
    pastor_name: str = "Pastor",
    church_name: str = "our church",
    event_or_service: str = "Sunday service",
) -> str:
    """
    Draft a follow-up message for a first-time visitor.

    Args:
        visitor:         The Visitor ORM object.
        day:             1 = same-day text, 3 = 3-day email, 14 = two-week invitation.
        pastor_name:     Pastor's first name for the signature.
        church_name:     Church name for the closing.
        event_or_service: Used in the Week-2 invitation template.

    Returns:
        A warm, pre-written draft the pastor reviews before sending.
        Not salesy. Zero pressure. Written like a person, not a system.
    """
    first_name = visitor.first_name

    if day == 1:
        return voice.FOLLOW_UP_DAY1_TEMPLATE.format(
            first_name=first_name,
            pastor_name=pastor_name,
            church_name=church_name,
        )
    elif day == 3:
        return voice.FOLLOW_UP_DAY3_TEMPLATE.format(
            first_name=first_name,
            pastor_name=pastor_name,
            church_name=church_name,
        )
    elif day == 14:
        return voice.FOLLOW_UP_WEEK2_TEMPLATE.format(
            first_name=first_name,
            pastor_name=pastor_name,
            church_name=church_name,
            event_or_service=event_or_service,
        )
    else:
        # Fallback: generic warm message
        return (
            f"Hey {first_name}, just wanted to check in and see how you're doing. "
            f"We'd love to see you again sometime. — Pastor {pastor_name}"
        )


# ── Pastoral Care Drafts ──────────────────────────────────────────────────────

def draft_care_message(
    member: Member,
    situation: str,
    pastor_name: str = "Pastor",
) -> str:
    """
    Draft a pastoral care message for a member in a difficult situation.

    Args:
        member:      The Member ORM object.
        situation:   One of: 'hospital', 'grief', 'crisis', or any freeform description.
        pastor_name: Pastor's first name for the signature.

    Returns:
        A warm, human-sounding draft. Not scripted, not clinical.
        The pastor reads it, edits if needed, and sends it.
    """
    first_name = member.first_name
    situation_lower = situation.lower()

    if "hospital" in situation_lower or "surgery" in situation_lower or "medical" in situation_lower:
        return voice.CARE_MESSAGE_HOSPITAL.format(
            first_name=first_name,
            pastor_name=pastor_name,
        )
    elif "grief" in situation_lower or "loss" in situation_lower or "death" in situation_lower or "died" in situation_lower:
        return voice.CARE_MESSAGE_GRIEF.format(
            first_name=first_name,
            pastor_name=pastor_name,
        )
    elif "crisis" in situation_lower or "emergency" in situation_lower or "urgent" in situation_lower:
        return voice.CARE_MESSAGE_CRISIS.format(
            first_name=first_name,
            pastor_name=pastor_name,
        )
    else:
        return voice.CARE_MESSAGE_GENERAL.format(
            first_name=first_name,
            pastor_name=pastor_name,
        )


# ── Proactive Nudges ──────────────────────────────────────────────────────────

def get_nudges(db: Session, church_id: str) -> List[str]:
    """
    Return a list of proactive nudge strings for the morning briefing.

    A nudge reminds the pastor of a member they haven't followed up with,
    based on the most recent MemberNote for each person with open care or notes.

    Logic:
      - Find members who have a MemberNote with a context_tag indicating
        a meaningful life event (job, health, family, etc.)
      - Filter to those not touched in the last NUDGE_LOOKBACK_DAYS days
      - Return up to 3 nudge strings (keeps the briefing from overwhelming)

    Returns:
        List of human-readable nudge strings, e.g.:
        "You haven't called John since he mentioned losing his job 3 weeks ago"
    """
    today = date.today()
    cutoff = datetime.utcnow() - timedelta(days=NUDGE_LOOKBACK_DAYS)

    # Pull recent notes that suggest a pastoral need
    meaningful_tags = {
        "job", "health", "family", "grief", "crisis", "prayer",
        "hospital", "financial", "marriage", "counseling", "struggling",
    }

    # Get the most recent note per member
    recent_notes = (
        db.query(MemberNote)
        .filter(MemberNote.church_id == church_id)
        .filter(MemberNote.created_at < cutoff)
        .filter(MemberNote.context_tag.isnot(None))
        .order_by(MemberNote.created_at.desc())
        .all()
    )

    seen_members = set()
    nudges = []

    for note in recent_notes:
        if note.member_id in seen_members:
            continue
        if note.context_tag and note.context_tag.lower() not in meaningful_tags:
            continue

        seen_members.add(note.member_id)
        member = note.member
        if not member:
            continue

        days_since = (datetime.utcnow() - note.created_at).days
        # Truncate the note to a short summary (first 80 chars)
        short_note = note.note_text[:80].rstrip() + ("…" if len(note.note_text) > 80 else "")
        note_date_str = note.created_at.strftime("%B %-d")

        nudge = (
            f"You haven't connected with {member.full_name} since {note_date_str} "
            f"({days_since} days ago). They mentioned: \"{short_note}\""
        )
        nudges.append(nudge)

        if len(nudges) >= 3:
            break

    return nudges


# ── Birthday / Anniversary Drafts ─────────────────────────────────────────────

def draft_birthday_message(
    member: Member,
    pastor_name: str = "Pastor",
) -> str:
    """
    Draft a birthday greeting for a member.

    Args:
        member:      The Member ORM object.
        pastor_name: Pastor's first name for the signature.

    Returns:
        A short, warm birthday text draft.
    """
    return voice.BIRTHDAY_TEMPLATE.format(
        first_name=member.first_name,
        pastor_name=pastor_name,
    )


def draft_anniversary_message(
    member: Member,
    pastor_name: str = "Pastor",
) -> str:
    """
    Draft a wedding anniversary greeting for a member.

    Args:
        member:      The Member ORM object.
        pastor_name: Pastor's first name for the signature.

    Returns:
        A short, warm anniversary text draft.
    """
    today = date.today()
    years = today.year - member.anniversary.year if member.anniversary else 0
    return voice.ANNIVERSARY_TEMPLATE.format(
        first_name=member.first_name,
        years=years,
        pastor_name=pastor_name,
    )


# ── Absence Check-In Drafts ───────────────────────────────────────────────────

def draft_absence_checkin(
    member: Member,
    pastor_name: str = "Pastor",
    church_name: str = "the church",
) -> str:
    """
    Draft a gentle 'we missed you' message for an absent member.

    Args:
        member:      The Member ORM object.
        pastor_name: Pastor's first name for the signature.
        church_name: Church name.

    Returns:
        A warm, zero-pressure check-in text draft.
    """
    return voice.ABSENCE_CHECKIN_TEMPLATE.format(
        first_name=member.first_name,
        pastor_name=pastor_name,
        church_name=church_name,
    )


# ── Briefing as Plain Text (for Telegram / email) ────────────────────────────

def render_briefing_text(briefing: dict) -> str:
    """
    Render a briefing dict as a human-readable plain-text string.

    Suitable for Telegram messages, email body, or stdout logging.
    Uses emoji section headers for readability on mobile.

    Args:
        briefing: The dict returned by generate_morning_briefing().

    Returns:
        Formatted multi-line string.
    """
    lines = [briefing["greeting"], ""]

    # Birthdays
    birthdays = briefing.get("birthdays_this_week", [])
    if birthdays:
        lines.append("🎂 BIRTHDAYS THIS WEEK")
        for m in birthdays:
            bday_str = m.birthday.strftime("%A") if m.birthday else ""
            lines.append(f"• {m.full_name} — birthday {bday_str}")
        lines.append("")

    # Anniversaries
    anniversaries = briefing.get("anniversaries_this_week", [])
    if anniversaries:
        lines.append("💍 ANNIVERSARIES THIS WEEK")
        for m in anniversaries:
            ann_str = m.anniversary.strftime("%A") if m.anniversary else ""
            lines.append(f"• {m.full_name} — anniversary {ann_str}")
        lines.append("")

    # Visitor follow-up
    visitors = briefing.get("visitors_needing_followup", [])
    if visitors:
        lines.append("👋 VISITOR FOLLOW-UP NEEDED")
        for v in visitors:
            days_ago = (date.today() - v.visit_date).days
            lines.append(f"• {v.full_name} — visited {days_ago} days ago, no follow-up yet")
        lines.append("")

    # Active care cases
    care_cases = briefing.get("active_care_cases", [])
    if care_cases:
        lines.append("🏥 ACTIVE CARE CASES")
        for c in care_cases:
            member_name = c.member.full_name if c.member else "Unknown"
            last = f"Last contact: {c.last_contact}" if c.last_contact else "No contact logged"
            lines.append(f"• {member_name} [{c.category}] — {last}")
        lines.append("")

    # Absent members
    absent = briefing.get("absent_members", [])
    if absent:
        lines.append("⏰ ABSENT MEMBERS")
        for m in absent:
            days_ago = (date.today() - m.last_attendance).days if m.last_attendance else "?"
            lines.append(f"• {m.full_name} — {days_ago} days since last attendance")
        lines.append("")

    # Prayer requests
    prayers = briefing.get("unanswered_prayers", [])
    if prayers:
        lines.append("🙏 PRAYER REQUESTS NEEDING FOLLOW-UP")
        for p in prayers:
            name = p.member.full_name if p.member else (p.submitted_by or "Anonymous")
            days_ago = (datetime.utcnow() - p.created_at).days
            snippet = p.request_text[:60] + ("…" if len(p.request_text) > 60 else "")
            lines.append(f"• {name} — \"{snippet}\" ({days_ago} days, no update)")
        lines.append("")

    # Nudges
    nudges = briefing.get("nudges", [])
    if nudges:
        lines.append("💭 TODAY'S NUDGE")
        for nudge in nudges:
            lines.append(f"• {nudge}")
        lines.append("")

    if not any([birthdays, anniversaries, visitors, care_cases, absent, prayers, nudges]):
        lines.append(voice.BRIEFING_EMPTY_STATE)

    return "\n".join(lines)
