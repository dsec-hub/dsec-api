"""Single source of truth for the MCP tool catalogue.

Every tool the server exposes is described here exactly once — its name, the
scope it requires, the feature group it belongs to, and a one-line summary.
Both the public setup endpoints (`/mcp-setup/info`) and the per-key LLM guide
(`/mcp-setup/llm`) are rendered from this list, so the docs an assistant reads
can never drift from the tools the server actually exposes.

`tests/test_mcp.py` asserts these names match the tools registered on the
FastMCP server, so adding or removing a tool without updating this catalogue
fails the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

# Scopes in the order we always present them (least → most privileged).
SCOPE_ORDER: tuple[str, ...] = ("read", "write", "trigger", "ingest")

# Human-readable, assistant-facing description of each scope.
SCOPE_SUMMARY: dict[str, str] = {
    "read": "View workspace data — members, finances, events, projects, tasks, "
            "meetings, documents, sponsors, partners and people.",
    "write": "Create and update workspace data — events (plus speaker, sponsor "
             "and partner line-ups), projects, task boards, meetings, documents, "
             "sponsors (pipeline, packages, leads, contacts), partners and people.",
    "trigger": "Run AI features that spend tokens — currently generating meeting "
               "notes and action items from a transcript.",
    "ingest": "Import the weekly DUSA membership / P&L spreadsheets. Admin-only "
              "and used by the ingestion pipeline over REST — there are no MCP "
              "tools for it.",
}


@dataclass(frozen=True)
class Tool:
    """One MCP tool. `scope` is the scope it enforces via `require_scope`, or
    "meta" for tools any authenticated key may call (only `whoami`)."""

    name: str
    scope: str  # "read" | "write" | "trigger" | "meta"
    group: str
    summary: str


# The catalogue, in presentation order. Grouped by feature folder; within a
# group reads come before writes. `whoami` is "meta" — always available.
CATALOG: tuple[Tool, ...] = (
    # ---- Getting started -------------------------------------------------- #
    Tool("whoami", "meta", "Getting started",
         "Show which DSEC API key you're using and exactly what it can do. Call this first."),

    # ---- Members ---------------------------------------------------------- #
    Tool("list_members", "read", "Members",
         "List club members (the paid roster from the weekly DUSA import)."),
    Tool("member_stats", "read", "Members",
         "Member counts and the week-by-week membership trend."),

    # ---- Finance ---------------------------------------------------------- #
    Tool("finance_summary", "read", "Finance",
         "Opening balance, income, expenses and closing balance for the term."),
    Tool("list_transactions", "read", "Finance",
         "Profit-and-loss ledger lines."),
    Tool("set_event_budget", "write", "Finance",
         "Set an event's budget (auto-applies the standard grant)."),

    # ---- Events ----------------------------------------------------------- #
    Tool("list_events", "read", "Events",
         "List club events (drafts and published)."),
    Tool("create_event", "write", "Events",
         "Create an event. It stays a private draft until is_public=true."),
    Tool("update_event", "write", "Events",
         "Update fields on an existing event."),
    Tool("archive_event", "write", "Events",
         "Soft-delete (archive) an event. Confirm with the human first."),
    Tool("create_event_review_form", "write", "Events",
         "Create the Tally post-event review form for an event."),
    Tool("get_event_review_responses", "read", "Events",
         "Read the submitted responses to an event's review form."),

    # ---- Event line-up (speakers / sponsors / partners) ------------------- #
    Tool("list_event_speakers", "read", "Event line-up",
         "List the speakers billed on an event."),
    Tool("add_event_speaker", "write", "Event line-up",
         "Add a speaker to an event's line-up."),
    Tool("update_event_speaker", "write", "Event line-up",
         "Update a speaker's details on an event."),
    Tool("remove_event_speaker", "write", "Event line-up",
         "Remove (soft-archive) a speaker from an event."),
    Tool("list_event_sponsors", "read", "Event line-up",
         "List the sponsors on an event's sponsor wall."),
    Tool("link_event_sponsor", "write", "Event line-up",
         "Link a sponsor to an event (idempotent)."),
    Tool("unlink_event_sponsor", "write", "Event line-up",
         "Remove a sponsor from an event's wall."),
    Tool("list_event_partners", "read", "Event line-up",
         "List the partner orgs co-hosting an event."),
    Tool("link_event_partner", "write", "Event line-up",
         "Link a partner org to an event (idempotent)."),
    Tool("unlink_event_partner", "write", "Event line-up",
         "Remove a partner org from an event."),
    Tool("list_event_connections", "read", "Event line-up",
         "List events visibly connected to this one (related-events links)."),
    Tool("link_event_connection", "write", "Event line-up",
         "Connect two events so each shows the other as related (visual-only)."),
    Tool("unlink_event_connection", "write", "Event line-up",
         "Remove the connection between two events."),

    # ---- Partners --------------------------------------------------------- #
    Tool("list_partners", "read", "Partners",
         "List collaborator clubs / partner organisations."),
    Tool("create_partner", "write", "Partners",
         "Add a partner org / club."),
    Tool("update_partner", "write", "Partners",
         "Update a partner's details."),

    # ---- Projects --------------------------------------------------------- #
    Tool("list_projects", "read", "Projects",
         "List community projects."),
    Tool("create_project", "write", "Projects",
         "Create a project. It stays a draft until is_public=true."),
    Tool("update_project", "write", "Projects",
         "Update fields on a project."),

    # ---- Tasks ------------------------------------------------------------ #
    Tool("list_boards", "read", "Tasks",
         "List task boards and their columns."),
    Tool("create_board", "write", "Tasks",
         "Create a task board."),
    Tool("list_tasks", "read", "Tasks",
         "List task cards (filterable by board, column, assignee, …)."),
    Tool("create_task", "write", "Tasks",
         "Create a task card."),
    Tool("update_task", "write", "Tasks",
         "Update a task's fields and cross-entity links."),
    Tool("move_task", "write", "Tasks",
         "Move a task to a column and position."),

    # ---- Meetings --------------------------------------------------------- #
    Tool("list_meetings", "read", "Meetings",
         "List meetings."),
    Tool("create_meeting", "write", "Meetings",
         "Create a meeting record."),
    Tool("generate_meeting_notes", "trigger", "Meetings",
         "AI-summarise a transcript into notes and action items (spends tokens)."),

    # ---- Documents -------------------------------------------------------- #
    Tool("list_documents", "read", "Documents",
         "List documents (notes, meeting notes, deliverables, policies)."),
    Tool("get_document", "read", "Documents",
         "Get one document with its full Markdown content."),
    Tool("create_document", "write", "Documents",
         "Create a document."),
    Tool("update_document", "write", "Documents",
         "Update a document's title, content, status or assignee."),

    # ---- Sponsors --------------------------------------------------------- #
    Tool("list_sponsors", "read", "Sponsors",
         "List sponsorship leads / relationships in the pipeline."),
    Tool("create_sponsor", "write", "Sponsors",
         "Add a sponsorship lead."),
    Tool("update_sponsor", "write", "Sponsors",
         "Advance a sponsor through the pipeline / edit its details."),

    # ---- Sponsor contacts ------------------------------------------------- #
    Tool("list_sponsor_contacts", "read", "Sponsor contacts",
         "List the people attached to a sponsor."),
    Tool("add_sponsor_contact", "write", "Sponsor contacts",
         "Attach a contact to a sponsor."),
    Tool("update_sponsor_contact", "write", "Sponsor contacts",
         "Update a sponsor contact's details."),
    Tool("remove_sponsor_contact", "write", "Sponsor contacts",
         "Remove (soft-archive) a sponsor contact."),

    # ---- Sponsor packages ------------------------------------------------- #
    Tool("list_sponsor_packages", "read", "Sponsor packages",
         "List the sponsorship tiers shown on the website."),
    Tool("create_sponsor_package", "write", "Sponsor packages",
         "Add a sponsorship package / tier."),
    Tool("update_sponsor_package", "write", "Sponsor packages",
         "Update a sponsorship package."),
    Tool("delete_sponsor_package", "write", "Sponsor packages",
         "Permanently delete a sponsorship package. Confirm with the human first."),

    # ---- Sponsor leads (inbound) ------------------------------------------ #
    Tool("list_sponsor_leads", "read", "Sponsor leads",
         "List inbound sponsorship enquiries from the website."),
    Tool("update_sponsor_lead", "write", "Sponsor leads",
         "Move an inbound lead through the pipeline and add notes."),

    # ---- People ----------------------------------------------------------- #
    Tool("list_people", "read", "People",
         "List people (exec, committee, external contacts) — use this to resolve assignees."),
    Tool("create_person", "write", "People",
         "Add a person (committee member or external contact)."),
    Tool("update_person", "write", "People",
         "Update a person's details."),

    # ---- Media & attachments ---------------------------------------------- #
    Tool("list_media", "read", "Media & attachments",
         "List images attached to an entity (read-only; uploads happen in the dashboard)."),
    Tool("list_attachments", "read", "Media & attachments",
         "List files (PDFs, images) attached to an entity (read-only)."),
)


def all_tool_names() -> set[str]:
    """Every tool name in the catalogue (used by the drift-guard test)."""
    return {t.name for t in CATALOG}


def tools_for_scopes(scopes: set[str]) -> list[Tool]:
    """Tools a key holding `scopes` can actually call. `whoami` is always in."""
    return [t for t in CATALOG if t.scope == "meta" or t.scope in scopes]


def tools_by_scope() -> dict[str, list[str]]:
    """`{scope: [tool names]}` for the machine-readable /info endpoint.

    `whoami` needs no scope but is listed under `read` (a key's effective
    minimum) to preserve the historical shape of that response.
    """
    out: dict[str, list[str]] = {"read": [], "write": [], "trigger": []}
    for t in CATALOG:
        bucket = "read" if t.scope == "meta" else t.scope
        if bucket in out:
            out[bucket].append(t.name)
    return out
