"""
Chat router — POST /chat

Lets the pastor talk to Marge in plain English.
Marge should not just answer like a chatbot — she should translate plain-English
pastoral updates into actual structured app actions whenever possible.
"""

import os
import re
import logging
from datetime import date
from typing import Optional, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Member, MemberNote, CareNote, PrayerRequest, Visitor
from app.services.marge import draft_care_message, draft_visitor_followup, draft_absence_checkin, draft_birthday_message, draft_anniversary_message

logger = logging.getLogger("marge.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

MARGE_SYSTEM_PROMPT = """You are Marge, an AI church secretary assistant. You are warm, direct, and pastoral — like a trusted colleague who has worked alongside this pastor for years.

When the pastor tells you something:
- Acknowledge it warmly and personally
- Confirm what you logged or will do next
- Offer one helpful follow-up action

Keep replies to 2-3 sentences max. Never be corporate or cold. Use pastoral language — congregation, not users. Members, not contacts. Care, not engagement. Always address the pastor as Pastor or by name if you know it."""


class ChatRequest(BaseModel):
    message: str
    pastor_name: str = "Pastor"


class ChatResponse(BaseModel):
    reply: str
    actions: List[dict] = []
    drafts: List[dict] = []
    suggested_prompts: List[str] = []


def _find_member_by_name(db: Session, text: str) -> Optional[Member]:
    members = db.query(Member).all()
    lowered = text.lower()
    best = None
    best_len = 0
    for member in members:
        name = member.full_name.lower()
        if name in lowered and len(name) > best_len:
            best = member
            best_len = len(name)
    return best


def _find_visitor_by_name(db: Session, text: str) -> Optional[Visitor]:
    visitors = db.query(Visitor).all()
    lowered = text.lower()
    best = None
    best_len = 0
    for visitor in visitors:
        name = visitor.full_name.lower()
        if name in lowered and len(name) > best_len:
            best = visitor
            best_len = len(name)
    return best


def _infer_context_tag(message: str) -> Optional[str]:
    lowered = message.lower()
    if any(word in lowered for word in ["hospital", "surgery", "medical"]):
        return "hospital"
    if any(word in lowered for word in ["prayer", "pray"]):
        return "prayer"
    if any(word in lowered for word in ["grief", "loss", "funeral", "died", "death"]):
        return "grief"
    if any(word in lowered for word in ["counsel", "marriage"]):
        return "counseling"
    if any(word in lowered for word in ["job", "work", "employment"]):
        return "job"
    if any(word in lowered for word in ["family", "kids", "wife", "husband"]):
        return "family"
    return "general"


def _extract_prayer_text(message: str) -> str:
    cleaned = re.sub(r"^.*?(pray(er)? request for|pray for)\s+", "", message, flags=re.IGNORECASE)
    return cleaned.strip() or message.strip()


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Send a plain-English message to Marge.

    Examples:
    - "I visited Martha Ellis today, she is doing better"
    - "Add a prayer request for Tom Henderson, he is going through a job loss"
    - "Log that James Whitmore visited last Sunday with his family"

    Marge will acknowledge, confirm, and offer a follow-up action.
    """
    pastor_name = os.getenv("PASTOR_NAME", request.pastor_name)
    church_name = os.getenv("CHURCH_NAME", "your church")
    message = request.message.strip()
    lowered = message.lower()

    actions = []
    drafts = []
    suggestions = []

    member = _find_member_by_name(db, message)
    visitor = _find_visitor_by_name(db, message)

    if member and any(word in lowered for word in ["visited", "called", "texted", "met with", "update", "log", "note"]):
        note = MemberNote(member_id=member.id, note_text=message, context_tag=_infer_context_tag(message))
        db.add(note)
        actions.append({"type": "member_note", "member_id": member.id, "member_name": member.full_name, "status": "logged"})

    if member and any(word in lowered for word in ["hospital", "surgery", "icu"]):
        existing = (
            db.query(CareNote)
            .filter(CareNote.member_id == member.id)
            .filter(CareNote.status == "active")
            .filter(CareNote.category == "hospital")
            .first()
        )
        if existing:
            if message not in (existing.description or ""):
                existing.description = ((existing.description or "") + f"\n\n[{date.today().isoformat()}] {message}").strip()
            actions.append({"type": "care_case", "member_id": member.id, "member_name": member.full_name, "category": "hospital", "status": "updated", "care_id": existing.id})
        else:
            care = CareNote(member_id=member.id, category="hospital", status="active", description=message)
            db.add(care)
            db.flush()
            actions.append({"type": "care_case", "member_id": member.id, "member_name": member.full_name, "category": "hospital", "status": "opened", "care_id": care.id})
        drafts.append({"type": "care_text", "member_id": member.id, "member_name": member.full_name, "draft": draft_care_message(member, "hospital", pastor_name)})
        suggestions.extend([
            f"Would you like me to log when you last contacted {member.first_name}?",
            f"Do you want a prayer follow-up note added for {member.first_name}?"
        ])

    if member and any(word in lowered for word in ["prayer request", "pray for", "please pray", "prayer"]):
        prayer = PrayerRequest(member_id=member.id, submitted_by=member.full_name, request_text=_extract_prayer_text(message), is_private=True, status="active")
        db.add(prayer)
        actions.append({"type": "prayer_request", "member_id": member.id, "member_name": member.full_name, "status": "opened"})
        suggestions.append(f"Do you want this prayer request kept private or visible in the internal list?")

    if visitor and any(word in lowered for word in ["visitor", "visited", "follow up", "follow-up", "came sunday", "came last sunday"]):
        drafts.extend([
            {"type": "visitor_day1", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 1, pastor_name, church_name)},
            {"type": "visitor_day3", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 3, pastor_name, church_name)},
            {"type": "visitor_week2", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 14, pastor_name, church_name)},
        ])
        actions.append({"type": "visitor_sequence", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "status": "prepared"})

    if member and any(word in lowered for word in ["missed", "hasn't been", "absent"]):
        drafts.append({"type": "absence_checkin", "member_id": member.id, "member_name": member.full_name, "draft": draft_absence_checkin(member, pastor_name, church_name)})

    if member and any(word in lowered for word in ["birthday", "anniversary"]):
        if "birthday" in lowered:
            drafts.append({"type": "birthday_text", "member_id": member.id, "member_name": member.full_name, "draft": draft_birthday_message(member, pastor_name)})
        if "anniversary" in lowered:
            drafts.append({"type": "anniversary_text", "member_id": member.id, "member_name": member.full_name, "draft": draft_anniversary_message(member, pastor_name)})

    if actions:
        db.commit()
        action_bits = []
        for action in actions:
            if action["type"] == "member_note":
                action_bits.append(f"logged a note for {action['member_name']}")
            elif action["type"] == "care_case":
                verb = "opened" if action["status"] == "opened" else "updated"
                action_bits.append(f"{verb} a {action['category']} care case for {action['member_name']}")
            elif action["type"] == "prayer_request":
                action_bits.append(f"logged a prayer request for {action['member_name']}")
            elif action["type"] == "visitor_sequence":
                action_bits.append(f"prepared the visitor follow-up sequence for {action['visitor_name']}")
        reply = f"Got it, {pastor_name}. I {', '.join(action_bits)}."
        if suggestions:
            reply += f" {suggestions[0]}"
        return ChatResponse(reply=reply, actions=actions, drafts=drafts, suggested_prompts=suggestions[:3])

    if visitor and any(word in lowered for word in ["prepare", "visitor", "follow up", "follow-up", "sequence", "draft"]):
        drafts.extend([
            {"type": "visitor_day1", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 1, pastor_name, church_name)},
            {"type": "visitor_day3", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 3, pastor_name, church_name)},
            {"type": "visitor_week2", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "draft": draft_visitor_followup(visitor, 14, pastor_name, church_name)},
        ])
        actions.append({"type": "visitor_sequence", "visitor_id": visitor.id, "visitor_name": visitor.full_name, "status": "prepared"})

    if actions:
        db.commit()
        action_bits = []
        for action in actions:
            if action["type"] == "member_note":
                action_bits.append(f"logged a note for {action['member_name']}")
            elif action["type"] == "care_case":
                verb = "opened" if action["status"] == "opened" else "updated"
                action_bits.append(f"{verb} a {action['category']} care case for {action['member_name']}")
            elif action["type"] == "prayer_request":
                action_bits.append(f"logged a prayer request for {action['member_name']}")
            elif action["type"] == "visitor_sequence":
                action_bits.append(f"prepared the visitor follow-up sequence for {action['visitor_name']}")
        reply = f"Got it, {pastor_name}. I {', '.join(action_bits)}."
        if suggestions:
            reply += f" {suggestions[0]}"
        return ChatResponse(reply=reply, actions=actions, drafts=drafts, suggested_prompts=suggestions[:3])

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        # Warm placeholder when no API key is configured
        return ChatResponse(
            reply=f"Got it, {pastor_name}. I heard you, but I could not confidently turn that into a concrete action yet. Try naming the person and what happened, and I will log it, open care, or prepare a draft.",
            actions=[],
            drafts=[],
            suggested_prompts=[
                "Log a note for Nathan Parikh: visited in the hospital today and doing better.",
                "Open a care case for Martha Ellis. Hip surgery recovery and needs a check-in.",
                "Prepare the visitor follow-up sequence for James Whitmore."
            ]
        )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        system = MARGE_SYSTEM_PROMPT + f"\n\nThe pastor's name is {pastor_name}. The church is {church_name}."

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": request.message},
            ],
            max_tokens=150,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        return ChatResponse(reply=reply, actions=[], drafts=[], suggested_prompts=[])
    
    except Exception as e:
        logger.error(f"OpenAI chat error: {e}")
        return ChatResponse(
            reply=f"Got it, {pastor_name}. I heard you, but I could not confidently turn that into a concrete action yet. Try naming the person and what happened, and I will log it, open care, or prepare a draft.",
            actions=[],
            drafts=[],
            suggested_prompts=[
                "Log a note for Nathan Parikh: visited in the hospital today and doing better.",
                "Open a care case for Martha Ellis. Hip surgery recovery and needs a check-in.",
                "Prepare the visitor follow-up sequence for James Whitmore."
            ]
        )
