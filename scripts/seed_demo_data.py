#!/usr/bin/env python3
"""
Seed Marge with realistic demo data that tells a real story.

Run this when the DB is empty or with --force to reset.
"""

import sys
import os
from datetime import date, datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine, Base
from app.models import Member, Visitor, CareNote, PrayerRequest, MemberNote

def check_if_empty(db):
    return db.query(Member).count() == 0 and db.query(Visitor).count() == 0

def seed(force=False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        if not force and not check_if_empty(db):
            print("DB already has data. Use --force to reseed.")
            return

        if force:
            print("Clearing existing data...")
            db.query(MemberNote).delete()
            db.query(PrayerRequest).delete()
            db.query(CareNote).delete()
            db.query(Visitor).delete()
            db.query(Member).delete()
            db.commit()

        today = date.today()
        print("Seeding members...")

        # ── Members ──────────────────────────────────────────────────────────

        # Tom Henderson — lost his job in March, stopped attending 3 weeks ago
        tom = Member(
            first_name="Tom",
            last_name="Henderson",
            email="tom.henderson@gmail.com",
            phone="817-555-0142",
            birthday=date(1978, 11, 14),
            last_attendance=today - timedelta(days=22),
        )
        db.add(tom)

        # Maria Santos — grief case, husband passed away last month
        maria = Member(
            first_name="Maria",
            last_name="Santos",
            email="maria.santos@yahoo.com",
            phone="817-555-0198",
            birthday=date(1965, 3, 22),
            anniversary=date(1989, 6, 10),
            last_attendance=today - timedelta(days=8),
        )
        db.add(maria)

        # David Park — surgery coming up, anxious
        david = Member(
            first_name="David",
            last_name="Park",
            email="dpark@outlook.com",
            phone="817-555-0231",
            birthday=date(1955, 7, 30),
            last_attendance=today - timedelta(days=5),
        )
        db.add(david)

        # The Garcia family — regular attenders, referred Sarah Kim
        carlos = Member(
            first_name="Carlos",
            last_name="Garcia",
            email="carlos.garcia@gmail.com",
            phone="817-555-0177",
            birthday=date(1982, 4, 8),
            anniversary=today - timedelta(days=365 * 12),  # ~12 years married
            last_attendance=today - timedelta(days=2),
        )
        db.add(carlos)

        linda_garcia = Member(
            first_name="Linda",
            last_name="Garcia",
            email="linda.garcia@gmail.com",
            phone="817-555-0178",
            birthday=date(1984, today.month, today.day + 2 if today.day <= 28 else 1),
            # Birthday in 2 days — want it to show up in briefing
            anniversary=today - timedelta(days=365 * 12),
            last_attendance=today - timedelta(days=2),
        )
        db.add(linda_garcia)

        # James & Ruth Morrison — older couple, pillars of the church
        james = Member(
            first_name="James",
            last_name="Morrison",
            email="jmorrison@att.net",
            phone="817-555-0103",
            birthday=date(1948, 9, 5),
            anniversary=date(1971, 8, 15),
            last_attendance=today - timedelta(days=3),
        )
        db.add(james)

        ruth = Member(
            first_name="Ruth",
            last_name="Morrison",
            email="jmorrison@att.net",
            phone="817-555-0103",
            birthday=date(1950, 2, 28),
            anniversary=date(1971, 8, 15),
            last_attendance=today - timedelta(days=3),
        )
        db.add(ruth)

        # Marcus Williams — young adult, financially struggling
        marcus = Member(
            first_name="Marcus",
            last_name="Williams",
            email="marcuswill@gmail.com",
            phone="817-555-0356",
            birthday=date(1998, 12, 3),
            last_attendance=today - timedelta(days=18),
        )
        db.add(marcus)

        # Jennifer Lee — new member, joined 2 months ago
        jennifer = Member(
            first_name="Jennifer",
            last_name="Lee",
            email="jlee.fw@gmail.com",
            phone="817-555-0289",
            birthday=date(1991, today.month, today.day + 5 if today.day <= 24 else 1),
            last_attendance=today - timedelta(days=6),
        )
        db.add(jennifer)

        # Bob Kline — absent for 5 weeks, no contact
        bob = Member(
            first_name="Bob",
            last_name="Kline",
            email="bobkline@hotmail.com",
            phone="817-555-0422",
            birthday=date(1960, 5, 18),
            last_attendance=today - timedelta(days=35),
        )
        db.add(bob)

        # Diana Torres — faithful volunteer, easy to overlook
        diana = Member(
            first_name="Diana",
            last_name="Torres",
            email="dianatorres@gmail.com",
            phone="817-555-0509",
            birthday=date(1975, 8, 27),
            last_attendance=today - timedelta(days=7),
        )
        db.add(diana)

        # Kevin & Amy Chen — solid couple, Kevin recently laid off
        kevin = Member(
            first_name="Kevin",
            last_name="Chen",
            email="kevinchen@gmail.com",
            phone="817-555-0611",
            birthday=date(1987, 10, 12),
            anniversary=date(2015, 5, 23),
            last_attendance=today - timedelta(days=14),
        )
        db.add(kevin)

        amy = Member(
            first_name="Amy",
            last_name="Chen",
            email="amychen@gmail.com",
            phone="817-555-0612",
            birthday=date(1989, 1, 7),
            anniversary=date(2015, 5, 23),
            last_attendance=today - timedelta(days=14),
        )
        db.add(amy)

        # Pastor Nathan (for reference completeness — not surfaced in briefing)
        # (skip — he's the one reading it)

        db.commit()
        print(f"  ✓ Added 12 members")

        # ── Visitors ─────────────────────────────────────────────────────────

        # Sarah Kim — visited 6 days ago, referred by Garcias, interested in small groups
        sarah_kim = Visitor(
            first_name="Sarah",
            last_name="Kim",
            email="sarahkim.tx@gmail.com",
            phone="817-555-0734",
            visit_date=today - timedelta(days=6),
            source="referral",
            follow_up_day1_sent=False,
            follow_up_day3_sent=False,
            follow_up_week2_sent=False,
            notes="Brought by the Garcias. Seemed genuinely interested in small groups. Mentioned she just moved to Fort Worth from Dallas.",
        )
        db.add(sarah_kim)

        # Michael Reyes — visited 4 days ago, walk-in
        michael_reyes = Visitor(
            first_name="Michael",
            last_name="Reyes",
            email="mreyes82@gmail.com",
            phone="817-555-0881",
            visit_date=today - timedelta(days=4),
            source="walk-in",
            follow_up_day1_sent=False,
            follow_up_day3_sent=False,
            follow_up_week2_sent=False,
            notes="Came alone. Quiet, sat in the back. Shook hands after service and asked about service times.",
        )
        db.add(michael_reyes)

        # The Patel family — visited 10 days ago, no follow-up (slipped through)
        patel_family = Visitor(
            first_name="Raj",
            last_name="Patel",
            email="rajpatel@gmail.com",
            phone="817-555-0952",
            visit_date=today - timedelta(days=10),
            source="web",
            follow_up_day1_sent=False,
            follow_up_day3_sent=False,
            follow_up_week2_sent=False,
            notes="Family of 4 (2 kids). Found us on Google. Wife's name is Priya. Kids seemed to enjoy children's ministry.",
        )
        db.add(patel_family)

        db.commit()
        print(f"  ✓ Added 3 visitors")

        # ── Care Notes ────────────────────────────────────────────────────────

        # Maria Santos — grief, husband passed last month, last contact was 10 days ago
        maria_care = CareNote(
            member_id=maria.id,
            category="grief",
            status="active",
            description="Husband Eduardo passed away March 2nd after a long illness. Maria is home most days. Daughter living with her for now but leaves next week. She's been coming to church but looks exhausted and isolated.",
            last_contact=today - timedelta(days=10),
        )
        db.add(maria_care)

        # David Park — hospital/surgery, anxious about recovery
        david_care = CareNote(
            member_id=david.id,
            category="hospital",
            status="active",
            description="Knee replacement surgery scheduled for April 15th. Has been anxious — mentions his wife had complications with a similar procedure. No family in the area to help post-surgery.",
            last_contact=today - timedelta(days=5),
        )
        db.add(david_care)

        # Marcus Williams — financial/crisis
        marcus_care = CareNote(
            member_id=marcus.id,
            category="crisis",
            status="active",
            description="Behind on rent for 2 months. Mentioned considering moving back in with parents in Houston. No family here. Seems ashamed to ask for help.",
            last_contact=today - timedelta(days=12),
        )
        db.add(marcus_care)

        db.commit()
        print(f"  ✓ Added 3 care cases")

        # ── Prayer Requests ───────────────────────────────────────────────────

        # David Park — surgery prayer
        pr1 = PrayerRequest(
            member_id=david.id,
            request_text="Surgery scheduled for April 15th. Anxious about recovery and being alone in recovery. Please pray for peace and quick healing.",
            is_private=False,
            status="active",
            created_at=datetime.utcnow() - timedelta(days=18),
        )
        db.add(pr1)

        # Maria Santos — grief prayer
        pr2 = PrayerRequest(
            member_id=maria.id,
            request_text="For strength and peace as I navigate life without Eduardo. Some days the silence in the house is overwhelming.",
            is_private=True,
            status="active",
            created_at=datetime.utcnow() - timedelta(days=28),
        )
        db.add(pr2)

        # Tom Henderson — job prayer (anonymous to protect dignity)
        pr3 = PrayerRequest(
            member_id=tom.id,
            request_text="For employment — lost my job in March and worried about my family's finances. Not sure how long we can stay in our house.",
            is_private=True,
            status="active",
            created_at=datetime.utcnow() - timedelta(days=20),
        )
        db.add(pr3)

        # Marcus Williams
        pr4 = PrayerRequest(
            member_id=marcus.id,
            request_text="Guidance on next steps. Feeling stuck and don't want to move back to Houston but may have to.",
            is_private=True,
            status="active",
            created_at=datetime.utcnow() - timedelta(days=15),
        )
        db.add(pr4)

        # Bob Kline — health scare
        pr5 = PrayerRequest(
            member_id=bob.id,
            request_text="Waiting on test results from doctor. Trying to trust God but struggling with anxiety.",
            is_private=False,
            status="active",
            created_at=datetime.utcnow() - timedelta(days=32),
        )
        db.add(pr5)

        db.commit()
        print(f"  ✓ Added 5 prayer requests")

        # ── Member Notes ──────────────────────────────────────────────────────

        # Tom Henderson — context about job loss and attendance drop
        mn1 = MemberNote(
            member_id=tom.id,
            note_text="Lost his job at FedEx in March — logistics coordinator, 8 years there. Worried about making rent. Wife Linda is working extra shifts at the hospital. He sounded embarrassed when it came up. Last spoke March 12 after service.",
            context_tag="job",
            created_at=datetime.utcnow() - timedelta(days=22),
        )
        db.add(mn1)

        # Maria Santos — context about husband's death and isolation
        mn2 = MemberNote(
            member_id=maria.id,
            note_text="Eduardo died March 2nd. We did the funeral March 7th — great turnout. Her daughter Sofia flew in from Austin and is staying through April. Maria told me she doesn't sleep well and finds the quiet mornings the hardest.",
            context_tag="grief",
            created_at=datetime.utcnow() - timedelta(days=25),
        )
        db.add(mn2)

        # Marcus Williams — financial context
        mn3 = MemberNote(
            member_id=marcus.id,
            note_text="Working at Amazon warehouse but hours got cut. Behind on rent 2 months. Doesn't know I know — he mentioned it casually in passing. Didn't ask for help directly — too proud. Worth a proactive reach.",
            context_tag="financial",
            created_at=datetime.utcnow() - timedelta(days=16),
        )
        db.add(mn3)

        # Kevin Chen — job note
        mn4 = MemberNote(
            member_id=kevin.id,
            note_text="Laid off from his software job at a startup last month. Amy still working. He seems fine but knows it won't be sustainable. Has been quiet in small group lately.",
            context_tag="job",
            created_at=datetime.utcnow() - timedelta(days=19),
        )
        db.add(mn4)

        # Bob Kline — health context + why he's absent
        mn5 = MemberNote(
            member_id=bob.id,
            note_text="Waiting on test results — possibly prostate cancer but not confirmed. He mentioned it quickly then changed the subject. His wife passed 2 years ago so he's going through this alone. We haven't reached out since March.",
            context_tag="health",
            created_at=datetime.utcnow() - timedelta(days=30),
        )
        db.add(mn5)

        db.commit()
        print(f"  ✓ Added 5 member notes")

        print("\n✅ Demo data seeded successfully!")
        print("\nKey story threads for the AI briefing:")
        print("  • Tom Henderson: Job loss (March) → stopped attending (3 weeks ago). Dots to connect.")
        print("  • Maria Santos: Grief case, husband died last month, last contact 10 days ago.")
        print("  • David Park: Surgery April 15, anxious, no family nearby.")
        print("  • Sarah Kim: Visitor 6 days ago, no follow-up, came via Garcias.")
        print("  • Bob Kline: Absent 35 days, possible cancer scare, going through it alone.")
        print("  • Marcus Williams: Financial crisis, too proud to ask for help directly.")

    finally:
        db.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    seed(force=force)
