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
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.core.llm import LLMError
from app.core.ratelimit import limiter
from app.db import SessionLocal

# Feature services + schemas (the same layer the REST API uses)
from app.features.attachments import service as attachments_service
from app.features.attachments.schemas import AttachmentOut
from app.features.documents import service as documents_service
from app.features.documents.schemas import DocumentCreate, DocumentOut, DocumentUpdate
from app.features.events import relations as events_relations
from app.features.events import service as events_service
from app.features.events.schemas import (
    EventConnectionOut,
    EventCreate,
    EventOut,
    EventPartnerOut,
    EventSpeakerCreate,
    EventSpeakerOut,
    EventSpeakerUpdate,
    EventSponsorOut,
    EventUpdate,
)
from app.features.finance import service as finance_service
from app.features.finance.schemas import EventBudgetOut, ReportOut, TransactionOut
from app.features.media import service as media_service
from app.features.media.schemas import MediaOut
from app.features.meetings import service as meetings_service
from app.features.meetings.notes import generate_meeting_notes as _gen_notes
from app.features.meetings.schemas import MeetingCreate, MeetingOut, MeetingUpdate
from app.features.links import service as links_service
from app.features.links.schemas import (
    LinkCreate,
    LinkOut,
    LinkProfileOut,
    LinkProfileUpdate,
    LinkUpdate,
)
from app.features.members import service as members_service
from app.features.members.schemas import MemberOut, MemberTrendPoint
from app.features.partners import service as partners_service
from app.features.partners.schemas import PartnerCreate, PartnerOut, PartnerUpdate
from app.features.people import service as people_service
from app.features.people.schemas import PersonCreate, PersonOut, PersonUpdate
from app.features.projects import service as projects_service
from app.features.projects.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.features.reviews import service as reviews_service
from app.features.reviews.schemas import ReviewFormOut, ReviewResponsesOut
from app.features.reviews.tally import TallyError, TallyNotConfigured
from app.features.sponsor_leads import service as leads_service
from app.features.sponsor_leads.schemas import SponsorLeadOut, SponsorLeadUpdate
from app.features.sponsor_packages import service as packages_service
from app.features.sponsor_packages.schemas import (
    SponsorPackageCreate,
    SponsorPackageOut,
    SponsorPackageUpdate,
)
from app.features.sponsors import contacts as sponsor_contacts
from app.features.sponsors import service as sponsors_service
from app.features.sponsors.schemas import (
    SponsorContactCreate,
    SponsorContactOut,
    SponsorContactUpdate,
    SponsorCreate,
    SponsorOut,
    SponsorUpdate,
)
from app.features.tasks import service as tasks_service
from app.features.tasks.schemas import (
    BoardCreate,
    BoardOut,
    BoardUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)

from .auth import current_key, require_scope

_INSTRUCTIONS = """DSEC committee workspace. Read and update the club's members,
finances, events (incl. speakers, sponsor & partner line-ups), community
projects, task boards, meetings, documents, sponsors (pipeline, packages, leads,
contacts) and partner orgs. Call `whoami` first to see what your API key is
allowed to do. Reads need the 'read' scope; creating/updating needs 'write'; AI
meeting-notes needs 'trigger'. Image/file uploads happen in the dashboard, not
here — the media/attachment tools are read-only listings. Dates are ISO
YYYY-MM-DD; events and projects are drafts until you set is_public=true."""

