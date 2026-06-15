"""The DSEC MCP server.

Exposes the whole committee workspace (members, finance, events, projects,
tasks, meetings, documents, sponsors, people) to MCP clients — Claude, ChatGPT,
etc. — so the exec can read and update everything from chat without opening the
dashboard. Every tool enforces the calling key's scopes (read / write / trigger)
via the contextvar set by MCPAuthMiddleware.

Mounted into the FastAPI app at /mcp (see app/main.py). Stateless HTTP so it
runs fine on serverless.
"""

from __future__ import annotations

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from app.core.llm import LLMError
from app.core.ratelimit import limiter
from app.db import SessionLocal

# Feature services + schemas (the same layer the REST API uses)
from app.features.documents import service as documents_service
from app.features.documents.schemas import DocumentCreate, DocumentOut, DocumentUpdate
from app.features.events import service as events_service
from app.features.events.schemas import EventCreate, EventOut, EventUpdate
from app.features.finance import service as finance_service
from app.features.finance.schemas import EventBudgetOut, TransactionOut
from app.features.meetings import service as meetings_service
from app.features.meetings.notes import generate_meeting_notes as _gen_notes
from app.features.meetings.schemas import MeetingCreate, MeetingOut, MeetingUpdate
from app.features.members import service as members_service
from app.features.members.schemas import MemberOut, MemberTrendPoint
from app.features.people import service as people_service
from app.features.people.schemas import PersonCreate, PersonOut, PersonUpdate
from app.features.projects import service as projects_service
from app.features.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.features.reviews import service as reviews_service
from app.features.reviews.schemas import ReviewFormOut, ReviewResponsesOut
from app.features.reviews.tally import TallyError, TallyNotConfigured
from app.features.sponsors import service as sponsors_service
from app.features.sponsors.schemas import SponsorCreate, SponsorOut, SponsorUpdate
from app.features.tasks import service as tasks_service
from app.features.tasks.schemas import BoardCreate, BoardOut, TaskCreate, TaskOut, TaskUpdate

from .auth import current_key, require_scope

_INSTRUCTIONS = """DSEC committee workspace. Read and update the club's members,
finances, events, community projects, task boards, meetings, documents, and
sponsors. Call `whoami` first to see what your API key is allowed to do. Reads
need the 'read' scope; creating/updating needs 'write'; AI meeting-notes needs
'trigger'. Dates are ISO YYYY-MM-DD."""

mcp = FastMCP("DSEC", stateless_http=True, streamable_http_path="/", instructions=_INSTRUCTIONS)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _dump(schema, obj) -> dict:
    return schema.model_validate(obj).model_dump(mode="json")


def _dump_list(schema, rows) -> list[dict]:
    return [schema.model_validate(r).model_dump(mode="json") for r in rows]


def _data(**kw) -> dict:
    """Drop None values (so 'not provided' never overwrites existing data)."""
    return {k: v for k, v in kw.items() if v is not None}


def _coerce(model_cls, raw: dict) -> dict:
    """Validate/coerce a kwargs dict through a Pydantic model (e.g. ISO date str
    -> date) and return only the provided fields."""
    return model_cls(**raw).model_dump(exclude_unset=True)


def _require(obj, msg: str):
    if obj is None:
        raise ValueError(msg)
    return obj


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #

@mcp.tool()
def whoami() -> dict:
    """Show which DSEC API key you're using and exactly what it can do."""
    ctx = current_key()
    if ctx is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "key_prefix": ctx.prefix,
        "scopes": sorted(ctx.scopes),
        "capabilities": {
            "read_data": "read" in ctx.scopes,
            "create_update_data": "write" in ctx.scopes,
            "generate_meeting_notes_ai": "trigger" in ctx.scopes,
            "ingest_dusa_imports": "ingest" in ctx.scopes,
        },
    }


