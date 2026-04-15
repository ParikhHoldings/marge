from datetime import date

from app.models import Member, Visitor
from app.services import marge


def test_generate_morning_briefing_surfaces_seeded_needs(db_session, seed_dataset):
    briefing = marge.generate_morning_briefing(db_session, pastor_name="Pastor Lee", church_name="Grace Church")

    assert briefing["pastor_name"] == "Pastor Lee"
    assert briefing["church_name"] == "Grace Church"
    assert any(m.id == seed_dataset["member_bday_id"] for m in briefing["birthdays_this_week"])
    assert any(m.id == seed_dataset["member_bday_id"] for m in briefing["anniversaries_this_week"])
    assert any(v.id == seed_dataset["visitor_followup_id"] for v in briefing["visitors_needing_followup"])
    assert any(c.id == seed_dataset["care_case_id"] for c in briefing["active_care_cases"])
    assert any(m.id == seed_dataset["member_bday_id"] for m in briefing["absent_members"])
    assert any(p.id == seed_dataset["prayer_id"] for p in briefing["unanswered_prayers"])
    assert briefing["nudges"]


def test_draft_helpers_return_expected_voice_shapes(db_session):
    member = Member(first_name="Ruth", last_name="Stone", anniversary=date(2010, 6, 15))
    visitor = Visitor(first_name="Sam", last_name="Guest", visit_date=date.today())

    assert "Ruth" in marge.draft_care_message(member, "hospital", pastor_name="Ana")
    assert "Ruth" in marge.draft_absence_checkin(member, pastor_name="Ana", church_name="New Hope")
    assert "Ruth" in marge.draft_birthday_message(member, pastor_name="Ana")
    assert "Ruth" in marge.draft_anniversary_message(member, pastor_name="Ana")

    day1 = marge.draft_visitor_followup(visitor, day=1, pastor_name="Ana", church_name="New Hope")
    fallback = marge.draft_visitor_followup(visitor, day=99, pastor_name="Ana", church_name="New Hope")

    assert "Sam" in day1
    assert "Pastor Ana" in fallback


def test_render_briefing_text_formats_key_sections(db_session, seed_dataset):
    briefing = marge.generate_morning_briefing(db_session, pastor_name="Pastor Lee", church_name="Grace Church")
    text = marge.render_briefing_text(briefing)

    assert "BIRTHDAYS THIS WEEK" in text
    assert "VISITOR FOLLOW-UP NEEDED" in text
    assert "ACTIVE CARE CASES" in text
    assert "PRAYER REQUESTS NEEDING FOLLOW-UP" in text