def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection for the streamable-HTTP transport.

    FastMCP auto-applies a localhost-only Host allowlist when its host is
    127.0.0.1 (the default), which 421s every request to a remote deploy
    (Host: api.dsec.club). dsec-api is a remote, token-authenticated HTTPS API,
    so we override that: an explicit MCP_ALLOWED_HOSTS list enables protection
    scoped to those hosts; blank (default) disables the check (a DNS-rebinding
    attacker can't supply a valid bearer token, and CORS already pins origins).
    """
    hosts = [h.strip() for h in settings.MCP_ALLOWED_HOSTS.split(",") if h.strip()]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in hosts],
    )


mcp = FastMCP(
    "DSEC",
    stateless_http=True,
    streamable_http_path="/",
    instructions=_INSTRUCTIONS,
    transport_security=_transport_security(),
)


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


@mcp.tool()
def get_member(member_id: int) -> dict:
    """Get one club member by id (from the weekly DUSA roster)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(MemberOut, _require(members_service.get_member(db, member_id), "member not found"))


# --------------------------------------------------------------------------- #
# finance
# --------------------------------------------------------------------------- #

@mcp.tool()
def finance_summary() -> dict:
    """Current finances: opening/income/expense/closing balance + total event budgets/grants."""
    require_scope("read:finance")
    with SessionLocal() as db:
        return finance_service.finances_summary(db)


@mcp.tool()
def list_transactions(kind: str | None = None, limit: int = 50) -> list[dict]:
    """List ledger lines from the latest P&L. kind is income | expense | balance."""
    require_scope("read:finance")
    with SessionLocal() as db:
        return _dump_list(TransactionOut, finance_service.list_transactions(db, kind=kind, limit=limit))


@mcp.tool()
def list_finance_reports(limit: int = 20) -> list[dict]:
    """List the imported P&L reports (one per weekly DUSA finance import), newest
    first — each with its opening/closing balance, income/expense totals and line
    count. The most recent (is_current=true) is what finance_summary reflects."""
    require_scope("read:finance")
    with SessionLocal() as db:
        return _dump_list(ReportOut, finance_service.list_reports(db, limit=limit))


@mcp.tool()
def set_event_budget(event_id: int, budget_aud: float, grant_rate: float = 0.5) -> dict:
    """Set an event's budget and auto-apply the grant (default 50% of budget)."""
    require_scope("write:finance")
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
def get_event(event_id: int) -> dict:
    """Get one event by id (full detail — including draft/published state)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(EventOut, _require(events_service.get_event(db, event_id), "event not found"))


@mcp.tool()
def create_event(name: str, type: str | None = None, status: str | None = None,
                 start_date: str | None = None, end_date: str | None = None,
                 trimester: str | None = None, format: str | None = None,
                 venue: str | None = None, committee: str | None = None,
                 event_lead_id: int | None = None, ticket_url: str | None = None,
                 ticket_tiers: list[dict] | None = None, food_provided: bool | None = None,
                 external_guests: bool | None = None, expected_attendance: int | None = None,
                 actual_attendance: int | None = None, description: str | None = None,
                 dusa_required: bool | None = None, dusa_deadline: str | None = None,
                 dusa_submission_status: str | None = None, support_types: list[str] | None = None,
                 partner_org: str | None = None, related_sponsor_id: int | None = None,
                 is_public: bool | None = None, co_owner_ids: list[int] | None = None) -> dict:
    """Create an event. Dates are ISO YYYY-MM-DD. `ticket_tiers` is tiered pricing:
    a list of {"label": str, "price": number | null} (price 0 = free, null = unset).
    `description` is free-form Markdown shown on the public website. New events are
    drafts — set is_public=true to publish to the public website. `support_types`
    + `partner_org` capture an in-kind/partner-run collaboration (no money).
    `event_lead_id` is the primary lead; `co_owner_ids` adds extra leads/owners."""
    require_scope("write")
    data = _coerce(EventCreate, _data(name=name, type=type, status=status, start_date=start_date,
                                      end_date=end_date, trimester=trimester, format=format,
                                      venue=venue, committee=committee, event_lead_id=event_lead_id,
                                      ticket_url=ticket_url, ticket_tiers=ticket_tiers,
                                      food_provided=food_provided, external_guests=external_guests,
                                      expected_attendance=expected_attendance,
                                      actual_attendance=actual_attendance, description=description,
                                      dusa_required=dusa_required, dusa_deadline=dusa_deadline,
                                      dusa_submission_status=dusa_submission_status,
                                      support_types=support_types, partner_org=partner_org,
                                      related_sponsor_id=related_sponsor_id, is_public=is_public,
                                      co_owner_ids=co_owner_ids))
    with SessionLocal() as db:
        return _dump(EventOut, events_service.create_event(db, data))


@mcp.tool()
def update_event(event_id: int, name: str | None = None, type: str | None = None,
                 status: str | None = None, start_date: str | None = None, end_date: str | None = None,
                 trimester: str | None = None, format: str | None = None,
                 venue: str | None = None, committee: str | None = None,
                 event_lead_id: int | None = None, ticket_url: str | None = None,
                 ticket_tiers: list[dict] | None = None, food_provided: bool | None = None,
                 external_guests: bool | None = None, expected_attendance: int | None = None,
                 actual_attendance: int | None = None, description: str | None = None,
                 dusa_required: bool | None = None, dusa_deadline: str | None = None,
                 dusa_submission_status: str | None = None, support_types: list[str] | None = None,
                 partner_org: str | None = None, related_sponsor_id: int | None = None,
                 is_public: bool | None = None, co_owner_ids: list[int] | None = None) -> dict:
    """Update an event (only the fields you pass change). `ticket_tiers` is tiered
    pricing: a list of {"label": str, "price": number | null} (price 0 = free).
    `description` is free-form Markdown shown on the public website. Set
    is_public=true to publish (or false to unpublish/return to draft).
    `co_owner_ids` replaces the extra leads/owners beyond the primary
    `event_lead_id` (pass [] to clear them; omit to leave unchanged)."""
    require_scope("write")
    data = _coerce(EventUpdate, _data(name=name, type=type, status=status, start_date=start_date,
                                      end_date=end_date, trimester=trimester, format=format,
                                      venue=venue, committee=committee, event_lead_id=event_lead_id,
                                      ticket_url=ticket_url, ticket_tiers=ticket_tiers,
                                      food_provided=food_provided, external_guests=external_guests,
                                      expected_attendance=expected_attendance,
                                      actual_attendance=actual_attendance, description=description,
                                      dusa_required=dusa_required, dusa_deadline=dusa_deadline,
                                      dusa_submission_status=dusa_submission_status,
                                      support_types=support_types, partner_org=partner_org,
                                      related_sponsor_id=related_sponsor_id, is_public=is_public,
                                      co_owner_ids=co_owner_ids))
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


@mcp.tool()
def archive_event(event_id: int) -> dict:
    """Soft-delete (archive) an event. It disappears from the dashboard and the
    public site but is never hard-deleted (keeps the audit trail)."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(EventOut, _require(events_service.archive_event(db, event_id), "event not found"))


# --------------------------------------------------------------------------- #
# event line-up: speakers, sponsor links, partner links
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_event_speakers(event_id: int) -> list[dict]:
    """List an event's speakers (name/title/bio, in display order)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(EventSpeakerOut, events_relations.list_speakers(db, event_id))


@mcp.tool()
def add_event_speaker(event_id: int, name: str | None = None, person_id: int | None = None,
                      title: str | None = None, bio: str | None = None,
                      sort_order: int | None = None) -> dict:
    """Add a speaker to an event. Give a `person_id` to link a roster person
    (reuses their headshot) OR a free-text `name` for an external guest."""
    require_scope("write")
    data = _coerce(EventSpeakerCreate, _data(name=name, person_id=person_id, title=title,
                                             bio=bio, sort_order=sort_order))
    with SessionLocal() as db:
        _require(events_service.get_event(db, event_id), "event not found")
        try:
            return _dump(EventSpeakerOut, events_relations.add_speaker(db, event_id, data))
        except ValueError as exc:
            raise ValueError(str(exc))


@mcp.tool()
def update_event_speaker(speaker_id: int, name: str | None = None, person_id: int | None = None,
                         title: str | None = None, bio: str | None = None,
                         sort_order: int | None = None) -> dict:
    """Update a speaker's details (only the fields you pass change)."""
    require_scope("write")
    data = _coerce(EventSpeakerUpdate, _data(name=name, person_id=person_id, title=title,
                                             bio=bio, sort_order=sort_order))
    with SessionLocal() as db:
        return _dump(EventSpeakerOut, _require(events_relations.update_speaker(db, speaker_id, data),
                                               "speaker not found"))


@mcp.tool()
def remove_event_speaker(speaker_id: int) -> dict:
    """Remove a speaker from an event (soft-archive)."""
    require_scope("write")
    with SessionLocal() as db:
        _require(events_relations.remove_speaker(db, speaker_id), "speaker not found")
        return {"removed": True, "speaker_id": speaker_id}


@mcp.tool()
def list_event_sponsors(event_id: int) -> list[dict]:
    """List the sponsors linked to an event (its sponsor wall)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(EventSponsorOut, events_relations.list_event_sponsors(db, event_id))


@mcp.tool()
def link_event_sponsor(event_id: int, sponsor_id: int, tier: str | None = None,
                       sort_order: int | None = None) -> dict:
    """Link a sponsor to an event so its logo shows on the event's sponsor wall.
    Idempotent — re-linking just updates the tier/order. `tier` is an optional
    per-event override."""
    require_scope("write")
    with SessionLocal() as db:
        _require(events_service.get_event(db, event_id), "event not found")
        row = events_relations.link_sponsor(db, event_id, sponsor_id, tier=tier, sort_order=sort_order)
        return _dump(EventSponsorOut, _require(row, "sponsor not found"))


@mcp.tool()
def unlink_event_sponsor(event_id: int, sponsor_id: int) -> dict:
    """Remove a sponsor from an event's sponsor wall."""
    require_scope("write")
    with SessionLocal() as db:
        if not events_relations.unlink_sponsor(db, event_id, sponsor_id):
            raise ValueError("sponsor link not found")
        return {"unlinked": True, "event_id": event_id, "sponsor_id": sponsor_id}


@mcp.tool()
def list_event_partners(event_id: int) -> list[dict]:
    """List the partner orgs/clubs co-hosting an event."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(EventPartnerOut, events_relations.list_event_partners(db, event_id))


@mcp.tool()
def link_event_partner(event_id: int, partner_id: int, role: str | None = None,
                       sort_order: int | None = None) -> dict:
    """Link a partner (collaborator club/org) to an event. Idempotent. `role` is
    an optional per-event label (e.g. 'Co-host')."""
    require_scope("write")
    with SessionLocal() as db:
        _require(events_service.get_event(db, event_id), "event not found")
        row = events_relations.link_partner(db, event_id, partner_id, role=role, sort_order=sort_order)
        return _dump(EventPartnerOut, _require(row, "partner not found"))


@mcp.tool()
def unlink_event_partner(event_id: int, partner_id: int) -> dict:
    """Remove a partner from an event."""
    require_scope("write")
    with SessionLocal() as db:
        if not events_relations.unlink_partner(db, event_id, partner_id):
            raise ValueError("partner link not found")
        return {"unlinked": True, "event_id": event_id, "partner_id": partner_id}


def _connection_dict(link, other, event_id: int) -> dict:
    """Shape a (link, other_event) pair into a JSON dict, resolved relative to
    the event it was queried from."""
    return EventConnectionOut(
        id=link.id, event_id=event_id, other_event_id=other.id,
        other_event_name=other.name, other_event_status=other.status,
        other_event_start_date=other.start_date, label=link.label,
        created_at=link.created_at, updated_at=link.updated_at,
    ).model_dump(mode="json")


@mcp.tool()
def list_event_connections(event_id: int) -> list[dict]:
    """List events visibly connected to this one (symmetric, visual-only links
    used to show how events relate, e.g. a series or a kickoff→closing pair)."""
    require_scope("read")
    with SessionLocal() as db:
        _require(events_service.get_event(db, event_id), "event not found")
        return [
            _connection_dict(link, other, event_id)
            for link, other in events_relations.list_connections(db, event_id)
        ]


@mcp.tool()
def link_event_connection(event_id: int, other_event_id: int,
                          label: str | None = None) -> dict:
    """Connect two events so each shows the other as related. Idempotent — re-linking
    just updates the `label` (an optional relation tag, e.g. 'Series'). Purely
    visual: it changes no behaviour, just how events are shown to relate."""
    require_scope("write")
    with SessionLocal() as db:
        _require(events_service.get_event(db, event_id), "event not found")
        try:
            result = events_relations.link_connection(
                db, event_id, other_event_id, label=label
            )
        except ValueError as exc:
            raise ValueError(str(exc))
        link, other = _require(result, "event to connect not found")
        return _connection_dict(link, other, event_id)


@mcp.tool()
def unlink_event_connection(event_id: int, other_event_id: int) -> dict:
    """Remove the connection between two events (order-independent)."""
    require_scope("write")
    with SessionLocal() as db:
        if not events_relations.unlink_connection(db, event_id, other_event_id):
            raise ValueError("connection not found")
        return {"unlinked": True, "event_id": event_id, "other_event_id": other_event_id}


# --------------------------------------------------------------------------- #
# partners (collaborator clubs / orgs)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_partners(status: str | None = None, search: str | None = None, limit: int = 50) -> list[dict]:
    """List partner organisations / collaborator clubs. Optional `status` filters
    by pipeline stage: lead | contacted | active | inactive."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(
            PartnerOut, partners_service.list_partners(db, status=status, search=search, limit=limit)
        )


