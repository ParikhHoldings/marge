"""
Chat router — POST /chat
"""

import os
import json
import logging
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Member, MemberNote, PrayerRequest, Visitor, CareNote
from app.services.marge import ai_provider_name

logger = logging.getLogger("marge.chat")
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    pastor_name: str = "Pastor"
    mode: Literal["demo", "live"] = "live"


class ChatResponse(BaseModel):
    reply: str
    action: str
    mode: Literal["demo", "live"]
    saved: bool


SYSTEM_PROMPT = """You are Marge, an AI church secretary assistant. Read the pastor's message and decide the single best action.
Return strict JSON with this shape:
{"action":"member_note|prayer_request|visitor|care_contact|none","person_name":"...","note_text":"...","context_tag":"job|health|family|grief|prayer|hospital|financial|general|visitor|followup","request_text":"...","care_note":"...","reply":"..."}
Rules:
- reply should sound warm, human, and useful in 2-3 sentences max
- If the pastor reports an update about a member, use member_note
- If they ask to pray or log a request, use prayer_request
- If they mention someone visiting church, use visitor
- If they mention contacting someone in an active care case, use care_contact
- If unclear, use none
- Return JSON only
"""


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(request: ChatRequest, db: Session = Depends(get_db)):
    if request.mode == "demo":
        return ChatResponse(
            reply="Got it. In demo mode I did not write anything to the database, but I would log that update and roll it into tomorrow's briefing. If you want, tell me the next thing you handled and I will show you how Marge would respond.",
            action="demo_preview",
            mode="demo",
            saved=False,
        )

    extracted = _extract_action(request.message, request.pastor_name)
    action = extracted.get("action") or "none"

    try:
        if action == "member_note":
            saved = _save_member_note(db, extracted)
        elif action == "prayer_request":
            saved = _save_prayer_request(db, extracted)
        elif action == "visitor":
            saved = _save_visitor(db, extracted)
        elif action == "care_contact":
            saved = _save_care_contact(db, extracted)
        else:
            saved = False
    except Exception as exc:
        logger.exception("chat save failed: %s", exc)
        saved = False
        action = "none"

    reply = extracted.get("reply") or "Got it. I have that noted."
    if not saved and action != "none":
        reply += " I understood the update, but I could not confidently save it, so please check the record once before you trust me on it."

    return ChatResponse(reply=reply, action=action, mode="live", saved=saved)


def _extract_action(message: str, pastor_name: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Pastor: {message}"},
                ],
                temperature=0.2,
                max_tokens=250,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            logger.warning("AI extraction failed, using heuristic fallback: %s", exc)
    return _heuristic_extract(message, pastor_name)


def _heuristic_extract(message: str, pastor_name: str) -> dict:
    lower = message.lower()
    person_name = _guess_person_name(message)
    if "prayer" in lower or "pray" in lower:
        return {
            "action": "prayer_request",
            "person_name": person_name,
            "request_text": message,
            "reply": f"Got it, {pastor_name}. I logged that prayer need so it stays in view and does not slip past you.",
        }
    if "visited" in lower or "visitor" in lower or "came sunday" in lower:
        return {
            "action": "visitor",
            "person_name": person_name,
            "note_text": message,
            "reply": f"Got it, {pastor_name}. I logged that visitor note and will keep follow-up in front of you.",
        }
    if "called" in lower or "texted" in lower or "visited" in lower:
        return {
            "action": "care_contact",
            "person_name": person_name,
            "care_note": message,
            "reply": f"Glad you made that touchpoint. I logged the contact so Marge does not keep nagging you about it tomorrow.",
        }
    return {
        "action": "member_note",
        "person_name": person_name,
        "note_text": message,
        "context_tag": _guess_context_tag(lower),
        "reply": f"Got it, {pastor_name}. I logged that note and will keep it in mind for tomorrow's briefing.",
    }


def _guess_context_tag(lower: str) -> str:
    for tag in ["job", "health", "family", "grief", "prayer", "hospital", "financial"]:
        if tag in lower:
            return tag
    return "general"


def _guess_person_name(message: str) -> str:
    words = [w.strip(" ,.!?") for w in message.split()]
    caps = [w for w in words if w[:1].isupper()]
    if len(caps) >= 2:
        return f"{caps[0]} {caps[1]}"
    if caps:
        return caps[0]
    return "Unknown"


def _find_member(db: Session, person_name: Optional[str]):
    if not person_name:
        return None
    parts = person_name.split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        member = db.query(Member).filter(Member.first_name.ilike(first), Member.last_name.ilike(last)).first()
        if member:
            return member
    first = parts[0]
    return db.query(Member).filter(Member.first_name.ilike(first)).first()


def _save_member_note(db: Session, extracted: dict) -> bool:
    member = _find_member(db, extracted.get("person_name"))
    if not member:
        return False
    note = MemberNote(member_id=member.id, note_text=extracted.get("note_text") or "", context_tag=extracted.get("context_tag") or "general")
    db.add(note)
    db.commit()
    return True


def _save_prayer_request(db: Session, extracted: dict) -> bool:
    member = _find_member(db, extracted.get("person_name"))
    prayer = PrayerRequest(
        member_id=member.id if member else None,
        submitted_by=extracted.get("person_name") if not member else None,
        request_text=extracted.get("request_text") or extracted.get("note_text") or "",
        is_private=True,
        status="active",
    )
    db.add(prayer)
    db.commit()
    return True


def _save_visitor(db: Session, extracted: dict) -> bool:
    person_name = extracted.get("person_name") or "Guest"
    parts = person_name.split()
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else "Guest"
    visitor = Visitor(
        first_name=first_name,
        last_name=last_name,
        visit_date=date.today() - timedelta(days=1),
        notes=extracted.get("note_text") or extracted.get("request_text") or "",
        follow_up_day1_sent=False,
        follow_up_day3_sent=False,
        follow_up_week2_sent=False,
    )
    db.add(visitor)
    db.commit()
    return True


def _save_care_contact(db: Session, extracted: dict) -> bool:
    member = _find_member(db, extracted.get("person_name"))
    if not member:
        return False
    care = db.query(CareNote).filter(CareNote.member_id == member.id, CareNote.status == "active").order_by(CareNote.created_at.desc()).first()
    if not care:
        note = MemberNote(member_id=member.id, note_text=extracted.get("care_note") or extracted.get("note_text") or "", context_tag="followup")
        db.add(note)
        db.commit()
        return True
    care.last_contact = date.today()
    extra = extracted.get("care_note") or extracted.get("note_text")
    if extra:
        existing = care.description or ""
        care.description = (existing + f"\n\n[{date.today().isoformat()}] {extra}").strip()
    db.commit()
    return True
