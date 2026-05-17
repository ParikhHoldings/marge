"""
Draft router — one place for pastoral message drafts.

The frontend and MCP clients use this instead of reimplementing templates
client-side. Marge drafts; the pastor reviews and sends.
"""

import os
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AccountPastorProfile, CareNote, Member, PrayerRequest, Visitor
from app.services.accounts import (
    PASTORAL_ROLES,
    account_access_from_token,
    require_role,
    scoped_query,
)
from app.services.marge import (
    draft_absence_checkin,
    draft_anniversary_message,
    draft_birthday_message,
    draft_care_message,
    draft_visitor_followup,
    pastor_display_name,
)
from app import marge_voice as voice

router = APIRouter(prefix="/drafts", tags=["drafts"])


DraftKind = Literal[
    "birthday",
    "anniversary",
    "care",
    "visitor",
    "absence",
    "prayer",
    "general",
]


class DraftRequest(BaseModel):
    kind: DraftKind
    member_id: Optional[int] = None
    visitor_id: Optional[int] = None
    care_id: Optional[int] = None
    prayer_id: Optional[int] = None
    day: int = 1
    situation: Optional[str] = None
    context: Optional[str] = None


class DraftResponse(BaseModel):
    kind: DraftKind
    recipient_id: Optional[int] = None
    recipient_name: str
    draft: str
    source: str = "marge"


@router.post("/", response_model=DraftResponse, summary="Draft a pastoral message")
def create_draft(
    request: DraftRequest,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "draft pastoral messages")
    account = access.account
    pastor_name, church_name = _pastor_context(db, account)

    if request.kind == "visitor":
        visitor = _visitor_or_404(db, request.visitor_id, account)
        return DraftResponse(
            kind=request.kind,
            recipient_id=visitor.id,
            recipient_name=visitor.full_name,
            draft=draft_visitor_followup(
                visitor=visitor,
                day=request.day,
                pastor_name=pastor_name,
                church_name=church_name,
            ),
        )

    if request.kind == "prayer":
        prayer = _prayer_or_404(db, request.prayer_id, account)
        name = prayer.member.full_name if prayer.member else (prayer.submitted_by or "Friend")
        first_name = prayer.member.first_name if prayer.member else name.split()[0]
        created_at = prayer.created_at or datetime.utcnow()
        days_ago = max((datetime.utcnow() - created_at).days, 0)
        short_summary = _shorten(request.context or prayer.request_text, 90)
        return DraftResponse(
            kind=request.kind,
            recipient_id=prayer.member_id,
            recipient_name=name,
            draft=voice.PRAYER_FOLLOWUP_TEMPLATE.format(
                first_name=first_name,
                days_ago=days_ago,
                short_summary=short_summary,
                pastor_name=pastor_display_name(pastor_name),
            ),
        )

    member = _resolve_member(db, request, account)

    if request.kind == "birthday":
        draft = draft_birthday_message(member, pastor_name=pastor_name)
    elif request.kind == "anniversary":
        draft = draft_anniversary_message(member, pastor_name=pastor_name)
    elif request.kind == "absence":
        draft = draft_absence_checkin(member, pastor_name=pastor_name, church_name=church_name)
    elif request.kind == "care":
        situation = request.situation or request.context or "general"
        draft = draft_care_message(member, situation=situation, pastor_name=pastor_name)
    else:
        situation = request.context or request.situation or "general check-in"
        draft = draft_care_message(member, situation=situation, pastor_name=pastor_name)

    return DraftResponse(
        kind=request.kind,
        recipient_id=member.id,
        recipient_name=member.full_name,
        draft=draft,
    )


def _pastor_context(db: Session, account) -> tuple[str, str]:
    if account:
        profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
        return (
            (profile.pastor_name if profile else None) or account.pastor_name or "Pastor",
            (profile.church_name if profile else None) or account.church_name or "our church",
        )
    return os.getenv("PASTOR_NAME", "Pastor"), os.getenv("CHURCH_NAME", "our church")


def _resolve_member(db: Session, request: DraftRequest, account=None) -> Member:
    if request.member_id:
        return _member_or_404(db, request.member_id, account)

    if request.care_id:
        care = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.id == request.care_id).first()
        if not care:
            raise HTTPException(status_code=404, detail="Care case not found")
        if not care.member:
            raise HTTPException(status_code=404, detail="Care case has no member")
        return care.member

    raise HTTPException(status_code=400, detail="member_id or care_id is required for this draft kind")


def _member_or_404(db: Session, member_id: int, account=None) -> Member:
    member = scoped_query(db.query(Member), Member, account).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _visitor_or_404(db: Session, visitor_id: Optional[int], account=None) -> Visitor:
    if not visitor_id:
        raise HTTPException(status_code=400, detail="visitor_id is required")
    visitor = scoped_query(db.query(Visitor), Visitor, account).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")
    return visitor


def _prayer_or_404(db: Session, prayer_id: Optional[int], account=None) -> PrayerRequest:
    if not prayer_id:
        raise HTTPException(status_code=400, detail="prayer_id is required")
    prayer = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.id == prayer_id).first()
    if not prayer:
        raise HTTPException(status_code=404, detail="Prayer request not found")
    return prayer


def _shorten(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."
