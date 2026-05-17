"""
Shared visitor follow-up helpers.

Visitor creation can happen through the CRUD router, assistant chat, MCP, or a
future connector sync. Keep the first welcome draft behavior in one place so
every path preserves the same pastor-review boundary.
"""

import json
import os
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AccountPastorProfile, AssistantAction, AuditLog, ChurchAccount, Visitor
from app.services.accounts import account_id
from app.services.marge import draft_visitor_followup


def queue_visitor_welcome_action(
    db: Session,
    visitor: Visitor,
    account: Optional[ChurchAccount] = None,
) -> Optional[AssistantAction]:
    """Queue a reviewable visitor welcome draft without sending anything."""
    dedupe_key = f"visitor_welcome:{account_id(account) or 'legacy'}:{visitor.id}:v1"
    existing = db.query(AssistantAction).filter(AssistantAction.dedupe_key == dedupe_key).first()
    if existing:
        return existing

    pastor_name, church_name, draft_context = _pastor_church_and_draft_context_for_visitor(db, account)
    email_payload = {
        "subject": f"Thanks for visiting {church_name}",
        "body": draft_visitor_followup(
            visitor,
            day=1,
            pastor_name=pastor_name,
            church_name=church_name,
            communication_style=draft_context.get("drafting_voice"),
            faith_tradition=draft_context.get("faith_tradition"),
        ),
        "recipient_name": visitor.full_name,
    }
    if visitor.email:
        email_payload["to"] = visitor.email

    payload = {
        "draft_kind": "visitor_welcome",
        "visitor_id": visitor.id,
        "email": email_payload,
    }
    if draft_context:
        payload["draft_context"] = draft_context

    action = AssistantAction(
        account_id=account_id(account),
        dedupe_key=dedupe_key,
        action_type="email_draft",
        status="pending",
        title="Review Visitor welcome",
        description=f"{visitor.full_name}: {visitor.notes or 'New visitor follow-up'}",
        payload_json=json.dumps(payload),
        source="visitors",
        related_type="visitor",
        related_id=visitor.id,
        privacy_level="pastoral",
    )
    db.add(action)
    db.flush()
    db.add(AuditLog(
        account_id=account_id(account),
        event_type="assistant_action.created_from_visitor",
        actor="system",
        summary=f"Queued visitor welcome draft for {visitor.full_name}.",
        action_id=action.id,
        payload_json=json.dumps({"visitor_id": visitor.id, "has_email": bool(visitor.email)}),
    ))
    return action


def _pastor_church_and_draft_context_for_visitor(
    db: Session,
    account: Optional[ChurchAccount] = None,
) -> tuple[str, str, dict]:
    if account:
        profile = db.query(AccountPastorProfile).filter(AccountPastorProfile.account_id == account.id).first()
        pastor = (profile.pastor_name if profile else None) or account.pastor_name or "Pastor"
        church = (profile.church_name if profile else None) or account.church_name or "our church"
        return pastor, church, _draft_context_from_profile(profile)
    return os.getenv("PASTOR_NAME", "Pastor"), os.getenv("CHURCH_NAME", "our church"), {}


def _draft_context_from_profile(profile: Optional[AccountPastorProfile]) -> dict:
    if not profile:
        return {}
    context = {
        "drafting_voice": _clean(profile.communication_style),
        "faith_tradition": _clean(profile.faith_tradition),
        "guardrail": _clean(profile.guardrails),
    }
    return {key: value for key, value in context.items() if value}


def _clean(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    return cleaned or None
