"""
Helpers for first-run setup actions.

These helpers keep setup-card lifecycle behavior shared across direct API,
assistant chat, and MCP-driven workflows.
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AssistantAction, AuditLog, ChurchAccount
from app.services.accounts import account_id


def retire_data_seed_actions(
    db: Session,
    account: Optional[ChurchAccount],
    *,
    reason: str,
    related_type: Optional[str] = None,
    related_id: Optional[int] = None,
) -> int:
    """
    Mark pending first-real-record setup prompts complete.

    The data_seed action asks the pastor to add the first real ministry record.
    Once that record exists, the setup prompt should leave the pending queue
    without affecting reviewable drafts created from the new context.
    """
    query = db.query(AssistantAction).filter(
        AssistantAction.account_id == account_id(account),
        AssistantAction.action_type == "data_seed",
        AssistantAction.status.in_(["pending", "approved"]),
    )
    now = datetime.utcnow()
    retired = 0
    for action in query.all():
        payload = _json_loads(action.payload_json)
        payload["completed_by"] = {
            "reason": reason,
            "related_type": related_type,
            "related_id": related_id,
            "completed_at": now.isoformat(),
        }
        action.payload_json = _json_dumps(payload)
        action.status = "executed"
        action.executed_at = now
        action.updated_at = now
        db.add(AuditLog(
            account_id=account_id(account),
            event_type="assistant_action.retired",
            actor="system",
            summary="Completed first-context setup prompt after real ministry context was saved.",
            action_id=action.id,
            payload_json=_json_dumps({
                "reason": reason,
                "related_type": related_type,
                "related_id": related_id,
            }),
        ))
        retired += 1
    return retired


def _json_dumps(payload: Optional[dict]) -> str:
    return json.dumps(payload or {}, default=str, separators=(",", ":"), sort_keys=True)


def _json_loads(payload_json: Optional[str]) -> dict:
    if not payload_json:
        return {}
    try:
        loaded = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