# --------------------------------------------------------------------------- #
# members (read-only — roster owned by the weekly DUSA import)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_members(current_only: bool = True, dusa_only: bool | None = None,
                 search: str | None = None, limit: int = 50) -> list[dict]:
    """List club members (the paid roster from the weekly DUSA import)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(MemberOut, members_service.list_members(
            db, current_only=current_only, dusa_only=dusa_only, search=search, limit=limit))


@mcp.tool()
def member_stats() -> dict:
    """Current member counts (total, DUSA vs non-DUSA) and the weekly trend."""
    require_scope("read")
    with SessionLocal() as db:
        return {
            "counts": members_service.member_counts(db),
            "trend": _dump_list(MemberTrendPoint, members_service.member_trend(db)),
        }


# --------------------------------------------------------------------------- #
# finance
# --------------------------------------------------------------------------- #

@mcp.tool()
def finance_summary() -> dict:
    """Current finances: opening/income/expense/closing balance + total event budgets/grants."""
    require_scope("read")
    with SessionLocal() as db:
        return finance_service.finances_summary(db)


@mcp.tool()
def list_transactions(kind: str | None = None, limit: int = 50) -> list[dict]:
    """List ledger lines from the latest P&L. kind is income | expense | balance."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(TransactionOut, finance_service.list_transactions(db, kind=kind, limit=limit))


@mcp.tool()
def set_event_budget(event_id: int, budget_aud: float, grant_rate: float = 0.5) -> dict:
    """Set an event's budget and auto-apply the grant (default 50% of budget)."""
    require_scope("write")
    with SessionLocal() as db:
        ev = _require(finance_service.set_event_budget(db, event_id, budget_aud, grant_rate),
                      "event not found")
        return _dump(EventBudgetOut, ev)


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_events(status: str | None = None, type: str | None = None, limit: int = 50) -> list[dict]:
    """List club events (most recent first)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(EventOut, events_service.list_events(db, status=status, type=type, limit=limit))


@mcp.tool()
def create_event(name: str, type: str | None = None, status: str | None = None,
                 start_date: str | None = None, end_date: str | None = None,
                 venue: str | None = None, committee: str | None = None,
                 event_lead_id: int | None = None, ticket_url: str | None = None,
                 ticket_tiers: list[dict] | None = None, food_provided: bool | None = None,
                 notes: str | None = None) -> dict:
    """Create an event. Dates are ISO YYYY-MM-DD. `ticket_tiers` is tiered pricing:
    a list of {"label": str, "price": number | null} (price 0 = free, null = unset)."""
    require_scope("write")
    data = _coerce(EventCreate, _data(name=name, type=type, status=status, start_date=start_date,
                                      end_date=end_date, venue=venue, committee=committee,
                                      event_lead_id=event_lead_id, ticket_url=ticket_url,
                                      ticket_tiers=ticket_tiers, food_provided=food_provided,
                                      notes=notes))
    with SessionLocal() as db:
        return _dump(EventOut, events_service.create_event(db, data))


@mcp.tool()
def update_event(event_id: int, name: str | None = None, type: str | None = None,
                 status: str | None = None, start_date: str | None = None, end_date: str | None = None,
                 venue: str | None = None, committee: str | None = None,
                 event_lead_id: int | None = None, ticket_url: str | None = None,
                 ticket_tiers: list[dict] | None = None, food_provided: bool | None = None,
                 notes: str | None = None) -> dict:
    """Update an event (only the fields you pass change). `ticket_tiers` is tiered
    pricing: a list of {"label": str, "price": number | null} (price 0 = free)."""
    require_scope("write")
    data = _coerce(EventUpdate, _data(name=name, type=type, status=status, start_date=start_date,
                                      end_date=end_date, venue=venue, committee=committee,
                                      event_lead_id=event_lead_id, ticket_url=ticket_url,
                                      ticket_tiers=ticket_tiers, food_provided=food_provided,
                                      notes=notes))
    with SessionLocal() as db:
        return _dump(EventOut, _require(events_service.update_event(db, event_id, data), "event not found"))


@mcp.tool()
def create_event_review_form(event_id: int, force: bool = False) -> dict:
    """Create a Tally post-event review form for an event and return its shareable
    link. Idempotent — returns the existing form unless force=true. Needs 'write'."""
    require_scope("write")
    with SessionLocal() as db:
        try:
            event = reviews_service.create_review_form(db, event_id, force=force)
        except (TallyNotConfigured, TallyError) as exc:
            raise ValueError(str(exc))
        event = _require(event, "event not found")
        return ReviewFormOut(
            event_id=event.id, configured=True, form_id=event.review_form_id,
            form_url=event.review_form_url, created_at=event.review_form_created_at,
            response_count=None,
        ).model_dump(mode="json")


@mcp.tool()
def get_event_review_responses(event_id: int) -> dict:
    """Read an event's post-event review submissions: count, average rating, and the
    free-text answers (what people enjoyed / want improved). Needs 'read'."""
    require_scope("read")
    with SessionLocal() as db:
        try:
            data = reviews_service.get_review_summary(db, event_id)
        except (TallyNotConfigured, TallyError) as exc:
            raise ValueError(str(exc))
        return ReviewResponsesOut(**_require(data, "event not found")).model_dump(mode="json")


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_projects(status: str | None = None, is_public: bool | None = None, limit: int = 50) -> list[dict]:
    """List community projects."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(ProjectOut, projects_service.list_projects(
            db, status=status, is_public=is_public, limit=limit))


