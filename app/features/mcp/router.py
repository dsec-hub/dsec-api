"""Onboarding for the DSEC MCP server — a setup guide + machine-readable info.

Public (no auth) so a committee member can read how to connect their chat client
before they have wired up their key. The MCP endpoint itself (/mcp) is always
authenticated.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from .catalog import SCOPE_SUMMARY, tools_by_scope
from .guide import build_llm_guide

router = APIRouter()

SERVER_URL = "https://api.dsec.club/mcp"

GUIDE = f"""# Connect the DSEC workspace to your AI assistant (MCP)

The DSEC API speaks **MCP** (Model Context Protocol), so you can manage the
club — members, finances, events, projects, tasks, meetings, documents and
sponsors — straight from **Claude** or **ChatGPT**, no dashboard needed.

## 1. Get an API key
Mint your own from the dashboard at **Settings → API & MCP** (the scopes you can
grant are bounded by your role), or ask a DSEC admin. A key looks like
`dsec_live_…`. Your key has **scopes** that decide what you can do:

- `read`  — view everything (members, finances, events, tasks, docs, sponsors, …)
- `write` — create/update events (+speakers, sponsor & partner line-ups),
  projects, tasks, docs, sponsors (pipeline, packages, leads, contacts),
  partners, and people
- `trigger` — use AI features (generate meeting notes from a transcript)

Grant only the scopes you need. In chat, run the **`whoami`** tool to see what
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

## Teaching your AI assistant
Want the assistant to know exactly which tools your key can use and the house
rules? Download a tailored **`llm.md`** guide from **Settings → API & MCP** (it's
generated when you mint a token, or per-token), or fetch it from
`{SERVER_URL.rsplit('/', 1)[0]}/mcp-setup/llm?scopes=read,write`. It contains no
secret — paste it into your assistant alongside the connection above.

## Notes
- Everything writes to the club's single source of truth (Neon), so changes show
  up in the dashboard and (for public projects/events) on the website.
- AI features and writes are rate-limited and cost-capped server-side.
"""

@router.get("", response_class=PlainTextResponse)
def guide() -> str:
    """Human-readable Markdown setup guide."""
    return GUIDE


@router.get("/info")
def info() -> dict:
    """Machine-readable connection info + the tools grouped by required scope.

    The tool inventory and scope summaries come from the single catalogue in
    `catalog.py`, so they stay in lock-step with the running server."""
    return {
        "server_url": SERVER_URL,
        "transport": "streamable-http",
        "auth": "Authorization: Bearer dsec_live_...",
        "scopes": {k: SCOPE_SUMMARY[k] for k in ("read", "write", "trigger")},
        "tools": tools_by_scope(),
    }


@router.get("/llm", response_class=PlainTextResponse)
def llm_guide(
    scopes: str = Query(
        "read,write,trigger",
        description="Comma-separated scopes the key holds, e.g. 'read,write'.",
    ),
    label: str | None = Query(None, description="Optional name to personalise the guide."),
) -> PlainTextResponse:
    """Render an `llm.md` — an instruction guide for an AI assistant — tailored
    to the scopes a key carries. Contains no secret (the key is a placeholder),
    so it's safe to serve unauthenticated and to commit/share."""
    requested = {s.strip() for s in scopes.split(",") if s.strip()}
    md = build_llm_guide(requested, server_url=SERVER_URL, label=label)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
