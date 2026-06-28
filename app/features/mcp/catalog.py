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

# Scopes in the order we always present them (least → most privileged). The
# coarse read/write/trigger/ingest scopes are the historical, global ones; the
# per-module read:<m>/write:<m> scopes isolate the "enforced" modules (Sponsors,
# Finance) so a key can be granted exactly those without blanket access.
SCOPE_ORDER: tuple[str, ...] = (
    "read", "write", "trigger", "ingest",
    "read:sponsors", "write:sponsors", "read:finance", "write:finance",
)

# Human-readable, assistant-facing description of each scope.
SCOPE_SUMMARY: dict[str, str] = {
    "read": "View workspace data — members, events, projects, tasks, meetings, "
            "documents, partners and people (a legacy read key also covers the "
            "isolated Sponsors and Finance modules).",
    "write": "Create and update workspace data — events (plus speaker, sponsor "
             "and partner line-ups), projects, task boards, meetings, documents, "
             "partners and people (a legacy write key also covers the isolated "
             "Sponsors and Finance modules).",
    "trigger": "Run AI features that spend tokens — currently generating meeting "
               "notes and action items from a transcript.",
    "ingest": "Import the weekly DUSA membership / P&L spreadsheets. Admin-only "
              "and used by the ingestion pipeline over REST — there are no MCP "
              "tools for it.",
    "read:sponsors": "View only the sponsorship pipeline — sponsors, contacts, "
                     "packages and inbound leads (the isolated Sponsors module).",
    "write:sponsors": "Create and update the sponsorship pipeline — sponsors, "
                      "contacts, packages and leads. Implies read:sponsors.",
    "read:finance": "View only the club finances — the term P&L summary and "
                    "ledger lines (the isolated Finance module).",
    "write:finance": "Set event budgets and auto-applied grants. Implies "
                     "read:finance.",
}


