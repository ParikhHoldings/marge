"""
Marge's voice — tone constants and message templates.

Marge is the beloved church secretary who's been at the church 30 years.
Warm, reliable, a little old-fashioned in the best possible way.
She never sounds like a chatbot, a database, or a corporate tool.

Read every notification aloud. If it sounds like it came from a database,
rewrite it. If it sounds like a caring colleague briefing the pastor before
a busy day, ship it.
"""

# ── Morning greeting ──────────────────────────────────────────────────────────

PASTOR_GREETING = "Good morning, {pastor_name}. Here is the ministry context I can see today."

BRIEFING_EMPTY_STATE = (
    "I do not see a real care, visitor, absence, birthday, anniversary, or prayer item needing follow-up in the current workspace yet. "
    "Add or sync ministry context when you are ready, and I will keep the next person from slipping."
)

# ── Visitor follow-up templates ───────────────────────────────────────────────
# These are starting drafts. The pastor reviews and sends.

FOLLOW_UP_DAY1_TEMPLATE = (
    "Hi {first_name}, just wanted to say how glad we were to have you with us Sunday! "
    "{pastor_name} was especially glad you came. "
    "No pressure at all — just wanted you to know you have a place here any time. "
    "— {church_name}"
)

FOLLOW_UP_DAY3_TEMPLATE = (
    "Hey {first_name}! This is {pastor_name} from {church_name}. "
    "We really enjoyed having you join us a few days ago. "
    "I'd love to grab coffee sometime if you're ever open to it — "
    "no agenda, just a chance to connect. "
    "Hope to see you again soon!"
)

FOLLOW_UP_WEEK2_TEMPLATE = (
    "Hi {first_name} — {pastor_name} here. "
    "We've got a {event_or_service} coming up that I think you'd enjoy. "
    "Would love to see you there. "
    "No obligation — just wanted to extend a personal invite. "
    "— {church_name}"
)

# ── Care nudge template ───────────────────────────────────────────────────────

CARE_NUDGE_TEMPLATE = (
    "You haven't connected with {first_name} {last_name} since {last_note}. "
    "It's been {days_since} days. A short text today would mean a lot."
)

# ── Care message templates ────────────────────────────────────────────────────

CARE_MESSAGE_HOSPITAL = (
    "Hey {first_name}, just wanted you to know I'm thinking about you and praying for you "
    "as you go through this. You're not alone in it. "
    "Let me know if there's anything you need or if you'd like a visit. "
    "— {pastor_name}"
)

CARE_MESSAGE_GRIEF = (
    "Hey {first_name}, I've been carrying you in my heart this week. "
    "Grief is hard, and there's no right way to move through it. "
    "I'm here whenever you need to talk, pray, or just sit with someone. "
    "— {pastor_name}"
)

CARE_MESSAGE_CRISIS = (
    "Hey {first_name}, I heard you're going through a really hard season right now. "
    "I want you to know the church is here for you, and so am I. "
    "Can we find a time to connect this week? "
    "— {pastor_name}"
)

CARE_MESSAGE_GENERAL = (
    "Hey {first_name}, just thinking about you today and wanted to check in. "
    "How are you doing? I'd love to hear. "
    "— {pastor_name}"
)

# ── Absence check-in template ─────────────────────────────────────────────────

ABSENCE_CHECKIN_TEMPLATE = (
    "Hey {first_name}! This is {pastor_name} — "
    "just noticed we haven't seen you in a little while and wanted to reach out. "
    "No worries if life's been busy — just wanted you to know we miss you "
    "and hope everything's going well. "
    "— {church_name}"
)

# ── Birthday / anniversary templates ─────────────────────────────────────────

BIRTHDAY_TEMPLATE = (
    "Happy birthday, {first_name}! "
    "Hope your day is full of good things. "
    "Thankful to do life with you. "
    "— {pastor_name}"
)

ANNIVERSARY_TEMPLATE = (
    "Happy anniversary, {first_name}! "
    "What a blessing to celebrate {years} years together. "
    "Praying for your marriage today. "
    "— {pastor_name}"
)

# ── Prayer follow-up template ─────────────────────────────────────────────────

PRAYER_FOLLOWUP_TEMPLATE = (
    "Hey {first_name}, just wanted to check in on your prayer request from {days_ago} days ago — "
    "you mentioned {short_summary}. "
    "Still praying for you. How are things going? "
    "— {pastor_name}"
)

# ── Post-action confirmations (in-app, Marge's voice) ────────────────────────

AFTER_SEND_CONFIRMATION = "Done. That kind of follow-through is what people remember."

MEMBER_RETURNED_AFTER_ABSENCE = (
    "Great news — {full_name} is back. "
    "Marge will note this and give it time before flagging them again."
)
