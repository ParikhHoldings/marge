"""
Briefing router — GET /briefing/today

Returns Marge's morning briefing for the pastor: birthdays, visitors,
care cases, absences, prayer requests, and proactive nudges.
"""

import os
from typing import List, Optional, Any
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.observability import inc_counter, time_workflow
from app.services.marge import generate_morning_briefing, render_briefing_text

router = APIRouter(prefix="/briefing", tags=["briefing"])

# ── Pydantic response models ──────────────────────────────────────────────────


class MemberBrief(BaseModel):
    id: int
    full_name: str
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    last_attendance: Optional[date] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    class Config:
        from_attributes = True


class VisitorBrief(BaseModel):
    id: int
    full_name: str
    visit_date: date
    follow_up_day1_sent: bool
    follow_up_day3_sent: bool
    follow_up_week2_sent: bool
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class CareBrief(BaseModel):
    id: int
    member_id: int
    member_name: Optional[str] = None
    category: str
    status: str
    description: Optional[str] = None
    last_contact: Optional[date] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PrayerBrief(BaseModel):
    id: int
    member_id: Optional[int] = None
    submitted_by: Optional[str] = None
    request_text: str
    is_private: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


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
    plain_text: str  # Pre-rendered text version for Telegram / email


# ── Route ─────────────────────────────────────────────────────────────────────


@router.get("/today", response_model=BriefingResponse, summary="Get today's morning briefing")
def get_today_briefing(db: Session = Depends(get_db)):
    """
    Generate and return Marge's morning briefing for today.

    Pulls from the local database to surface:
    - Birthdays and anniversaries this week
    - First-time visitors needing follow-up
    - Active care cases with no recent contact
    - Members absent more than 21 days
    - Prayer requests older than 14 days with no update
    - Proactive relationship nudges

    Also returns a plain_text version suitable for sending via Telegram or email.
    """
    pastor_name = os.getenv("PASTOR_NAME", "Pastor")
    church_name = os.getenv("CHURCH_NAME", "your church")
    try:
        with time_workflow("briefing_generation_latency_ms"):
            briefing = generate_morning_briefing(db, pastor_name=pastor_name, church_name=church_name)
        inc_counter("briefing_generation_total", status="success")
    except Exception:
        inc_counter("briefing_generation_total", status="failure")
        raise

    # Serialize ORM objects into Pydantic-compatible dicts
    def member_to_brief(m) -> dict:
        return {
            "id": m.id,
            "full_name": m.full_name,
            "birthday": m.birthday,
            "anniversary": m.anniversary,
            "last_attendance": m.last_attendance,
            "email": m.email,
            "phone": m.phone,
        }

    def visitor_to_brief(v) -> dict:
        return {
            "id": v.id,
            "full_name": v.full_name,
            "visit_date": v.visit_date,
            "follow_up_day1_sent": v.follow_up_day1_sent,
            "follow_up_day3_sent": v.follow_up_day3_sent,
            "follow_up_week2_sent": v.follow_up_week2_sent,
            "notes": v.notes,
        }

    def care_to_brief(c) -> dict:
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

    def prayer_to_brief(p) -> dict:
        return {
            "id": p.id,
            "member_id": p.member_id,
            "submitted_by": p.submitted_by or (p.member.full_name if p.member else None),
            "request_text": p.request_text,
            "is_private": p.is_private,
            "status": p.status.value if hasattr(p.status, "value") else p.status,
            "created_at": p.created_at,
        }

    plain_text = render_briefing_text(briefing)

    return BriefingResponse(
        greeting=briefing["greeting"],
        pastor_name=briefing["pastor_name"],
        church_name=briefing["church_name"],
        generated_at=briefing["generated_at"],
        birthdays_this_week=[member_to_brief(m) for m in briefing["birthdays_this_week"]],
        anniversaries_this_week=[member_to_brief(m) for m in briefing["anniversaries_this_week"]],
        visitors_needing_followup=[visitor_to_brief(v) for v in briefing["visitors_needing_followup"]],
        active_care_cases=[care_to_brief(c) for c in briefing["active_care_cases"]],
        absent_members=[member_to_brief(m) for m in briefing["absent_members"]],
        unanswered_prayers=[prayer_to_brief(p) for p in briefing["unanswered_prayers"]],
        nudges=briefing["nudges"],
        plain_text=plain_text,
    )