@mcp.tool()
def get_partner(partner_id: int) -> dict:
    """Get one partner org/club by id."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(PartnerOut, _require(partners_service.get_partner(db, partner_id), "partner not found"))


@mcp.tool()
def create_partner(name: str, website: str | None = None, email: str | None = None,
                   instagram: str | None = None, linkedin: str | None = None,
                   facebook: str | None = None, notes: str | None = None,
                   status: str | None = None) -> dict:
    """Add a partner org/club. Link it to events with `link_event_partner`.
    `status` is the pipeline stage (lead | contacted | active | inactive) and
    defaults to "lead" when omitted."""
    require_scope("write")
    data = _coerce(PartnerCreate, _data(name=name, website=website, email=email,
                                        instagram=instagram, linkedin=linkedin,
                                        facebook=facebook, notes=notes, status=status))
    with SessionLocal() as db:
        return _dump(PartnerOut, partners_service.create_partner(db, data))


@mcp.tool()
def update_partner(partner_id: int, name: str | None = None, website: str | None = None,
                   email: str | None = None, instagram: str | None = None,
                   linkedin: str | None = None, facebook: str | None = None,
                   notes: str | None = None, status: str | None = None) -> dict:
    """Update a partner org (only the fields you pass change). `status` moves the
    club along the pipeline: lead | contacted | active | inactive."""
    require_scope("write")
    data = _coerce(PartnerUpdate, _data(name=name, website=website, email=email,
                                        instagram=instagram, linkedin=linkedin,
                                        facebook=facebook, notes=notes, status=status))
    with SessionLocal() as db:
        return _dump(PartnerOut, _require(partners_service.update_partner(db, partner_id, data),
                                          "partner not found"))


@mcp.tool()
def archive_partner(partner_id: int) -> dict:
    """Soft-delete (archive) a partner org/club. It's hidden from the dashboard and
    public site but never hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(PartnerOut, _require(partners_service.archive_partner(db, partner_id),
                                          "partner not found"))


