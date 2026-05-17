"""
Marge's brain — briefing generation, nudge logic, and draft generation.

All the pastoral intelligence lives here. The routers call these functions;
the functions pull from the DB and return clean dicts or strings that the
API layer serializes and delivers.
"""

import os
import re
import anthropic as anthropic_sdk
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import Member, Visitor, CareNote, PrayerRequest, MemberNote
from app import marge_voice as voice


ABSENCE_THRESHOLD_DAYS = int(os.getenv("ABSENCE_THRESHOLD_DAYS", "21"))
CARE_OVERDUE_DAYS = int(os.getenv("CARE_OVERDUE_DAYS", "7"))
PRAYER_OVERDUE_DAYS = int(os.getenv("PRAYER_OVERDUE_DAYS", "14"))
VISITOR_FOLLOWUP_DELAY_HOURS = int(os.getenv("VISITOR_FOLLOWUP_DELAY_HOURS", "48"))
BIRTHDAY_LOOKAHEAD_DAYS = int(os.getenv("BIRTHDAY_LOOKAHEAD_DAYS", "7"))
NUDGE_LOOKBACK_DAYS = int(os.getenv("NUDGE_LOOKBACK_DAYS", "14"))
_PASTOR_TITLE_RE = re.compile(r"^(pastor|rev\.?|reverend|bishop|elder|father|dr\.?)\b", re.IGNORECASE)


def pastor_display_name(pastor_name: str = "Pastor") -> str:
    """Return a natural display name for pastor-authored copy."""
    cleaned = (pastor_name or "").strip()
    if not cleaned:
        return "Pastor"
    if _PASTOR_TITLE_RE.match(cleaned):
        return cleaned
    return f"Pastor {cleaned}"


def _get_anthropic_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic_sdk.Anthropic(api_key=api_key)


def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=api_key)


def _call_llm(prompt: str, *, max_tokens: int = 400, temperature: float = 0.5) -> Tuple[Optional[str], Optional[str]]:
    anthropic_client = _get_anthropic_client()
    if anthropic_client:
        try:
            message = anthropic_client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-7-sonnet-latest"),
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip(), "anthropic"
        except Exception:
            pass

    openai_client = _get_openai_client()
    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip(), "openai"
        except Exception:
            pass

    return None, None


def ai_provider_name() -> Optional[str]:
    _text, provider = _call_llm("Reply with the word ready.", max_tokens=8, temperature=0)
    return provider


def generate_morning_briefing(db: Session, pastor_name: str, church_name: str, account_id: Optional[int] = None) -> dict:
    today = date.today()
    week_end = today + timedelta(days=BIRTHDAY_LOOKAHEAD_DAYS)

    return {
        "greeting": voice.PASTOR_GREETING.format(pastor_name=pastor_name),
        "pastor_name": pastor_name,
        "church_name": church_name,
        "generated_at": datetime.utcnow().isoformat(),
        "birthdays_this_week": _get_birthdays_this_week(db, today, week_end, account_id),
        "anniversaries_this_week": _get_anniversaries_this_week(db, today, week_end, account_id),
        "visitors_needing_followup": _get_visitors_needing_followup(db, today, account_id),
        "active_care_cases": _get_active_care_cases(db, today, account_id),
        "absent_members": _get_absent_members(db, today, account_id),
        "unanswered_prayers": _get_unanswered_prayers(db, today, account_id),
        "nudges": get_nudges(db, account_id),
    }


