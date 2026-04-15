"""
Marge MCP Server

Exposes Marge's pastoral care tools to Claude Desktop, ChatGPT, and any
other MCP-compatible AI client.

Usage:
    python mcp_server/server.py

Environment variables:
    MARGE_API_URL   Base URL of your Marge instance (default: http://localhost:8000)

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "marge": {
          "command": "python",
          "args": ["/path/to/marge/mcp_server/server.py"],
          "env": {
            "MARGE_API_URL": "https://your-marge-instance.railway.app"
          }
        }
      }
    }
"""

import os
import json
import httpx
from datetime import date
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── Config ────────────────────────────────────────────────────────────────────

MARGE_API_URL = os.getenv("MARGE_API_URL", "http://localhost:8000").rstrip("/")

# ── Server ────────────────────────────────────────────────────────────────────

server = Server(
    name="marge",
    version="0.1.0",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> dict:
    """Synchronous GET to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.get(f"{MARGE_API_URL}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    """Synchronous POST to Marge API."""
    with httpx.Client(timeout=15) as client:
        r = client.post(f"{MARGE_API_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def _find_member(name: str) -> dict | None:
    """Find first member matching name search."""
    results = _get("/members/", params={"q": name})
    if isinstance(results, list) and results:
        return results[0]
    return None


def _normalize_api_error(error: httpx.HTTPStatusError) -> str:
    """
    Convert raw API failures into actionable pastor-facing prompts.
    """
    status = error.response.status_code
    payload: Any = None
    detail = ""
    try:
        payload = error.response.json()
        detail = payload.get("detail", "") if isinstance(payload, dict) else ""
    except Exception:
        detail = error.response.text.strip()

    if status == 400:
        return (
            "I couldn't process that request because some information was missing or invalid. "
            f"Please review the details and try again. ({detail or 'Bad request'})"
        )
    if status == 404:
        return (
            "I couldn't find that record. Please confirm the name/ID and try again, "
            "or run list_members first to verify who you're looking for."
        )
    if status == 422:
        return (
            "I need a little more detail to complete that action. "
            f"Please check the required fields and try again. ({detail or 'Validation error'})"
        )
    if 400 <= status < 500:
        return (
            f"That request was rejected by Marge (HTTP {status}). "
            f"Please adjust the input and retry. ({detail or 'Client error'})"
        )
    if 500 <= status < 600:
        return (
            f"Marge is having trouble right now (HTTP {status}). "
            "Please try again in a moment. If this continues, contact your admin."
        )
    return f"Unexpected API error (HTTP {status}). Please try again."


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
                    "visit_date": {
                        "type": "string",
                        "description": "Date of visit in YYYY-MM-DD format. Defaults to today if not provided.",
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
            description="Add a prayer request for a member or anonymous person.",
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
                        "description": "Context for the message (e.g. hospital, grief, encouragement). Defaults to 'general'.",
                        "default": "general",
                    },
                },
                "required": ["member_name"],
            },
        ),
        types.Tool(
            name="tell_marge",
            description=(
                "Tell Marge something in plain English. She will acknowledge it, confirm what she logged, "
                "and suggest a follow-up action. "
                "Examples: 'I visited Martha today, she is doing better', "
                "'Tom Henderson mentioned he lost his job', "
                "'The Wilson family has been absent for a month — can you flag them?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Your plain-English message to Marge.",
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
            return text(data.get("plain_text", json.dumps(data, indent=2)))

        elif name == "list_members":
            search = arguments.get("search", "")
            members = _get("/members/", params={"q": search} if search else {})
            if not members:
                return text("No members found matching that search.")
            lines = [f"Found {len(members)} member(s):"]
            for m in members[:20]:
                lines.append(f"  • {m['full_name']} (ID: {m['id']})" +
                             (f" — last attended {m.get('last_attendance', 'unknown')}" if m.get('last_attendance') else ""))
            return text("\n".join(lines))

        elif name == "log_visitor":
            visit_date = arguments.get("visit_date") or date.today().isoformat()
            body = {
                "first_name": arguments["first_name"],
                "last_name": arguments["last_name"],
                "visit_date": visit_date,
                "notes": arguments.get("notes", ""),
            }
            result = _post("/visitors/", body)
            return text(
                f"Logged visitor: {result['full_name']} visited on {result['visit_date']}. "
                f"Marge will start a follow-up sequence automatically."
            )

        elif name == "log_care_event":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(f"Could not find a member named '{arguments['member_name']}'. Try list_members to search.")
            body = {
                "member_id": member["id"],
                "category": arguments["category"],
                "description": arguments["description"],
            }
            result = _post("/care/", body)
            return text(
                f"Care case opened for {member['full_name']} (ID: {result['id']}). "
                f"Category: {result['category']}. Marge will surface this in the morning briefing."
            )

        elif name == "mark_contacted":
            body = {"note": arguments.get("note", "Contacted via Marge")}
            result = _post(f"/care/{arguments['care_id']}/contact", body)
            member_name = result.get("member_name", "the member")
            return text(f"Logged — contact with {member_name} recorded today. Marge will update the briefing accordingly.")

        elif name == "add_prayer_request":
            member_id = None
            member_name_str = ""
            if arguments.get("member_name"):
                member = _find_member(arguments["member_name"])
                if member:
                    member_id = member["id"]
                    member_name_str = member["full_name"]

            body = {
                "request_text": arguments["request_text"],
                "is_private": arguments.get("is_private", False),
            }
            if member_id:
                body["member_id"] = member_id

            result = _post("/care/prayers/", body)
            name_part = f"for {member_name_str} " if member_name_str else ""
            privacy = "privately" if result.get("is_private") else "and added to the prayer list"
            return text(f"Prayer request {name_part}logged {privacy}. Marge will track it and prompt you to follow up.")

        elif name == "add_member_note":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(f"Could not find a member named '{arguments['member_name']}'. Try list_members to search.")
            body = {
                "note_text": arguments["note_text"],
                "context_tag": arguments.get("context_tag", "general"),
            }
            _post(f"/members/{member['id']}/notes", body)
            return text(
                f"Note logged for {member['full_name']}. "
                f"Marge will remember this and surface it in future briefings when relevant."
            )

        elif name == "draft_message":
            member = _find_member(arguments["member_name"])
            if not member:
                return text(f"Could not find a member named '{arguments['member_name']}'. Try list_members to search.")
            situation = arguments.get("situation", "general")
            result = _get(f"/members/{member['id']}/draft/care", params={"situation": situation})
            draft = result.get("draft") or result.get("message") or json.dumps(result)
            return text(f"Draft for {member['full_name']}:\n\n{draft}")

        elif name == "tell_marge":
            result = _post("/chat/", {"message": arguments["message"]})
            return text(result.get("reply", "Got it."))

        else:
            return text(f"Unknown tool: {name}")

    except httpx.HTTPStatusError as e:
        return text(_normalize_api_error(e))
    except Exception as e:
        return text(f"Error: {str(e)}")


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
