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

from typing import List, Optional
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AccountPastorProfile, Member, MemberNote
from app.services.accounts import (
    PASTORAL_ROLES,
    STAFF_ROLES,
    account_access_from_token,
    account_id,
    require_role,
    require_workspace,
    scoped_query,
)
from app.services.marge import draft_care_message
from app.services.setup_actions import retire_data_seed_actions
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


class MemberCareResponse(BaseModel):
    id: int
    category: str
    status: str
    description: Optional[str] = None
    last_contact: Optional[date] = None
    created_at: datetime


class MemberPrayerResponse(BaseModel):
    id: int
    request_text: str
    is_private: bool
    status: str
    created_at: datetime
    updated_at: datetime


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
    care_cases: List[MemberCareResponse] = []
    prayer_requests: List[MemberPrayerResponse] = []


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
    provider: Optional[str] = None
    status: Optional[str] = None
    synced_at: Optional[datetime] = None
    items_seen: Optional[int] = None
    items_created: Optional[int] = None
    items_updated: Optional[int] = None
    actions_prepared: Optional[int] = None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=MemberResponse, status_code=201, summary="Add a congregation member")
def create_member(
    member_in: MemberCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Manually add a congregation member.

    Most members will come in via Rock RMS sync, but this endpoint
    lets the pastor add someone directly (e.g. a new visitor who just joined).
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "add congregation members")
    account = access.account
    payload = member_in.model_dump()
    payload["rock_id"] = payload.get("rock_id").strip() if payload.get("rock_id") else None
    if payload["rock_id"] and _existing_rock_member(db, payload["rock_id"], account):
        raise HTTPException(status_code=409, detail="A member with this Rock RMS ID already exists in this church workspace.")
    member = Member(**payload, account_id=account_id(account))
    db.add(member)
    db.flush()
    retire_data_seed_actions(db, account, reason="member_created", related_type="member", related_id=member.id)
    db.commit()
    db.refresh(member)
    return _to_response(member)


@router.get("/", response_model=List[MemberResponse], summary="List congregation members")
def list_members(
    q: Optional[str] = Query(None, description="Search by name or email"),
    search: Optional[str] = Query(None, description="Alias for q; useful for MCP clients"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    List congregation members with optional name/email search.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, STAFF_ROLES, "view the member directory")
    account = access.account
    query = scoped_query(db.query(Member), Member, account)
    term = q or search
    if term:
        cleaned = term.strip()
        like = f"%{cleaned}%"
        clauses = [
            Member.first_name.ilike(like),
            Member.last_name.ilike(like),
            Member.email.ilike(like),
        ]
        parts = [part for part in cleaned.split() if part]
        if len(parts) >= 2:
            clauses.append(and_(Member.first_name.ilike(f"%{parts[0]}%"), Member.last_name.ilike(f"%{parts[-1]}%")))
        query = query.filter(or_(*clauses))
    members = query.order_by(Member.last_name, Member.first_name).offset(skip).limit(limit).all()
    return [_to_response(m) for m in members]


@router.get("/{member_id}", response_model=MemberDetailResponse, summary="Get member detail + notes")
def get_member(
    member_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Retrieve a member's full record including all pastoral notes.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view member pastoral details")
    account = access.account
    member = _get_or_404(db, member_id, account)
    return _to_detail_response(member)


@router.patch("/{member_id}", response_model=MemberResponse, summary="Update member info")
def update_member(
    member_id: int,
    update: MemberUpdate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Update a congregation member's contact or date information.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "update congregation members")
    account = access.account
    member = _get_or_404(db, member_id, account)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    db.commit()
    db.refresh(member)
    return _to_response(member)


@router.delete("/{member_id}", status_code=204, summary="Remove a member")
def delete_member(
    member_id: int,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Remove a member from Marge's database.
    This does not affect Rock RMS — it only removes them from the local cache.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "remove congregation members")
    account = access.account
    member = _get_or_404(db, member_id, account)
    db.delete(member)
    db.commit()


@router.post("/{member_id}/notes", response_model=NoteResponse, status_code=201, summary="Add a pastoral note")
def add_note(
    member_id: int,
    note_in: NoteCreate,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Add a pastoral note to a congregation member's record.

    These notes feed Marge's nudge engine. Include a context_tag (e.g. 'job',
    'health', 'family') for the best nudge quality.

    Example tags: job, health, family, grief, marriage, counseling, prayer, struggling
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "add pastoral notes")
    account = access.account
    _get_or_404(db, member_id, account)
    note = MemberNote(
        account_id=account_id(account),
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
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    List all pastoral notes for a congregation member, most recent first.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "view pastoral notes")
    account = access.account
    _get_or_404(db, member_id, account)
    notes = (
        scoped_query(db.query(MemberNote), MemberNote, account)
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
    situation: str = Query("general", description="e.g. 'hospital', 'grief', 'crisis', or freeform"),
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Generate a warm, pastoral care message draft for a congregation member.

    Situation examples: 'hospital', 'grief', 'loss', 'crisis', 'struggling', 'job loss'

    The draft is returned for the pastor to review. Marge never sends on its own.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "draft care messages")
    account = access.account
    member = _get_or_404(db, member_id, account)
    pastor_name = _pastor_name(db, account)

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
def sync_from_rock(
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Legacy compatibility route for Rock RMS sync.

    Delegates to the assistant connector path so Rock uses encrypted workspace
    credentials, safe credential verification, connected-context summaries, and
    reviewable follow-up actions.
    """
    access = account_access_from_token(db, x_marge_account_token)
    require_role(access, PASTORAL_ROLES, "sync Rock RMS")
    require_workspace(access, "sync Rock RMS")
    from app.routers.assistant import _sync_rock_rms

    result = _sync_rock_rms(db, account=access.account)
    return SyncResponse(
        rock_sync_enabled=result.status == "synced",
        message=result.message,
        provider=result.provider,
        status=result.status,
        synced_at=result.synced_at,
        items_seen=result.items_seen,
        items_created=result.items_created,
        items_updated=result.items_updated,
        actions_prepared=result.actions_prepared,
        members={
            "items_seen": result.items_seen,
            "items_created": result.items_created,
            "items_updated": result.items_updated,
        },
        attendance={"actions_prepared": result.actions_prepared},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_or_404(db: Session, member_id: int, account=None) -> Member:
    member = scoped_query(db.query(Member), Member, account).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _pastor_name(db: Session, account=None) -> str:
    if account:
        profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
        return (profile.pastor_name if profile else None) or account.pastor_name or "Pastor"
    return "Pastor"


def _existing_rock_member(db: Session, rock_id: str, account=None) -> Optional[Member]:
    return scoped_query(db.query(Member), Member, account).filter(Member.rock_id == rock_id).first()


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
    base["care_cases"] = [
        {
            "id": c.id,
            "category": c.category.value if hasattr(c.category, "value") else c.category,
            "status": c.status.value if hasattr(c.status, "value") else c.status,
            "description": c.description,
            "last_contact": c.last_contact,
            "created_at": c.created_at,
        }
        for c in sorted(m.care_notes, key=lambda x: x.created_at, reverse=True)
    ]
    base["prayer_requests"] = [
        {
            "id": p.id,
            "request_text": p.request_text,
            "is_private": p.is_private,
            "status": p.status.value if hasattr(p.status, "value") else p.status,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in sorted(m.prayer_requests, key=lambda x: x.created_at, reverse=True)
    ]
    return base