# --------------------------------------------------------------------------- #
# link tree (the public /links page: a profile header + a stack of buttons)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_links(include_hidden: bool = True, include_archived: bool = False,
               limit: int = 200) -> list[dict]:
    """List the buttons on the public link-tree page, in display order
    (display_order, then created_at). `include_hidden=False` drops links not
    currently shown publicly; `include_archived=True` includes soft-deleted ones."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(
            LinkOut,
            links_service.list_links(
                db, include_hidden=include_hidden, archived=include_archived, limit=limit
            ),
        )


@mcp.tool()
def get_link(link_id: int) -> dict:
    """Get one link-tree button by id."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(LinkOut, _require(links_service.get_link(db, link_id), "link not found"))


@mcp.tool()
def create_link(title: str, url: str, subtitle: str | None = None,
                icon: str | None = None, accent: str | None = None,
                display_order: int | None = None, is_visible: bool | None = None) -> dict:
    """Add a button to the public link-tree page. `url` is an absolute http(s)
    link or a relative path like /events. `icon` is a single emoji; `accent` is
    one of: blue, pink, yellow, mint, sky, violet, lime, coral (omit to
    auto-cycle by position). New links default to visible."""
    require_scope("write")
    data = _coerce(LinkCreate, _data(title=title, url=url, subtitle=subtitle, icon=icon,
                                     accent=accent, display_order=display_order,
                                     is_visible=is_visible))
    with SessionLocal() as db:
        return _dump(LinkOut, links_service.create_link(db, data))


@mcp.tool()
def update_link(link_id: int, title: str | None = None, url: str | None = None,
                subtitle: str | None = None, icon: str | None = None,
                accent: str | None = None, display_order: int | None = None,
                is_visible: bool | None = None) -> dict:
    """Update a link-tree button (only the fields you pass change). Use
    `is_visible` to show/hide it on the public page without deleting it."""
    require_scope("write")
    data = _coerce(LinkUpdate, _data(title=title, url=url, subtitle=subtitle, icon=icon,
                                     accent=accent, display_order=display_order,
                                     is_visible=is_visible))
    with SessionLocal() as db:
        return _dump(LinkOut, _require(links_service.update_link(db, link_id, data),
                                       "link not found"))


@mcp.tool()
def archive_link(link_id: int) -> dict:
    """Soft-delete (archive) a link-tree button. It's removed from the page but
    never hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(LinkOut, _require(links_service.archive_link(db, link_id),
                                       "link not found"))


@mcp.tool()
def reorder_links(ordered_ids: list[int]) -> list[dict]:
    """Reorder the link-tree buttons: pass every link id in the new top-to-bottom
    order. Each link's display_order is set to its index. Returns the new
    display-ordered list."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump_list(LinkOut, links_service.reorder_links(db, ordered_ids))


@mcp.tool()
def get_link_profile() -> dict:
    """Get the public link-tree page header (title, tagline, mascot)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(LinkProfileOut, links_service.get_profile(db))


@mcp.tool()
def update_link_profile(title: str | None = None, tagline: str | None = None,
                        mascot: str | None = None) -> dict:
    """Update the public link-tree page header. `mascot` is a PixelDuck sprite
    name (e.g. duck-mascot, duck-wave, duck-trophy)."""
    require_scope("write")
    data = _coerce(LinkProfileUpdate, _data(title=title, tagline=tagline, mascot=mascot))
    with SessionLocal() as db:
        return _dump(LinkProfileOut, links_service.update_profile(db, data))


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
def get_project(project_id: int) -> dict:
    """Get one community project by id (full detail — including draft/published state)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(ProjectOut, _require(projects_service.get_project(db, project_id),
                                          "project not found"))


@mcp.tool()
def create_project(name: str, summary: str | None = None, description: str | None = None,
                   status: str | None = None, category: str | None = None,
                   tech_tags: list[str] | None = None, lead_id: int | None = None,
                   repo_url: str | None = None, demo_url: str | None = None,
                   start_date: str | None = None, end_date: str | None = None,
                   related_event_id: int | None = None, notes: str | None = None,
                   is_public: bool | None = None, featured: bool | None = None,
                   co_owner_ids: list[int] | None = None) -> dict:
    """Create a community project. Set is_public=true to show it on the website
    (it's a draft until then); featured=true pins it. `tech_tags` is a list of
    strings; `notes` is internal-only (never shown publicly). `lead_id` is the
    primary lead; `co_owner_ids` adds extra owners (multi-lead)."""
    require_scope("write")
    data = _coerce(ProjectCreate, _data(name=name, summary=summary, description=description,
                                        status=status, category=category, tech_tags=tech_tags,
                                        lead_id=lead_id, repo_url=repo_url, demo_url=demo_url,
                                        start_date=start_date, end_date=end_date,
                                        related_event_id=related_event_id, notes=notes,
                                        is_public=is_public, featured=featured,
                                        co_owner_ids=co_owner_ids))
    with SessionLocal() as db:
        return _dump(ProjectOut, projects_service.create_project(db, data))


@mcp.tool()
def update_project(project_id: int, name: str | None = None, summary: str | None = None,
                   description: str | None = None, status: str | None = None,
                   category: str | None = None, tech_tags: list[str] | None = None,
                   lead_id: int | None = None, related_event_id: int | None = None,
                   start_date: str | None = None, end_date: str | None = None,
                   notes: str | None = None, is_public: bool | None = None,
                   featured: bool | None = None, repo_url: str | None = None,
                   demo_url: str | None = None, co_owner_ids: list[int] | None = None) -> dict:
    """Update a community project (only the fields you pass change). Set
    is_public=true/false to publish/unpublish. `co_owner_ids` replaces the extra
    owners beyond the primary `lead_id` (pass [] to clear them; omit to leave
    unchanged)."""
    require_scope("write")
    data = _coerce(ProjectUpdate, _data(name=name, summary=summary, description=description,
                                        status=status, category=category, tech_tags=tech_tags,
                                        lead_id=lead_id, related_event_id=related_event_id,
                                        start_date=start_date, end_date=end_date, notes=notes,
                                        is_public=is_public, featured=featured,
                                        repo_url=repo_url, demo_url=demo_url,
                                        co_owner_ids=co_owner_ids))
    with SessionLocal() as db:
        return _dump(ProjectOut, _require(projects_service.update_project(db, project_id, data),
                                          "project not found"))


