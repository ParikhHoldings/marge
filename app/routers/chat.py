"""Chat router — intent parsing, typed action execution, and audit tracing."""

import json
import logging
import os
import re
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CareNote, ChatActionTrace, Member, MemberNote, PrayerRequest, Visitor
from app.services.marge import (
    draft_absence_checkin,
    draft_anniversary_message,
    draft_birthday_message,
    draft_care_message,
    draft_visitor_followup,
)

logger = logging.getLogger("marge.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

MARGE_SYSTEM_PROMPT = """You are Marge, an AI church secretary assistant. Parse the pastor message into structured action JSON only.

Rules:
- Prefer these action types: log_note, open_care_case, create_prayer_request, draft_message.
- Use resolve_care_case, delete_note, or set_prayer_privacy only when explicitly requested.
- Set requires_confirmation=true for any high-impact action (resolve/delete/privacy changes).
- Set confidence from 0.0-1.0 based on certainty.
- If a name is ambiguous or missing, include the best target_name and lower confidence.
- Return strict JSON that matches the schema exactly.
"""

NAME_STOPWORDS = {
    "log", "add", "note", "for", "open", "care", "case", "prepare", "visitor",
    "follow", "up", "sequence", "draft", "a", "an", "the", "and", "she", "he",
    "is", "in", "after", "needs", "check", "request", "prayer", "about", "marge",
}

HIGH_IMPACT_ACTIONS = {"resolve_care_case", "delete_note", "set_prayer_privacy"}


class ActionType(str, Enum):
    log_note = "log_note"
    open_care_case = "open_care_case"
    create_prayer_request = "create_prayer_request"
    draft_message = "draft_message"
    resolve_care_case = "resolve_care_case"
    delete_note = "delete_note"
    set_prayer_privacy = "set_prayer_privacy"


class ParsedAction(BaseModel):
    action_type: ActionType
    intent_summary: str = Field(min_length=3)
    target_type: Literal["member", "visitor", "care_case", "prayer_request", "note", "none"] = "none"
    target_name: Optional[str] = None
    target_id: Optional[int] = None
    note_text: Optional[str] = None
    care_category: Optional[Literal["hospital", "crisis", "grief", "general"]] = None
    prayer_text: Optional[str] = None
    draft_kind: Optional[Literal["care", "visitor_day1", "visitor_day3", "visitor_week2", "absence", "birthday", "anniversary"]] = None
    is_private: Optional[bool] = None
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool = False


class IntentPlan(BaseModel):
    overall_intent: str
    confidence: float = Field(ge=0, le=1)
    actions: List[ParsedAction] = Field(default_factory=list)
    disambiguation_question: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    pastor_name: str = "Pastor"
    confirmed_high_impact: bool = False


class ChatResponse(BaseModel):
    reply: str
    confidence: float
    inferred_intent: str
    actions: List[dict] = []
    drafts: List[dict] = []
    requires_confirmation: List[dict] = []
    disambiguation_options: List[str] = []
    suggested_prompts: List[str] = []


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


def _member_candidates_by_name(db: Session, name: str) -> List[Member]:
    name_tokens = [token.lower() for token in name.split() if token]
    if not name_tokens:
        return []
    candidates = []
    for member in db.query(Member).all():
        full = member.full_name.lower()
        if all(token in full for token in name_tokens):
            candidates.append(member)
    return candidates


def _visitor_candidates_by_name(db: Session, name: str) -> List[Visitor]:
    name_tokens = [token.lower() for token in name.split() if token]
    if not name_tokens:
        return []
    candidates = []
    for visitor in db.query(Visitor).all():
        full = visitor.full_name.lower()
        if all(token in full for token in name_tokens):
            candidates.append(visitor)
    return candidates


def _extract_prayer_text(message: str) -> str:
    cleaned = re.sub(r"^.*?(pray(er)? request for|pray for)\s+", "", message, flags=re.IGNORECASE)
    return cleaned.strip() or message.strip()


def _heuristic_intent_plan(message: str) -> IntentPlan:
    lowered = message.lower()
    name_hint = _extract_name_hint(message)
    actions: List[ParsedAction] = []

    if any(word in lowered for word in ["delete note", "remove note"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.delete_note,
                intent_summary="Delete a pastoral note",
                target_type="note",
                target_name=name_hint,
                confidence=0.67,
                requires_confirmation=True,
            )
        )
    if any(word in lowered for word in ["resolve care", "close care case"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.resolve_care_case,
                intent_summary="Resolve an active care case",
                target_type="member",
                target_name=name_hint,
                confidence=0.72,
                requires_confirmation=True,
            )
        )
    if any(word in lowered for word in ["private", "make prayer private", "toggle privacy"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.set_prayer_privacy,
                intent_summary="Change prayer request privacy",
                target_type="prayer_request",
                target_name=name_hint,
                is_private=True,
                confidence=0.7,
                requires_confirmation=True,
            )
        )

    if any(word in lowered for word in ["log", "note", "visited", "called", "texted", "met with"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.log_note,
                intent_summary="Log a member care note",
                target_type="member",
                target_name=name_hint,
                note_text=message,
                confidence=0.78 if name_hint else 0.58,
            )
        )

    if any(word in lowered for word in ["hospital", "surgery", "icu", "care case"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.open_care_case,
                intent_summary="Open or update a care case",
                target_type="member",
                target_name=name_hint,
                note_text=message,
                care_category="hospital" if any(word in lowered for word in ["hospital", "surgery", "icu"]) else "general",
                confidence=0.83 if name_hint else 0.62,
            )
        )

    if any(word in lowered for word in ["pray", "prayer request", "please pray"]):
        actions.append(
            ParsedAction(
                action_type=ActionType.create_prayer_request,
                intent_summary="Create prayer request",
                target_type="member",
                target_name=name_hint,
                prayer_text=_extract_prayer_text(message),
                is_private=True,
                confidence=0.8 if name_hint else 0.6,
            )
        )

    if any(word in lowered for word in ["draft", "prepare", "follow-up", "follow up", "birthday", "anniversary", "absent"]):
        draft_kind = "care"
        if "birthday" in lowered:
            draft_kind = "birthday"
        elif "anniversary" in lowered:
            draft_kind = "anniversary"
        elif any(word in lowered for word in ["absent", "hasn't been", "missed"]):
            draft_kind = "absence"
        elif "visitor" in lowered:
            draft_kind = "visitor_day1"

        actions.append(
            ParsedAction(
                action_type=ActionType.draft_message,
                intent_summary="Draft a pastoral message",
                target_type="visitor" if "visitor" in lowered else "member",
                target_name=name_hint,
                draft_kind=draft_kind,
                confidence=0.74 if name_hint else 0.55,
            )
        )

    if not actions:
        return IntentPlan(overall_intent="unclassified", confidence=0.25, actions=[])

    confidence = round(sum(a.confidence for a in actions) / len(actions), 2)
    return IntentPlan(overall_intent="structured_actions", confidence=confidence, actions=actions)


def _intent_schema() -> Dict[str, Any]:
    return {
        "name": "marge_chat_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overall_intent": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "disambiguation_question": {"type": ["string", "null"]},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "action_type": {
                                "type": "string",
                                "enum": [
                                    "log_note",
                                    "open_care_case",
                                    "create_prayer_request",
                                    "draft_message",
                                    "resolve_care_case",
                                    "delete_note",
                                    "set_prayer_privacy",
                                ],
                            },
                            "intent_summary": {"type": "string"},
                            "target_type": {
                                "type": "string",
                                "enum": ["member", "visitor", "care_case", "prayer_request", "note", "none"],
                            },
                            "target_name": {"type": ["string", "null"]},
                            "target_id": {"type": ["integer", "null"]},
                            "note_text": {"type": ["string", "null"]},
                            "care_category": {"type": ["string", "null"], "enum": ["hospital", "crisis", "grief", "general", None]},
                            "prayer_text": {"type": ["string", "null"]},
                            "draft_kind": {
                                "type": ["string", "null"],
                                "enum": ["care", "visitor_day1", "visitor_day3", "visitor_week2", "absence", "birthday", "anniversary", None],
                            },
                            "is_private": {"type": ["boolean", "null"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "requires_confirmation": {"type": "boolean"},
                        },
                        "required": [
                            "action_type",
                            "intent_summary",
                            "target_type",
                            "target_name",
                            "target_id",
                            "note_text",
                            "care_category",
                            "prayer_text",
                            "draft_kind",
                            "is_private",
                            "confidence",
                            "requires_confirmation",
                        ],
                    },
                },
            },
            "required": ["overall_intent", "confidence", "disambiguation_question", "actions"],
        },
    }