def generate_ai_briefing(briefing_data: dict, pastor_name: str, church_name: str) -> str:
    provider = ai_provider_name()
    if not provider:
        return render_briefing_text(briefing_data)

    sections = []

    birthdays = briefing_data.get("birthdays_this_week", [])
    if birthdays:
        sections.append(f"Birthdays this week: {', '.join(m.full_name for m in birthdays)}")

    anniversaries = briefing_data.get("anniversaries_this_week", [])
    if anniversaries:
        sections.append(f"Anniversaries this week: {', '.join(m.full_name for m in anniversaries)}")

    visitors = briefing_data.get("visitors_needing_followup", [])
    if visitors:
        visitor_lines = []
        for v in visitors:
            note = f", notes: {v.notes}" if v.notes else ""
            visitor_lines.append(f"{v.full_name} (visited {(date.today() - v.visit_date).days} days ago{note})")
        sections.append("Visitors needing follow-up: " + "; ".join(visitor_lines))

    care = briefing_data.get("active_care_cases", [])
    if care:
        lines = []
        for c in care:
            member_name = c.member.full_name if c.member else "Unknown"
            last = f"last contact {(date.today() - c.last_contact).days} days ago" if c.last_contact else "no contact logged"
            category = c.category.value if hasattr(c.category, "value") else c.category
            details = c.description[:120] if c.description else "no details"
            lines.append(f"{member_name} [{category}] — {last}: {details}")
        sections.append("Active care cases:\n" + "\n".join(f"- {line}" for line in lines))

    absent = briefing_data.get("absent_members", [])
    if absent:
        sections.append(
            "Absent members: " + ", ".join(
                f"{m.full_name} ({(date.today() - m.last_attendance).days} days)" for m in absent if m.last_attendance
            )
        )

    prayers = briefing_data.get("unanswered_prayers", [])
    if prayers:
        sections.append(
            "Prayer requests needing follow-up:\n" + "\n".join(
                f"- {(p.member.full_name if p.member else (p.submitted_by or 'Anonymous'))}: \"{p.request_text[:90]}\""
                for p in prayers
            )
        )

    nudges = briefing_data.get("nudges", [])
    if nudges:
        sections.append("Relational nudges:\n" + "\n".join(f"- {n}" for n in nudges))

    context = "\n\n".join(sections) if sections else "No urgent pastoral needs flagged today."

    prompt = f"""You are Marge, the AI church secretary for {pastor_name} at {church_name}.

Here is today's pastoral context:
{context}

Write a genuinely useful morning briefing for {pastor_name}.
Rules:
- Sound like a wise, warm ministry assistant, not software
- Mention specific people and connect dots the pastor might miss
- Prioritize by pastoral urgency
- Keep it concise enough to read on a phone
- End with 2-3 very specific suggested next moves for today
- Plain natural prose, not bullets
- Never say 'based on the data' or sound like a dashboard
"""

    text, _provider = _call_llm(prompt, max_tokens=700, temperature=0.35)
    return text or render_briefing_text(briefing_data)


def _scope_account(query, model, account_id: Optional[int]):
    if hasattr(model, "account_id"):
        return query.filter(model.account_id == account_id)
    return query


def _get_birthdays_this_week(db: Session, today: date, week_end: date, account_id: Optional[int]) -> List[Member]:
    members = _scope_account(db.query(Member), Member, account_id).filter(Member.birthday.isnot(None)).all()
    result = []
    for m in members:
        bday = m.birthday
        try:
            this_year_bday = bday.replace(year=today.year)
        except ValueError:
            this_year_bday = bday.replace(year=today.year, day=28)
        if today <= this_year_bday <= week_end:
            result.append(m)
        else:
            try:
                next_year_bday = bday.replace(year=today.year + 1)
            except ValueError:
                next_year_bday = bday.replace(year=today.year + 1, day=28)
            if today <= next_year_bday <= week_end:
                result.append(m)
    return result


def _get_anniversaries_this_week(db: Session, today: date, week_end: date, account_id: Optional[int]) -> List[Member]:
    members = _scope_account(db.query(Member), Member, account_id).filter(Member.anniversary.isnot(None)).all()
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


