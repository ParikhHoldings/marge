"""
Visitors router — CRUD + follow-up draft generation.

Endpoints:
  POST   /visitors/             Create a new visitor record
  GET    /visitors/             List all visitors (paginated)
  GET    /visitors/{id}         Get a specific visitor
  PATCH  /visitors/{id}         Update visitor (mark follow-up sent, add notes, etc.)
  DELETE /visitors/{id}         Delete a visitor record
  GET    /visitors/{id}/draft   Get a pre-written follow-up message draft
"""

from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AccountPastorProfile, Visitor
from app.services.accounts import (
    PASTORAL_ROLES,
    STAFF_ROLES,
    account_access_from_token,
    account_id,
    require_role,
    scoped_query,
)
from app.services.marge import draft_visitor_followup
from app.services.setup_actions import retire_data_seed_actions
from app.services.visitor_followup import queue_visitor_welcome_action

router = APIRouter(prefix="/visitors", tags=["visitors"])

# ── Pydantic schemas ──────────────────────────────────────────────────────────


class VisitorCreate(BaseModel):
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    visit_date: date
    source: Optional[str] = None
    notes: Optional[str] = None


class VisitorUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    visit_date: Optional[date] = None
    source: Optional[str] = None
    follow_up_day1_sent: Optional[bool] = None
    follow_up_day3_sent: Optional[bool] = None
    follow_up_week2_sent: Optional[bool] = None
    notes: Optional[str] = None


class VisitorResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    visit_date: date
    source: Optional[str] = None
    follow_up_day1_sent: bool
    follow_up_day3_sent: bool
    follow_up_week2_sent: bool
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DraftResponse(BaseModel):
    visitor_id: int
    visitor_name: str
    day: int
    draft: str


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=VisitorResponse, status_code=201, summary="Log a new visitor")
def create_visitor(
    visitor_in: VisitorCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Log a first-time (or repeat) visitor.

    Marge queues a reviewable welcome draft immediately, then keeps the
    visitor visible in follow-up views until the pastor marks follow-up sent.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "log visitors")
    account = access.account
    visitor = Visitor(**visitor_in.model_dump(), account_id=account_id(account))
    db.add(visitor)
    db.flush()
    queue_visitor_welcome_action(db, visitor, account)
    retire_data_seed_actions(db, account, reason="visitor_created", related_type="visitor", related_id=visitor.id)
    db.commit()
    db.refresh(visitor)
    return _to_response(visitor)


@router.get("/", response_model=List[VisitorResponse], summary="List visitors")
def list_visitors(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    needs_followup: bool = Query(False, description="Filter to visitors needing Day-1 follow-up"),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    List all visitors, optionally filtered to those needing follow-up.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, STAFF_ROLES, "view visitors")
    account = access.account
    query = scoped_query(db.query(Visitor), Visitor, account)
    if needs_followup:
        cutoff = date.today()
        query = query.filter(
            Visitor.visit_date <= cutoff,
            Visitor.follow_up_day1_sent == False,  # noqa: E712
        )
    visitors = query.order_by(Visitor.visit_date.desc()).offset(skip).limit(limit).all()
    return [_to_response(v) for v in visitors]


@router.get("/{visitor_id}", response_model=VisitorResponse, summary="Get a visitor")
def get_visitor(
    visitor_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Retrieve a single visitor record by ID."""
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, STAFF_ROLES, "view visitors")
    account = access.account
    visitor = _get_or_404(db, visitor_id, account)
    return _to_response(visitor)


@router.patch("/{visitor_id}", response_model=VisitorResponse, summary="Update a visitor")
def update_visitor(
    visitor_id: int,
    update: VisitorUpdate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Update visitor details or mark a follow-up step as sent.

    Example: mark Day-1 follow-up as sent after the pastor sends the text.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "update visitors")
    account = access.account
    visitor = _get_or_404(db, visitor_id, account)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(visitor, field, value)
    db.commit()
    db.refresh(visitor)
    return _to_response(visitor)


@router.delete("/{visitor_id}", status_code=204, summary="Delete a visitor record")
def delete_visitor(
    visitor_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Delete a visitor record. Use when a visitor becomes a member or was entered in error."""
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "delete visitors")
    account = access.account
    visitor = _get_or_404(db, visitor_id, account)
    db.delete(visitor)
    db.commit()


@router.get(
    "/{visitor_id}/draft",
    response_model=DraftResponse,
    summary="Get a follow-up message draft for a visitor",
)
def get_visitor_draft(
    visitor_id: int,
    day: int = Query(1, description="Follow-up day: 1 (text), 3 (email), 14 (invitation)"),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Generate a warm, pastoral follow-up message draft for a visitor.

    day=1  → Same-day welcome text (sent after the visit)
    day=3  → 3-day email follow-up
    day=14 → Two-week invitation to return

    The pastor reviews the draft and sends it — Marge never sends automatically.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "draft visitor follow-up")
    account = access.account
    visitor = _get_or_404(db, visitor_id, account)
    pastor_name, church_name = _pastor_context(db, account)

    draft = draft_visitor_followup(
        visitor=visitor,
        day=day,
        pastor_name=pastor_name,
        church_name=church_name,
    )

    return DraftResponse(
        visitor_id=visitor.id,
        visitor_name=visitor.full_name,
        day=day,
        draft=draft,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_or_404(db: Session, visitor_id: int, account=None) -> Visitor:
    visitor = scoped_query(db.query(Visitor), Visitor, account).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")
    return visitor


def _pastor_context(db: Session, account=None) -> tuple[str, str]:
    if account:
        profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
        return (
            (profile.pastor_name if profile else None) or account.pastor_name or "Pastor",
            (profile.church_name if profile else None) or account.church_name or "our church",
        )
    return "Pastor", "our church"


def _to_response(v: Visitor) -> dict:
    return {
        "id": v.id,
        "first_name": v.first_name,
        "last_name": v.last_name,
        "full_name": v.full_name,
        "email": v.email,
        "phone": v.phone,
        "visit_date": v.visit_date,
        "source": v.source,
        "follow_up_day1_sent": v.follow_up_day1_sent,
        "follow_up_day3_sent": v.follow_up_day3_sent,
        "follow_up_week2_sent": v.follow_up_week2_sent,
        "notes": v.notes,
        "created_at": v.created_at,
    }
