# Marge Build Task

You are building new features for Marge — an AI pastoral assistant. The repo is already set up at /root/marge-build. You are on the staging branch. Do NOT touch any existing backend files in app/ unless adding new endpoints.

## Task 1: Add /chat endpoint to the FastAPI backend

Create app/routers/chat.py:

- POST /chat
- Body: {"message": "I visited Martha today, she is doing better"}
- Response: {"reply": "Got it. I have logged a care contact for Martha Ellis today. Want me to set a follow-up reminder?"}

The endpoint should:
1. Accept plain text message from pastor
2. Use OpenAI API (gpt-4o) to parse intent and generate a Marge-voiced reply
3. System prompt: "You are Marge, an AI church secretary assistant. You are warm, direct, and pastoral. When the pastor tells you something, acknowledge it warmly, confirm what you logged or will do, and offer one helpful follow-up action. Keep replies to 2-3 sentences max. Never be corporate or cold."
4. For now: just return the AI reply, do not write to DB
5. If OPENAI_API_KEY env var is not set: return a warm placeholder reply

Add openai to requirements.txt if not already there.
Register the router in app/main.py.

## Task 2: Enhance frontend/index.html

The current frontend fetches from https://marge-staging-staging.up.railway.app/briefing/today and displays cards.

### A. Real "Draft a text" button
- On click: generate a draft client-side using a warm template based on the context (birthday, care case, visitor, absent, prayer)
- Show drafted text in a modal overlay (cream background #FAF7F2, Lora font)
- Modal has: editable textarea with the draft, "Copy to clipboard" button, close button
- After copy: button changes to "Copied!" for 2 seconds

### B. "Mark as contacted" button
- Add a small "Mark contacted" button next to each care case row
- Care cases: call POST /care/{care_id}/contact with body {"note": "Contacted via Marge briefing"}
- After marking: fade out that card row smoothly

### C. "Tell Marge" chat input
- Sticky input bar at bottom of page
- Placeholder: "Tell Marge something... e.g. I visited Martha today, she is doing better"
- On submit: POST to /chat with {"message": "..."}
- Show thinking indicator while waiting
- Show Marge reply in a warm callout above the input
- Keep last 3 exchanges visible

## Task 3: Build MCP server

Create mcp_server/ directory with:

### mcp_server/server.py
MCP server using the mcp Python SDK. Transport: stdio.

Server name: "marge"
Server instructions: "Marge is your AI church secretary. She helps you care for your congregation by tracking visitors, care cases, prayer requests, and sending you a morning briefing of who needs attention today."

Expose these tools (each calls the Marge REST API at MARGE_API_URL env var, default http://localhost:8000):

1. get_morning_briefing() - GET /briefing/today - returns plain_text field from response
2. list_members(search: str = "") - GET /members/?search=X
3. log_visitor(first_name: str, last_name: str, visit_date: str, notes: str = "") - POST /visitors/
4. log_care_event(member_id: int, category: str, description: str) - POST /care/
5. mark_contacted(care_id: int, note: str = "") - POST /care/{care_id}/contact
6. add_prayer_request(request_text: str, member_id: int = None, is_private: bool = False) - POST /care/prayers/
7. add_member_note(member_id: int, note_text: str) - POST /members/{member_id}/notes
8. draft_message(member_id: int) - GET /members/{member_id}/draft/care

### mcp_server/requirements.txt
mcp>=1.0.0
httpx>=0.27.0

### mcp_server/README.md
Short README:
- What Marge MCP is
- Install: pip install -r mcp_server/requirements.txt  
- Claude Desktop config JSON block
- MARGE_API_URL env var explanation

## Git
Commit in logical chunks with descriptive messages. Push all to origin/staging.

When finished, run this exact command:
openclaw system event --text "Done: Marge chat endpoint + briefing actions + MCP server pushed to staging" --mode now