def _parse_intent_with_model(message: str, pastor_name: str, church_name: str) -> IntentPlan:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _heuristic_intent_plan(message)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("CHAT_INTENT_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_schema", "json_schema": _intent_schema()},
            messages=[
                {
                    "role": "system",
                    "content": MARGE_SYSTEM_PROMPT
                    + f"\nPastor: {pastor_name}\nChurch: {church_name}",
                },
                {"role": "user", "content": message},
            ],
        )
        payload = response.choices[0].message.content or "{}"
        return IntentPlan.model_validate_json(payload)
    except Exception as exc:
        logger.warning("Intent parse fallback: %s", exc)
        return _heuristic_intent_plan(message)


def _resolve_action_targets(db: Session, actions: List[ParsedAction]) -> Tuple[List[ParsedAction], List[str], Optional[str]]:
    disambiguation_options: List[str] = []
    disambiguation_prompt: Optional[str] = None

    for action in actions:
        if action.target_id or not action.target_name:
            continue

        if action.target_type == "member":
            matches = _member_candidates_by_name(db, action.target_name)
            if len(matches) == 1:
                action.target_id = matches[0].id
                action.target_name = matches[0].full_name
            elif len(matches) > 1:
                disambiguation_options = [m.full_name for m in matches[:5]]
                disambiguation_prompt = (
                    f"I found multiple members for '{action.target_name}': "
                    + ", ".join(disambiguation_options)
                    + ". Which one should I use?"
                )
                action.confidence = min(action.confidence, 0.45)

        if action.target_type == "visitor":
            matches = _visitor_candidates_by_name(db, action.target_name)
            if len(matches) == 1:
                action.target_id = matches[0].id
                action.target_name = matches[0].full_name
            elif len(matches) > 1:
                disambiguation_options = [v.full_name for v in matches[:5]]
                disambiguation_prompt = (
                    f"I found multiple visitors for '{action.target_name}': "
                    + ", ".join(disambiguation_options)
                    + ". Which one should I use?"
                )
                action.confidence = min(action.confidence, 0.45)

    return actions, disambiguation_options, disambiguation_prompt