@mcp.tool()
def create_project(name: str, summary: str | None = None, description: str | None = None,
                   status: str | None = None, category: str | None = None,
                   tech_tags: list[str] | None = None, lead_id: int | None = None,
                   repo_url: str | None = None, demo_url: str | None = None,
                   is_public: bool | None = None, featured: bool | None = None) -> dict:
    """Create a community project. Set is_public=true to show it on the website."""
    require_scope("write")
    data = _coerce(ProjectCreate, _data(name=name, summary=summary, description=description,
                                        status=status, category=category, tech_tags=tech_tags,
                                        lead_id=lead_id, repo_url=repo_url, demo_url=demo_url,
                                        is_public=is_public, featured=featured))
    with SessionLocal() as db:
        return _dump(ProjectOut, projects_service.create_project(db, data))


@mcp.tool()
def update_project(project_id: int, name: str | None = None, summary: str | None = None,
                   description: str | None = None, status: str | None = None,
                   is_public: bool | None = None, featured: bool | None = None,
                   repo_url: str | None = None, demo_url: str | None = None) -> dict:
    """Update a community project (only the fields you pass change)."""
    require_scope("write")
    data = _coerce(ProjectUpdate, _data(name=name, summary=summary, description=description,
                                        status=status, is_public=is_public, featured=featured,
                                        repo_url=repo_url, demo_url=demo_url))
    with SessionLocal() as db:
        return _dump(ProjectOut, _require(projects_service.update_project(db, project_id, data),
                                          "project not found"))


# --------------------------------------------------------------------------- #
# task boards + tasks (Trello-style)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_boards() -> list[dict]:
    """List task boards (each has a `columns` list — the Trello-style lists)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(BoardOut, tasks_service.list_boards(db))


@mcp.tool()
def create_board(name: str, description: str | None = None, committee: str | None = None,
                 columns: list[str] | None = None) -> dict:
    """Create a task board. columns defaults to Backlog/To Do/In Progress/Done."""
    require_scope("write")
    data = _coerce(BoardCreate, _data(name=name, description=description, committee=committee, columns=columns))
    with SessionLocal() as db:
        return _dump(BoardOut, tasks_service.create_board(db, data))


@mcp.tool()
def list_tasks(board_id: int | None = None, assignee_id: int | None = None,
               status: str | None = None, committee: str | None = None, limit: int = 100) -> list[dict]:
    """List task cards, optionally filtered by board, assignee, column (status), or committee."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(TaskOut, tasks_service.list_tasks(
            db, board_id=board_id, assignee_id=assignee_id, status=status,
            committee=committee, limit=limit))


