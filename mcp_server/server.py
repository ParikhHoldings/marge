"""
Marge MCP Server

Exposes Marge's pastoral care tools to Claude Desktop, ChatGPT, and any
other MCP-compatible AI client.

Usage:
    python mcp_server/server.py

Environment variables:
    MARGE_API_URL         Base URL of your Marge instance (default: http://localhost:8000)
    MARGE_ACCOUNT_TOKEN   Optional church workspace token from /assistant/signup

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "marge": {
          "command": "python",
          "args": ["/path/to/marge/mcp_server/server.py"],
          "env": {
            "MARGE_API_URL": "https://your-marge-instance.railway.app",
            "MARGE_ACCOUNT_TOKEN": "marge_..."
          }
        }
      }
    }
"""

import os
import json
import re
import httpx
from datetime import date

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── Config ────────────────────────────────────────────────────────────────────

MARGE_API_URL = os.getenv("MARGE_API_URL", "http://localhost:8000").rstrip("/")
MARGE_ACCOUNT_TOKEN = os.getenv("MARGE_ACCOUNT_TOKEN", "").strip()

# ── Server ────────────────────────────────────────────────────────────────────

server = Server(
    name="marge",
    version="0.1.0",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> dict:
    """Synchronous GET to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.get(f"{MARGE_API_URL}{path}", params=params, headers=_headers())
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict, params: dict = None) -> dict:
    """Synchronous POST to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.post(f"{MARGE_API_URL}{path}", params=params, json=body, headers=_headers())
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> dict:
    """Synchronous DELETE to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.delete(f"{MARGE_API_URL}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


def _patch(path: str, body: dict) -> dict:
    """Synchronous PATCH to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.patch(f"{MARGE_API_URL}{path}", json=body, headers=_headers())
        r.raise_for_status()
        return r.json()


def _headers() -> dict:
    headers = {"Accept": "application/json"}
    if MARGE_ACCOUNT_TOKEN:
        headers["X-Marge-Account-Token"] = MARGE_ACCOUNT_TOKEN
    return headers


def _find_member(name: str) -> dict | None:
    """Find a safe member match by exact name or single search result."""
    results = _get("/members/", params={"q": name})
    if not isinstance(results, list) or not results:
        return None
    requested = _normalize_person_name(name)
    exact = [
        member
        for member in results
        if _normalize_person_name(member.get("full_name")) == requested
        or _normalize_person_name(" ".join(filter(None, [member.get("first_name"), member.get("last_name")]))) == requested
    ]
    if len(exact) == 1:
        return exact[0]
    if len(results) == 1:
        return results[0]
    return None


def _normalize_person_name(value) -> str:
    return " ".join(str(value or "").lower().split())


def _normalize_care_category(category: str) -> str:
    """Map agent-friendly labels to the backend's current care enum."""
    value = (category or "general").lower().strip()
    if value in {"hospital", "crisis", "grief", "general"}:
        return value
    if value in {"counseling", "other", "financial", "health", "family", "followup"}:
        return "general"
    return "general"


def _care_case_name(case: dict) -> str:
    return _redact_secret_text(case.get("member_name") or "Name not linked")


def _prayer_request_name(prayer: dict) -> str:
    return _redact_secret_text(prayer.get("member_name") or prayer.get("submitted_by") or "Name withheld")


def _member_not_found_message(name: str, next_step: str) -> str:
    safe_name = _redact_secret_text(name)
    return (
        f"No saved person matched '{safe_name}'. {next_step} "
        "If this is a real person, first add them through tell_marge with enough pastoral context, "
        f"for example: Help me add {safe_name} as a person."
    )


def _member_display_name(member: dict) -> str:
    return _redact_secret_text(_text_or(
        member.get("full_name")
        or " ".join(str(part).strip() for part in [member.get("first_name"), member.get("last_name")] if part).strip(),
        "Member name not linked",
    ))


def _text_or(value, fallback: str) -> str:
    if value is None:
        return fallback
    rendered = str(value).strip()
    return rendered or fallback


def _format_desk_item(item: dict) -> str:
    title = _redact_secret_text(_text_or(item.get("title"), "Review item title not included"))
    detail = _redact_secret_text(item.get("detail") or item.get("subtitle") or "")
    action = _redact_secret_text(item.get("action") or "Review")
    priority = _redact_secret_text(item.get("priority") or "medium")
    return f"  • {title} [{priority}] — {action}" + (f": {detail}" if detail else "")


def _format_action(action: dict) -> str:
    status = _redact_secret_text(action.get("status", "pending"))
    action_type = _redact_secret_text(action.get("action_type", "assistant_action"))
    privacy = _redact_secret_text(action.get("privacy_level", "pastoral"))
    title = _redact_secret_text(_text_or(action.get("title"), "Assistant action title not included"))
    description = _redact_secret_text(action.get("description") or "")
    line = f"  • #{action.get('id')} {title} [{status}, {action_type}, {privacy}]"
    return f"{line} — {description}" if description else line


def _format_integration(item: dict) -> str:
    display = _redact_secret_text(item.get("display_name") or item.get("provider"))
    provider = _redact_secret_text(item.get("provider") or "")
    status = _redact_secret_text((item.get("status") or "planned").replace("_", " "))
    if provider == "mcp":
        hint = _redact_secret_text(item.get("config_hint") or item.get("secure_note") or "")
        boundary = (
            "local agent bridge, not a church-tool provider. "
            "It lets LLM clients call Marge; it does not prove Google Workspace, "
            "Planning Center, Microsoft 365, Breeze, or Rock RMS are connected"
        )
        return f"  • {display}: {status}, {boundary}" + (f" — {hint}" if hint else "")
    write = "write enabled" if item.get("write_enabled") else "read/review only"
    verified = _redact_secret_text(item.get("verified_at"))
    ready_statuses = {"connected", "configured", "available"}
    credential_state = ""
    if verified:
        credential_state = f", checked {verified}"
    elif item.get("status") in ready_statuses:
        credential_state = ", needs credential check before sync"
    hint = _redact_secret_text(item.get("config_hint") or item.get("secure_note") or "")
    return f"  • {display}: {status}, {write}{credential_state}" + (f" — {hint}" if hint else "")


def _format_chat_message(message: dict) -> str:
    role = "Pastor" if message.get("role") == "user" else "Marge"
    content = _redact_secret_text((message.get("content") or "").strip())
    intent = _redact_secret_text(message.get("intent") or "chat")
    return f"  • {role} [{intent}]: {content}"


def _format_tell_marge_response(result: dict) -> str:
    lines = [_redact_secret_text(result.get("reply") or "Got it.")]
    metadata = []
    if result.get("intent"):
        metadata.append(f"intent={_redact_secret_text(result['intent'])}")
    if "saved" in result:
        metadata.append(f"saved={bool(result.get('saved'))}")
    if result.get("mode"):
        metadata.append(f"mode={_redact_secret_text(result['mode'])}")
    if metadata:
        lines.extend(["", "Response metadata: " + ", ".join(metadata)])

    profile = result.get("profile") or {}
    if profile:
        missing = [_redact_secret_text(field) for field in profile.get("missing_fields") or []]
        lines.append(
            f"Profile: {profile.get('completion_percent', 0)}% complete"
            + (f"; missing {', '.join(missing)}" if missing else "; complete")
        )

    actions = result.get("actions") or []
    if actions:
        lines.extend(["", "Returned action cards:"])
        lines.extend(_format_desk_item(action) for action in actions[:8])

    prompts = result.get("suggested_prompts") or []
    if prompts:
        lines.extend(["", "Suggested prompts:"])
        lines.extend(f"  • {_redact_secret_text(prompt)}" for prompt in prompts[:6])
    return "\n".join(lines)


def _compact_payload(payload: dict, limit: int = 800) -> str:
    rendered = _redact_secret_text(json.dumps(payload, indent=2, default=str))
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 3].rstrip() + "..."


