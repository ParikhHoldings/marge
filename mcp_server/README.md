# Marge MCP Server

Connect Marge to Claude Desktop, ChatGPT, or any MCP-compatible AI client.

Ask your AI: *"Who needs care today?"* and get Marge's full morning briefing — pulled from your actual congregation data.

## What it does

The Marge MCP server exposes your congregation data as tools any AI can use:

| Tool | What it does |
|------|-------------|
| `get_morning_briefing` | Get today's full briefing — birthdays, visitors, care cases, absent members, prayer requests |
| `list_members` | Search your congregation by name |
| `log_visitor` | Record a first-time visitor (triggers follow-up sequence) |
| `log_care_event` | Open a care case for hospital, crisis, grief, counseling |
| `mark_contacted` | Log that you reached out — updates the care record |
| `add_prayer_request` | Add a prayer request to the list |
| `add_member_note` | Log a pastoral note after a visit or conversation |
| `draft_message` | Generate an outreach text or email in your voice |
| `tell_marge` | Tell Marge anything in plain English — she handles the rest |

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
        "MARGE_API_URL": "https://marge-staging-staging.up.railway.app"
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

## Example prompts

Once connected, try these in Claude or ChatGPT:

- *"Who needs care today?"*
- *"Log that I visited Martha Ellis — she's recovering well from her surgery"*
- *"Draft a text for Tom Henderson's birthday"*
- *"Add a prayer request for David Park — he lost his job last month"*
- *"The Wilson family hasn't been in 6 weeks — flag them for follow-up"*