def _log_trace(
    db: Session,
    message: str,
    plan: IntentPlan,
    executed_actions: List[dict],
    outcome_status: str,
    outcome_detail: str,
) -> None:
    trace = ChatActionTrace(
        input_text=message,
        inferred_intent=plan.overall_intent,
        inferred_actions=json.dumps([a.model_dump() for a in plan.actions]),
        parser_confidence=plan.confidence,
        executed_actions=json.dumps(executed_actions),
        outcome_status=outcome_status,
        outcome_detail=outcome_detail,
    )
    db.add(trace)


def _execute_actions(
    db: Session,
    actions: List[ParsedAction],
    pastor_name: str,
    church_name: str,
    confirmed_high_impact: bool,
) -> Tuple[List[dict], List[dict], List[dict]]:
    executed: List[dict] = []
    drafts: List[dict] = []
    confirmations: List[dict] = []

    for action in actions:
        is_high_impact = action.action_type.value in HIGH_IMPACT_ACTIONS
        if is_high_impact and not confirmed_high_impact:
            confirmations.append(action.model_dump())
            continue

        if action.action_type == ActionType.log_note:
            if not action.target_id or not action.note_text:
                continue
            note = MemberNote(member_id=action.target_id, note_text=action.note_text, context_tag="general")
            db.add(note)
            executed.append({"type": "log_note", "member_id": action.target_id, "status": "logged"})

        elif action.action_type == ActionType.open_care_case:
            if not action.target_id:
                continue
            existing = (
                db.query(CareNote)
                .filter(CareNote.member_id == action.target_id)
                .filter(CareNote.status == "active")
                .filter(CareNote.category == (action.care_category or "general"))
                .first()
            )
            if existing:
                if action.note_text:
                    existing.description = ((existing.description or "") + f"\n\n[{date.today().isoformat()}] {action.note_text}").strip()
                executed.append({"type": "open_care_case", "member_id": action.target_id, "status": "updated", "care_id": existing.id})
            else:
                care = CareNote(
                    member_id=action.target_id,
                    category=action.care_category or "general",
                    status="active",
                    description=action.note_text,
                )
                db.add(care)
                db.flush()
                executed.append({"type": "open_care_case", "member_id": action.target_id, "status": "opened", "care_id": care.id})

        elif action.action_type == ActionType.create_prayer_request:
            prayer = PrayerRequest(
                member_id=action.target_id,
                submitted_by=action.target_name,
                request_text=action.prayer_text or "Prayer request",
                is_private=True if action.is_private is None else action.is_private,
                status="active",
            )
            db.add(prayer)
            executed.append({"type": "create_prayer_request", "member_id": action.target_id, "status": "opened"})

        elif action.action_type == ActionType.draft_message:
            if action.draft_kind == "visitor_day1" and action.target_id:
                visitor = db.query(Visitor).filter(Visitor.id == action.target_id).first()
                if visitor:
                    drafts.append({"type": "visitor_day1", "visitor_id": visitor.id, "draft": draft_visitor_followup(visitor, 1, pastor_name, church_name)})
            elif action.draft_kind == "visitor_day3" and action.target_id:
                visitor = db.query(Visitor).filter(Visitor.id == action.target_id).first()
                if visitor:
                    drafts.append({"type": "visitor_day3", "visitor_id": visitor.id, "draft": draft_visitor_followup(visitor, 3, pastor_name, church_name)})
            elif action.draft_kind == "visitor_week2" and action.target_id:
                visitor = db.query(Visitor).filter(Visitor.id == action.target_id).first()
                if visitor:
                    drafts.append({"type": "visitor_week2", "visitor_id": visitor.id, "draft": draft_visitor_followup(visitor, 14, pastor_name, church_name)})
            elif action.target_id:
                member = db.query(Member).filter(Member.id == action.target_id).first()
                if not member:
                    continue
                if action.draft_kind == "care":
                    drafts.append({"type": "care", "member_id": member.id, "draft": draft_care_message(member, "general", pastor_name)})
                elif action.draft_kind == "absence":
                    drafts.append({"type": "absence", "member_id": member.id, "draft": draft_absence_checkin(member, pastor_name, church_name)})
                elif action.draft_kind == "birthday":
                    drafts.append({"type": "birthday", "member_id": member.id, "draft": draft_birthday_message(member, pastor_name)})
                elif action.draft_kind == "anniversary":
                    drafts.append({"type": "anniversary", "member_id": member.id, "draft": draft_anniversary_message(member, pastor_name)})
            executed.append({"type": "draft_message", "target_id": action.target_id, "draft_kind": action.draft_kind, "status": "prepared"})

        elif action.action_type == ActionType.resolve_care_case:
            if action.target_id:
                case = (
                    db.query(CareNote)
                    .filter(CareNote.member_id == action.target_id)
                    .filter(CareNote.status == "active")
                    .order_by(CareNote.created_at.desc())
                    .first()
                )
                if case:
                    case.status = "resolved"
                    executed.append({"type": "resolve_care_case", "care_id": case.id, "status": "resolved"})

        elif action.action_type == ActionType.delete_note:
            if action.target_id:
                note = db.query(MemberNote).filter(MemberNote.id == action.target_id).first()
                if note:
                    db.delete(note)
                    executed.append({"type": "delete_note", "note_id": action.target_id, "status": "deleted"})

        elif action.action_type == ActionType.set_prayer_privacy:
            if action.target_id and action.is_private is not None:
                prayer = db.query(PrayerRequest).filter(PrayerRequest.id == action.target_id).first()
                if prayer:
                    prayer.is_private = action.is_private
                    executed.append({"type": "set_prayer_privacy", "prayer_id": action.target_id, "is_private": action.is_private, "status": "updated"})

    return executed, drafts, confirmations


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(request: ChatRequest, db: Session = Depends(get_db)):
    pastor_name = os.getenv("PASTOR_NAME", request.pastor_name)
    church_name = os.getenv("CHURCH_NAME", "your church")
    message = request.message.strip()

    try:
        plan = _parse_intent_with_model(message, pastor_name, church_name)
        plan.actions, disambiguation_options, disambiguation_prompt = _resolve_action_targets(db, plan.actions)

        if disambiguation_prompt:
            _log_trace(
                db,
                message,
                plan,
                executed_actions=[],
                outcome_status="disambiguation_required",
                outcome_detail=disambiguation_prompt,
            )
            db.commit()
            return ChatResponse(
                reply=f"Got it, {pastor_name}. {disambiguation_prompt}",
                confidence=round(min(plan.confidence, 0.45), 2),
                inferred_intent=plan.overall_intent,
                actions=[],
                drafts=[],
                requires_confirmation=[],
                disambiguation_options=disambiguation_options,
                suggested_prompts=["Reply with the exact full name so I can continue."],
            )

        executed_actions, drafts, confirmations = _execute_actions(
            db,
            plan.actions,
            pastor_name,
            church_name,
            request.confirmed_high_impact,
        )

        if not executed_actions and not drafts and not confirmations:
            _log_trace(
                db,
                message,
                plan,
                executed_actions=[],
                outcome_status="no_action",
                outcome_detail="Intent parsed but no executable action could be safely performed.",
            )
            db.commit()
            return ChatResponse(
                reply=(
                    f"Got it, {pastor_name}. I heard you, but I need a little more detail "
                    "to safely log this. Please include the person and what you want me to do."
                ),
                confidence=plan.confidence,
                inferred_intent=plan.overall_intent,
                actions=[],
                drafts=[],
                requires_confirmation=[],
                disambiguation_options=[],
                suggested_prompts=[
                    "Log a note for Nathan Parikh: visited in the hospital today and doing better.",
                    "Open a care case for Martha Ellis. Hip surgery recovery and needs a check-in.",
                    "Create a prayer request for James Whitmore: pray for job interviews.",
                ],
            )

        _log_trace(
            db,
            message,
            plan,
            executed_actions=executed_actions,
            outcome_status="confirmed_required" if confirmations else "executed",
            outcome_detail="Pending high-impact confirmations." if confirmations else "Actions executed successfully.",
        )
        db.commit()

        reply = f"Got it, {pastor_name}. "
        if executed_actions:
            reply += f"I completed {len(executed_actions)} action(s)."
        if confirmations:
            reply += " I paused high-impact changes until you confirm."
        if drafts:
            reply += f" I also prepared {len(drafts)} draft(s)."

        return ChatResponse(
            reply=reply,
            confidence=plan.confidence,
            inferred_intent=plan.overall_intent,
            actions=executed_actions,
            drafts=drafts,
            requires_confirmation=confirmations,
            disambiguation_options=[],
            suggested_prompts=["If you want me to run pending high-impact actions, resend with confirmed_high_impact=true."] if confirmations else [],
        )

    except ValidationError as exc:
        db.rollback()
        fallback_plan = IntentPlan(overall_intent="invalid_model_payload", confidence=0.0, actions=[])
        _log_trace(
            db,
            message,
            fallback_plan,
            executed_actions=[],
            outcome_status="validation_error",
            outcome_detail=str(exc),
        )
        db.commit()
        return ChatResponse(
            reply=f"Thanks, {pastor_name}. I couldn't safely validate that instruction. Could you rephrase?",
            confidence=0.0,
            inferred_intent="invalid_model_payload",
            actions=[],
            drafts=[],
            requires_confirmation=[],
            disambiguation_options=[],
            suggested_prompts=["Try: 'Log a note for <full name>: <what happened>'."],
        )
    except Exception as exc:
        logger.error("Chat execution error: %s", exc)
        db.rollback()
        fallback_plan = IntentPlan(overall_intent="execution_error", confidence=0.0, actions=[])
        _log_trace(
            db,
            message,
            fallback_plan,
            executed_actions=[],
            outcome_status="execution_error",
            outcome_detail=str(exc),
        )
        db.commit()
        return ChatResponse(
            reply=f"Thanks, {pastor_name}. I hit an internal error before writing anything. Please try again.",
            confidence=0.0,
            inferred_intent="execution_error",
            actions=[],
            drafts=[],
            requires_confirmation=[],
            disambiguation_options=[],
            suggested_prompts=[],
        )
