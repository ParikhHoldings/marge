"""
Marge's brain — briefing generation, nudge logic, and draft generation.

All the pastoral intelligence lives here. The routers call these functions;
the functions pull from the DB and return clean dicts or strings that the
API layer serializes and delivers.
"""

import os
import anthropic as anthropic_sdk
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


def _get_anthropic_client():
    """Return Anthropic client if API key is available, else None."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic_sdk.Anthropic(api_key=api_key)


# ── Morning Briefing ──────────────────────────────────────────────────────────

def generate_morning_briefing(db: Session, pastor_name: str, church_name: str) -> dict:
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
        "birthdays_this_week": _get_birthdays_this_week(db, today, week_end),
        "anniversaries_this_week": _get_anniversaries_this_week(db, today, week_end),
        "visitors_needing_followup": _get_visitors_needing_followup(db, today),
        "active_care_cases": _get_active_care_cases(db, today),
        "absent_members": _get_absent_members(db, today),
        "unanswered_prayers": _get_unanswered_prayers(db, today),
        "nudges": get_nudges(db),
    }

    return briefing


def generate_ai_briefing(briefing_data: dict, pastor_name: str, church_name: str) -> str:
    """
    Generate a Claude-powered AI briefing that reasons about pastoral needs.

    Args:
        briefing_data: The dict returned by generate_morning_briefing() (with ORM objects)
        pastor_name:   Pastor's first name
        church_name:   Church name

    Returns:
        AI-generated prose briefing, or falls back to plain_text if no API key.
    """
    client = _get_anthropic_client()
    if not client:
        # Graceful fallback to plain text
        return render_briefing_text(briefing_data)

    # Build context for Claude
    sections = []

    birthdays = briefing_data.get("birthdays_this_week", [])
    if birthdays:
        names = [m.full_name for m in birthdays]
        sections.append(f"Birthdays this week: {', '.join(names)}")

    anniversaries = briefing_data.get("anniversaries_this_week", [])
    if anniversaries:
        names = [m.full_name for m in anniversaries]
        sections.append(f"Anniversaries this week: {', '.join(names)}")

    visitors = briefing_data.get("visitors_needing_followup", [])
    if visitors:
        v_info = [f"{v.full_name} (visited {(date.today()-v.visit_date).days} days ago{', notes: ' + v.notes if v.notes else ''})" for v in visitors]
        sections.append(f"Visitors needing follow-up: {', '.join(v_info)}")

    care = briefing_data.get("active_care_cases", [])
    if care:
        c_info = []
        for c in care:
            member_name = c.member.full_name if c.member else "Unknown"
            last = f"last contact {(date.today()-c.last_contact).days} days ago" if c.last_contact else "no contact logged"
            c_info.append(f"{member_name} [{c.category.value if hasattr(c.category, 'value') else c.category}] - {last}: {c.description[:80] if c.description else 'no details'}")
        sections.append("Active care cases:\n" + "\n".join(f"  - {x}" for x in c_info))

    absent = briefing_data.get("absent_members", [])
    if absent:
        a_info = [f"{m.full_name} ({(date.today()-m.last_attendance).days} days)" for m in absent if m.last_attendance]
        if a_info:
            sections.append(f"Absent members: {', '.join(a_info)}")

    prayers = briefing_data.get("unanswered_prayers", [])
    if prayers:
        p_info = [f"{p.member.full_name if p.member else (p.submitted_by or 'Anonymous')}: \"{p.request_text[:60]}\"" for p in prayers]
        sections.append("Prayer requests needing follow-up:\n" + "\n".join(f"  - {x}" for x in p_info))

    nudges = briefing_data.get("nudges", [])
    if nudges:
        sections.append("Notes from recent conversations:\n" + "\n".join(f"  - {n}" for n in nudges))

    if not sections:
        context = "No urgent pastoral needs flagged today. The congregation appears to be in good shape."
    else:
        context = "\n\n".join(sections)

    prompt = f"""You are Marge, the AI church secretary for {pastor_name} at {church_name}.

You have just reviewed all the pastoral care data for today. Here is what you found:

{context}

Write Pastor {pastor_name}'s morning briefing. You are a wise, warm church secretary who genuinely knows and cares about these people.

Rules:
- Use the pastor's first name ({pastor_name}), not "Pastor {pastor_name}" every time
- Mention specific people by name with specific context
- Connect dots the pastor might miss (e.g., someone who lost their job AND stopped attending — those are probably related)
- Prioritize by urgency, not alphabetically
- Be concise — pastors are busy Monday mornings
- End with 1-2 specific suggested actions for today
- Never sound like a CRM. Sound like a person who knows these people.
- Warm, direct, helpful. No fluff. No bullet points — write it as natural prose.
- If nothing urgent, say so warmly and suggest one proactive thing to do.

Write the briefing now."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        # Fallback to plain text on any API error
        return render_briefing_text(briefing_data) + f"\n\n[AI briefing unavailable: {str(e)[:100]}]"