def _get_visitors_needing_followup(db: Session, today: date, account_id: Optional[int]) -> List[Visitor]:
    cutoff = today - timedelta(hours=VISITOR_FOLLOWUP_DELAY_HOURS / 24)
    return (
        _scope_account(db.query(Visitor), Visitor, account_id)
        .filter(Visitor.visit_date <= cutoff)
        .filter(Visitor.follow_up_day1_sent == False)
        .order_by(Visitor.visit_date)
        .all()
    )


def _get_active_care_cases(db: Session, today: date, account_id: Optional[int]) -> List[CareNote]:
    cutoff = today - timedelta(days=CARE_OVERDUE_DAYS)
    return (
        _scope_account(db.query(CareNote), CareNote, account_id)
        .filter(CareNote.status == "active")
        .filter((CareNote.last_contact == None) | (CareNote.last_contact < cutoff))
        .order_by(CareNote.last_contact.asc().nullsfirst())
        .all()
    )


def _get_absent_members(db: Session, today: date, account_id: Optional[int]) -> List[Member]:
    cutoff = today - timedelta(days=ABSENCE_THRESHOLD_DAYS)
    return (
        _scope_account(db.query(Member), Member, account_id)
        .filter(Member.last_attendance.isnot(None))
        .filter(Member.last_attendance < cutoff)
        .order_by(Member.last_attendance.asc())
        .all()
    )


def _get_unanswered_prayers(db: Session, today: date, account_id: Optional[int]) -> List[PrayerRequest]:
    cutoff = datetime.utcnow() - timedelta(days=PRAYER_OVERDUE_DAYS)
    return (
        _scope_account(db.query(PrayerRequest), PrayerRequest, account_id)
        .filter(PrayerRequest.status == "active")
        .filter(PrayerRequest.created_at < cutoff)
        .order_by(PrayerRequest.created_at.asc())
        .all()
    )


def draft_visitor_followup(
    visitor: Visitor,
    day: int,
    pastor_name: str = "Pastor",
    church_name: str = "our church",
    event_or_service: str = "Sunday service",
    communication_style: Optional[str] = None,
    faith_tradition: Optional[str] = None,
) -> str:
    pastor_name = pastor_display_name(pastor_name)
    if not ai_provider_name():
        first_name = visitor.first_name or "friend"
        visitor_note = _visitor_note_sentence(visitor.notes)
        style = (communication_style or "").lower()
        if day == 1:
            if "formal" in style:
                return (
                    f"Hello {first_name}, this is {pastor_name} from {church_name}. "
                    f"Thank you for visiting with us Sunday. {visitor_note}"
                    "I would be glad to answer any questions or help you take a next step whenever you are ready.\n\n"
                    f"- {pastor_name}"
                )
            if "direct" in style:
                return (
                    f"Hey {first_name}, this is {pastor_name} from {church_name}. "
                    f"I was glad you joined us Sunday. {visitor_note}"
                    "Would it help if I followed up this week to answer questions or help you get connected?\n\n"
                    f"- {pastor_name}"
                )
            if "brief" in style:
                return (
                    f"Hi {first_name}, this is {pastor_name} from {church_name}. "
                    f"I was glad you joined us Sunday. {visitor_note}"
                    "No pressure at all; I just wanted you to know you are welcome here.\n\n"
                    f"- {pastor_name}"
                )
            return (
                f"Hi {first_name}, this is {pastor_name} from {church_name}. "
                f"I was really glad you joined us Sunday. {visitor_note}"
                "I would love to hear how your visit was and answer any questions you have.\n\n"
                f"- {pastor_name}"
            )
        if day == 3:
            return voice.FOLLOW_UP_DAY3_TEMPLATE.format(first_name=first_name, pastor_name=pastor_name, church_name=church_name)
        if day == 14:
            return voice.FOLLOW_UP_WEEK2_TEMPLATE.format(first_name=first_name, pastor_name=pastor_name, church_name=church_name, event_or_service=event_or_service)
        return f"Hey {first_name}, just wanted to check in and see how you're doing. We'd love to see you again sometime. — {pastor_name}"

    prompt = f"""Draft a follow-up from {pastor_name} to {visitor.full_name}, who visited {church_name} {(date.today() - visitor.visit_date).days} days ago.
Additional visitor context: {visitor.notes or 'none'}
Pastor's saved drafting voice: {communication_style or 'warm and brief'}
Church voice or tradition to respect: {faith_tradition or 'plain pastoral language'}
This is follow-up day {day}.
Rules: short, human, no pressure, mention specific visitor context when helpful, sign off as {pastor_name}. Return only the message."""
    text, _provider = _call_llm(prompt, max_tokens=220, temperature=0.65)
    return text or voice.FOLLOW_UP_DAY1_TEMPLATE.format(first_name=visitor.first_name, pastor_name=pastor_name, church_name=church_name)