@mcp.tool()
def archive_project(project_id: int) -> dict:
    """Soft-delete (archive) a community project. It's hidden from the dashboard and
    public site but never hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(ProjectOut, _require(projects_service.archive_project(db, project_id),
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
def update_board(board_id: int, name: str | None = None, description: str | None = None,
                 committee: str | None = None, columns: list[str] | None = None) -> dict:
    """Update a task board (only the fields you pass change). `columns` replaces the
    board's Trello-style list of column names."""
    require_scope("write")
    data = _coerce(BoardUpdate, _data(name=name, description=description, committee=committee,
                                      columns=columns))
    with SessionLocal() as db:
        return _dump(BoardOut, _require(tasks_service.update_board(db, board_id, data), "board not found"))


@mcp.tool()
def archive_board(board_id: int) -> dict:
    """Soft-delete (archive) a task board and hide it from the dashboard. The board
    is never hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(BoardOut, _require(tasks_service.archive_board(db, board_id), "board not found"))


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
def create_task(title: str, board_id: int | None = None, parent_task_id: int | None = None,
                status: str | None = None,
                description: str | None = None, priority: str | None = None,
                assignee_id: int | None = None, committee: str | None = None,
                due_date: str | None = None, related_event_id: int | None = None,
                related_project_id: int | None = None, related_sponsor_id: int | None = None,
                co_owner_ids: list[int] | None = None) -> dict:
    """Create a task card. status is the board column (defaults to Backlog).
    Pass parent_task_id to make it a subtask (one level only). `assignee_id` is
    the primary owner; `co_owner_ids` adds extra owners (multi-assignee)."""
    require_scope("write")
    data = _coerce(TaskCreate, _data(title=title, board_id=board_id, parent_task_id=parent_task_id,
                                     status=status,
                                     description=description, priority=priority, assignee_id=assignee_id,
                                     committee=committee, due_date=due_date, related_event_id=related_event_id,
                                     related_project_id=related_project_id, related_sponsor_id=related_sponsor_id,
                                     co_owner_ids=co_owner_ids))
    with SessionLocal() as db:
        return _dump(TaskOut, tasks_service.create_task(db, data))


@mcp.tool()
def update_task(task_id: int, title: str | None = None, description: str | None = None,
                priority: str | None = None, assignee_id: int | None = None,
                start_date: str | None = None, due_date: str | None = None,
                committee: str | None = None, board_id: int | None = None,
                parent_task_id: int | None = None,
                related_event_id: int | None = None, related_project_id: int | None = None,
                related_sponsor_id: int | None = None, co_owner_ids: list[int] | None = None) -> dict:
    """Update a task card's fields, including its cross-entity links
    (related_event_id / related_project_id / related_sponsor_id), which board
    it lives on, and its parent (parent_task_id). Use move_task to change
    column (status)/order. `co_owner_ids` replaces the extra owners beyond the
    primary `assignee_id` (pass [] to clear them; omit to leave unchanged)."""
    require_scope("write")
    data = _coerce(TaskUpdate, _data(title=title, description=description, priority=priority,
                                     assignee_id=assignee_id, start_date=start_date, due_date=due_date,
                                     committee=committee, board_id=board_id, parent_task_id=parent_task_id,
                                     related_event_id=related_event_id,
                                     related_project_id=related_project_id,
                                     related_sponsor_id=related_sponsor_id, co_owner_ids=co_owner_ids))
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.update_task(db, task_id, data), "task not found"))


@mcp.tool()
def move_task(task_id: int, status: str, position: int = 0) -> dict:
    """Move a task to a column (status) and position. Moving to 'Done' marks it complete."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.move_task(db, task_id, status=status, position=position),
                                       "task not found"))


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Get one task card by id (full detail, including its cross-entity links)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.get_task(db, task_id), "task not found"))


@mcp.tool()
def archive_task(task_id: int) -> dict:
    """Soft-delete (archive) a task card. It's removed from its board but never
    hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(TaskOut, _require(tasks_service.archive_task(db, task_id), "task not found"))


# --------------------------------------------------------------------------- #
# meetings (+ AI notes)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_meetings(type: str | None = None, committee: str | None = None,
                  status: str | None = None, limit: int = 50) -> list[dict]:
    """List meetings (most recent first). Pass `committee` to scope to one committee's meetings."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(MeetingOut, meetings_service.list_meetings(
            db, type=type, committee=committee, status=status, limit=limit))


@mcp.tool()
def get_meeting(meeting_id: int) -> dict:
    """Get one meeting by id, including its transcript, AI summary, notes and
    action items."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(MeetingOut, _require(meetings_service.get_meeting(db, meeting_id),
                                          "meeting not found"))


@mcp.tool()
def create_meeting(title: str, type: str | None = None, committee: str | None = None,
                   meeting_date: str | None = None, meeting_time: str | None = None,
                   location: str | None = None, attendees: list[str] | None = None,
                   transcript: str | None = None, related_event_id: int | None = None,
                   agenda_items: list[dict] | None = None) -> dict:
    """Create a meeting record. Pass a transcript now, or add it later before generating notes.

    `meeting_date` is ISO YYYY-MM-DD; `meeting_time` is an optional local start time
    as "HH:MM" (24h). `committee` scopes the meeting (and any notes generated from
    it) to one committee. `agenda_items` optionally sets the pre-meeting agenda at
    creation time — a list of {title, owner_person_id?, duration_minutes?, notes?,
    related_task_id?, related_event_id?} in display order (see set_meeting_agenda).
    It starts as a private draft until you call share_meeting_agenda."""
    require_scope("write")
    data = _coerce(MeetingCreate, _data(title=title, type=type, committee=committee,
                                        meeting_date=meeting_date, meeting_time=meeting_time,
                                        location=location, attendees=attendees, transcript=transcript,
                                        related_event_id=related_event_id, agenda_items=agenda_items))
    with SessionLocal() as db:
        return _dump(MeetingOut, meetings_service.create_meeting(db, data))


@mcp.tool()
def update_meeting(meeting_id: int, title: str | None = None, type: str | None = None,
                   committee: str | None = None, meeting_date: str | None = None,
                   meeting_time: str | None = None,
                   location: str | None = None, attendees: list[str] | None = None,
                   transcript: str | None = None, summary: str | None = None,
                   notes: str | None = None, action_items: list[str] | None = None,
                   status: str | None = None, related_event_id: int | None = None) -> dict:
    """Update a meeting (only the fields you pass change). Use this to edit a
    transcript, set the `meeting_date`/`meeting_time` ("HH:MM" 24h), or hand-write
    `summary` / `notes` / `action_items` instead of generating them with
    generate_meeting_notes."""
    require_scope("write")
    data = _coerce(MeetingUpdate, _data(title=title, type=type, committee=committee,
                                        meeting_date=meeting_date, meeting_time=meeting_time,
                                        location=location,
                                        attendees=attendees, transcript=transcript, summary=summary,
                                        notes=notes, action_items=action_items, status=status,
                                        related_event_id=related_event_id))
    with SessionLocal() as db:
        return _dump(MeetingOut, _require(meetings_service.update_meeting(db, meeting_id, data),
                                          "meeting not found"))


@mcp.tool()
def archive_meeting(meeting_id: int) -> dict:
    """Soft-delete (archive) a meeting. It's hidden from the dashboard but never
    hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(MeetingOut, _require(meetings_service.archive_meeting(db, meeting_id),
                                          "meeting not found"))


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
# meeting agendas (pre-meeting, shared read-only with invitees)
# --------------------------------------------------------------------------- #