def _get_birthdays_this_week(db: Session, today: date, week_end: date) -> List[Member]:
    """
    Return Members whose birthday falls within the next BIRTHDAY_LOOKAHEAD_DAYS days.

    Compares month+day only (year-agnostic).
    """
    members = db.query(Member).filter(Member.birthday.isnot(None)).all()
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


def _get_anniversaries_this_week(db: Session, today: date, week_end: date) -> List[Member]:
    """
    Return Members whose wedding anniversary falls within the next BIRTHDAY_LOOKAHEAD_DAYS days.
    """
    members = db.query(Member).filter(Member.anniversary.isnot(None)).all()
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


def _get_visitors_needing_followup(db: Session, today: date) -> List[Visitor]:
    """
    Return Visitors where:
      - visit_date was at least VISITOR_FOLLOWUP_DELAY_HOURS ago
      - Day-1 follow-up has NOT been sent yet
    """
    cutoff = today - timedelta(hours=VISITOR_FOLLOWUP_DELAY_HOURS / 24)
    return (
        db.query(Visitor)
        .filter(Visitor.visit_date <= cutoff)
        .filter(Visitor.follow_up_day1_sent == False)  # noqa: E712
        .order_by(Visitor.visit_date)
        .all()
    )


def _get_active_care_cases(db: Session, today: date) -> List[CareNote]:
    """
    Return CareNotes where:
      - status = 'active'
      - last_contact is NULL or was more than CARE_OVERDUE_DAYS ago
    """
    cutoff = today - timedelta(days=CARE_OVERDUE_DAYS)
    return (
        db.query(CareNote)
        .filter(CareNote.status == "active")
        .filter(
            (CareNote.last_contact == None) |  # noqa: E711
            (CareNote.last_contact < cutoff)
        )
        .order_by(CareNote.last_contact.asc().nullsfirst())
        .all()
    )


def _get_absent_members(db: Session, today: date) -> List[Member]:
    """
    Return Members where last_attendance was more than ABSENCE_THRESHOLD_DAYS ago.

    Members with no attendance record at all are not surfaced (they may be new
    additions without a history yet). Only those who have attended before and
    then went absent are flagged.
    """
    cutoff = today - timedelta(days=ABSENCE_THRESHOLD_DAYS)
    return (
        db.query(Member)
        .filter(Member.last_attendance.isnot(None))
        .filter(Member.last_attendance < cutoff)
        .order_by(Member.last_attendance.asc())
        .all()
    )


