from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CareNote, Member, MemberNote, PrayerRequest, Visitor

SEED_PATH = Path(__file__).parent / "fixtures" / "pastoral_workflow_seed.json"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seed_dataset(db_session):
    today = date.today()
    now = datetime.utcnow()
    seed = json.loads(SEED_PATH.read_text())

    members: dict[str, Member] = {}
    for item in seed["members"]:
        member = Member(
            first_name=item["first_name"],
            last_name=item["last_name"],
            email=item.get("email"),
            birthday=today + timedelta(days=item["birthday_in_days"]) if "birthday_in_days" in item else None,
            anniversary=today + timedelta(days=item["anniversary_in_days"]) if "anniversary_in_days" in item else None,
            last_attendance=today - timedelta(days=item["last_attendance_days_ago"]) if "last_attendance_days_ago" in item else None,
        )
        db_session.add(member)
        db_session.flush()
        members[item["key"]] = member

    visitors: dict[str, Visitor] = {}
    for item in seed["visitors"]:
        visitor = Visitor(
            first_name=item["first_name"],
            last_name=item["last_name"],
            email=item.get("email"),
            visit_date=today - timedelta(days=item["visit_days_ago"]),
            follow_up_day1_sent=False,
            follow_up_day3_sent=False,
            follow_up_week2_sent=False,
            source=item.get("source"),
        )
        db_session.add(visitor)
        db_session.flush()
        visitors[item["key"]] = visitor

    care_case = CareNote(
        member_id=members["member_hospital"].id,
        category="hospital",
        status="active",
        description="Recovering after surgery",
        last_contact=today - timedelta(days=10),
    )
    prayer = PrayerRequest(
        member_id=members["member_hospital"].id,
        submitted_by=members["member_hospital"].full_name,
        request_text="Please pray for post-op recovery.",
        is_private=True,
        status="active",
        created_at=now - timedelta(days=20),
    )
    old_note = MemberNote(
        member_id=members["member_nudge"].id,
        note_text="Job uncertainty and anxiety this month.",
        context_tag="job",
        created_at=now - timedelta(days=30),
    )

    db_session.add_all([care_case, prayer, old_note])
    db_session.commit()

    return {
        "member_bday_id": members["member_bday"].id,
        "member_hospital_id": members["member_hospital"].id,
        "member_nudge_id": members["member_nudge"].id,
        "visitor_followup_id": visitors["visitor_followup"].id,
        "visitor_recent_id": visitors["visitor_recent"].id,
        "care_case_id": care_case.id,
        "prayer_id": prayer.id,
    }
