"""
Care router — Active pastoral care cases.

Endpoints:
  POST   /care/                   Open a new care case
  GET    /care/                   List care cases (filterable by status/category)
  GET    /care/{id}               Get a specific care case
  PATCH  /care/{id}               Update status, last_contact, description
  DELETE /care/{id}               Delete a care case
  POST   /care/{id}/resolve       Shortcut to mark a case resolved
  POST   /care/{id}/contact       Log a contact and update last_contact date

Prayer request endpoints:
  POST   /care/prayers/           Create a prayer request
  GET    /care/prayers/           List prayer requests
  GET    /care/prayers/{id}       Get a specific prayer request
  PATCH  /care/prayers/{id}       Update status (answered, archived, etc.)
"""

from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CareNote, PrayerRequest, Member
from app.services.accounts import (
    PASTORAL_ROLES,
    account_access_from_token,
    account_id,
    require_role,
    scoped_query,
)
from app.services.setup_actions import retire_data_seed_actions

router = APIRouter(prefix="/care", tags=["care"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class CareCreate(BaseModel):
    member_id: int
    category: str  # hospital | crisis | grief | general
    description: Optional[str] = None
    last_contact: Optional[date] = None


class CareUpdate(BaseModel):
    category: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    last_contact: Optional[date] = None


class CareResponse(BaseModel):
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


class ContactLog(BaseModel):
    contact_date: Optional[date] = None  # defaults to today
    note: Optional[str] = None           # optionally append to description


class PrayerCreate(BaseModel):
    member_id: Optional[int] = None
    submitted_by: Optional[str] = None
    request_text: str
    is_private: bool = False


class PrayerUpdate(BaseModel):
    status: Optional[str] = None
    is_private: Optional[bool] = None
    request_text: Optional[str] = None


class PrayerResponse(BaseModel):
    id: int
    member_id: Optional[int] = None
    member_name: Optional[str] = None
    submitted_by: Optional[str] = None
    request_text: str
    is_private: bool
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Care case routes ──────────────────────────────────────────────────────────


@router.post("/", response_model=CareResponse, status_code=201, summary="Open a new care case")
def create_care_case(
    care_in: CareCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Open a new pastoral care case for a congregation member.

    category options: hospital, crisis, grief, general

    Marge will surface active cases with no recent contact in the morning
    briefing after 7 days.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "open care cases")
    account = access.account
    member = scoped_query(db.query(Member), Member, account).filter(Member.id == care_in.member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    care = CareNote(
        account_id=account_id(account),
        member_id=care_in.member_id,
        category=care_in.category,
        description=care_in.description,
        last_contact=care_in.last_contact,
        status="active",
    )
    db.add(care)
    db.flush()
    retire_data_seed_actions(db, account, reason="care_case_created", related_type="care", related_id=care.id)
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.get("/", response_model=List[CareResponse], summary="List care cases")
def list_care_cases(
    status: Optional[str] = Query(None, description="Filter by status: active | resolved"),
    category: Optional[str] = Query(None, description="Filter by category: hospital | crisis | grief | general"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    List pastoral care cases, optionally filtered by status and/or category.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view care cases")
    account = access.account
    query = scoped_query(db.query(CareNote), CareNote, account)
    if status:
        query = query.filter(CareNote.status == status)
    if category:
        query = query.filter(CareNote.category == category)
    cases = query.order_by(CareNote.last_contact.asc().nullsfirst()).offset(skip).limit(limit).all()
    return [_to_care_response(c) for c in cases]


@router.get("/{care_id:int}", response_model=CareResponse, summary="Get a care case")
def get_care_case(
    care_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Retrieve a single care case by ID."""
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view care cases")
    account = access.account
    care = _get_care_or_404(db, care_id, account)
    return _to_care_response(care)


@router.patch("/{care_id:int}", response_model=CareResponse, summary="Update a care case")
def update_care_case(
    care_id: int,
    update: CareUpdate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Update a care case — change category, status, description, or last_contact date.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "update care cases")
    account = access.account
    care = _get_care_or_404(db, care_id, account)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(care, field, value)
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.delete("/{care_id:int}", status_code=204, summary="Delete a care case")
def delete_care_case(
    care_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Delete a care case record."""
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "delete care cases")
    account = access.account
    care = _get_care_or_404(db, care_id, account)
    db.delete(care)
    db.commit()


@router.post("/{care_id:int}/resolve", response_model=CareResponse, summary="Resolve a care case")
def resolve_care_case(
    care_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Mark a care case as resolved.

    Marge will stop surfacing it in the morning briefing.
    The case remains in the database for historical reference.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "resolve care cases")
    account = access.account
    care = _get_care_or_404(db, care_id, account)
    care.status = "resolved"
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.post("/{care_id:int}/contact", response_model=CareResponse, summary="Log a pastoral contact")
def log_contact(
    care_id: int,
    log: ContactLog,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Log a pastoral contact for a care case and update last_contact.

    This resets Marge's 7-day follow-up timer for this case.
    Optionally appends a note to the care case description.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "log pastoral contacts")
    account = access.account
    care = _get_care_or_404(db, care_id, account)
    care.last_contact = log.contact_date or date.today()

    if log.note:
        existing = care.description or ""
        timestamp = date.today().isoformat()
        care.description = f"{existing}\n\n[{timestamp}] {log.note}".strip()

    db.commit()
    db.refresh(care)
    return _to_care_response(care)


# ── Prayer request routes ─────────────────────────────────────────────────────


@router.post("/prayers/", response_model=PrayerResponse, status_code=201, summary="Submit a prayer request")
def create_prayer_request(
    prayer_in: PrayerCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Create a new prayer request.

    member_id is optional — anonymous requests are supported.
    is_private=True keeps the request out of the public prayer list.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "submit prayer requests")
    account = access.account
    if prayer_in.member_id:
        member = scoped_query(db.query(Member), Member, account).filter(Member.id == prayer_in.member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

    prayer = PrayerRequest(
        account_id=account_id(account),
        member_id=prayer_in.member_id,
        submitted_by=prayer_in.submitted_by,
        request_text=prayer_in.request_text,
        is_private=prayer_in.is_private,
        status="active",
    )
    db.add(prayer)
    db.flush()
    retire_data_seed_actions(db, account, reason="prayer_created", related_type="prayer", related_id=prayer.id)
    db.commit()
    db.refresh(prayer)
    return _to_prayer_response(prayer)


@router.get("/prayers/", response_model=List[PrayerResponse], summary="List prayer requests")
def list_prayer_requests(
    status: Optional[str] = Query(None, description="Filter by status: active | answered | expired"),
    include_private: bool = Query(False, description="Include private requests"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    List prayer requests, optionally filtered by status.

    Private requests are excluded by default to protect member privacy.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view prayer requests")
    account = access.account
    query = scoped_query(db.query(PrayerRequest), PrayerRequest, account)
    if status:
        query = query.filter(PrayerRequest.status == status)
    if not include_private:
        query = query.filter(PrayerRequest.is_private == False)  # noqa: E712
    prayers = query.order_by(PrayerRequest.created_at.desc()).offset(skip).limit(limit).all()
    return [_to_prayer_response(p) for p in prayers]


@router.get("/prayers/{prayer_id:int}", response_model=PrayerResponse, summary="Get a prayer request")
def get_prayer_request(
    prayer_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Retrieve a single prayer request by ID."""
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view prayer requests")
    account = access.account
    prayer = _get_prayer_or_404(db, prayer_id, account)
    return _to_prayer_response(prayer)


@router.patch("/prayers/{prayer_id:int}", response_model=PrayerResponse, summary="Update a prayer request")
def update_prayer_request(
    prayer_id: int,
    update: PrayerUpdate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Update a prayer request status or text.

    Common updates:
    - status='answered' — close the loop, optionally draft a celebration message
    - status='expired'  — archive stale requests
    - is_private=True   — make a public request private
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "update prayer requests")
    account = access.account
    prayer = _get_prayer_or_404(db, prayer_id, account)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(prayer, field, value)
    prayer.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(prayer)
    return _to_prayer_response(prayer)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_care_or_404(db: Session, care_id: int, account=None) -> CareNote:
    care = scoped_query(db.query(CareNote), CareNote, account).filter(CareNote.id == care_id).first()
    if not care:
        raise HTTPException(status_code=404, detail="Care case not found")
    return care


def _get_prayer_or_404(db: Session, prayer_id: int, account=None) -> PrayerRequest:
    prayer = scoped_query(db.query(PrayerRequest), PrayerRequest, account).filter(PrayerRequest.id == prayer_id).first()
    if not prayer:
        raise HTTPException(status_code=404, detail="Prayer request not found")
    return prayer


def _to_care_response(c: CareNote) -> dict:
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


def _to_prayer_response(p: PrayerRequest) -> dict:
    return {
        "id": p.id,
        "member_id": p.member_id,
        "member_name": p.member.full_name if p.member else None,
        "submitted_by": p.submitted_by or (p.member.full_name if p.member else None),
        "request_text": p.request_text,
        "is_private": p.is_private,
        "status": p.status.value if hasattr(p.status, "value") else p.status,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
