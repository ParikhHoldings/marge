"""
Members router — Member CRM + pastoral notes.

Endpoints:
  POST   /members/                  Create a member
  GET    /members/                  List members (with search)
  GET    /members/{id}              Get member + care history + notes
  PATCH  /members/{id}              Update member info
  DELETE /members/{id}              Delete member
  POST   /members/{id}/notes        Add a pastoral note to a member
  GET    /members/{id}/notes        List all notes for a member
  GET    /members/{id}/draft/care   Draft a care message for a member
  POST   /sync/rock                 Trigger Rock RMS sync (background)
"""

import os
from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Member, MemberNote
from app.observability import inc_counter, time_workflow
from app.services.marge import draft_care_message
from app.integrations import rock as rock_sync

router = APIRouter(prefix="/members", tags=["members"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class MemberCreate(BaseModel):
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    last_attendance: Optional[date] = None
    rock_id: Optional[str] = None


class MemberUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    last_attendance: Optional[date] = None


class NoteCreate(BaseModel):
    note_text: str
    context_tag: Optional[str] = None  # job, health, family, grief, etc.


class NoteResponse(BaseModel):
    id: int
    member_id: int
    note_text: str
    context_tag: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MemberResponse(BaseModel):
    id: int
    rock_id: Optional[str] = None
    first_name: str
    last_name: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[date] = None
    anniversary: Optional[date] = None
    last_attendance: Optional[date] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MemberDetailResponse(MemberResponse):
    notes: List[NoteResponse] = []


class CareDraftResponse(BaseModel):
    member_id: int
    member_name: str
    situation: str
    draft: str


class SyncResponse(BaseModel):
    rock_sync_enabled: bool
    message: Optional[str] = None
    members: Optional[dict] = None
    attendance: Optional[dict] = None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=MemberResponse, status_code=201, summary="Add a congregation member")
def create_member(member_in: MemberCreate, db: Session = Depends(get_db)):
    """
    Manually add a congregation member.

    Most members will come in via Rock RMS sync, but this endpoint
    lets the pastor add someone directly (e.g. a new visitor who just joined).
    """
    member = Member(**member_in.model_dump())
    db.add(member)
    db.commit()
    db.refresh(member)
    return _to_response(member)


@router.get("/", response_model=List[MemberResponse], summary="List congregation members")
def list_members(
    q: Optional[str] = Query(None, description="Search by name or email"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    List congregation members with optional name/email search.
    """
    query = db.query(Member)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Member.first_name.ilike(like) |
            Member.last_name.ilike(like) |
            Member.email.ilike(like)
        )
    members = query.order_by(Member.last_name, Member.first_name).offset(skip).limit(limit).all()
    return [_to_response(m) for m in members]


@router.get("/{member_id}", response_model=MemberDetailResponse, summary="Get member detail + notes")
def get_member(member_id: int, db: Session = Depends(get_db)):
    """
    Retrieve a member's full record including all pastoral notes.
    """
    member = _get_or_404(db, member_id)
    return _to_detail_response(member)


@router.patch("/{member_id}", response_model=MemberResponse, summary="Update member info")
def update_member(member_id: int, update: MemberUpdate, db: Session = Depends(get_db)):
    """
    Update a congregation member's contact or date information.
    """
    member = _get_or_404(db, member_id)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    db.commit()
    db.refresh(member)
    return _to_response(member)


@router.delete("/{member_id}", status_code=204, summary="Remove a member")
def delete_member(member_id: int, db: Session = Depends(get_db)):
    """
    Remove a member from Marge's database.
    This does not affect Rock RMS — it only removes them from the local cache.
    """
    member = _get_or_404(db, member_id)
    db.delete(member)
    db.commit()


@router.post("/{member_id}/notes", response_model=NoteResponse, status_code=201, summary="Add a pastoral note")
def add_note(member_id: int, note_in: NoteCreate, db: Session = Depends(get_db)):
    """
    Add a pastoral note to a congregation member's record.

    These notes feed Marge's nudge engine. Include a context_tag (e.g. 'job',
    'health', 'family') for the best nudge quality.

    Example tags: job, health, family, grief, marriage, counseling, prayer, struggling
    """
    _get_or_404(db, member_id)
    note = MemberNote(
        member_id=member_id,
        note_text=note_in.note_text,
        context_tag=note_in.context_tag,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/{member_id}/notes", response_model=List[NoteResponse], summary="List pastoral notes for a member")
def list_notes(
    member_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    List all pastoral notes for a congregation member, most recent first.
    """
    _get_or_404(db, member_id)
    notes = (
        db.query(MemberNote)
        .filter(MemberNote.member_id == member_id)
        .order_by(MemberNote.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return notes


@router.get(
    "/{member_id}/draft/care",
    response_model=CareDraftResponse,
    summary="Draft a pastoral care message",
)
def draft_care(
    member_id: int,
    situation: str = Query(..., description="e.g. 'hospital', 'grief', 'crisis', or freeform"),
    db: Session = Depends(get_db),
):
    """
    Generate a warm, pastoral care message draft for a congregation member.

    Situation examples: 'hospital', 'grief', 'loss', 'crisis', 'struggling', 'job loss'

    The draft is returned for the pastor to review. Marge never sends on its own.
    """
    member = _get_or_404(db, member_id)
    pastor_name = os.getenv("PASTOR_NAME", "Pastor")

    draft = draft_care_message(
        member=member,
        situation=situation,
        pastor_name=pastor_name,
    )

    return CareDraftResponse(
        member_id=member.id,
        member_name=member.full_name,
        situation=situation,
        draft=draft,
    )


@router.post("/sync/rock", response_model=SyncResponse, summary="Sync members from Rock RMS")
def sync_from_rock(db: Session = Depends(get_db)):
    """
    Trigger a full Rock RMS sync: pull active people and recent attendance.

    Safe to call even without Rock credentials — returns a clear message
    if the API key is not configured.
    """
    with time_workflow("rock_sync_latency_ms"):
        result = rock_sync.run_full_sync(db)
    if result.get("rock_sync_enabled"):
        member_stats = result.get("members") or {}
        attendance_stats = result.get("attendance") or {}
        total_created = int(member_stats.get("created", 0))
        total_updated = int(member_stats.get("updated", 0)) + int(attendance_stats.get("updated", 0))
        if total_created > 0 or total_updated > 0:
            inc_counter("rock_sync_outcome_total", status="success")
        else:
            inc_counter("rock_sync_outcome_total", status="degraded")
    else:
        inc_counter("rock_sync_outcome_total", status="disabled")
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_or_404(db: Session, member_id: int) -> Member:
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _to_response(m: Member) -> dict:
    return {
        "id": m.id,
        "rock_id": m.rock_id,
        "first_name": m.first_name,
        "last_name": m.last_name,
        "full_name": m.full_name,
        "email": m.email,
        "phone": m.phone,
        "birthday": m.birthday,
        "anniversary": m.anniversary,
        "last_attendance": m.last_attendance,
        "created_at": m.created_at,
    }


def _to_detail_response(m: Member) -> dict:
    base = _to_response(m)
    base["notes"] = [
        {
            "id": n.id,
            "member_id": n.member_id,
            "note_text": n.note_text,
            "context_tag": n.context_tag,
            "created_at": n.created_at,
        }
        for n in sorted(m.notes, key=lambda x: x.created_at, reverse=True)
    ]
    return base