def _visitor_note_sentence(raw_note: Optional[str]) -> str:
    note = (raw_note or "").strip()
    if not note:
        return ""
    note = re.sub(r"\s+", " ", note).strip(" .")
    note = re.sub(r"^(?:and\s+)?(?:they\s+)?(?:he\s+|she\s+)?(?:asked about|asked)\s+", "", note, flags=re.IGNORECASE)
    note = re.sub(r"^about\s+", "", note, flags=re.IGNORECASE)
    if not note:
        return ""
    note = note[:1].lower() + note[1:]
    return f"I saw your note about {note}, and I would be glad to help with that. "


def draft_care_message(
    member: Member,
    situation: str,
    pastor_name: str = "Pastor",
    communication_style: Optional[str] = None,
    faith_tradition: Optional[str] = None,
) -> str:
    pastor_name = pastor_display_name(pastor_name)
    if not ai_provider_name():
        first_name = member.first_name or "friend"
        situation_lower = situation.lower()
        situation_sentence = _care_situation_sentence(situation)
        style = (communication_style or "").lower()
        if situation_sentence:
            if "formal" in style:
                return (
                    f"Hello {first_name}, this is {pastor_name}. "
                    f"I have been thinking about you and praying for you. {situation_sentence}"
                    "I would be glad to talk or visit if that would be helpful.\n\n"
                    f"- {pastor_name}"
                )
            if "direct" in style:
                return (
                    f"Hey {first_name}, this is {pastor_name}. "
                    f"{situation_sentence}"
                    "Can I check in or find a time to visit this week?\n\n"
                    f"- {pastor_name}"
                )
            if "brief" in style:
                return (
                    f"Hey {first_name}, this is {pastor_name}. "
                    f"{situation_sentence}"
                    "I'm praying for you and would be glad to visit if that would help.\n\n"
                    f"- {pastor_name}"
                )
        if any(x in situation_lower for x in ["hospital", "surgery", "medical"]):
            return voice.CARE_MESSAGE_HOSPITAL.format(first_name=first_name, pastor_name=pastor_name)
        if any(x in situation_lower for x in ["grief", "loss", "death"]):
            return voice.CARE_MESSAGE_GRIEF.format(first_name=first_name, pastor_name=pastor_name)
        if any(x in situation_lower for x in ["crisis", "emergency"]):
            return voice.CARE_MESSAGE_CRISIS.format(first_name=first_name, pastor_name=pastor_name)
        return voice.CARE_MESSAGE_GENERAL.format(first_name=first_name, pastor_name=pastor_name)

    prompt = f"""Draft a pastoral care message from {pastor_name} to {member.full_name}.
Situation: {situation}.
Pastor's saved drafting voice: {communication_style or 'warm and brief'}.
Church voice or tradition to respect: {faith_tradition or 'plain pastoral language'}.
Rules: warm, short, human, no cliches, include the concrete care context when helpful, sign off as {pastor_name}. Return only the message."""
    text, _provider = _call_llm(prompt, max_tokens=220, temperature=0.65)
    return text or voice.CARE_MESSAGE_GENERAL.format(first_name=member.first_name, pastor_name=pastor_name)