def _provider_display_name(provider: str) -> str:
    names = {
        "google_workspace": "Google Workspace",
        "microsoft_365": "Microsoft 365",
        "planning_center": "Planning Center",
        "breeze": "Breeze",
        "rock": "Rock RMS",
        "mcp": "MCP",
    }
    return _redact_secret_text(names.get(provider, provider.replace("_", " ").title()))


def _http_error_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        payload = exc.response.json()
    except ValueError:
        return _redact_secret_text(exc.response.text or str(exc))
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return _redact_secret_text(detail)
        if detail is not None:
            return _redact_secret_text(json.dumps(detail, default=str))
    return _redact_secret_text(json.dumps(payload, default=str))


SECRET_TEXT_REDACTIONS = [
    (
        re.compile(
            r"\b(access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|token[_-]?ciphertext)\s*[:=]\s*[^\s,;]+",
            re.IGNORECASE,
        ),
        r"\1=<redacted>",
    ),
    (re.compile(r"\b(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"\bmarge_sess_[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-marge-session>"),
    (re.compile(r"\bya29\.[A-Za-z0-9._~+\-/=]{8,}"), "<redacted-google-token>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-jwt>",
    ),
]


def _redact_secret_text(value) -> str:
    text = str(value or "")
    for pattern, replacement in SECRET_TEXT_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _sync_needs_credential_check(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 409:
        return False
    detail = _http_error_detail(exc).lower()
    return "check credentials" in detail and "before syncing" in detail


def _format_sync_precheck_verification(provider: str, result: dict) -> str:
    display = _provider_display_name(result.get("provider") or provider)
    identity = result.get("identity") or {}
    identity_bits = _format_identity_bits(identity)
    lines = [
        f"{display} credentials verified at {_redact_secret_text(result.get('verified_at'))} without syncing ministry data.",
        "I did not import people, email, calendar, or attendance context, and I did not queue actions.",
        "Ask to sync this connector again when you want Marge to import fresh ministry context.",
    ]
    if identity_bits:
        lines.append("Non-secret identity check: " + ", ".join(identity_bits))
    return "\n".join(lines)


def _format_identity_bits(identity: dict) -> list[str]:
    bits = []
    labels = {
        "email": "email",
        "display_name": "name",
        "name": "name",
        "id": "id",
        "people_access_confirmed": "people access",
    }
    for key, label in labels.items():
        if key not in identity or identity.get(key) is None:
            continue
        value = identity[key]
        if isinstance(value, bool):
            rendered = "confirmed" if value else "not confirmed"
        else:
            rendered = _redact_secret_text(value)
        bits.append(f"{label}: {rendered}")
    return bits


# ── Tool Definitions ──────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_morning_briefing",
            description=(
                "Get Marge's morning briefing — a summary of who needs pastoral care today. "
                "Includes birthdays, anniversaries, visitors needing follow-up, active care cases, "
                "absent members, and prayer requests. Use this to start the day with a clear picture."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="list_members",
            description="Search for members in the congregation by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Name or partial name to search for. Leave empty to list all members.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="log_visitor",
            description=(
                "Record a first-time visitor to the church. "
                "Marge will start a follow-up sequence automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "email": {
                        "type": "string",
                        "description": "Optional email address. Include it when available so Marge can queue a reviewable welcome email draft.",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Optional phone number for pastoral follow-up.",
                    },
                    "visit_date": {
                        "type": "string",
                        "description": "Date of visit in YYYY-MM-DD format. Defaults to today if not provided.",
                    },
                    "source": {
                        "type": "string",
                        "description": "How they came in, such as walk-in, web, referral, or Sunday card.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any context about the visitor — family, background, how they heard about the church.",
                    },
                },
                "required": ["first_name", "last_name"],
            },
        ),
        types.Tool(
            name="log_care_event",
            description=(
                "Open a care case for a congregation member. "
                "Use for hospital visits, crisis situations, or any ongoing pastoral care need."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "member_name": {
                        "type": "string",
                        "description": "Full name or partial name of the member.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Type of care: hospital, crisis, grief, counseling, general, or other.",
                        "enum": ["hospital", "crisis", "grief", "counseling", "general", "other"],
                    },
                    "description": {
                        "type": "string",
                        "description": "Details about the care need.",
                    },
                },
                "required": ["member_name", "category", "description"],
            },
        ),
        types.Tool(
            name="list_care_cases",
            description="List active or resolved pastoral care cases.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by active or resolved.",
                        "enum": ["active", "resolved"],
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="mark_contacted",
            description="Log that you made contact with someone on a care case. Updates last_contact date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "care_id": {
                        "type": "integer",
                        "description": "ID of the care case (from get_morning_briefing or log_care_event).",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional note about the contact (e.g. 'Called, she is doing better').",
                    },
                },
                "required": ["care_id"],
            },
        ),
        types.Tool(
            name="add_prayer_request",
            description="Add a prayer request for a saved member or an unlinked named/private submitter.",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_text": {
                        "type": "string",
                        "description": "The prayer request details.",
                    },
                    "member_name": {
                        "type": "string",
                        "description": "Name of the member this is for (optional — search will be used to find them).",
                    },
                    "submitted_by": {
                        "type": "string",
                        "description": "Name to preserve when the request is not linked to a saved member.",
                    },
                    "is_private": {
                        "type": "boolean",
                        "description": "If true, this request will not appear in the public prayer bulletin.",
                        "default": False,
                    },
                },
                "required": ["request_text"],
            },
        ),
        types.Tool(
            name="list_prayer_requests",
            description="List prayer requests, excluding private requests unless include_private is true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by active, answered, or expired.",
                        "enum": ["active", "answered", "expired"],
                    },
                    "include_private": {
                        "type": "boolean",
                        "description": "Include private requests if the pastor is allowed to see them.",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="add_member_note",
            description=(
                "Add a pastoral note to a member's record. "
                "Use after a visit, conversation, or any interaction worth remembering. "
                "Marge will surface this in future briefings and nudges."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "member_name": {
                        "type": "string",
                        "description": "Name of the member.",
                    },
                    "note_text": {
                        "type": "string",
                        "description": "The note to log (e.g. 'Visited at home. Mentioned job stress. Wife seems worried too.').",
                    },
                    "context_tag": {
                        "type": "string",
                        "description": "Optional tag for the note topic: job, health, family, grief, faith, financial, marriage, general.",
                    },
                },
                "required": ["member_name", "note_text"],
            },
        ),
        types.Tool(
            name="draft_message",
            description=(
                "Draft a pastoral outreach message for a member in the pastor's voice. "
                "Use for birthday texts, care follow-ups, visitor welcome messages, and more."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "member_name": {
                        "type": "string",
                        "description": "Name of the member to draft a message for.",
                    },
                    "situation": {
                        "type": "string",
                        "description": "Optional context such as hospital, grief, job loss, birthday, or general check-in.",
                    },
                },
                "required": ["member_name"],
            },
        ),
        types.Tool(
            name="get_assistant_desk",
            description=(
                "Get Marge's connected secretary desk: greeting, first-run setup steps, priorities, "
                "drafts, calendar suggestions, approval queue, connector status, and suggested prompts. "
                "Use this before deciding what Marge should do next."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Use auto for normal account-scoped work, live for real rows, or demo for sample data.",
                        "enum": ["auto", "live", "demo"],
                        "default": "auto",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_assistant_chat_history",
            description=(
                "List recent persisted Marge chat turns for the current church workspace. "
                "Use this to preserve first-run context and avoid asking the pastor to repeat what he already taught Marge."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="clear_assistant_chat_history",
            description=(
                "Clear persisted Marge chat history for the current church workspace. "
                "This removes conversation transcript rows only; saved profile context, people, care, prayer, and approvals remain."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_ministry_profile",
            description=(
                "Get the current church workspace profile Marge uses for onboarding, drafting voice, "
                "follow-up burden, personal support style, secure connector setup, weekly rhythm, and guardrails."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="update_ministry_profile",
            description=(
                "Save pastor/church context Marge should remember. Use this for first-run onboarding "
                "answers such as role, church context, follow-up pain, support style, tools in use, drafting voice, weekly rhythm, and guardrails."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pastor_name": {"type": "string"},
                    "church_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "congregation_size": {"type": "string"},
                    "church_context": {"type": "string"},
                    "ministry_priorities": {"type": "string"},
                    "followup_pain": {"type": "string"},
                    "support_preferences": {"type": "string"},
                    "weekly_rhythm": {"type": "string"},
                    "communication_style": {"type": "string"},
                    "tools_in_use": {"type": "string"},
                    "guardrails": {"type": "string"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_integrations",
            description=(
                "List Marge connector status for Rock RMS, Planning Center, Breeze, Google Workspace, "
                "Microsoft 365, and local MCP. Use this before asking the pastor to connect a tool."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="start_integration_setup",
            description=(
                "Start secure connector setup for a provider. Returns OAuth authorization or server config guidance. "
                "Never ask the pastor to paste passwords, OAuth secrets, or API keys into chat."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "Provider key.",
                        "enum": ["google_workspace", "microsoft_365", "planning_center", "breeze", "rock", "mcp"],
                    }
                },
                "required": ["provider"],
            },
        ),
        types.Tool(
            name="sync_integration",
            description=(
                "Sync connected external context into Marge. This reads provider context and queues review actions; "
                "it should not send messages or write to external systems. If credentials have not been checked, "
                "this tool verifies them first without syncing data, then stops and asks for an explicit sync retry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["google_workspace", "microsoft_365", "planning_center", "breeze", "rock"],
                    },
                    "email_limit": {"type": "integer", "default": 5},
                    "people_limit": {"type": "integer", "default": 25},
                    "calendar_days": {"type": "integer", "default": 14},
                },
                "required": ["provider"],
            },
        ),
        types.Tool(
            name="verify_integration",
            description=(
                "Verify that a connector credential works without syncing ministry data or queuing actions. "
                "Use this after secure setup and before asking Marge to sync."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["google_workspace", "microsoft_365", "planning_center", "breeze", "rock", "mcp"],
                    }
                },
                "required": ["provider"],
            },
        ),
        types.Tool(
            name="disconnect_integration",
            description=(
                "Disconnect the current Marge user's OAuth credential for a provider. "
                "This removes Marge's encrypted token payload and disables writeback if no credentials remain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["google_workspace", "microsoft_365", "planning_center"],
                    }
                },
                "required": ["provider"],
            },
        ),
        types.Tool(
            name="list_connected_context",
            description="List synced inbox, calendar, people, or connector context Marge has cached for the current church workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "item_type": {
                        "type": "string",
                        "description": "Examples: email, calendar_event, person, sync_summary.",
                    },
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_approval_queue",
            description=(
                "List Marge's assistant action queue. Pending actions are drafts, setup steps, synced-context reviews, "
                "or external write proposals waiting for pastor review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status, or all.",
                        "enum": ["pending", "approved", "executed", "skipped", "all"],
                        "default": "pending",
                    },
                    "limit": {"type": "integer", "default": 25},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="prepare_approval_queue",
            description=(
                "Ask Marge to prepare today's proactive work into reviewable assistant actions. "
                "This queues drafts/setup proposals; it does not send or write externally."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "live", "demo"],
                        "default": "auto",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="approve_action",
            description="Approve one assistant action by ID. External writes still require provider policy and a separate execute step.",
            inputSchema={
                "type": "object",
                "properties": {"action_id": {"type": "integer"}},
                "required": ["action_id"],
            },
        ),
        types.Tool(
            name="execute_action",
            description=(
                "Execute an already-approved assistant action by ID. Use only after the pastor has reviewed the exact action."
            ),
            inputSchema={
                "type": "object",
                "properties": {"action_id": {"type": "integer"}},
                "required": ["action_id"],
            },
        ),
        types.Tool(
            name="skip_action",
            description="Skip an assistant action by ID when the pastor decides not to proceed.",
            inputSchema={
                "type": "object",
                "properties": {"action_id": {"type": "integer"}},
                "required": ["action_id"],
            },
        ),
        types.Tool(
            name="tell_marge",
            description=(
                "Chat with Marge in plain English. She can answer from the current desk, save ministry context, "
                "log pastoral updates, prepare drafts, start secure connector setup, and suggest follow-up actions. "
                "Use the real person, family, or ministry situation the pastor gives you instead of inventing examples."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Your plain-English message to Marge.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Use live for real workspace data or demo for sample data.",
                        "enum": ["live", "demo"],
                        "default": "live",
                    }
                },
                "required": ["message"],
            },
        ),
    ]


