"""Render a per-key LLM guide (Markdown) from the tool catalogue.

The output is an `llm.md` — an instruction document you hand to an AI assistant
(Claude Code, Codex, ChatGPT, a Claude chat, …) so it knows how to drive the
DSEC MCP server: what the connected key can and can't do, the house rules, the
exact tools available, and a few worked recipes. It is tailored to the *scopes*
a key carries — which are themselves bounded by the minter's dashboard role —
so a read-only key never reads about tools it can't call.

The guide contains **no secret**: the key is always shown as a
`dsec_live_YOUR_KEY` placeholder, so the file is safe to commit or paste into a
chat. The real key lives only in the MCP client's config.
"""

from __future__ import annotations

from itertools import groupby

from .catalog import SCOPE_ORDER, SCOPE_SUMMARY, Tool, tools_for_scopes

PLACEHOLDER = "dsec_live_YOUR_KEY"


def _normalise(scopes: set[str]) -> list[str]:
    """Granted scopes in canonical order, ignoring anything unknown."""
    return [s for s in SCOPE_ORDER if s in scopes]


def _scope_table(granted: list[str]) -> str:
    """A checklist of every scope, marking which this key holds. Showing the
    *ungranted* ones too tells the assistant why a tool might be refused."""
    lines = []
    for s in SCOPE_ORDER:
        mark = "**yes**" if s in granted else "no"
        lines.append(f"- `{s}` — {mark}. {SCOPE_SUMMARY[s]}")
    return "\n".join(lines)


def _tool_sections(tools: list[Tool]) -> str:
    """Catalogue tools, grouped, in catalogue order. Write/AI tools flagged."""
    parts: list[str] = []
    for group, items in groupby(tools, key=lambda t: t.group):
        parts.append(f"### {group}")
        for t in items:
            flag = ""
            if t.scope == "write":
                flag = " _(write)_"
            elif t.scope == "trigger":
                flag = " _(AI — spends tokens)_"
            parts.append(f"- `{t.name}`{flag} — {t.summary}")
        parts.append("")  # blank line between groups
    return "\n".join(parts).rstrip()


def _connect_section(server_url: str) -> str:
    return f"""## Connect (the human does this once)

- **Server URL:** `{server_url}`
- **Transport:** Streamable HTTP
- **Auth:** either sign in with a DSEC account (OAuth) or send a key as a header —
  `Authorization: Bearer {PLACEHOLDER}`

There are two ways to connect:

1. **Sign in (OAuth, no token).** Paste just the server URL into a client that
   supports OAuth (e.g. Claude.ai's *Add custom connector*). The client opens a
   DSEC sign-in page; log in and approve. Access is bounded by your dashboard
   role. Nothing to paste here — skip the key steps below.
2. **API key.** Replace `{PLACEHOLDER}` with a real `dsec_live_…` key (shown once
   when minted at **Settings → API & MCP** in the dashboard) using one of:

**Claude Code (CLI)**
```bash
claude mcp add --transport http dsec {server_url} \\
  --header "Authorization: Bearer {PLACEHOLDER}"
```

**Claude Desktop** — `claude_desktop_config.json`
```json
{{
  "mcpServers": {{
    "dsec": {{
      "type": "http",
      "url": "{server_url}",
      "headers": {{ "Authorization": "Bearer {PLACEHOLDER}" }}
    }}
  }}
}}
```

**Claude.ai (Add custom connector)** — best path: paste just `{server_url}` and
sign in when prompted (OAuth, option 1 above — no key needed). If you'd rather
use a key, the dialog has no header field, so put the key in the URL:
```
{server_url}?key={PLACEHOLDER}
```
Treat that whole URL as a secret. ChatGPT / Codex custom connectors that *do*
expose a header field can instead use `{server_url}` with
`Authorization: Bearer {PLACEHOLDER}`."""