def _care_situation_sentence(raw_situation: Optional[str]) -> str:
    situation = (raw_situation or "").strip()
    if not situation:
        return ""
    situation = re.sub(r"\s+", " ", situation).strip(" .")
    situation = re.sub(r"^(?:general|hospital|grief|crisis|medical)\s+", "", situation, flags=re.IGNORECASE)
    situation = re.sub(r"^(?:and\s+)?(?:is\s+|has\s+|was\s+)?", "", situation, flags=re.IGNORECASE)
    if not situation:
        return ""
    situation = situation[:1].lower() + situation[1:]
    return f"I saw the note about {situation}. "


def draft_birthday_message(member: Member, pastor_name: str = "Pastor") -> str:
    pastor_name = pastor_display_name(pastor_name)
    if not ai_provider_name():
        return voice.BIRTHDAY_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name)
    prompt = f"Draft a brief warm birthday text from {pastor_name} to {member.full_name}. 1-3 sentences, human, no cliches, sign off as {pastor_name}. Return only the message."
    text, _provider = _call_llm(prompt, max_tokens=120, temperature=0.6)
    return text or voice.BIRTHDAY_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name)


def draft_anniversary_message(member: Member, pastor_name: str = "Pastor") -> str:
    pastor_name = pastor_display_name(pastor_name)
    today = date.today()
    years = today.year - member.anniversary.year if member.anniversary else 0
    if not ai_provider_name():
        return voice.ANNIVERSARY_TEMPLATE.format(first_name=member.first_name, years=years, pastor_name=pastor_name)
    prompt = f"Draft a brief warm anniversary text from {pastor_name} to {member.full_name} celebrating {years} years. 1-3 sentences, human, sign off as {pastor_name}. Return only the message."
    text, _provider = _call_llm(prompt, max_tokens=120, temperature=0.6)
    return text or voice.ANNIVERSARY_TEMPLATE.format(first_name=member.first_name, years=years, pastor_name=pastor_name)


def draft_absence_checkin(member: Member, pastor_name: str = "Pastor", church_name: str = "the church") -> str:
    pastor_name = pastor_display_name(pastor_name)
    if not ai_provider_name():
        return voice.ABSENCE_CHECKIN_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name, church_name=church_name)
    days_absent = (date.today() - member.last_attendance).days if member.last_attendance else None
    prompt = f"Draft a gentle zero-pressure check-in text from {pastor_name} to {member.full_name}, who hasn't been to {church_name} in {days_absent or 'a while'}. 2-4 sentences, warm, not recruiting, sign off as {pastor_name}. Return only the message."
    text, _provider = _call_llm(prompt, max_tokens=160, temperature=0.65)
    return text or voice.ABSENCE_CHECKIN_TEMPLATE.format(first_name=member.first_name, pastor_name=pastor_name, church_name=church_name)


