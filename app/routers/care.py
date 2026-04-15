"""Care router — Active pastoral care cases + prayer requests."""

from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import AuthContext, ROLE_ADMIN, ROLE_PASTOR, ROLE_READ_ONLY, ROLE_STAFF, require_roles
from app.database import get_db
from app.models import CareNote, PrayerRequest, Member

router = APIRouter(prefix="/care", tags=["care"])


class CareCreate(BaseModel):
    member_id: int
    category: str
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
    contact_date: Optional[date] = None
    note: Optional[str] = None


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


@router.post("/", response_model=CareResponse, status_code=201, summary="Open a new care case")
def create_care_case(
    care_in: CareCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    member = db.query(Member).filter(Member.id == care_in.member_id, Member.church_id == auth.church_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    care = CareNote(
        church_id=auth.church_id,
        member_id=care_in.member_id,
        category=care_in.category,
        description=care_in.description,
        last_contact=care_in.last_contact,
        status="active",
    )
    db.add(care)
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.get("/", response_model=List[CareResponse], summary="List care cases")
def list_care_cases(
    status: Optional[str] = Query(None, description="Filter by status: active | resolved"),
    category: Optional[str] = Query(None, description="Filter by category: hospital | crisis | grief | general"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    query = db.query(CareNote).filter(CareNote.church_id == auth.church_id)
    if status:
        query = query.filter(CareNote.status == status)
    if category:
        query = query.filter(CareNote.category == category)
    cases = query.order_by(CareNote.last_contact.asc().nullsfirst()).offset(skip).limit(limit).all()
    return [_to_care_response(c) for c in cases]


@router.get("/{care_id}", response_model=CareResponse, summary="Get a care case")
def get_care_case(
    care_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    care = _get_care_or_404(db, care_id, auth.church_id)
    return _to_care_response(care)


@router.patch("/{care_id}", response_model=CareResponse, summary="Update a care case")
def update_care_case(
    care_id: int,
    update: CareUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    care = _get_care_or_404(db, care_id, auth.church_id)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(care, field, value)
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.delete("/{care_id}", status_code=204, summary="Delete a care case")
def delete_care_case(
    care_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN)),
):
    care = _get_care_or_404(db, care_id, auth.church_id)
    db.delete(care)
    db.commit()


@router.post("/{care_id}/resolve", response_model=CareResponse, summary="Resolve a care case")
def resolve_care_case(
    care_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    care = _get_care_or_404(db, care_id, auth.church_id)
    care.status = "resolved"
    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.post("/{care_id}/contact", response_model=CareResponse, summary="Log a pastoral contact")
def log_contact(
    care_id: int,
    log: ContactLog,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    care = _get_care_or_404(db, care_id, auth.church_id)
    care.last_contact = log.contact_date or date.today()

    if log.note:
        existing = care.description or ""
        timestamp = date.today().isoformat()
        care.description = f"{existing}\n\n[{timestamp}] {log.note}".strip()

    db.commit()
    db.refresh(care)
    return _to_care_response(care)


@router.post("/prayers/", response_model=PrayerResponse, status_code=201, summary="Submit a prayer request")
def create_prayer_request(
    prayer_in: PrayerCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    if prayer_in.is_private and auth.role not in {ROLE_PASTOR, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Forbidden: only pastor/admin can create private prayer requests")

    if prayer_in.member_id:
        member = db.query(Member).filter(Member.id == prayer_in.member_id, Member.church_id == auth.church_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

    prayer = PrayerRequest(
        church_id=auth.church_id,
        member_id=prayer_in.member_id,
        submitted_by=prayer_in.submitted_by,
        request_text=prayer_in.request_text,
        is_private=prayer_in.is_private,
        status="active",
    )
    db.add(prayer)
    db.commit()
    db.refresh(prayer)
    return _to_prayer_response(prayer)


@router.get("/prayers/", response_model=List[PrayerResponse], summary="List prayer requests")
def list_prayer_requests(
    status: Optional[str] = Query(None, description="Filter by status: active | answered | expired"),
    include_private: bool = Query(False, description="Include private requests"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF, ROLE_READ_ONLY)),
):
    if include_private and auth.role not in {ROLE_PASTOR, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Forbidden: private prayer requests are pastor/admin only")

    query = db.query(PrayerRequest).filter(PrayerRequest.church_id == auth.church_id)
    if status:
        query = query.filter(PrayerRequest.status == status)
    if auth.role not in {ROLE_PASTOR, ROLE_ADMIN} or not include_private:
        query = query.filter(PrayerRequest.is_private == False)  # noqa: E712

    prayers = query.order_by(PrayerRequest.created_at.desc()).offset(skip).limit(limit).all()
    return [_to_prayer_response(p) for p in prayers]


@router.get("/prayers/{prayer_id}", response_model=PrayerResponse, summary="Get a prayer request")
def get_prayer_request(
    prayer_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF, ROLE_READ_ONLY)),
):
    prayer = _get_prayer_or_404(db, prayer_id, auth.church_id)
    if prayer.is_private and auth.role not in {ROLE_PASTOR, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Forbidden: private prayer requests are pastor/admin only")
    return _to_prayer_response(prayer)


@router.patch("/prayers/{prayer_id}", response_model=PrayerResponse, summary="Update a prayer request")
def update_prayer_request(
    prayer_id: int,
    update: PrayerUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(ROLE_PASTOR, ROLE_ADMIN, ROLE_STAFF)),
):
    prayer = _get_prayer_or_404(db, prayer_id, auth.church_id)
    if prayer.is_private and auth.role not in {ROLE_PASTOR, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Forbidden: private prayer requests are pastor/admin only")
    if update.is_private is True and auth.role not in {ROLE_PASTOR, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Forbidden: only pastor/admin can mark prayer requests private")

    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(prayer, field, value)
    prayer.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(prayer)
    return _to_prayer_response(prayer)


def _get_care_or_404(db: Session, care_id: int, church_id: str) -> CareNote:
    care = db.query(CareNote).filter(CareNote.id == care_id, CareNote.church_id == church_id).first()
    if not care:
        raise HTTPException(status_code=404, detail="Care case not found")
    return care


def _get_prayer_or_404(db: Session, prayer_id: int, church_id: str) -> PrayerRequest:
    prayer = db.query(PrayerRequest).filter(PrayerRequest.id == prayer_id, PrayerRequest.church_id == church_id).first()
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