@mcp.tool()
def create_task(title: str, board_id: int | None = None, status: str | None = None,
                description: str | None = None, priority: str | None = None,
                assignee_id: int | None = None, committee: str | None = None,
                due_date: str | None = None, related_event_id: int | None = None,
                related_project_id: int | None = None, related_sponsor_id: int | None = None) -> dict:
    """Create a task card. status is the board column (defaults to Backlog)."""
    require_scope("write")
    data = _coerce(TaskCreate, _data(title=title, board_id=board_id, status=status,
                                     description=description, priority=priority, assignee_id=assignee_id,
                                     committee=committee, due_date=due_date, related_event_id=related_event_id,
                                     related_project_id=related_project_id, related_sponsor_id=related_sponsor_id))
    with SessionLocal() as db:
        return _dump(TaskOut, tasks_service.create_task(db, data))


@mcp.tool()
def update_task(task_id: int, title: str | None = None, description: str | None = None,
                priority: str | None = None, assignee_id: int | None = None,
                due_date: str | None = None, committee: str | None = None) -> dict:
    """Update a task card's fields (use move_task to change column/order)."""
    require_scope("write")
    data = _coerce(TaskUpdate, _data(title=title, description=description, priority=priority,
                                     assignee_id=assignee_id, due_date=due_date, committee=committee))
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.update_task(db, task_id, data), "task not found"))


@mcp.tool()
def move_task(task_id: int, status: str, position: int = 0) -> dict:
    """Move a task to a column (status) and position. Moving to 'Done' marks it complete."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.move_task(db, task_id, status=status, position=position),
                                       "task not found"))


# --------------------------------------------------------------------------- #
# meetings (+ AI notes)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_meetings(type: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    """List meetings (most recent first)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(MeetingOut, meetings_service.list_meetings(db, type=type, status=status, limit=limit))


@mcp.tool()
def create_meeting(title: str, type: str | None = None, meeting_date: str | None = None,
                   location: str | None = None, attendees: list[str] | None = None,
                   transcript: str | None = None, related_event_id: int | None = None) -> dict:
    """Create a meeting record. Pass a transcript now, or add it later before generating notes."""
    require_scope("write")
    data = _coerce(MeetingCreate, _data(title=title, type=type, meeting_date=meeting_date,
                                        location=location, attendees=attendees, transcript=transcript,
                                        related_event_id=related_event_id))
    with SessionLocal() as db:
        return _dump(MeetingOut, meetings_service.create_meeting(db, data))


@mcp.tool()
def generate_meeting_notes(meeting_id: int, transcript: str | None = None) -> dict:
    """Summarise a meeting's transcript into notes + action items using AI.

    Needs the 'trigger' scope (it spends on the LLM) and is subject to the daily
    LLM cap. If `transcript` is given it replaces the stored one. Also creates a
    MeetingNotes document.
    """
    ctx = require_scope("trigger")
    with SessionLocal() as db:
        try:
            limiter.check_and_count_trigger(db, key_id=ctx.id)
        except HTTPException as exc:
            raise ValueError(exc.detail)
        meeting = _require(meetings_service.get_meeting(db, meeting_id), "meeting not found")
        try:
            _gen_notes(db, meeting, transcript=transcript)
        except LLMError as exc:
            raise ValueError(f"LLM error: {exc}")
        return _dump(MeetingOut, meeting)


# --------------------------------------------------------------------------- #
# documents (Notion-style)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_documents(type: str | None = None, status: str | None = None,
                   assignee_id: int | None = None, top_level: bool = False, limit: int = 50) -> list[dict]:
    """List documents. type is Note|MeetingNotes|SponsorDoc|Deliverable|Policy|General."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(DocumentOut, documents_service.list_documents(
            db, type=type, status=status, assignee_id=assignee_id, top_level=top_level, limit=limit))


@mcp.tool()
def get_document(document_id: int) -> dict:
    """Get one document including its full markdown content."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(DocumentOut, _require(documents_service.get_document(db, document_id),
                                           "document not found"))