def get_nudges(db: Session, account_id: Optional[int] = None) -> List[str]:
    cutoff = datetime.utcnow() - timedelta(days=NUDGE_LOOKBACK_DAYS)
    meaningful_tags = {"job", "health", "family", "grief", "crisis", "prayer", "hospital", "financial", "marriage", "counseling", "struggling"}
    recent_notes = (
        _scope_account(db.query(MemberNote), MemberNote, account_id)
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
        short_note = note.note_text[:80].rstrip() + ("…" if len(note.note_text) > 80 else "")
        note_date_str = note.created_at.strftime("%B %d").replace(" 0", " ")
        nudges.append(f"You haven't connected with {member.full_name} since {note_date_str} ({days_since} days ago). They mentioned: \"{short_note}\"")
        if len(nudges) >= 3:
            break
    return nudges


def render_briefing_text(briefing: dict) -> str:
    lines = [briefing["greeting"], ""]

    birthdays = briefing.get("birthdays_this_week", [])
    if birthdays:
        lines.append("🎂 BIRTHDAYS THIS WEEK")
        for m in birthdays:
            birthday = m.birthday if hasattr(m, "birthday") else m.get("birthday")
            full_name = m.full_name if hasattr(m, "full_name") else m.get("full_name")
            bday_str = birthday.strftime("%A") if hasattr(birthday, "strftime") else str(birthday)
            lines.append(f"• {full_name} — birthday {bday_str}")
        lines.append("")

    anniversaries = briefing.get("anniversaries_this_week", [])
    if anniversaries:
        lines.append("💍 ANNIVERSARIES THIS WEEK")
        for m in anniversaries:
            anniversary = m.anniversary if hasattr(m, "anniversary") else m.get("anniversary")
            full_name = m.full_name if hasattr(m, "full_name") else m.get("full_name")
            ann_str = anniversary.strftime("%A") if hasattr(anniversary, "strftime") else str(anniversary)
            lines.append(f"• {full_name} — anniversary {ann_str}")
        lines.append("")

    visitors = briefing.get("visitors_needing_followup", [])
    if visitors:
        lines.append("👋 VISITOR FOLLOW-UP NEEDED")
        for v in visitors:
            visit_date = v.visit_date if hasattr(v, "visit_date") else v.get("visit_date")
            full_name = v.full_name if hasattr(v, "full_name") else v.get("full_name")
            days_ago = (date.today() - visit_date).days if hasattr(visit_date, "toordinal") else "?"
            lines.append(f"• {full_name} — visited {days_ago} days ago, no follow-up yet")
        lines.append("")

    care_cases = briefing.get("active_care_cases", [])
    if care_cases:
        lines.append("🏥 ACTIVE CARE CASES")
        for c in care_cases:
            if hasattr(c, "member"):
                member_name = c.member.full_name if c.member else "Unknown"
                category = c.category.value if hasattr(c.category, "value") else c.category
                last_contact = c.last_contact
            else:
                member_name = c.get("member_name") or "Unknown"
                category = c.get("category")
                last_contact = c.get("last_contact")
            last = f"Last contact: {last_contact}" if last_contact else "No contact logged"
            lines.append(f"• {member_name} [{category}] — {last}")
        lines.append("")

    absent = briefing.get("absent_members", [])
    if absent:
        lines.append("⏰ ABSENT MEMBERS")
        for m in absent:
            last_attendance = m.last_attendance if hasattr(m, "last_attendance") else m.get("last_attendance")
            full_name = m.full_name if hasattr(m, "full_name") else m.get("full_name")
            days_ago = (date.today() - last_attendance).days if hasattr(last_attendance, "toordinal") else "?"
            lines.append(f"• {full_name} — {days_ago} days since last attendance")
        lines.append("")

    prayers = briefing.get("unanswered_prayers", [])
    if prayers:
        lines.append("🙏 PRAYER REQUESTS NEEDING FOLLOW-UP")
        for p in prayers:
            if hasattr(p, "member"):
                name = p.member.full_name if p.member else (p.submitted_by or "Anonymous")
                created_at = p.created_at
                request_text = p.request_text
            else:
                name = p.get("submitted_by") or "Anonymous"
                created_at = p.get("created_at")
                request_text = p.get("request_text", "")
            days_ago = (datetime.utcnow() - created_at).days if isinstance(created_at, datetime) else "?"
            snippet = request_text[:60] + ("…" if len(request_text) > 60 else "")
            lines.append(f"• {name} — \"{snippet}\" ({days_ago} days, no update)")
        lines.append("")

    nudges = briefing.get("nudges", [])
    if nudges:
        lines.append("💭 TODAY'S NUDGE")
        for nudge in nudges:
            lines.append(f"• {nudge}")
        lines.append("")

    if not any([birthdays, anniversaries, visitors, care_cases, absent, prayers, nudges]):
        lines.append(voice.BRIEFING_EMPTY_STATE)

    return "\n".join(lines)