# ── Tool Handlers ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    def text(content: str) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=content)]

    try:
        if name == "get_morning_briefing":
            data = _get("/briefing/today")
            return text(_redact_secret_text(data.get("plain_text", json.dumps(data, indent=2))))

        elif name == "list_members":
            search = arguments.get("search", "")
            members = _get("/members/", params={"q": search} if search else {})
            if not members:
                if search:
                    return text(
                        f"No saved person matched '{_redact_secret_text(search)}'. If this is a real person, add them with enough context "
                        "before creating care, prayer, or follow-up work."
                    )
                return text(
                    "No people are saved in this workspace yet. Add the first real person, visitor, care case, "
                    "or synced directory context before treating the directory as checked."
                )
            lines = [f"Found {len(members)} member(s):"]
            for m in members[:20]:
                attendance = f" — last attended {_redact_secret_text(m['last_attendance'])}" if m.get("last_attendance") else ""
                lines.append(f"  • {_member_display_name(m)} (ID: {m.get('id', 'id not included')}){attendance}")
            return text("\n".join(lines))

        elif name == "log_visitor":
            visit_date = arguments.get("visit_date") or date.today().isoformat()
            body = {
                "first_name": arguments["first_name"],
                "last_name": arguments["last_name"],
                "email": arguments.get("email"),
                "phone": arguments.get("phone"),
                "visit_date": visit_date,
                "source": arguments.get("source"),
                "notes": arguments.get("notes", ""),
            }
            body = {key: value for key, value in body.items() if value not in (None, "")}
            result = _post("/visitors/", body)
            recipient_note = " with recipient details" if result.get("email") else ""
            return text(
                f"Logged visitor: {_redact_secret_text(result['full_name'])} visited on {_redact_secret_text(result['visit_date'])}. "
                f"Marge queued a welcome draft for pastor review{recipient_note}; nothing was sent."
            )

        elif name == "log_care_event":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(_member_not_found_message(
                    arguments["member_name"],
                    "I did not open a care case because I cannot safely link it to a saved member.",
                ))
            body = {
                "member_id": member["id"],
                "category": _normalize_care_category(arguments["category"]),
                "description": arguments["description"],
            }
            result = _post("/care/", body)
            return text(
                f"Care case opened for {_member_display_name(member)} (ID: {result['id']}). "
                f"Category: {_redact_secret_text(result['category'])}. Marge will surface this in the morning briefing."
            )

        elif name == "list_care_cases":
            params = {}
            if arguments.get("status"):
                params["status"] = arguments["status"]
            cases = _get("/care/", params=params)
            if not cases:
                return text(
                    "No care cases are saved in this workspace yet. Add the first real person needing care "
                    "before Marge plans care follow-up."
                )
            lines = [f"Found {len(cases)} care case(s):"]
            for case in cases[:20]:
                last = _redact_secret_text(case.get("last_contact") or "no contact logged")
                category = _redact_secret_text(case.get("category") or "general")
                status = _redact_secret_text(case.get("status") or "active")
                lines.append(
                    f"  • {_care_case_name(case)} (care ID: {case['id']}) "
                    f"[{category}, {status}] — last contact: {last}"
                )
            return text("\n".join(lines))

        elif name == "mark_contacted":
            body = {"note": arguments.get("note", "Contacted via Marge")}
            result = _post(f"/care/{arguments['care_id']}/contact", body)
            member_name = _redact_secret_text(result.get("member_name", "the member"))
            return text(f"Logged — contact with {member_name} recorded today. Marge will update the briefing accordingly.")

        elif name == "add_prayer_request":
            member_id = None
            member_name_str = ""
            submitted_by = _text_or(arguments.get("submitted_by"), "")
            requested_member_name = _text_or(arguments.get("member_name"), "")
            if requested_member_name:
                member = _find_member(requested_member_name)
                if member:
                    member_id = member["id"]
                    member_name_str = member["full_name"]
                elif not submitted_by:
                    submitted_by = requested_member_name

            body = {
                "request_text": arguments["request_text"],
                "is_private": arguments.get("is_private", False),
            }
            if member_id:
                body["member_id"] = member_id
            elif submitted_by:
                body["submitted_by"] = submitted_by

            result = _post("/care/prayers/", body)
            if member_name_str:
                name_part = f"for {_redact_secret_text(member_name_str)} "
            elif submitted_by:
                name_part = f"for {_redact_secret_text(submitted_by)} (not linked to a saved member yet) "
            else:
                name_part = ""
            privacy = "privately" if result.get("is_private") else "and added to the prayer list"
            return text(f"Prayer request {name_part}logged {privacy}. Marge will track it and prompt you to follow up.")

        elif name == "list_prayer_requests":
            params = {}
            if arguments.get("status"):
                params["status"] = arguments["status"]
            if "include_private" in arguments:
                params["include_private"] = arguments["include_private"]
            prayers = _get("/care/prayers/", params=params)
            if not prayers:
                return text(
                    "No prayer requests are saved in this workspace yet. Add the first real prayer request "
                    "before Marge treats prayer follow-up as checked."
                )
            lines = [f"Found {len(prayers)} prayer request(s):"]
            for prayer in prayers[:20]:
                privacy = "private" if prayer.get("is_private") else "public"
                who = _prayer_request_name(prayer)
                snippet = _redact_secret_text(prayer.get("request_text", ""))
                if len(snippet) > 90:
                    snippet = snippet[:89].rstrip() + "…"
                status = _redact_secret_text(prayer.get("status") or "active")
                lines.append(
                    f"  • {who} (prayer ID: {prayer['id']}, {privacy}, {status}) — {snippet}"
                )
            return text("\n".join(lines))

        elif name == "add_member_note":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(_member_not_found_message(
                    arguments["member_name"],
                    "I did not save the note because I cannot safely link it to a saved member.",
                ))
            body = {
                "note_text": arguments["note_text"],
                "context_tag": arguments.get("context_tag", "general"),
            }
            _post(f"/members/{member['id']}/notes", body)
            return text(
                f"Note logged for {_member_display_name(member)}. "
                f"Marge will remember this and surface it in future briefings when relevant."
            )

        elif name == "draft_message":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(_member_not_found_message(
                    arguments["member_name"],
                    "I did not draft a message because I cannot safely link it to a saved member.",
                ))
            result = _post("/drafts/", {
                "kind": "care",
                "member_id": member["id"],
                "situation": arguments.get("situation", "general check-in"),
            })
            draft = result.get("draft") or result.get("message") or json.dumps(result)
            return text(f"Draft for {_member_display_name(member)}:\n\n{_redact_secret_text(draft)}")

        elif name == "get_assistant_desk":
            mode = arguments.get("mode") or "auto"
            data = _get("/assistant/desk", params={"mode": mode})
            lines = [
                _redact_secret_text(data.get("greeting") or "Marge assistant desk"),
                "",
                f"Mode: {_redact_secret_text(data.get('mode'))} | Pastor: {_redact_secret_text(data.get('pastor_name'))} | Church: {_redact_secret_text(data.get('church_name'))}",
                f"Profile: {data.get('profile', {}).get('completion_percent', 0)}% complete",
                "",
                "Proactive summary:",
                _redact_secret_text(data.get("proactive_summary") or "No summary available."),
            ]
            setup = data.get("setup_steps") or []
            if setup:
                lines.extend(["", "Next setup steps:"])
                lines.extend(_format_desk_item(item) for item in setup[:5])
            priorities = data.get("priorities") or []
            if priorities:
                lines.extend(["", "Priorities:"])
                lines.extend(_format_desk_item(item) for item in priorities[:5])
            approvals = data.get("approvals") or []
            if approvals:
                lines.extend(["", "Approval desk:"])
                lines.extend(_format_desk_item(item) for item in approvals[:5])
            integrations = data.get("integrations") or []
            if integrations:
                lines.extend(["", "Connectors:"])
                lines.extend(_format_integration(item) for item in integrations[:8])
            prompts = data.get("suggested_prompts") or []
            if prompts:
                lines.extend(["", "Suggested prompts:"])
                lines.extend(f"  • {_redact_secret_text(prompt)}" for prompt in prompts[:5])
            return text("\n".join(lines))

        elif name == "list_assistant_chat_history":
            messages = _get("/assistant/chat/history", params={"limit": arguments.get("limit") or 20})
            if not messages:
                return text("No persisted assistant chat history found for this workspace.")
            lines = [f"Recent assistant chat history ({len(messages)} message(s)):"]
            lines.extend(_format_chat_message(message) for message in messages)
            return text("\n".join(lines))

        elif name == "clear_assistant_chat_history":
            result = _delete("/assistant/chat/history")
            return text(_redact_secret_text(result.get("message") or f"Cleared {result.get('messages_deleted', 0)} assistant chat message(s)."))

        elif name == "get_ministry_profile":
            profile = _get("/assistant/profile")
            missing = [_redact_secret_text(field) for field in profile.get("missing_fields") or []]
            lines = [
                f"Profile completion: {profile.get('completion_percent', 0)}%",
                f"Pastor: {_redact_secret_text(profile.get('pastor_name') or 'not set')}",
                f"Church: {_redact_secret_text(profile.get('church_name') or 'not set')}",
                f"Role: {_redact_secret_text(profile.get('role_title') or 'not set')}",
                f"Church context: {_redact_secret_text(profile.get('church_context') or 'not set')}",
                f"Follow-up burden: {_redact_secret_text(profile.get('followup_pain') or 'not set')}",
                f"Support style: {_redact_secret_text(profile.get('support_preferences') or 'not set')}",
                f"Tools in use: {_redact_secret_text(profile.get('tools_in_use') or 'not set')}",
                f"Drafting voice: {_redact_secret_text(profile.get('communication_style') or 'not set')}",
                f"Weekly rhythm: {_redact_secret_text(profile.get('weekly_rhythm') or 'not set')}",
                f"Guardrails: {_redact_secret_text(profile.get('guardrails') or 'not set')}",
                f"Missing fields: {', '.join(missing) if missing else 'none'}",
            ]
            return text("\n".join(lines))

        elif name == "update_ministry_profile":
            allowed = {
                "pastor_name",
                "church_name",
                "role_title",
                "congregation_size",
                "church_context",
                "ministry_priorities",
                "followup_pain",
                "support_preferences",
                "weekly_rhythm",
                "communication_style",
                "tools_in_use",
                "guardrails",
            }
            body = {key: value for key, value in arguments.items() if key in allowed and value not in (None, "")}
            if not body:
                return text("No ministry profile fields were provided to update.")
            profile = _patch("/assistant/profile", body)
            missing = [_redact_secret_text(field) for field in profile.get("missing_fields") or []]
            return text(
                "Saved ministry profile context for Marge.\n"
                f"Profile completion: {profile.get('completion_percent', 0)}%.\n"
                f"Missing fields: {', '.join(missing) if missing else 'none'}."
            )

        elif name == "list_integrations":
            integrations = _get("/assistant/integrations")
            if not integrations:
                return text(
                    "No integrations are registered. Connect a real church-tool provider, "
                    "then check credentials before syncing ministry data."
                )
            return text(
                "Connector status:\n"
                "Note: MCP is only the local agent bridge; live provider readiness requires a verified church-tool connector.\n"
                + "\n".join(_format_integration(item) for item in integrations)
            )

        elif name == "start_integration_setup":
            provider = arguments["provider"]
            setup = _post(f"/assistant/integrations/{provider}/start", {})
            lines = [
                f"{_redact_secret_text(setup.get('display_name') or provider)}: {_redact_secret_text(setup.get('status'))}",
                _redact_secret_text(setup.get("secure_note") or "Use secure setup. Do not paste secrets into chat."),
            ]
            if setup.get("authorization_url"):
                lines.append(f"Authorization URL: {_redact_secret_text(setup['authorization_url'])}")
            if setup.get("missing_config"):
                lines.append("Server config needed: " + ", ".join(_redact_secret_text(item) for item in setup["missing_config"]))
            instructions = setup.get("instructions") or []
            if instructions:
                lines.extend(["Instructions:"] + [f"  • {_redact_secret_text(item)}" for item in instructions])
            return text("\n".join(lines))

        elif name == "sync_integration":
            provider = arguments["provider"]
            params = {
                "email_limit": arguments.get("email_limit", 5),
                "people_limit": arguments.get("people_limit", 25),
                "calendar_days": arguments.get("calendar_days", 14),
            }
            try:
                result = _post(f"/assistant/integrations/{provider}/sync", {}, params=params)
            except httpx.HTTPStatusError as exc:
                if not _sync_needs_credential_check(exc):
                    raise
                try:
                    verification = _post(f"/assistant/integrations/{provider}/verify", {})
                except httpx.HTTPStatusError as verify_exc:
                    return text(
                        f"I stopped before syncing {_provider_display_name(provider)} because credentials need to be checked first.\n"
                        f"The safe no-sync credential check failed: {_http_error_detail(verify_exc)}"
                    )
                return text(_format_sync_precheck_verification(provider, verification))
            return text(
                f"{_redact_secret_text(result.get('provider') or provider)} sync {_redact_secret_text(result.get('status'))} at {_redact_secret_text(result.get('synced_at'))}.\n"
                f"Seen: {result.get('items_seen', 0)} | Created: {result.get('items_created', 0)} | "
                f"Updated: {result.get('items_updated', 0)} | Actions prepared: {result.get('actions_prepared', 0)}.\n"
                f"{_redact_secret_text(result.get('message') or '')}"
            )

        elif name == "verify_integration":
            provider = arguments["provider"]
            result = _post(f"/assistant/integrations/{provider}/verify", {})
            identity = result.get("identity") or {}
            identity_bits = _format_identity_bits(identity)
            scope = _redact_secret_text(result.get("credential_scope") or "server")
            return text(
                f"{_redact_secret_text(result.get('provider') or provider)} verification {_redact_secret_text(result.get('status'))} at {_redact_secret_text(result.get('verified_at'))}.\n"
                f"Credential scope: {scope}.\n"
                f"{_redact_secret_text(result.get('message') or '')}"
                + (f"\nIdentity: {', '.join(identity_bits)}" if identity_bits else "")
            )

        elif name == "disconnect_integration":
            provider = arguments["provider"]
            result = _delete(f"/assistant/integrations/{provider}")
            return text(
                f"{_redact_secret_text(result.get('provider') or provider)} disconnect {_redact_secret_text(result.get('status'))} at {_redact_secret_text(result.get('disconnected_at'))}.\n"
                f"Removed credentials: {result.get('removed_credentials', 0)} | "
                f"Remaining credentials: {result.get('remaining_credentials', 0)} | "
                f"Writeback enabled: {bool(result.get('write_enabled'))}.\n"
                f"{_redact_secret_text(result.get('message') or '')}"
            )

        elif name == "list_connected_context":
            params = {}
            if arguments.get("provider"):
                params["provider"] = arguments["provider"]
            if arguments.get("item_type"):
                params["item_type"] = arguments["item_type"]
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            items = _get("/assistant/connected-items", params=params)
            if not items:
                return text(
                    "No synced connected context is available for this workspace yet. Connect a real provider, "
                    "run the no-sync credential check, then ask Marge to sync when the pastor is ready."
                )
            lines = [f"Found {len(items)} connected context item(s):"]
            for item in items[:30]:
                when = _redact_secret_text(_text_or(item.get("occurred_at") or item.get("created_at"), "date not included by provider"))
                title = _redact_secret_text(_text_or(item.get("title"), "title not included by provider"))
                snippet = _redact_secret_text(item.get("snippet") or item.get("subtitle") or "")
                provider = _redact_secret_text(item.get("provider") or "provider not included")
                item_type = _redact_secret_text(item.get("item_type") or "item type not included")
                lines.append(f"  • #{item.get('id')} {provider} {item_type} — {title} ({when})" + (f": {snippet}" if snippet else ""))
            return text("\n".join(lines))

        elif name == "list_approval_queue":
            params = {
                "status": arguments.get("status") or "pending",
                "limit": arguments.get("limit") or 25,
            }
            actions = _get("/assistant/actions", params=params)
            if not actions:
                return text(
                    "No assistant actions were found for that status. Prepare reviewable work, add the first real "
                    "ministry record, or connect a credential-checked provider before treating the desk as clear."
                )
            lines = [f"Found {len(actions)} assistant action(s):"]
            lines.extend(_format_action(action) for action in actions)
            return text("\n".join(lines))

        elif name == "prepare_approval_queue":
            mode = arguments.get("mode") or "auto"
            actions = _post("/assistant/actions/prepare", {}, params={"mode": mode})
            if not actions:
                return text("Marge did not find anything to queue right now.")
            lines = [f"Prepared {len(actions)} assistant action(s) for pastor review:"]
            lines.extend(_format_action(action) for action in actions)
            return text("\n".join(lines))

        elif name == "approve_action":
            action = _post(f"/assistant/actions/{arguments['action_id']}/approve", {})
            return text("Approved assistant action:\n" + _format_action(action))

        elif name == "execute_action":
            action = _post(f"/assistant/actions/{arguments['action_id']}/execute", {})
            payload = action.get("payload") or {}
            return text("Executed assistant action:\n" + _format_action(action) + "\nPayload:\n" + _compact_payload(payload))

        elif name == "skip_action":
            action = _post(f"/assistant/actions/{arguments['action_id']}/skip", {})
            return text("Skipped assistant action:\n" + _format_action(action))

        elif name == "tell_marge":
            result = _post("/assistant/chat", {"message": arguments["message"], "mode": arguments.get("mode") or "live"})
            return text(_format_tell_marge_response(result))

        else:
            return text(f"Unknown tool: {name}")

    except httpx.HTTPStatusError as e:
        return text(f"Marge API error ({e.response.status_code}): {_http_error_detail(e)}")
    except Exception as e:
        return text(f"Error: {_redact_secret_text(e)}")


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