@mcp.tool()
def create_document(title: str, content: str | None = None, type: str | None = None,
                    status: str | None = None, parent_id: int | None = None,
                    assignee_id: int | None = None, related_event_id: int | None = None,
                    related_sponsor_id: int | None = None, related_project_id: int | None = None,
                    related_meeting_id: int | None = None) -> dict:
    """Create a document (markdown `content`). Use type=Deliverable + assignee_id for a per-person deliverable."""
    require_scope("write")
    data = _coerce(DocumentCreate, _data(title=title, content=content, type=type, status=status,
                                         parent_id=parent_id, assignee_id=assignee_id,
                                         related_event_id=related_event_id, related_sponsor_id=related_sponsor_id,
                                         related_project_id=related_project_id, related_meeting_id=related_meeting_id))
    with SessionLocal() as db:
        return _dump(DocumentOut, documents_service.create_document(db, data))


@mcp.tool()
def update_document(document_id: int, title: str | None = None, content: str | None = None,
                    status: str | None = None, assignee_id: int | None = None) -> dict:
    """Update a document's title, markdown content, status, or assignee."""
    require_scope("write")
    data = _coerce(DocumentUpdate, _data(title=title, content=content, status=status, assignee_id=assignee_id))
    with SessionLocal() as db:
        return _dump(DocumentOut, _require(documents_service.update_document(db, document_id, data),
                                           "document not found"))


# --------------------------------------------------------------------------- #
# sponsors (CRM pipeline)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_sponsors(stage: str | None = None, tier: str | None = None, limit: int = 50) -> list[dict]:
    """List sponsorship leads/relationships."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(SponsorOut, sponsors_service.list_sponsors(db, stage=stage, tier=tier, limit=limit))


@mcp.tool()
def create_sponsor(organisation: str, stage: str | None = None, tier: str | None = None,
                   value_aud: float | None = None, contact_email: str | None = None,
                   website: str | None = None, next_action: str | None = None,
                   next_action_date: str | None = None, notes: str | None = None) -> dict:
    """Add a sponsorship lead."""
    require_scope("write")
    data = _coerce(SponsorCreate, _data(organisation=organisation, stage=stage, tier=tier,
                                        value_aud=value_aud, contact_email=contact_email, website=website,
                                        next_action=next_action, next_action_date=next_action_date, notes=notes))
    with SessionLocal() as db:
        return _dump(SponsorOut, sponsors_service.create_sponsor(db, data))


@mcp.tool()
def update_sponsor(sponsor_id: int, stage: str | None = None, tier: str | None = None,
                   value_aud: float | None = None, dusa_approved: bool | None = None,
                   next_action: str | None = None, next_action_date: str | None = None,
                   last_contact_date: str | None = None, notes: str | None = None) -> dict:
    """Advance a sponsor through the pipeline (stage, next action, approval, etc.)."""
    require_scope("write")
    data = _coerce(SponsorUpdate, _data(stage=stage, tier=tier, value_aud=value_aud,
                                        dusa_approved=dusa_approved, next_action=next_action,
                                        next_action_date=next_action_date, last_contact_date=last_contact_date,
                                        notes=notes))
    with SessionLocal() as db:
        return _dump(SponsorOut, _require(sponsors_service.update_sponsor(db, sponsor_id, data),
                                          "sponsor not found"))


# --------------------------------------------------------------------------- #
# people (committee + contacts)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_people(type: str | None = None, committee: str | None = None,
                search: str | None = None, limit: int = 50) -> list[dict]:
    """List people (exec, committee, contacts) — used to find assignee ids."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(PersonOut, people_service.list_people(
            db, type=type, committee=committee, search=search, limit=limit))


@mcp.tool()
def create_person(name: str, type: str | None = None, committee: str | None = None,
                  role_title: str | None = None, email: str | None = None,
                  status: str | None = None) -> dict:
    """Add a person (committee member or external contact)."""
    require_scope("write")
    data = _coerce(PersonCreate, _data(name=name, type=type, committee=committee,
                                       role_title=role_title, email=email, status=status))
    with SessionLocal() as db:
        return _dump(PersonOut, people_service.create_person(db, data))


# The mounted Starlette app (its lifespan runs the session manager — see main.py).
mcp_app = mcp.streamable_http_app()
