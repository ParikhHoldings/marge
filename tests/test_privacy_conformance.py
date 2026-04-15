from datetime import datetime, timedelta
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db
from app.main import app
from app.models import PrayerRequest


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, TestingSessionLocal


def _create_member(client, first_name="Alice", last_name="Member"):
    response = client.post(
        "/members/",
        json={"first_name": first_name, "last_name": last_name},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_prayer_read_write_update_conformance():
    client, _ = _client()
    member_id = _create_member(client)

    created = client.post(
        "/care/prayers/",
        headers={"x-marge-role": "pastor"},
        json={
            "member_id": member_id,
            "request_text": "Please pray for surgery recovery",
            "confidentiality_class": "sensitive",
            "is_private": True,
        },
    )
    assert created.status_code == 201
    prayer_id = created.json()["id"]
    assert created.json()["request_text"] == "Please pray for surgery recovery"

    public_read = client.get(f"/care/prayers/{prayer_id}", headers={"x-marge-role": "public"})
    assert public_read.status_code == 200
    assert public_read.json()["request_text"] == "[REDACTED]"

    updated = client.patch(
        f"/care/prayers/{prayer_id}",
        headers={"x-marge-role": "pastor"},
        json={"confidentiality_class": "public", "is_private": False},
    )
    assert updated.status_code == 200
    assert updated.json()["confidentiality_class"] == "public"

    bulletin = client.get("/care/prayers/bulletin")
    assert bulletin.status_code == 200
    assert bulletin.json()[0]["request_text"] == "Please pray for surgery recovery"


def test_care_and_notes_read_write_update_conformance():
    client, _ = _client()
    member_id = _create_member(client, first_name="Brian", last_name="Care")

    care_created = client.post(
        "/care/",
        headers={"x-marge-role": "pastor"},
        json={
            "member_id": member_id,
            "category": "hospital",
            "description": "ICU admission this morning",
            "confidentiality_class": "sensitive",
        },
    )
    assert care_created.status_code == 201
    care_id = care_created.json()["id"]

    staff_read = client.get(f"/care/{care_id}", headers={"x-marge-role": "staff"})
    assert staff_read.status_code == 200
    assert staff_read.json()["description"] == "[REDACTED]"

    care_updated = client.patch(
        f"/care/{care_id}",
        headers={"x-marge-role": "pastor"},
        json={"confidentiality_class": "private"},
    )
    assert care_updated.status_code == 200

    staff_read_after = client.get(f"/care/{care_id}", headers={"x-marge-role": "staff"})
    assert staff_read_after.status_code == 200
    assert staff_read_after.json()["description"] == "ICU admission this morning"

    note_created = client.post(
        f"/members/{member_id}/notes",
        headers={"x-marge-role": "pastor"},
        json={
            "note_text": "Counseling intake complete",
            "context_tag": "counseling",
            "confidentiality_class": "sensitive",
        },
    )
    assert note_created.status_code == 201

    public_notes = client.get(f"/members/{member_id}/notes", headers={"x-marge-role": "public"})
    assert public_notes.status_code == 200
    assert public_notes.json()[0]["note_text"] == "[REDACTED]"


def test_chat_actions_and_public_output_guards():
    client, TestingSessionLocal = _client()
    member_id = _create_member(client, first_name="Cara", last_name="Chat")

    chat_response = client.post(
        "/chat/",
        headers={"x-marge-role": "public"},
        json={
            "message": "Cara Chat is in the hospital ICU and needs prayer",
            "pastor_name": "Sam",
        },
    )
    assert chat_response.status_code == 200
    payload = chat_response.json()
    assert any(a.get("confidentiality_class") == "sensitive" for a in payload["actions"])
    sensitive_actions = [a for a in payload["actions"] if a.get("confidentiality_class") == "sensitive"]
    assert all(v in ("[REDACTED]", None) for a in sensitive_actions for k, v in a.items() if k in {"description", "request_text", "note_text"})

    # Make an overdue private prayer and verify public briefing/export guards exclude it.
    with TestingSessionLocal() as db:
        private_prayer = db.query(PrayerRequest).filter(PrayerRequest.member_id == member_id).first()
        private_prayer.created_at = datetime.utcnow() - timedelta(days=30)
        db.commit()

    public_briefing = client.get("/briefing/today?audience=public", headers={"x-marge-role": "public"})
    assert public_briefing.status_code == 200
    assert public_briefing.json()["active_care_cases"] == []
    assert public_briefing.json()["unanswered_prayers"] == []

    public_export = client.get("/care/prayers/export?audience=public", headers={"x-marge-role": "public"})
    assert public_export.status_code == 200
    assert public_export.json() == []