def _rules_section(has_write: bool, has_trigger: bool) -> str:
    rules = [
        "**Call `whoami` first** to confirm which scopes this key holds before "
        "planning any work.",
        "**Never invent IDs.** Use the matching `list_*` tool to find the id you "
        "need; resolve people (assignees, leads, speakers) with `list_people`.",
        "**Dates are ISO `YYYY-MM-DD`.**",
    ]
    if has_write:
        rules += [
            "**Events and projects are private drafts** until you set "
            "`is_public=true` — they only reach the public website once published.",
            "**Updates are partial:** pass only the fields you want to change; "
            "anything you omit is left untouched.",
            "**Uploads aren't here.** Images and files are added in the dashboard; "
            "the `list_media` / `list_attachments` tools are read-only.",
            "**Confirm irreversible actions with the human first** — "
            "`archive_event`, `delete_sponsor_package`, `remove_*` and `unlink_*` "
            "change shared club data.",
        ]
    if has_trigger:
        rules.append(
            "**AI tools spend real tokens** and are cost-capped server-side — "
            "use `generate_meeting_notes` deliberately, not speculatively."
        )
    rules.append(
        "Writes and AI calls are **rate-limited**; if a call is throttled, back "
        "off and tell the human rather than hammering it."
    )
    return "## House rules (follow these)\n\n" + "\n".join(f"- {r}" for r in rules)


def _recipes_section(has_read: bool, has_write: bool, has_trigger: bool) -> str:
    recipes: list[str] = []
    if has_read:
        recipes.append(
            "- **Status check:** “How many current members do we have, what's our "
            "closing balance this term, and what events are coming up?” → "
            "`member_stats`, `finance_summary`, `list_events`."
        )
    if has_write:
        recipes.append(
            "- **Run a new event end-to-end:** `create_event` (leave it a draft) → "
            "`set_event_budget` → `add_event_speaker` → `link_event_sponsor`; "
            "publish later with `update_event(is_public=true)`."
        )
        recipes.append(
            "- **Plan work:** `create_board` → `create_task` (resolve the assignee "
            "with `list_people` first) → `move_task` as it progresses."
        )
    if has_trigger:
        recipes.append(
            "- **Minutes from a transcript:** `create_meeting` → paste the "
            "transcript into `generate_meeting_notes` → it writes notes and action "
            "items back to the meeting."
        )
    if not recipes:
        return ""
    return "## Recipes\n\n" + "\n".join(recipes)


def build_llm_guide(scopes: set[str], *, server_url: str, label: str | None = None) -> str:
    """Render the `llm.md` guide for a key holding `scopes`."""
    granted = _normalise(scopes)
    has_read = "read" in granted
    has_write = "write" in granted
    has_trigger = "trigger" in granted

    scope_list = ", ".join(f"`{s}`" for s in granted) if granted else "_(none)_"
    label_line = f"\n_For: {label}._" if label else ""

    if has_write:
        verb = "read and update"
    elif has_read:
        verb = "read"
    else:
        verb = "work with"

    sections: list[str] = [
        f"""# DSEC workspace — MCP guide for AI assistants
{label_line}
_Generated for a key with scope(s): {scope_list}. This file contains no secret —
the key is shown only as `{PLACEHOLDER}` — so it's safe to commit or paste into a chat._

You are connected (or about to connect) to the **DSEC committee workspace** over
**MCP (Model Context Protocol)**. Through it you can {verb} the club's members,
finances, events, community projects, task boards, meetings, documents, sponsors
and partner orgs — the same data the human sees in the dashboard. Everything you
change writes to the club's single source of truth and shows up in the dashboard
(and, for published events/projects, on the public website).""",

        "## What this key can do\n\n"
        + _scope_table(granted)
        + "\n\nScopes are **coarse and global**: a `write` key can write *every* "
          "module (not just one), and a `read` key can read everything. They are "
          "bounded by the dashboard role of whoever minted the key, not by module.",

        _connect_section(server_url),

        _rules_section(has_write, has_trigger),

        "## Tools available to this key\n\n"
        + (_tool_sections(tools_for_scopes(scopes))
           if tools_for_scopes(scopes)
           else "_No callable tools — this key has no usable scope._"),
    ]

    if not has_write:
        sections.append(
            "> This key is **read-only**. Creating or updating data (events, "
            "tasks, sponsors, documents, …) needs a key with the `write` scope; "
            "AI meeting notes need `trigger`. Mint one at **Settings → API & MCP** "
            "if your role allows it."
        )

    recipes = _recipes_section(has_read, has_write, has_trigger)
    if recipes:
        sections.append(recipes)

    sections.append(
        "## When a tool is refused\n\n"
        "If a call fails with a message like `scope 'write' required`, this key "
        "simply lacks that scope — it's not a bug. Tell the human to mint a key "
        "with the scope they need at **Settings → API & MCP** (the scopes anyone "
        "can grant are capped by their dashboard role, so they may need a broader "
        "role first)."
    )

    return "\n\n".join(sections).rstrip() + "\n"