@mcp.tool()
def get_meeting_agenda(meeting_id: int) -> dict:
    """Get a meeting's pre-meeting agenda: the ordered items (each with its title,
    owner, duration and any linked task/event), the total estimated duration, and
    the share state (draft | shared | locked, plus the public link if shared)."""
    require_scope("read")
    with SessionLocal() as db:
        meeting = _require(meetings_service.get_meeting(db, meeting_id), "meeting not found")
        return meetings_service.agenda_view(meeting).model_dump(mode="json")


@mcp.tool()
def set_meeting_agenda(meeting_id: int, items: list[dict]) -> dict:
    """Replace a meeting's full agenda. `items` is the complete ordered list — to
    add, edit, remove or reorder, send the whole list in the order you want it.

    Each item: {title (required), owner_person_id?, duration_minutes?, notes?
    (markdown), related_task_id?, related_event_id?}. Keep an item's existing `id`
    to preserve it; new items get an id automatically. Owner/task/event ids are
    validated against People/Tasks/Events. A locked agenda can't be edited."""
    require_scope("write")
    with SessionLocal() as db:
        try:
            meeting = meetings_service.set_meeting_agenda(db, meeting_id, items)
        except meetings_service.AgendaLockedError as exc:
            raise ValueError(str(exc))
        # _validate_and_normalise raises ValueError for unknown owner/task/event
        # ids; that already surfaces to the MCP client as a clean tool error.
        meeting = _require(meeting, "meeting not found")
        return meetings_service.agenda_view(meeting).model_dump(mode="json")


@mcp.tool()
def share_meeting_agenda(meeting_id: int, confirm: bool = False) -> dict:
    """Share a meeting's agenda with invitees: marks it 'shared' and returns a
    stable, PUBLIC, no-auth read-only link (share_url). Idempotent — the link
    never changes once minted.

    This publishes the agenda externally, so it's gated: call with confirm=true to
    actually share. Without confirm it just tells you what would happen."""
    require_scope("write")
    if not confirm:
        raise ValueError(
            "share_meeting_agenda publishes a public, no-auth link to this agenda. "
            "Re-call with confirm=true to share it and get the link."
        )
    with SessionLocal() as db:
        meeting = _require(meetings_service.share_meeting_agenda(db, meeting_id),
                           "meeting not found")
        return meetings_service.agenda_view(meeting).model_dump(mode="json")


@mcp.tool()
def lock_meeting_agenda(meeting_id: int) -> dict:
    """Freeze a meeting's agenda once the meeting starts (status -> locked). It
    stays publicly viewable at its share link but can no longer be edited."""
    require_scope("write")
    with SessionLocal() as db:
        meeting = _require(meetings_service.lock_meeting_agenda(db, meeting_id),
                           "meeting not found")
        return meetings_service.agenda_view(meeting).model_dump(mode="json")


# --------------------------------------------------------------------------- #
# documents (Notion-style)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_documents(type: str | None = None, status: str | None = None,
                   assignee_id: int | None = None, related_task_id: int | None = None,
                   top_level: bool = False, limit: int = 50) -> list[dict]:
    """List documents. type is Note|MeetingNotes|SponsorDoc|Deliverable|Policy|General.

    Pass `related_task_id` to list the documents linked to a given task."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(DocumentOut, documents_service.list_documents(
            db, type=type, status=status, assignee_id=assignee_id,
            related_task_id=related_task_id, top_level=top_level, limit=limit))


@mcp.tool()
def get_document(document_id: int) -> dict:
    """Get one document including its full markdown content."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(DocumentOut, _require(documents_service.get_document(db, document_id),
                                           "document not found"))


@mcp.tool()
def create_document(title: str, content: str | None = None, type: str | None = None,
                    committee: str | None = None,
                    status: str | None = None, parent_id: int | None = None,
                    assignee_id: int | None = None, related_event_id: int | None = None,
                    related_sponsor_id: int | None = None, related_project_id: int | None = None,
                    related_meeting_id: int | None = None, related_task_id: int | None = None) -> dict:
    """Create a document (markdown `content`). Use type=Deliverable + assignee_id for a per-person deliverable.

    `committee` scopes the document to one committee (notes visibility).
    `related_task_id` links the doc to a task (e.g. a spec or brief for that task)."""
    require_scope("write")
    data = _coerce(DocumentCreate, _data(title=title, content=content, type=type, committee=committee,
                                         status=status,
                                         parent_id=parent_id, assignee_id=assignee_id,
                                         related_event_id=related_event_id, related_sponsor_id=related_sponsor_id,
                                         related_project_id=related_project_id, related_meeting_id=related_meeting_id,
                                         related_task_id=related_task_id))
    with SessionLocal() as db:
        return _dump(DocumentOut, documents_service.create_document(db, data))


@mcp.tool()
def update_document(document_id: int, title: str | None = None, content: str | None = None,
                    status: str | None = None, assignee_id: int | None = None,
                    related_task_id: int | None = None) -> dict:
    """Update a document's title, markdown content, status, or assignee.

    Pass `related_task_id` to link the doc to a task (or 0 to clear the link)."""
    require_scope("write")
    data = _coerce(DocumentUpdate, _data(title=title, content=content, status=status,
                                         assignee_id=assignee_id,
                                         related_task_id=related_task_id or None))
    # 0 is the explicit "unlink" sentinel; _data drops None, so re-inject a NULL.
    if related_task_id == 0:
        data["related_task_id"] = None
    with SessionLocal() as db:
        return _dump(DocumentOut, _require(documents_service.update_document(db, document_id, data),
                                           "document not found"))


