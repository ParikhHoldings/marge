"""Visitors router — CRUD + follow-up draft generation."""

import os
from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import AuthContext, ROLE_ADMIN, ROLE_PASTOR, ROLE_READ_ONLY, ROLE_STAFF, require_roles
from app.database import get_db
from app.models import Visitor
from app.services.marge import draft_visitor_followup

router = APIRouter(prefix="/visitors", tags=["visitors"])


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


@router.post("/", response_model=VisitorResponse, status_code=201, summary="Log a new visitor")
def create_visitor(
    visitor_in: VisitorCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    visitor = Visitor(**visitor_in.model_dump(), church_id=auth.church_id)
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    return _to_response(visitor)


@router.get("/", response_model=List[VisitorResponse], summary="List visitors")
def list_visitors(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    needs_followup: bool = Query(False, description="Filter to visitors needing Day-1 follow-up"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF, ROLE_READ_ONLY)),
):
    query = db.query(Visitor).filter(Visitor.church_id == auth.church_id)
    if needs_followup:
        cutoff = date.today()
        query = query.filter(Visitor.visit_date <= cutoff, Visitor.follow_up_day1_sent == False)  # noqa: E712
    visitors = query.order_by(Visitor.visit_date.desc()).offset(skip).limit(limit).all()
    return [_to_response(v) for v in visitors]


@router.get("/{visitor_id}", response_model=VisitorResponse, summary="Get a visitor")
def get_visitor(
    visitor_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF, ROLE_READ_ONLY)),
):
    visitor = _get_or_404(db, visitor_id, auth.church_id)
    return _to_response(visitor)


@router.patch("/{visitor_id}", response_model=VisitorResponse, summary="Update a visitor")
def update_visitor(
    visitor_id: int,
    update: VisitorUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    visitor = _get_or_404(db, visitor_id, auth.church_id)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(visitor, field, value)
    db.commit()
    db.refresh(visitor)
    return _to_response(visitor)


@router.delete("/{visitor_id}", status_code=204, summary="Delete a visitor record")
def delete_visitor(
    visitor_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN)),
):
    visitor = _get_or_404(db, visitor_id, auth.church_id)
    db.delete(visitor)
    db.commit()


@router.get("/{visitor_id}/draft", response_model=DraftResponse, summary="Get a follow-up message draft for a visitor")
def get_visitor_draft(
    visitor_id: int,
    day: int = Query(1, description="Follow-up day: 1 (text), 3 (email), 14 (invitation)"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    visitor = _get_or_404(db, visitor_id, auth.church_id)
    pastor_name = os.getenv("PASTOR_NAME", "Pastor")
    church_name = os.getenv("CHURCH_NAME", "our church")

    draft = draft_visitor_followup(visitor=visitor, day=day, pastor_name=pastor_name, church_name=church_name)
    return DraftResponse(visitor_id=visitor.id, visitor_name=visitor.full_name, day=day, draft=draft)


def _get_or_404(db: Session, visitor_id: int, church_id: str) -> Visitor:
    visitor = db.query(Visitor).filter(Visitor.id == visitor_id, Visitor.church_id == church_id).first()
    if not visitor:
        raise HTTPException(status_code=404, detail="Visitor not found")
    return visitor


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
