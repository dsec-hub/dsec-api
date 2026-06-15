"""Onboarding for the DSEC MCP server — a setup guide + machine-readable info.

Public (no auth) so a committee member can read how to connect their chat client
before they have wired up their key. The MCP endpoint itself (/mcp) is always
authenticated.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()

SERVER_URL = "https://api.dsec.club/mcp"

GUIDE = f"""# Connect the DSEC workspace to your AI assistant (MCP)

The DSEC API speaks **MCP** (Model Context Protocol), so you can manage the
club — members, finances, events, projects, tasks, meetings, documents and
sponsors — straight from **Claude** or **ChatGPT**, no dashboard needed.

## 1. Get an API key
Ask a DSEC admin for a key (it looks like `dsec_live_…`). Your key has **scopes**
that decide what you can do:

- `read`  — view everything (members, finances, tasks, docs, …)
- `write` — create and update events, projects, tasks, docs, sponsors, people
- `trigger` — use AI features (generate meeting notes from a transcript)

Ask only for the scopes you need. In chat, run the **`whoami`** tool to see what
your key allows.

## 2. Add the server

**Server URL:** `{SERVER_URL}`
**Transport:** Streamable HTTP
**Auth:** send your key as a header — `Authorization: Bearer dsec_live_…`

### Claude (Desktop / Code) — `claude_desktop_config.json`
```json
{{
  "mcpServers": {{
    "dsec": {{
      "type": "http",
      "url": "{SERVER_URL}",
      "headers": {{ "Authorization": "Bearer dsec_live_YOUR_KEY" }}
    }}
  }}
}}
```

### Claude.ai / ChatGPT (custom connector)
Add a custom MCP/connector, set the URL to `{SERVER_URL}`, and add the header
`Authorization: Bearer dsec_live_YOUR_KEY`.

## 3. Try it
Ask your assistant things like:

- "Using DSEC, how many current members do we have, and how many are DUSA members?"
- "What's our current club balance and biggest expense this term?"
- "Create an event 'Intro to Git Workshop' on 2026-08-05 at Burwood, then set its budget to $300."
- "Make a Sponsorship board and add a task 'Email ACME' due next Friday, high priority."
- "Here's our meeting transcript: … — generate minutes and action items."
- "Draft a deliverables doc for Alex covering the website refresh."

## Notes
- Everything writes to the club's single source of truth (Neon), so changes show
  up in the dashboard and (for public projects/events) on the website.
- AI features and writes are rate-limited and cost-capped server-side.
"""

# Short list of what's available, for clients that want structured info.
TOOLS = {
    "read": [
        "whoami", "list_members", "member_stats", "finance_summary", "list_transactions",
        "list_events", "list_projects", "list_boards", "list_tasks", "list_meetings",
        "list_documents", "get_document", "list_sponsors", "list_people",
    ],
    "write": [
        "set_event_budget", "create_event", "update_event", "create_project", "update_project",
        "create_board", "create_task", "update_task", "move_task", "create_meeting",
        "create_document", "update_document", "create_sponsor", "update_sponsor", "create_person",
    ],
    "trigger": ["generate_meeting_notes"],
}


@router.get("", response_class=PlainTextResponse)
def guide() -> str:
    """Human-readable Markdown setup guide."""
    return GUIDE


@router.get("/info")
def info() -> dict:
    """Machine-readable connection info + the tools grouped by required scope."""
    return {
        "server_url": SERVER_URL,
        "transport": "streamable-http",
        "auth": "Authorization: Bearer dsec_live_...",
        "scopes": {
            "read": "view all data",
            "write": "create/update events, projects, tasks, docs, sponsors, people",
            "trigger": "AI features (meeting notes)",
        },
        "tools": TOOLS,
    }