@mcp.tool()
def archive_document(document_id: int) -> dict:
    """Soft-delete (archive) a document. It's hidden from the dashboard but never
    hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(DocumentOut, _require(documents_service.archive_document(db, document_id),
                                           "document not found"))


# --------------------------------------------------------------------------- #
# sponsors (CRM pipeline)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_sponsors(stage: str | None = None, tier: str | None = None, limit: int = 50) -> list[dict]:
    """List sponsorship leads/relationships."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump_list(SponsorOut, sponsors_service.list_sponsors(db, stage=stage, tier=tier, limit=limit))


@mcp.tool()
def get_sponsor(sponsor_id: int) -> dict:
    """Get one sponsor / pipeline relationship by id (full detail)."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump(SponsorOut, _require(sponsors_service.get_sponsor(db, sponsor_id), "sponsor not found"))


@mcp.tool()
def create_sponsor(organisation: str, stage: str | None = None, tier: str | None = None,
                   relationship_type: str | None = None, value_aud: float | None = None,
                   support_types: list[str] | None = None, contact_person_id: int | None = None,
                   contact_email: str | None = None, website: str | None = None,
                   next_action: str | None = None, next_action_date: str | None = None,
                   show_on_website: bool | None = None, notes: str | None = None) -> dict:
    """Add a sponsorship lead. `relationship_type` is 'Sponsor' (gives money) or
    'Partner' (in-kind only); `support_types` is what they provide (e.g.
    ["Cash","Venue"]). Set show_on_website=true to put a confirmed sponsor (with
    its uploaded logo) on the public sponsor wall."""
    require_scope("write:sponsors")
    data = _coerce(SponsorCreate, _data(organisation=organisation, stage=stage, tier=tier,
                                        relationship_type=relationship_type, value_aud=value_aud,
                                        support_types=support_types, contact_person_id=contact_person_id,
                                        contact_email=contact_email, website=website,
                                        next_action=next_action, next_action_date=next_action_date,
                                        show_on_website=show_on_website, notes=notes))
    with SessionLocal() as db:
        return _dump(SponsorOut, sponsors_service.create_sponsor(db, data))


@mcp.tool()
def update_sponsor(sponsor_id: int, organisation: str | None = None, stage: str | None = None,
                   tier: str | None = None, relationship_type: str | None = None,
                   value_aud: float | None = None, support_types: list[str] | None = None,
                   contact_person_id: int | None = None, contact_email: str | None = None,
                   website: str | None = None, dusa_approved: bool | None = None,
                   show_on_website: bool | None = None, next_action: str | None = None,
                   next_action_date: str | None = None, last_contact_date: str | None = None,
                   notes: str | None = None) -> dict:
    """Advance a sponsor through the pipeline (stage, next action, approval, etc.)
    or publish it (show_on_website). Only the fields you pass change."""
    require_scope("write:sponsors")
    data = _coerce(SponsorUpdate, _data(organisation=organisation, stage=stage, tier=tier,
                                        relationship_type=relationship_type, value_aud=value_aud,
                                        support_types=support_types, contact_person_id=contact_person_id,
                                        contact_email=contact_email, website=website,
                                        dusa_approved=dusa_approved, show_on_website=show_on_website,
                                        next_action=next_action, next_action_date=next_action_date,
                                        last_contact_date=last_contact_date, notes=notes))
    with SessionLocal() as db:
        return _dump(SponsorOut, _require(sponsors_service.update_sponsor(db, sponsor_id, data),
                                          "sponsor not found"))


@mcp.tool()
def archive_sponsor(sponsor_id: int) -> dict:
    """Soft-delete (archive) a sponsor / pipeline relationship. It's removed from
    the dashboard and the public sponsor wall but never hard-deleted. Confirm with
    the human first."""
    require_scope("write:sponsors")
    with SessionLocal() as db:
        return _dump(SponsorOut, _require(sponsors_service.archive_sponsor(db, sponsor_id),
                                          "sponsor not found"))


@mcp.tool()
def list_sponsor_contacts(sponsor_id: int) -> list[dict]:
    """List the individual people attached to a sponsorship (organiser, signatory, …)."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump_list(SponsorContactOut, sponsor_contacts.list_contacts(db, sponsor_id))


@mcp.tool()
def add_sponsor_contact(sponsor_id: int, name: str | None = None, person_id: int | None = None,
                        role: str | None = None, email: str | None = None,
                        phone: str | None = None, notes: str | None = None,
                        sort_order: int | None = None) -> dict:
    """Attach a contact to a sponsor. Give a `person_id` to link a roster person
    OR a free-text `name`. `role` is Organiser/Contact/Signatory/Other."""
    require_scope("write:sponsors")
    data = _coerce(SponsorContactCreate, _data(name=name, person_id=person_id, role=role,
                                               email=email, phone=phone, notes=notes,
                                               sort_order=sort_order))
    with SessionLocal() as db:
        _require(sponsors_service.get_sponsor(db, sponsor_id), "sponsor not found")
        try:
            return _dump(SponsorContactOut, sponsor_contacts.add_contact(db, sponsor_id, data))
        except ValueError as exc:
            raise ValueError(str(exc))


@mcp.tool()
def update_sponsor_contact(contact_id: int, name: str | None = None, person_id: int | None = None,
                           role: str | None = None, email: str | None = None,
                           phone: str | None = None, notes: str | None = None,
                           sort_order: int | None = None) -> dict:
    """Update a sponsor contact (only the fields you pass change)."""
    require_scope("write:sponsors")
    data = _coerce(SponsorContactUpdate, _data(name=name, person_id=person_id, role=role,
                                               email=email, phone=phone, notes=notes,
                                               sort_order=sort_order))
    with SessionLocal() as db:
        return _dump(SponsorContactOut, _require(sponsor_contacts.update_contact(db, contact_id, data),
                                                 "contact not found"))


@mcp.tool()
def remove_sponsor_contact(contact_id: int) -> dict:
    """Remove a contact from a sponsor (soft-archive)."""
    require_scope("write:sponsors")
    with SessionLocal() as db:
        _require(sponsor_contacts.remove_contact(db, contact_id), "contact not found")
        return {"removed": True, "contact_id": contact_id}


