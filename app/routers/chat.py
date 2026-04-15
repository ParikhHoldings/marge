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
from app.privacy import AccessRole, ConfidentialityClass, get_request_role, redact_for_role
from app.services.marge import (
    draft_care_message,
    draft_visitor_followup,
    draft_absence_checkin,
    draft_birthday_message,
    draft_anniversary_message,
)

logger = logging.getLogger("marge.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

MARGE_SYSTEM_PROMPT = """You are Marge, an AI church secretary assistant. You are warm, direct, and pastoral — like a trusted colleague who has worked alongside this pastor for years.

When the pastor tells you something:
- Acknowledge it warmly and personally
- Confirm what you logged or will do next
- Offer one helpful follow-up action

Keep replies to 2-3 sentences max. Never be corporate or cold. Use pastoral language — congregation, not users. Members, not contacts. Care, not engagement. Always address the pastor as Pastor or by name if you know it."""

NAME_STOPWORDS = {
    "log", "add", "note", "for", "open", "care", "case", "prepare", "visitor",
    "follow", "up", "sequence", "draft", "a", "an", "the", "and", "she", "he",
    "is", "in", "after", "needs", "check", "request", "prayer", "about", "marge",
}


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


def _extract_name_hint(message: str) -> Optional[str]:
    patterns = [
        r"(?:for|to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            candidate = match.group(1).strip()
            parts = [p for p in candidate.split() if p.lower() not in NAME_STOPWORDS]
            if len(parts) >= 2:
                return " ".join(parts[:3])
    caps = re.findall(r"\b[A-Z][a-z]+\b", message)
    filtered = [word for word in caps if word.lower() not in NAME_STOPWORDS]
    if len(filtered) >= 2:
        return " ".join(filtered[:3])
    return None


def _infer_confidentiality(message: str, default: ConfidentialityClass = ConfidentialityClass.private) -> ConfidentialityClass:
    lowered = message.lower()
    if any(word in lowered for word in ["counsel", "therapy", "abuse", "financial", "conflict", "icu", "suicide"]):
        return ConfidentialityClass.sensitive
    if any(word in lowered for word in ["public", "bulletin", "share church-wide"]):
        return ConfidentialityClass.public
    return default


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(
    request: ChatRequest,
    db: Session = Depends(get_db),
    role: AccessRole = Depends(get_request_role),
):
    pastor_name = os.getenv("PASTOR_NAME", request.pastor_name)
    church_name = os.getenv("CHURCH_NAME", "your church")
    message = request.message.strip()
    lowered = message.lower()

    actions = []
    drafts = []
    suggestions = []

    member = _find_member_by_name(db, message)
    visitor = _find_visitor_by_name(db, message)
    name_hint = _extract_name_hint(message)

    if member and any(word in lowered for word in ["visited", "called", "texted", "met with", "update", "log", "note"]):
        note_confidentiality = _infer_confidentiality(message, ConfidentialityClass.private)
        note = MemberNote(
            member_id=member.id,
            note_text=message,
            context_tag=_infer_context_tag(message),
            confidentiality_class=note_confidentiality.value,
        )
        db.add(note)
        actions.append({
            "type": "member_note",
            "member_id": member.id,
            "member_name": member.full_name,
            "status": "logged",
            "confidentiality_class": note_confidentiality.value,
            "note_text": redact_for_role(message, note_confidentiality, role),
        })

    if member and any(word in lowered for word in ["hospital", "surgery", "icu"]):
        existing = (
            db.query(CareNote)
            .filter(CareNote.member_id == member.id)
            .filter(CareNote.status == "active")
            .filter(CareNote.category == "hospital")
            .first()
        )
        if existing:
            existing_confidentiality = (
                existing.confidentiality_class.value
                if hasattr(existing.confidentiality_class, "value")
                else existing.confidentiality_class
            )
            if message not in (existing.description or ""):
                existing.description = ((existing.description or "") + f"\n\n[{date.today().isoformat()}] {message}").strip()
            actions.append({
                "type": "care_case",
                "member_id": member.id,
                "member_name": member.full_name,
                "category": "hospital",
                "status": "updated",
                "care_id": existing.id,
                "confidentiality_class": existing_confidentiality,
                "description": redact_for_role(message, existing_confidentiality, role),
            })
        else:
            care_confidentiality = _infer_confidentiality(message, ConfidentialityClass.sensitive)
            care = CareNote(
                member_id=member.id,
                category="hospital",
                status="active",
                description=message,
                confidentiality_class=care_confidentiality.value,
            )
            db.add(care)
            db.flush()
            actions.append({
                "type": "care_case",
                "member_id": member.id,
                "member_name": member.full_name,
                "category": "hospital",
                "status": "opened",
                "care_id": care.id,
                "confidentiality_class": care_confidentiality.value,
                "description": redact_for_role(message, care_confidentiality, role),
            })
        drafts.append({"type": "care_text", "member_id": member.id, "member_name": member.full_name, "draft": draft_care_message(member, "hospital", pastor_name)})
        suggestions.extend([
            f"Would you like me to log when you last contacted {member.first_name}?",
            f"Do you want a prayer follow-up note added for {member.first_name}?"
        ])

    if member and any(word in lowered for word in ["prayer request", "pray for", "please pray", "prayer"]):
        prayer_confidentiality = _infer_confidentiality(message, ConfidentialityClass.private)
        prayer = PrayerRequest(
            member_id=member.id,
            submitted_by=member.full_name,
            request_text=_extract_prayer_text(message),
            is_private=True,
            confidentiality_class=prayer_confidentiality.value,
            status="active",
        )
        db.add(prayer)
        actions.append({
            "type": "prayer_request",
            "member_id": member.id,
            "member_name": member.full_name,
            "status": "opened",
            "confidentiality_class": prayer_confidentiality.value,
            "request_text": redact_for_role(prayer.request_text, prayer_confidentiality, role),
        })
        suggestions.append("Do you want this prayer request kept private or visible in the internal list?")

    if visitor and any(word in lowered for word in ["visitor", "visited", "follow up", "follow-up", "came sunday", "came last sunday", "prepare", "sequence", "draft"]):
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

    if not actions and name_hint:
        first_name = name_hint.split()[0]
        if any(word in lowered for word in ["visitor", "follow up", "follow-up", "sequence", "prepare"]):
            actions.append({"type": "suggested_visitor_sequence", "target_name": name_hint, "status": "needs_record"})
            drafts.extend([
                {"type": "visitor_day1", "visitor_name": name_hint, "draft": f"Hi {first_name}, just wanted to say we were really glad you joined us Sunday. No pressure at all — just wanted you to know you are welcome here anytime. — Pastor {pastor_name}"},
                {"type": "visitor_day3", "visitor_name": name_hint, "draft": f"Hey {first_name}, this is Pastor {pastor_name}. I was glad to meet you. If you are ever open, I would love to grab coffee and hear more of your story."},
                {"type": "visitor_week2", "visitor_name": name_hint, "draft": f"Hi {first_name} — just wanted to reach back out and let you know you are always welcome at {church_name}. Would love to see you again when it fits."},
            ])
            suggestions.append(f"If {name_hint} is not in Marge yet, add them as a visitor first so the sequence can be tracked.")
        elif any(word in lowered for word in ["care case", "hospital", "surgery", "crisis", "grief"]):
            actions.append({"type": "suggested_care_case", "target_name": name_hint, "status": "needs_member_match"})
            drafts.append({"type": "care_text", "member_name": name_hint, "draft": f"Hey {first_name}, just wanted you to know I am thinking about you and praying for you today. I am here if you need anything. — Pastor {pastor_name}"})
            suggestions.append(f"I could not match {name_hint} to a member yet. If you add or sync them first, I can track the care case visibly.")

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
            elif action["type"] == "suggested_visitor_sequence":
                action_bits.append(f"prepared a visitor sequence draft for {action['target_name']} but still need them added as a visitor record")
            elif action["type"] == "suggested_care_case":
                action_bits.append(f"prepared a care draft for {action['target_name']} but still need them matched to a member record")
        reply = f"Got it, {pastor_name}. I {', '.join(action_bits)}."
        if suggestions:
            reply += f" {suggestions[0]}"
        return ChatResponse(reply=reply, actions=actions, drafts=drafts, suggested_prompts=suggestions[:3])

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
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