@dataclass(frozen=True)
class Tool:
    """One MCP tool. `scope` is the scope it enforces via `require_scope`, or
    "meta" for tools any authenticated key may call (only `whoami`)."""

    name: str
    # "read" | "write" | "trigger" | "meta", or a per-module scope for the
    # enforced modules: "read:sponsors" | "write:sponsors" | "read:finance" |
    # "write:finance". A legacy "read"/"write" key still satisfies the module
    # scopes (see app/features/mcp/auth.py::has_scope).
    scope: str
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
    Tool("get_member", "read", "Members",
         "Get one club member by id."),
    Tool("member_stats", "read", "Members",
         "Member counts and the week-by-week membership trend."),

    # ---- Finance ---------------------------------------------------------- #
    Tool("finance_summary", "read:finance", "Finance",
         "Opening balance, income, expenses and closing balance for the term."),
    Tool("list_transactions", "read:finance", "Finance",
         "Profit-and-loss ledger lines."),
    Tool("list_finance_reports", "read:finance", "Finance",
         "List the imported P&L reports (one per weekly finance import), newest first."),
    Tool("set_event_budget", "write:finance", "Finance",
         "Set an event's budget (auto-applies the standard grant)."),

    # ---- Events ----------------------------------------------------------- #
    Tool("list_events", "read", "Events",
         "List club events (drafts and published)."),
    Tool("get_event", "read", "Events",
         "Get one event by id (full detail)."),
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
         "List collaborator clubs / partner orgs (optionally filter by status: "
         "lead | contacted | active | inactive)."),
    Tool("get_partner", "read", "Partners",
         "Get one partner org / club by id."),
    Tool("create_partner", "write", "Partners",
         "Add a partner org / club (name, website, email, socials, status)."),
    Tool("update_partner", "write", "Partners",
         "Update a partner's details / move it along the pipeline (status)."),
    Tool("archive_partner", "write", "Partners",
         "Soft-delete (archive) a partner org. Confirm with the human first."),

    # ---- Link tree -------------------------------------------------------- #
    Tool("list_links", "read", "Link tree",
         "List the buttons on the public link-tree page (incl. hidden), in display order."),
    Tool("get_link", "read", "Link tree",
         "Get one link-tree button by id."),
    Tool("create_link", "write", "Link tree",
         "Add a link-tree button (title, url, optional emoji icon, accent, subtitle)."),
    Tool("update_link", "write", "Link tree",
         "Update a link-tree button (only the fields you pass change)."),
    Tool("archive_link", "write", "Link tree",
         "Soft-delete (archive) a link-tree button. Confirm with the human first."),
    Tool("reorder_links", "write", "Link tree",
         "Reorder the link-tree buttons by passing their ids in the new top-to-bottom order."),
    Tool("get_link_profile", "read", "Link tree",
         "Get the link-tree page header (title, tagline, mascot)."),
    Tool("update_link_profile", "write", "Link tree",
         "Update the link-tree page header (title, tagline, mascot)."),

    # ---- Projects --------------------------------------------------------- #
    Tool("list_projects", "read", "Projects",
         "List community projects."),
    Tool("get_project", "read", "Projects",
         "Get one community project by id (full detail)."),
    Tool("create_project", "write", "Projects",
         "Create a project. It stays a draft until is_public=true."),
    Tool("update_project", "write", "Projects",
         "Update fields on a project."),
    Tool("archive_project", "write", "Projects",
         "Soft-delete (archive) a project. Confirm with the human first."),

    # ---- Tasks ------------------------------------------------------------ #
    Tool("list_boards", "read", "Tasks",
         "List task boards and their columns."),
    Tool("create_board", "write", "Tasks",
         "Create a task board."),
    Tool("update_board", "write", "Tasks",
         "Update a task board's name, description, committee or columns."),
    Tool("archive_board", "write", "Tasks",
         "Soft-delete (archive) a task board. Confirm with the human first."),
    Tool("list_tasks", "read", "Tasks",
         "List task cards (filterable by board, column, assignee, …)."),
    Tool("get_task", "read", "Tasks",
         "Get one task card by id (full detail)."),
    Tool("create_task", "write", "Tasks",
         "Create a task card."),
    Tool("update_task", "write", "Tasks",
         "Update a task's fields and cross-entity links."),
    Tool("move_task", "write", "Tasks",
         "Move a task to a column and position."),
    Tool("archive_task", "write", "Tasks",
         "Soft-delete (archive) a task card. Confirm with the human first."),

    # ---- Meetings --------------------------------------------------------- #
    Tool("list_meetings", "read", "Meetings",
         "List meetings."),
    Tool("get_meeting", "read", "Meetings",
         "Get one meeting by id (transcript, notes, action items)."),
    Tool("create_meeting", "write", "Meetings",
         "Create a meeting record."),
    Tool("update_meeting", "write", "Meetings",
         "Update a meeting (edit transcript, or hand-write notes/action items)."),
    Tool("archive_meeting", "write", "Meetings",
         "Soft-delete (archive) a meeting. Confirm with the human first."),
    Tool("generate_meeting_notes", "trigger", "Meetings",
         "AI-summarise a transcript into notes and action items (spends tokens)."),
    Tool("get_meeting_agenda", "read", "Meetings",
         "Get a meeting's pre-meeting agenda — ordered items, total duration and share state."),
    Tool("set_meeting_agenda", "write", "Meetings",
         "Replace a meeting's agenda (send the whole ordered item list to add/edit/reorder)."),
    Tool("share_meeting_agenda", "write", "Meetings",
         "Share the agenda with invitees and return the public read-only link. Confirm first."),
    Tool("lock_meeting_agenda", "write", "Meetings",
         "Freeze the agenda once the meeting starts (still viewable, no longer editable)."),

    # ---- Documents -------------------------------------------------------- #
    Tool("list_documents", "read", "Documents",
         "List documents (notes, meeting notes, deliverables, policies)."),
    Tool("get_document", "read", "Documents",
         "Get one document with its full Markdown content."),
    Tool("create_document", "write", "Documents",
         "Create a document."),
    Tool("update_document", "write", "Documents",
         "Update a document's title, content, status or assignee."),
    Tool("archive_document", "write", "Documents",
         "Soft-delete (archive) a document. Confirm with the human first."),

    # ---- Sponsors --------------------------------------------------------- #
    Tool("list_sponsors", "read:sponsors", "Sponsors",
         "List sponsorship leads / relationships in the pipeline."),
    Tool("get_sponsor", "read:sponsors", "Sponsors",
         "Get one sponsor / pipeline relationship by id."),
    Tool("create_sponsor", "write:sponsors", "Sponsors",
         "Add a sponsorship lead."),
    Tool("update_sponsor", "write:sponsors", "Sponsors",
         "Advance a sponsor through the pipeline / edit its details."),
    Tool("archive_sponsor", "write:sponsors", "Sponsors",
         "Soft-delete (archive) a sponsor. Confirm with the human first."),

    # ---- Sponsor contacts ------------------------------------------------- #
    Tool("list_sponsor_contacts", "read:sponsors", "Sponsor contacts",
         "List the people attached to a sponsor."),
    Tool("add_sponsor_contact", "write:sponsors", "Sponsor contacts",
         "Attach a contact to a sponsor."),
    Tool("update_sponsor_contact", "write:sponsors", "Sponsor contacts",
         "Update a sponsor contact's details."),
    Tool("remove_sponsor_contact", "write:sponsors", "Sponsor contacts",
         "Remove (soft-archive) a sponsor contact."),

    # ---- Sponsor packages ------------------------------------------------- #
    Tool("list_sponsor_packages", "read:sponsors", "Sponsor packages",
         "List the sponsorship tiers shown on the website."),
    Tool("get_sponsor_package", "read:sponsors", "Sponsor packages",
         "Get one sponsorship package / tier by id."),
    Tool("create_sponsor_package", "write:sponsors", "Sponsor packages",
         "Add a sponsorship package / tier."),
    Tool("update_sponsor_package", "write:sponsors", "Sponsor packages",
         "Update a sponsorship package."),
    Tool("delete_sponsor_package", "write:sponsors", "Sponsor packages",
         "Permanently delete a sponsorship package. Confirm with the human first."),

    # ---- Sponsor leads (inbound) ------------------------------------------ #
    Tool("list_sponsor_leads", "read:sponsors", "Sponsor leads",
         "List inbound sponsorship enquiries from the website."),
    Tool("update_sponsor_lead", "write:sponsors", "Sponsor leads",
         "Move an inbound lead through the pipeline and add notes."),

    # ---- People ----------------------------------------------------------- #
    Tool("list_people", "read", "People",
         "List people (exec, committee, external contacts) — use this to resolve assignees."),
    Tool("get_person", "read", "People",
         "Get one person by id (committee member or external contact)."),
    Tool("create_person", "write", "People",
         "Add a person (committee member or external contact)."),
    Tool("update_person", "write", "People",
         "Update a person's details."),
    Tool("archive_person", "write", "People",
         "Soft-delete (archive) a person. Confirm with the human first."),

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
    """Tools a key holding `scopes` can actually call. `whoami` is always in.

    Uses the same scope algebra the server enforces (``auth.has_scope``) so a
    legacy ``read``/``write`` key still lists the isolated Sponsors/Finance
    tools, while a per-module key lists only its module's tools.
    """
    from app.features.mcp.auth import has_scope

    sc = frozenset(scopes)
    return [t for t in CATALOG if t.scope == "meta" or has_scope(sc, t.scope)]


def tools_by_scope() -> dict[str, list[str]]:
    """`{scope: [tool names]}` for the machine-readable /info endpoint.

    Coarse buckets only: per-module read/write tools are folded into the `read`
    and `write` buckets so the public inventory keeps its historical shape.
    `whoami` is listed under `read` (a key's effective minimum).
    """
    out: dict[str, list[str]] = {"read": [], "write": [], "trigger": []}
    for t in CATALOG:
        if t.scope == "meta" or t.scope == "read" or t.scope.startswith("read:"):
            bucket = "read"
        elif t.scope == "write" or t.scope.startswith("write:"):
            bucket = "write"
        else:
            bucket = t.scope
        if bucket in out:
            out[bucket].append(t.name)
    return out