# --------------------------------------------------------------------------- #
# sponsor packages (public-facing tier definitions) + inbound leads
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_sponsor_packages() -> list[dict]:
    """List the sponsorship packages/tiers shown on the public website."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump_list(SponsorPackageOut, packages_service.list_packages(db))


@mcp.tool()
def get_sponsor_package(package_id: int) -> dict:
    """Get one sponsorship package/tier by id."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump(SponsorPackageOut, _require(packages_service.get_package(db, package_id),
                                                 "package not found"))


@mcp.tool()
def create_sponsor_package(name: str, pitch: str | None = None, price: str | None = None,
                           includes: list[str] | None = None, featured: bool | None = None,
                           is_visible: bool | None = None, display_order: int | None = None) -> dict:
    """Add a sponsorship package. `price` is free text (e.g. 'from $500'); `includes`
    is a list of perk strings. Hidden (is_visible=false) packages stay off the site."""
    require_scope("write:sponsors")
    data = _coerce(SponsorPackageCreate, _data(name=name, pitch=pitch, price=price, includes=includes,
                                               featured=featured, is_visible=is_visible,
                                               display_order=display_order))
    with SessionLocal() as db:
        return _dump(SponsorPackageOut, packages_service.create_package(db, data))


@mcp.tool()
def update_sponsor_package(package_id: int, name: str | None = None, pitch: str | None = None,
                           price: str | None = None, includes: list[str] | None = None,
                           featured: bool | None = None, is_visible: bool | None = None,
                           display_order: int | None = None) -> dict:
    """Update a sponsorship package (only the fields you pass change)."""
    require_scope("write:sponsors")
    data = _coerce(SponsorPackageUpdate, _data(name=name, pitch=pitch, price=price, includes=includes,
                                               featured=featured, is_visible=is_visible,
                                               display_order=display_order))
    with SessionLocal() as db:
        return _dump(SponsorPackageOut, _require(packages_service.update_package(db, package_id, data),
                                                 "package not found"))


@mcp.tool()
def delete_sponsor_package(package_id: int) -> dict:
    """Permanently delete a sponsorship package."""
    require_scope("write:sponsors")
    with SessionLocal() as db:
        if not packages_service.delete_package(db, package_id):
            raise ValueError("package not found")
        return {"deleted": True, "package_id": package_id}


@mcp.tool()
def list_sponsor_leads(status: str | None = None, limit: int = 50) -> list[dict]:
    """List inbound sponsorship leads from the website (pricing-unlock, enquiry
    forms, Cal.com bookings). status is new | contacted | converted | closed."""
    require_scope("read:sponsors")
    with SessionLocal() as db:
        return _dump_list(SponsorLeadOut, leads_service.list_leads(db, status=status, limit=limit))


@mcp.tool()
def update_sponsor_lead(lead_id: int, status: str | None = None, notes: str | None = None) -> dict:
    """Move an inbound lead through its pipeline (status) and add internal notes.
    status is new | contacted | converted | closed."""
    require_scope("write:sponsors")
    data = _coerce(SponsorLeadUpdate, _data(status=status, notes=notes))
    with SessionLocal() as db:
        return _dump(SponsorLeadOut, _require(leads_service.update_lead(db, lead_id, data),
                                              "lead not found"))


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
def get_person(person_id: int) -> dict:
    """Get one person by id (committee member or external contact)."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump(PersonOut, _require(people_service.get_person(db, person_id), "person not found"))


@mcp.tool()
def create_person(name: str, type: str | None = None, committee: str | None = None,
                  role_title: str | None = None, email: str | None = None,
                  status: str | None = None, notes: str | None = None, bio: str | None = None,
                  show_on_website: bool | None = None, display_order: int | None = None) -> dict:
    """Add a person (committee member or external contact). `bio` is a public
    one-liner; `notes` is internal-only. Set show_on_website=true to publish them
    on the website team grid (their headshot is uploaded in the dashboard)."""
    require_scope("write")
    data = _coerce(PersonCreate, _data(name=name, type=type, committee=committee,
                                       role_title=role_title, email=email, status=status,
                                       notes=notes, bio=bio, show_on_website=show_on_website,
                                       display_order=display_order))
    with SessionLocal() as db:
        return _dump(PersonOut, people_service.create_person(db, data))


@mcp.tool()
def update_person(person_id: int, name: str | None = None, type: str | None = None,
                  committee: str | None = None, role_title: str | None = None,
                  email: str | None = None, status: str | None = None, notes: str | None = None,
                  bio: str | None = None, show_on_website: bool | None = None,
                  display_order: int | None = None) -> dict:
    """Update a person (only the fields you pass change). Set show_on_website
    true/false to add/remove them from the public website team grid; `display_order`
    sorts that grid (lower first)."""
    require_scope("write")
    data = _coerce(PersonUpdate, _data(name=name, type=type, committee=committee,
                                       role_title=role_title, email=email, status=status,
                                       notes=notes, bio=bio, show_on_website=show_on_website,
                                       display_order=display_order))
    with SessionLocal() as db:
        return _dump(PersonOut, _require(people_service.update_person(db, person_id, data),
                                         "person not found"))


@mcp.tool()
def archive_person(person_id: int) -> dict:
    """Soft-delete (archive) a person. They're removed from the dashboard and the
    public team grid but never hard-deleted. Confirm with the human first."""
    require_scope("write")
    with SessionLocal() as db:
        return _dump(PersonOut, _require(people_service.archive_person(db, person_id),
                                         "person not found"))


# --------------------------------------------------------------------------- #
# media + attachments (read-only — uploads happen in the dashboard)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_media(entity_type: str, entity_id: int) -> list[dict]:
    """List the images attached to an entity (their public URLs + alt text).
    entity_type is event | project | sponsor | speaker | person | partner. Uploads
    are done in the dashboard (they need the cropped binary); this is read-only."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(MediaOut, media_service.list_media(
            db, entity_type=entity_type, entity_id=entity_id))


@mcp.tool()
def list_attachments(entity_type: str, entity_id: int) -> list[dict]:
    """List the files (PDFs/images) attached to an entity (sponsor today), with
    their public URLs. Read-only — uploads happen in the dashboard."""
    require_scope("read")
    with SessionLocal() as db:
        return _dump_list(AttachmentOut, attachments_service.list_attachments(
            db, entity_type=entity_type, entity_id=entity_id))


# The mounted Starlette app (its lifespan runs the session manager — see main.py).
mcp_app = mcp.streamable_http_app()
