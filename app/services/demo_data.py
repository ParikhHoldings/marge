from datetime import date, datetime, timedelta


def build_demo_briefing(pastor_name: str, church_name: str) -> dict:
    today = date.today()
    now = datetime.utcnow().isoformat()

    birthdays = [
        {
            "id": 101,
            "full_name": "Tom Henderson",
            "birthday": date(today.year - 48, today.month, min(today.day + 2, 28)),
            "anniversary": None,
            "last_attendance": today - timedelta(days=22),
            "email": "tom.henderson@gmail.com",
            "phone": "817-555-0142",
        }
    ]
    anniversaries = [
        {
            "id": 102,
            "full_name": "Mike Chen",
            "birthday": None,
            "anniversary": date(2015, today.month, min(today.day + 1, 28)),
            "last_attendance": today - timedelta(days=4),
            "email": "mike.chen@gmail.com",
            "phone": "817-555-0202",
        }
    ]
    visitors = [
        {
            "id": 201,
            "full_name": "Sarah Kim",
            "visit_date": today - timedelta(days=6),
            "follow_up_day1_sent": False,
            "follow_up_day3_sent": False,
            "follow_up_week2_sent": False,
            "notes": "Brought by the Garcias. Just moved to Fort Worth and asked about small groups.",
        },
        {
            "id": 202,
            "full_name": "Raj Patel",
            "visit_date": today - timedelta(days=10),
            "follow_up_day1_sent": False,
            "follow_up_day3_sent": False,
            "follow_up_week2_sent": False,
            "notes": "Came with Priya and their two kids. Found the church on Google.",
        },
    ]
    care_cases = [
        {
            "id": 301,
            "member_id": 1,
            "member_name": "Maria Santos",
            "category": "grief",
            "status": "active",
            "description": "Her husband Eduardo passed away last month. Her daughter heads back to Austin next week, so the house is about to feel quiet again.",
            "last_contact": today - timedelta(days=10),
            "created_at": datetime.utcnow() - timedelta(days=28),
        },
        {
            "id": 302,
            "member_id": 2,
            "member_name": "David Park",
            "category": "hospital",
            "status": "active",
            "description": "Knee replacement is coming up. He is anxious and does not have much local help for recovery.",
            "last_contact": today - timedelta(days=8),
            "created_at": datetime.utcnow() - timedelta(days=9),
        },
    ]
    absent_members = [
        {
            "id": 401,
            "full_name": "Bob Kline",
            "birthday": None,
            "anniversary": None,
            "last_attendance": today - timedelta(days=35),
            "email": None,
            "phone": None,
        }
    ]
    prayers = [
        {
            "id": 501,
            "member_id": None,
            "submitted_by": "Tom Henderson",
            "request_text": "Please pray for work. I lost my job last month and I am trying not to spiral.",
            "is_private": True,
            "status": "active",
            "created_at": datetime.utcnow() - timedelta(days=20),
        }
    ]
    nudges = [
        'Tom Henderson lost his job in March and has also been absent for three weeks. That likely is not two separate stories.',
        'If you have ten minutes today, Maria Santos probably needs presence more than polished words.',
    ]
    ai_briefing = (
        f"Good morning, {pastor_name}. The biggest thread to notice today is Tom Henderson — "
        "his job loss and his recent absence probably belong to the same burden, so he would be worth a personal call, not just a quick text. "
        "Maria Santos still feels like the most tender care situation on your list; her daughter leaving soon may make the grief feel sharper again. "
        "David Park's surgery is close enough now that a simple check-in this weekend would probably steady him. "
        "Sarah Kim and the Patel family are your easiest wins — both visited recently, both left with enough warmth to return, and neither has had a real follow-up yet. "
        "If I were choosing your day for you, I would call Tom, text Maria, and send one welcome note before lunch."
    )

    plain_text = "\n".join([
        f"Good morning, Pastor {pastor_name}. Here are your people for today.",
        "",
        "🎂 BIRTHDAYS THIS WEEK",
        "• Tom Henderson — birthday in two days",
        "",
        "💍 ANNIVERSARIES THIS WEEK",
        "• Mike Chen — anniversary tomorrow",
        "",
        "👋 VISITOR FOLLOW-UP NEEDED",
        "• Sarah Kim — visited 6 days ago, no follow-up yet",
        "• Raj Patel — visited 10 days ago, no follow-up yet",
        "",
        "🏥 ACTIVE CARE CASES",
        "• Maria Santos [grief] — last contact 10 days ago",
        "• David Park [hospital] — last contact 8 days ago",
        "",
        "⏰ ABSENT MEMBERS",
        "• Bob Kline — 35 days since last attendance",
        "",
        "🙏 PRAYER REQUESTS NEEDING FOLLOW-UP",
        '• Tom Henderson — "Please pray for work..." (20 days, no update)',
        "",
        "💭 TODAY'S NUDGE",
        "• Tom Henderson's absence and job loss likely belong to the same story.",
    ])

    return {
        "greeting": f"Good morning, Pastor {pastor_name}. Here are your people for today.",
        "pastor_name": pastor_name,
        "church_name": church_name,
        "generated_at": now,
        "birthdays_this_week": birthdays,
        "anniversaries_this_week": anniversaries,
        "visitors_needing_followup": visitors,
        "active_care_cases": care_cases,
        "absent_members": absent_members,
        "unanswered_prayers": prayers,
        "nudges": nudges,
        "plain_text": plain_text,
        "ai_briefing": ai_briefing,
        "mode": "demo",
        "data_status": "demo_sample",
        "stats": {
            "members": 12,
            "visitors": 2,
            "care_cases": 2,
            "prayer_requests": 1,
        },
    }
