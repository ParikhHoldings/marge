"""
ORM models for Marge — AI Pastoral Assistant.

Tables:
  - Member         : congregation member records
  - Visitor        : first-time visitor tracking
  - CareNote       : active care / crisis cases
  - PrayerRequest  : prayer requests with lifecycle
  - MemberNote     : pastoral CRM notes per member
  - ChatActionTrace: parsed-intent/action audit records
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime, Float,
    Boolean, ForeignKey, Enum
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class CareCategoryEnum(str, enum.Enum):
    hospital = "hospital"
    crisis = "crisis"
    grief = "grief"
    general = "general"


class CareStatusEnum(str, enum.Enum):
    active = "active"
    resolved = "resolved"


class PrayerStatusEnum(str, enum.Enum):
    active = "active"
    answered = "answered"
    expired = "expired"


class Member(Base):
    """A congregation member. May be synced from Rock RMS via rock_id."""

    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    rock_id = Column(String, nullable=True, index=True, unique=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    birthday = Column(Date, nullable=True)
    anniversary = Column(Date, nullable=True)
    last_attendance = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    care_notes = relationship("CareNote", back_populates="member", cascade="all, delete-orphan")
    prayer_requests = relationship("PrayerRequest", back_populates="member", cascade="all, delete-orphan")
    notes = relationship("MemberNote", back_populates="member", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self):
        return f"<Member id={self.id} name={self.full_name!r}>"


class Visitor(Base):
    """A first-time (or repeat) visitor. Drives the follow-up sequence."""

    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    visit_date = Column(Date, nullable=False)
    source = Column(String, nullable=True)  # walk-in, web, referral, etc.

    # Follow-up sequence tracking
    follow_up_day1_sent = Column(Boolean, default=False)
    follow_up_day3_sent = Column(Boolean, default=False)
    follow_up_week2_sent = Column(Boolean, default=False)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self):
        return f"<Visitor id={self.id} name={self.full_name!r} visit={self.visit_date}>"


class CareNote(Base):
    """
    An active pastoral care case.

    category: hospital | crisis | grief | general
    status:   active | resolved
    """

    __tablename__ = "care_notes"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    category = Column(
        Enum(CareCategoryEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=CareCategoryEnum.general,
    )
    status = Column(
        Enum(CareStatusEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=CareStatusEnum.active,
    )
    description = Column(Text, nullable=True)
    last_contact = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="care_notes")

    def __repr__(self):
        return f"<CareNote id={self.id} member_id={self.member_id} category={self.category} status={self.status}>"


class PrayerRequest(Base):
    """
    A prayer request submitted by or on behalf of a member (or anonymous).

    status: active | answered | expired
    """

    __tablename__ = "prayer_requests"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=True)  # nullable for anonymous requests
    submitted_by = Column(String, nullable=True)  # name/label if member_id is None
    request_text = Column(Text, nullable=False)
    is_private = Column(Boolean, default=False)
    status = Column(
        Enum(PrayerStatusEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=PrayerStatusEnum.active,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="prayer_requests")

    def __repr__(self):
        return f"<PrayerRequest id={self.id} status={self.status} private={self.is_private}>"


class MemberNote(Base):
    """
    A pastoral CRM note attached to a member.

    context_tag: optional label such as 'hospital', 'counseling', 'conversation', 'prayer', etc.
    """

    __tablename__ = "member_notes"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    note_text = Column(Text, nullable=False)
    context_tag = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    member = relationship("Member", back_populates="notes")

    def __repr__(self):
        return f"<MemberNote id={self.id} member_id={self.member_id} tag={self.context_tag!r}>"


class ChatActionTrace(Base):
    """Audit trace for chat intent parsing and action execution."""

    __tablename__ = "chat_action_traces"

    id = Column(Integer, primary_key=True, index=True)
    input_text = Column(Text, nullable=False)
    inferred_intent = Column(String, nullable=False)
    inferred_actions = Column(Text, nullable=False)
    parser_confidence = Column(Float, nullable=False)
    executed_actions = Column(Text, nullable=False)
    outcome_status = Column(String, nullable=False)
    outcome_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ChatActionTrace id={self.id} status={self.outcome_status}>"