def _get_unanswered_prayers(db: Session, today: date) -> List[PrayerRequest]:
    """
    Return PrayerRequests that are:
      - status = 'active'
      - created_at more than PRAYER_OVERDUE_DAYS ago with no update
    """
    cutoff = datetime.utcnow() - timedelta(days=PRAYER_OVERDUE_DAYS)
    return (
        db.query(PrayerRequest)
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
    Draft a follow-up message for a first-time visitor using Claude.

    Args:
        visitor:         The Visitor ORM object.
        day:             1 = same-day text, 3 = 3-day email, 14 = two-week invitation.
        pastor_name:     Pastor's first name for the signature.
        church_name:     Church name for the closing.
        event_or_service: Used in the Week-2 invitation template.

    Returns:
        A warm, AI-generated draft the pastor reviews before sending.
    """
    client = _get_anthropic_client()

    if not client:
        # Fallback to templates
        first_name = visitor.first_name
        if day == 1:
            return voice.FOLLOW_UP_DAY1_TEMPLATE.format(
                first_name=first_name, pastor_name=pastor_name, church_name=church_name,
            )
        elif day == 3:
            return voice.FOLLOW_UP_DAY3_TEMPLATE.format(
                first_name=first_name, pastor_name=pastor_name, church_name=church_name,
            )
        elif day == 14:
            return voice.FOLLOW_UP_WEEK2_TEMPLATE.format(
                first_name=first_name, pastor_name=pastor_name, church_name=church_name,
                event_or_service=event_or_service,
            )
        else:
            return (
                f"Hey {first_name}, just wanted to check in and see how you're doing. "
                f"We'd love to see you again sometime. — {pastor_name}"
            )

    day_context = {
        1: "This is a same-day or next-day brief friendly text.",
        3: "This is a 3-day follow-up — a bit warmer, maybe answer a question they might have.",
        14: "This is a 2-week gentle invitation to come back.",
    }.get(day, "This is a follow-up message.")

    days_since = (date.today() - visitor.visit_date).days

    prompt = f"""Draft a {day_context} from {pastor_name} to {visitor.full_name}, who visited {church_name} {days_since} days ago.

Additional context: {visitor.notes or 'none'}

Rules:
- Short (2-4 sentences max for text, 4-6 for email)
- Warm, genuine, zero pressure
- No sales language
- Sounds like it was written by a real person, not a church office
- Sign off as "{pastor_name}" (first name only)

Write only the message, no subject line or explanation."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        # Fallback to template on error
        first_name = visitor.first_name
        if day == 1:
            return voice.FOLLOW_UP_DAY1_TEMPLATE.format(
                first_name=first_name, pastor_name=pastor_name, church_name=church_name,
            )
        return f"Hey {first_name}, just wanted to reach out. — {pastor_name}"


# ── Pastoral Care Drafts ──────────────────────────────────────────────────────

def draft_care_message(
    member: Member,
    situation: str,
    pastor_name: str = "Pastor",
) -> str:
    """
    Draft a pastoral care message for a member in a difficult situation using Claude.
    """
    client = _get_anthropic_client()

    if not client:
        # Template fallback
        first_name = member.first_name
        situation_lower = situation.lower()
        if "hospital" in situation_lower or "surgery" in situation_lower or "medical" in situation_lower:
            return voice.CARE_MESSAGE_HOSPITAL.format(first_name=first_name, pastor_name=pastor_name)
        elif "grief" in situation_lower or "loss" in situation_lower or "death" in situation_lower:
            return voice.CARE_MESSAGE_GRIEF.format(first_name=first_name, pastor_name=pastor_name)
        elif "crisis" in situation_lower or "emergency" in situation_lower:
            return voice.CARE_MESSAGE_CRISIS.format(first_name=first_name, pastor_name=pastor_name)
        else:
            return voice.CARE_MESSAGE_GENERAL.format(first_name=first_name, pastor_name=pastor_name)

    prompt = f"""Draft a pastoral care message from {pastor_name} to {member.full_name}.

Situation: {situation}

Rules:
- Warm, compassionate, genuinely human
- Short (3-5 sentences)
- Offer presence, not solutions
- No clichés ("everything happens for a reason", "God has a plan")
- Sign off as "{pastor_name}" (first name only)

Write only the message."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception:
        return voice.CARE_MESSAGE_GENERAL.format(first_name=member.first_name, pastor_name=pastor_name)


# ── Birthday / Anniversary Drafts ─────────────────────────────────────────────

def draft_birthday_message(
    member: Member,
    pastor_name: str = "Pastor",
) -> str:
    """Draft a birthday greeting for a member using Claude."""
    client = _get_anthropic_client()

    if not client:
        return voice.BIRTHDAY_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name)

    prompt = f"""Draft a brief, warm birthday text message from {pastor_name} to {member.full_name}.

Rules:
- 1-3 sentences max
- Genuine and personal, not generic
- No "may God bless you abundantly" clichés
- Sign off as "{pastor_name}" (first name only)

Write only the message."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception:
        return voice.BIRTHDAY_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name)


def draft_anniversary_message(
    member: Member,
    pastor_name: str = "Pastor",
) -> str:
    """Draft a wedding anniversary greeting for a member using Claude."""
    client = _get_anthropic_client()

    today = date.today()
    years = today.year - member.anniversary.year if member.anniversary else 0

    if not client:
        return voice.ANNIVERSARY_TEMPLATE.format(
            first_name=member.first_name, years=years, pastor_name=pastor_name,
        )

    prompt = f"""Draft a brief, warm anniversary text message from {pastor_name} to {member.full_name}, celebrating {years} years of marriage.

Rules:
- 1-3 sentences max
- Genuine warmth, not formulaic
- Sign off as "{pastor_name}" (first name only)

Write only the message."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception:
        return voice.ANNIVERSARY_TEMPLATE.format(
            first_name=member.first_name, years=years, pastor_name=pastor_name,
        )


# ── Absence Check-In Drafts ───────────────────────────────────────────────────

def draft_absence_checkin(
    member: Member,
    pastor_name: str = "Pastor",
    church_name: str = "the church",
) -> str:
    """Draft a gentle 'we missed you' message for an absent member using Claude."""
    client = _get_anthropic_client()

    if not client:
        return voice.ABSENCE_CHECKIN_TEMPLATE.format(
            first_name=member.first_name, pastor_name=pastor_name, church_name=church_name,
        )

    days_absent = (date.today() - member.last_attendance).days if member.last_attendance else None
    time_context = f"{days_absent} days" if days_absent else "a while"

    prompt = f"""Draft a gentle check-in text from {pastor_name} to {member.full_name}, who hasn't been to {church_name} in {time_context}.

Rules:
- Zero pressure, zero guilt
- Genuinely warm — this is a person, not a metric
- 2-4 sentences
- Just checking in, not recruiting
- Sign off as "{pastor_name}" (first name only)

Write only the message."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception:
        return voice.ABSENCE_CHECKIN_TEMPLATE.format(
            first_name=member.first_name, pastor_name=pastor_name, church_name=church_name,
        )


# ── Proactive Nudges ──────────────────────────────────────────────────────────

def get_nudges(db: Session) -> List[str]:
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
