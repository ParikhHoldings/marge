# Marge MCP Server

Connect Marge to Claude Desktop, ChatGPT, or any MCP-compatible AI client.

Ask your AI: *"Who needs care today?"* and get Marge's full morning briefing — pulled from your actual congregation data.

## What it does

The Marge MCP server exposes your congregation data as tools any AI can use:

| Tool | What it does |
|------|-------------|
| `get_morning_briefing` | Get today's full briefing — birthdays, visitors, care cases, absent members, prayer requests |
| `list_members` | Search your congregation by name |
| `log_visitor` | Record a first-time visitor with contact details and queue a welcome draft for pastor review |
| `log_care_event` | Open a care case for hospital, crisis, grief, counseling |
| `list_care_cases` | List active or resolved care cases |
| `mark_contacted` | Log that you reached out — updates the care record |
| `add_prayer_request` | Add a prayer request to the list |
| `list_prayer_requests` | List prayer requests, with optional private-request visibility |
| `add_member_note` | Log a pastoral note after a visit or conversation |
| `draft_message` | Generate an outreach text or email in your voice |
| `get_assistant_desk` | Get Marge's connected secretary desk, setup steps, priorities, drafts, approvals, and connector status |
| `get_ministry_profile` | Read the pastor/church context Marge uses for onboarding and personalization |
| `update_ministry_profile` | Save first-run ministry context such as role, follow-up burden, tools, voice, rhythm, and guardrails |
| `list_integrations` | Show secure connector status for Rock, Planning Center, Breeze, Google Workspace, Microsoft 365, and MCP |
| `start_integration_setup` | Start secure connector setup without asking the pastor to paste secrets into chat |
| `sync_integration` | Pull connected provider context into Marge's normalized cache and approval queue after credentials have been checked |
| `list_connected_context` | List synced inbox, calendar, people, or connector context |
| `list_approval_queue` | Inspect Marge's persisted assistant action queue |
| `prepare_approval_queue` | Ask Marge to stage today's proactive work for pastor review |
| `approve_action` | Approve a queued assistant action by ID |
| `execute_action` | Execute an already-approved action when policies allow it |
| `skip_action` | Skip a queued assistant action |
| `tell_marge` | Chat with Marge in plain English — she can save context, log updates, prepare work, and guide setup |

## Install

```bash
pip install -r mcp_server/requirements.txt
```

## Claude Desktop setup

Add this to your Claude Desktop config (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "marge": {
      "command": "python",
      "args": ["/path/to/marge/mcp_server/server.py"],
      "env": {
        "MARGE_API_URL": "https://marge-staging-staging.up.railway.app",
        "MARGE_ACCOUNT_TOKEN": "marge_..."
      }
    }
  }
}
```

Restart Claude Desktop. You will see "marge" appear in the tools panel.

## ChatGPT (ChatGPT Desktop, GPT-4o with MCP)

ChatGPT supports MCP servers via the desktop app (as of early 2025).

1. Open ChatGPT Desktop settings → Extensions → Add MCP Server
2. Point it to the same server.py with MARGE_API_URL set to your instance

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARGE_API_URL` | `http://localhost:8000` | Base URL of your Marge instance |
| `MARGE_ACCOUNT_TOKEN` | empty | Optional owner/pastor session or user token returned by `POST /assistant/sessions`, `/assistant/signup`, or `/assistant/users/invite`; sends `X-Marge-Account-Token` on every tool call |

Use `MARGE_ACCOUNT_TOKEN` for real pastoral data. Without it, MCP calls only use legacy unscoped local rows. Prefer revocable session tokens for ongoing MCP use; role-scoped user tokens can read or act only within the permissions assigned in Marge. MCP clients do not use the browser's HttpOnly `marge_session` cookie, so copy the returned session token into `MARGE_ACCOUNT_TOKEN` for desktop-tool use.

For OAuth connectors, use the same owner/pastor user token that should own the provider consent. Marge stores new OAuth state and encrypted credentials against that workspace user, so one staff member's MCP token cannot silently sync or write through another user's Google, Planning Center, or Microsoft consent.

Use `list_assistant_chat_history` before continuing first-run onboarding from an MCP client. It reads the same persisted account-scoped chat history that `/app` loads after refresh, so the pastor should not have to repeat ministry context he already taught Marge. Use `clear_assistant_chat_history` when the pastor wants the transcript removed while keeping saved profile context and ministry records.

## Example prompts

Once connected, try these in Claude or ChatGPT:

- *"Who needs care today?"*
- *"Log that I visited Martha Ellis — she's recovering well from her surgery"*
- *"Draft a text for Tom Henderson's birthday"*
- *"Add a prayer request for David Park — he lost his job last month"*
- *"Show me active private prayer requests that need a follow-up"*
- *"The Wilson family hasn't been in 6 weeks — flag them for follow-up"*
- *"Show Marge's assistant desk and tell me what I should do first"*
- *"Update my ministry profile: I am a solo pastor, we use Planning Center and Gmail, and visitor follow-up is where things fall through the cracks"*
- *"Start secure setup for Google Workspace"*
- *"Verify Google Workspace before syncing anything"*
- *"Prepare the approval queue, then show me what I need to approve first"*

## Approval and connector posture

The MCP server uses the same assistant/action model as the web app. It can queue drafts, setup steps, synced inbox reviews, and calendar proposals, but external writes still require:

- a church workspace user token in `MARGE_ACCOUNT_TOKEN`
- secure provider setup or server-side configuration
- connector write policy enabled in Marge
- per-action pastor approval
- an explicit execute step

Do not ask a pastor to paste OAuth secrets, API keys, passwords, or refresh tokens into an LLM chat. Use `start_integration_setup` and the server-side environment variables documented in the main `README.md`.

Use `verify_integration` after setup when you need to confirm credentials without pulling live ministry data into Marge. Use `sync_integration` only after verification or an explicit pastor request to import context.

If `sync_integration` is called before a connected provider has been checked, the MCP server runs the safe credential verification first, imports no ministry data, queues no actions, and asks the client to request sync again explicitly.

Use `disconnect_integration` when a pastor asks Marge to stop using an OAuth-connected tool for the current Marge user. It removes the encrypted token payload and keeps the action audit metadata-only.

For command-line live verification outside MCP, use the main repo script:

```bash
MARGE_API_URL=http://localhost:8000 MARGE_ACCOUNT_TOKEN=marge_sess_... .venv/bin/python scripts/verify_live_integrations.py
```
