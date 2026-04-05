"""
Briefing router — GET /briefing/today
"""

import os
from typing import List, Optional, Literal
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Member, Visitor, CareNote, PrayerRequest
from app.services.marge import generate_morning_briefing, generate_ai_briefing, render_briefing_text, ai_provider_name
from app.services.demo_data import build_demo_briefing

router = APIRouter(prefix="/briefing", tags=["briefing"])


class MemberBrief(BaseModel):
    id: int
    full_name: str
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    last_attendance: Optional[date] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class VisitorBrief(BaseModel):
    id: int
    full_name: str
    visit_date: date
    follow_up_day1_sent: bool
    follow_up_day3_sent: bool
    follow_up_week2_sent: bool
    notes: Optional[str] = None


class CareBrief(BaseModel):
    id: int
    member_id: int
    member_name: Optional[str] = None
    category: str
    status: str
    description: Optional[str] = None
    last_contact: Optional[date] = None
    created_at: datetime


class PrayerBrief(BaseModel):
    id: int
    member_id: Optional[int] = None
    submitted_by: Optional[str] = None
    request_text: str
    is_private: bool
    status: str
    created_at: datetime


class BriefingStats(BaseModel):
    members: int
    visitors: int
    care_cases: int
    prayer_requests: int


class BriefingResponse(BaseModel):
    greeting: str
    pastor_name: str
    church_name: str
    generated_at: str
    birthdays_this_week: List[MemberBrief]
    anniversaries_this_week: List[MemberBrief]
    visitors_needing_followup: List[VisitorBrief]
    active_care_cases: List[CareBrief]
    absent_members: List[MemberBrief]
    unanswered_prayers: List[PrayerBrief]
    nudges: List[str]
    plain_text: str
    ai_briefing: str
    mode: Literal["demo", "live"]
    data_status: str
    ai_provider: Optional[str] = None
    stats: BriefingStats


@router.get("/today", response_model=BriefingResponse, summary="Get today's morning briefing")
def get_today_briefing(
    mode: Literal["auto", "demo", "live"] = Query("auto"),
    db: Session = Depends(get_db),
):
    pastor_name = os.getenv("PASTOR_NAME", "Pastor")
    church_name = os.getenv("CHURCH_NAME", "your church")

    stats = {
        "members": db.query(Member).count(),
        "visitors": db.query(Visitor).count(),
        "care_cases": db.query(CareNote).count(),
        "prayer_requests": db.query(PrayerRequest).count(),
    }
    has_live_data = any(stats.values())

    if mode == "demo" or (mode == "auto" and not has_live_data):
        demo = build_demo_briefing(pastor_name=pastor_name, church_name=church_name)
        return BriefingResponse(**demo)

    briefing = generate_morning_briefing(db, pastor_name=pastor_name, church_name=church_name)
    plain_text = render_briefing_text(briefing)
    ai_text = generate_ai_briefing(briefing, pastor_name=pastor_name, church_name=church_name)

    return BriefingResponse(
        greeting=briefing["greeting"],
        pastor_name=briefing["pastor_name"],
        church_name=briefing["church_name"],
        generated_at=briefing["generated_at"],
        birthdays_this_week=[_member_to_brief(m) for m in briefing["birthdays_this_week"]],
        anniversaries_this_week=[_member_to_brief(m) for m in briefing["anniversaries_this_week"]],
        visitors_needing_followup=[_visitor_to_brief(v) for v in briefing["visitors_needing_followup"]],
        active_care_cases=[_care_to_brief(c) for c in briefing["active_care_cases"]],
        absent_members=[_member_to_brief(m) for m in briefing["absent_members"]],
        unanswered_prayers=[_prayer_to_brief(p) for p in briefing["unanswered_prayers"]],
        nudges=briefing["nudges"],
        plain_text=plain_text,
        ai_briefing=ai_text,
        mode="live",
        data_status="live_data" if has_live_data else "live_empty",
        ai_provider=ai_provider_name(),
        stats=stats,
    )


def _member_to_brief(m) -> dict:
    return {
        "id": m.id,
        "full_name": m.full_name,
        "birthday": m.birthday,
        "anniversary": m.anniversary,
        "last_attendance": m.last_attendance,
        "email": m.email,
        "phone": m.phone,
    }


def _visitor_to_brief(v) -> dict:
    return {
        "id": v.id,
        "full_name": v.full_name,
        "visit_date": v.visit_date,
        "follow_up_day1_sent": v.follow_up_day1_sent,
        "follow_up_day3_sent": v.follow_up_day3_sent,
        "follow_up_week2_sent": v.follow_up_week2_sent,
        "notes": v.notes,
    }


def _care_to_brief(c) -> dict:
    return {
        "id": c.id,
        "member_id": c.member_id,
        "member_name": c.member.full_name if c.member else None,
        "category": c.category.value if hasattr(c.category, "value") else c.category,
        "status": c.status.value if hasattr(c.status, "value") else c.status,
        "description": c.description,
        "last_contact": c.last_contact,
        "created_at": c.created_at,
    }


def _prayer_to_brief(p) -> dict:
    return {
        "id": p.id,
        "member_id": p.member_id,
        "submitted_by": p.submitted_by or (p.member.full_name if p.member else None),
        "request_text": p.request_text,
        "is_private": p.is_private,
        "status": p.status.value if hasattr(p.status, "value") else p.status,
        "created_at": p.created_at,
    }
