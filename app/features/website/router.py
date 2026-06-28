"""Public, no-auth website feed.

Serves ONLY published/safe data so the public marketing site (dsec-website) can
render live events, showcased projects, and real social-proof stats instead of
hardcoded placeholders. Per-IP rate limited; never exposes members' PII or
internal fields. The dashboard/MCP API (authenticated) is the write path.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.features.links import service as links_service
from app.features.media import service as media_service
from app.features.projects import service as projects_service
from app.features.sponsor_leads import service as sponsor_leads_service
from app.features.website.preview import verify_preview_token
from app.models import (
    Event,
    EventConnection,
    EventPartner,
    EventSpeaker,
    EventSponsor,
    FinanceReport,
    FlagshipSignup,
    MediaAsset,
    Member,
    Partner,
    Person,
    Project,
    Sponsor,
    SponsorPackage,
)

from .schemas import (
    FlagshipSignupIn,
    PublicEvent,
    PublicEventPartner,
    PublicEventSponsor,
    PublicLead,
    PublicLink,
    PublicLinkProfile,
    PublicLinkTree,
    PublicMedia,
    PublicPartner,
    PublicPerson,
    PublicPersonDetail,
    PublicPersonEvent,
    PublicPersonProject,
    PublicProject,
    PublicRelatedEvent,
    PublicSpeaker,
    PublicSponsor,
    PublicSponsorPackage,
    SiteStats,
)

router = APIRouter()

# Primary-image preference when an entity has several uploads.
_ROLE_PRIORITY = {"banner": 0, "image": 1, "poster": 2}




def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "event"


def _media_block(
    assets: list[MediaAsset], *, fallback_image: str | None = None
) -> tuple[str | None, str | None, list[PublicMedia]]:
    """Return (primary webp url, primary png url, full media list) for an entity.

    Primary = lowest-priority role (banner → image → poster), then sort order.
    Falls back to the legacy `image_url` string when no uploads exist.
    """
    media = [
        PublicMedia(
            role=a.role, webp=a.webp_url, png=a.png_url,
            alt=a.alt_text, width=a.width, height=a.height,
        )
        for a in assets
    ]
    if not assets:
        return fallback_image, None, []
    primary = min(assets, key=lambda a: (_ROLE_PRIORITY.get(a.role, 9), a.sort_order, a.id))
    return primary.webp_url, primary.png_url, media


def _role_media(assets: list[MediaAsset], role: str) -> tuple[str | None, str | None]:
    """First (webp, png) for a single-image role (speaker photo / sponsor logo)."""
    for a in assets:
        if a.role == role:
            return a.webp_url, a.png_url
    return None, None


def _leads_map(db: Session, person_ids: list[int | None]) -> dict[int, PublicLead]:
    """Resolve a set of lead person ids to public bylines (name + role + photo).

    Batched (one people query + one media query) so the events/projects list
    endpoints stay free of N+1 lookups. Skips null ids and archived people.
    """
    ids = sorted({pid for pid in person_ids if pid})
    if not ids:
        return {}
    people = db.execute(
        select(Person).where(Person.id.in_(ids), Person.archived.is_(False))
    ).scalars().all()
    photos = media_service.list_media_for(db, entity_type="person", entity_ids=ids)
    out: dict[int, PublicLead] = {}
    for p in people:
        webp, _png = _role_media(photos.get(p.id, []), "photo")
        out[p.id] = PublicLead(name=p.name, role=p.role_title, photo=webp)
    return out


def _event_speakers(db: Session, event_id: int) -> list[PublicSpeaker]:
    """Speakers for an event, with their headshots, resolving linked-person names.

    Photo precedence: a speaker's own uploaded headshot (entity_type="speaker")
    wins; a speaker linked to a directory person but with no own photo falls back
    to that person's profile photo (entity_type="person"). So linking a person
    reuses their headshot automatically, while a speaker-specific upload overrides.
    """
    rows = db.execute(
        select(EventSpeaker)
        .where(EventSpeaker.event_id == event_id, EventSpeaker.archived.is_(False))
        .order_by(EventSpeaker.sort_order, EventSpeaker.id)
    ).scalars().all()
    if not rows:
        return []
    # Linked people: used to resolve missing display names and to fall back to
    # their profile photo when the speaker has no own headshot.
    linked_ids = sorted({r.person_id for r in rows if r.person_id})
    people = {
        p.id: p.name
        for p in (
            db.execute(select(Person).where(Person.id.in_(linked_ids))).scalars().all()
            if linked_ids
            else []
        )
    }
    speaker_photos = media_service.list_media_for(
        db, entity_type="speaker", entity_ids=[r.id for r in rows]
    )
    person_photos = media_service.list_media_for(
        db, entity_type="person", entity_ids=linked_ids
    )
    out: list[PublicSpeaker] = []
    for r in rows:
        name = r.name or people.get(r.person_id or -1) or "Speaker"
        webp, png = _role_media(speaker_photos.get(r.id, []), "photo")
        if not webp and r.person_id:
            webp, png = _role_media(person_photos.get(r.person_id, []), "photo")
        out.append(
            PublicSpeaker(name=name, title=r.title, bio=r.bio, photo=webp, photo_png=png)
        )
    return out


def _event_sponsors(db: Session, event_id: int) -> list[PublicEventSponsor]:
    """Sponsors backing an event, with their logos."""
    rows = db.execute(
        select(EventSponsor, Sponsor)
        .join(Sponsor, Sponsor.id == EventSponsor.sponsor_id)
        .where(
            EventSponsor.event_id == event_id,
            EventSponsor.archived.is_(False),
            Sponsor.archived.is_(False),
        )
        .order_by(EventSponsor.sort_order, EventSponsor.id)
    ).all()
    if not rows:
        return []
    logos = media_service.list_media_for(
        db, entity_type="sponsor", entity_ids=[s.id for _link, s in rows]
    )
    out: list[PublicEventSponsor] = []
    for link, s in rows:
        webp, png = _role_media(logos.get(s.id, []), "logo")
        out.append(
            PublicEventSponsor(
                name=s.organisation, website=s.website, tier=link.tier,
                logo=webp, logo_png=png,
            )
        )
    return out


def _event_partners(db: Session, event_id: int) -> list[PublicEventPartner]:
    """Partners (collaborator clubs) shown publicly for an event, with their
    logos. Only partners opted in via show_on_website appear — partners are
    internal by default, so linking one to an event does NOT publish it."""
    rows = db.execute(
        select(EventPartner, Partner)
        .join(Partner, Partner.id == EventPartner.partner_id)
        .where(
            EventPartner.event_id == event_id,
            EventPartner.archived.is_(False),
            Partner.archived.is_(False),
            Partner.show_on_website.is_(True),
        )
        .order_by(EventPartner.sort_order, EventPartner.id)
    ).all()
    if not rows:
        return []
    logos = media_service.list_media_for(
        db, entity_type="partner", entity_ids=[p.id for _link, p in rows]
    )
    out: list[PublicEventPartner] = []
    for link, p in rows:
        webp, png = _role_media(logos.get(p.id, []), "logo")
        out.append(
            PublicEventPartner(
                name=p.name, website=p.website, role=link.role,
                logo=webp, logo_png=png,
            )
        )
    return out


def _public_project(
    p: Project, assets: list[MediaAsset], *, lead: PublicLead | None = None
) -> PublicProject:
    image, download, media = _media_block(assets, fallback_image=p.image_url)
    return PublicProject(
        slug=p.slug, title=p.name, summary=p.summary, description=p.description,
        tags=p.tech_tags, status=p.status, category=p.category,
        repo=p.repo_url, demo=p.demo_url, image=image, download=download, media=media,
        lead=lead,
    )


@router.get("/projects", response_model=list[PublicProject])
def public_projects(request: Request, db: Session = Depends(get_db)) -> list[PublicProject]:
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = projects_service.list_projects(db, is_public=True, limit=100)
    media_map = media_service.list_media_for(
        db, entity_type="project", entity_ids=[p.id for p in rows]
    )
    leads = _leads_map(db, [p.lead_id for p in rows])
    return [
        _public_project(p, media_map.get(p.id, []), lead=leads.get(p.lead_id))
        for p in rows
    ]


@router.get("/projects/{slug}", response_model=PublicProject)
def public_project(slug: str, request: Request, db: Session = Depends(get_db)) -> PublicProject:
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    p = db.execute(
        select(Project).where(Project.slug == slug, Project.is_public.is_(True),
                              Project.archived.is_(False))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    assets = media_service.list_media(db, entity_type="project", entity_id=p.id)
    lead = _leads_map(db, [p.lead_id]).get(p.lead_id)
    return _public_project(p, assets, lead=lead)


def _event_slug(e: Event) -> str:
    return (
        f"{_slugify(e.name)}-{e.start_date.isoformat()}"
        if e.start_date
        else _slugify(e.name)
    )


def _related_events(db: Session, e: Event, *, today: date) -> list[PublicRelatedEvent]:
    """Events visibly connected to this one. Only *published*, dated, non-archived
    events surface (so every link resolves to a live page — drafts never leak).
    The relation is symmetric, so the "other" side is whichever column isn't this
    event. Newest first."""
    links = db.execute(
        select(EventConnection).where(
            EventConnection.archived.is_(False),
            or_(
                EventConnection.event_a_id == e.id,
                EventConnection.event_b_id == e.id,
            ),
        )
    ).scalars().all()
    if not links:
        return []
    label_by_other: dict[int, str | None] = {}
    for link in links:
        other_id = link.event_b_id if link.event_a_id == e.id else link.event_a_id
        label_by_other[other_id] = link.label
    others = db.execute(
        select(Event).where(
            Event.id.in_(list(label_by_other.keys())),
            Event.archived.is_(False),
            Event.is_public.is_(True),
            Event.start_date.is_not(None),
        )
    ).scalars().all()
    others.sort(key=lambda o: o.start_date or date.min, reverse=True)
    return [
        PublicRelatedEvent(
            slug=_event_slug(o),
            title=o.name,
            label=label_by_other.get(o.id),
            upcoming=bool(o.start_date and o.start_date >= today),
        )
        for o in others
    ]


def _public_event(
    e: Event,
    assets: list[MediaAsset],
    *,
    today: date,
    lead: PublicLead | None = None,
    speakers: list[PublicSpeaker] | None = None,
    sponsors: list[PublicEventSponsor] | None = None,
    partners: list[PublicEventPartner] | None = None,
    related_events: list[PublicRelatedEvent] | None = None,
) -> PublicEvent:
    image, download, media = _media_block(assets)
    # An event auto-completes once its start date passes; its ticketing (link +
    # prices) is only meaningful while upcoming, so hide it for past events.
    upcoming = bool(e.start_date and e.start_date >= today)
    description = e.description
    venue = e.venue
    ticket_url = e.ticket_url if upcoming else None
    ticket_tiers = (e.ticket_tiers or None) if upcoming else None
    speakers = speakers or []
    sponsors = sponsors or []
    partners = partners or []
    # Flagship secrecy gating: a flagship event still in `teaser` state must NULL
    # OUT its real specifics in the public payload so they can't be scraped before
    # the committee flips it to `revealed`. Only the safe shell (title, type,
    # dates, image) + the flagship_* fields remain. A non-flagship event — or a
    # revealed flagship — exposes everything as normal.
    flagship = bool(getattr(e, "is_flagship", False))
    state = (e.flagship_state or "teaser") if flagship else None
    if flagship and state == "teaser":
        description = None
        venue = None
        ticket_url = None
        ticket_tiers = None
        speakers = []
        sponsors = []
        partners = []
    return PublicEvent(
        slug=_event_slug(e),
        title=e.name, type=e.type, status=e.status,
        description=description,
        date=e.start_date.isoformat() if e.start_date else None,
        end_date=e.end_date.isoformat() if e.end_date else None,
        venue=venue, format=e.format,
        ticket_url=ticket_url,
        ticket_tiers=ticket_tiers,
        food_provided=bool(e.food_provided),
        upcoming=upcoming,
        image=image, download=download, media=media,
        lead=lead,
        speakers=speakers,
        sponsors=sponsors,
        partners=partners,
        related_events=related_events or [],
        flagship=flagship,
        flagship_theme=e.flagship_theme if flagship else None,
        flagship_state=state,
        flagship_teaser_title=e.flagship_teaser_title if flagship else None,
        flagship_teaser_body=e.flagship_teaser_body if flagship else None,
        flagship_reveal_at=(
            e.flagship_reveal_at.isoformat()
            if flagship and e.flagship_reveal_at
            else None
        ),
    )


@router.get("/events", response_model=list[PublicEvent])
def public_events(request: Request, db: Session = Depends(get_db)) -> list[PublicEvent]:
    """Published, non-archived events with a confirmed date, soonest upcoming first.

    Draft events (is_public=False) are hidden — they only exist in the
    authenticated dashboard until someone publishes them.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    today = date.today()
    rows = db.execute(
        select(Event).where(
            Event.archived.is_(False),
            Event.is_public.is_(True),
            Event.start_date.is_not(None),
        )
        .order_by(Event.start_date.desc())
    ).scalars().all()
    media_map = media_service.list_media_for(
        db, entity_type="event", entity_ids=[e.id for e in rows]
    )
    leads = _leads_map(db, [e.event_lead_id for e in rows])
    out = [
        _public_event(e, media_map.get(e.id, []), today=today, lead=leads.get(e.event_lead_id))
        for e in rows
    ]
    # upcoming first (soonest), then past (most recent)
    upcoming = sorted([e for e in out if e.upcoming], key=lambda e: e.date or "")
    past = [e for e in out if not e.upcoming]
    return upcoming + past


@router.get("/events/{slug}", response_model=PublicEvent)
def public_event(slug: str, request: Request, db: Session = Depends(get_db)) -> PublicEvent:
    """One published event by its computed slug (matches /website/events)."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    today = date.today()
    rows = db.execute(
        select(Event).where(
            Event.archived.is_(False),
            Event.is_public.is_(True),
            Event.start_date.is_not(None),
        )
    ).scalars().all()
    match = next((e for e in rows if _event_slug(e) == slug), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    assets = media_service.list_media(db, entity_type="event", entity_id=match.id)
    lead = _leads_map(db, [match.event_lead_id]).get(match.event_lead_id)
    # Speakers/sponsors/partners/related events are only loaded on the detail
    # page (keeps the list lean).
    return _public_event(
        match, assets, today=today, lead=lead,
        speakers=_event_speakers(db, match.id),
        sponsors=_event_sponsors(db, match.id),
        partners=_event_partners(db, match.id),
        related_events=_related_events(db, match, today=today),
    )


@router.get("/events/preview/{token}", response_model=PublicEvent)
def public_event_preview(
    token: str, request: Request, db: Session = Depends(get_db)
) -> PublicEvent:
    """Render ONE event — published OR draft — from a signed preview token.

    Powers the committee's "preview before publishing" link: the dashboard mints
    an unguessable, time-limited token (see ``preview.make_preview_token``) and
    points dsec-website at ``/events/preview/<token>``, which renders the exact
    public layout the event will have once live. The payload is identical to the
    normal detail endpoint (same flagship-secrecy gating), so a preview is truly
    WYSIWYG. A bad/expired token or a missing/archived event returns 404 without
    revealing which — drafts never leak to anyone without the link.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    event_id = verify_preview_token(token)
    if event_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    today = date.today()
    e = db.execute(
        select(Event).where(Event.id == event_id, Event.archived.is_(False))
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    assets = media_service.list_media(db, entity_type="event", entity_id=e.id)
    lead = _leads_map(db, [e.event_lead_id]).get(e.event_lead_id)
    return _public_event(
        e, assets, today=today, lead=lead,
        speakers=_event_speakers(db, e.id),
        sponsors=_event_sponsors(db, e.id),
        partners=_event_partners(db, e.id),
        related_events=_related_events(db, e, today=today),
    )


@router.post("/flagship/{slug}/signup")
def flagship_signup(
    slug: str,
    body: FlagshipSignupIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Public, no-auth funnel sink for a flagship event's teaser page.

    Captures `notify` (reveal-email) and `sponsor` (backer) interest while an
    event is still secret. Resolves the event by the SAME computed slug the
    public feed uses, and only for an actual flagship event. The insert is
    idempotent on (event_id, kind, email) so a re-submit is a no-op success —
    it NEVER 500s on a duplicate. For `sponsor` signups we ALSO best-effort drop
    a lead into the existing sponsor-lead pipeline (never blocking the funnel on
    it). Always returns {"ok": true}.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    kind = (body.kind or "").strip().lower()
    if kind not in {"notify", "sponsor"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "kind must be 'notify' or 'sponsor'"
        )
    email = (body.email or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "valid email required")

    # Resolve via the shared slug (handles date-TBA flagship events too — those
    # have no start_date, so we don't filter on it the way the feed does).
    rows = db.execute(
        select(Event).where(
            Event.archived.is_(False),
            Event.is_flagship.is_(True),
        )
    ).scalars().all()
    event = next((e for e in rows if _event_slug(e) == slug), None)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "flagship event not found")

    signup = FlagshipSignup(
        event_id=event.id,
        kind=kind,
        email=email,
        name=(body.name or None),
        company=(body.company or None),
        message=(body.message or None),
        source="website",
    )
    db.add(signup)
    try:
        db.commit()
    except IntegrityError:
        # Duplicate (event_id, kind, email) — already captured. Roll back and
        # report success so the public form is idempotent, never an error.
        db.rollback()
        return {"ok": True}

    # Best-effort: mirror a sponsor signup into the existing sponsor-lead pipeline
    # so External Affairs works it alongside every other inbound enquiry. The
    # flagship_signup row above stays the funnel's own source of truth, so any
    # failure here is swallowed — it must never block/break the public form.
    if kind == "sponsor":
        try:
            sponsor_leads_service.create_lead(
                db,
                {
                    "source": "flagship",
                    "name": body.name or None,
                    "email": email,
                    "company": body.company or None,
                    "message": (
                        f"[Flagship: {event.name}] {body.message}"
                        if body.message
                        else f"Sponsor interest in flagship event: {event.name}"
                    ),
                },
            )
        except Exception:  # noqa: BLE001 — lead mirror is strictly best-effort
            db.rollback()

    return {"ok": True}


@router.get("/sponsor-packages", response_model=list[PublicSponsorPackage])
def public_sponsor_packages(
    request: Request, db: Session = Depends(get_db)
) -> list[PublicSponsorPackage]:
    """Visible sponsorship packages, ordered by display_order.

    Returns an empty list when no packages exist so dsec-website falls back
    to its hardcoded tiers — no failure, just graceful degradation.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = db.execute(
        select(SponsorPackage)
        .where(SponsorPackage.is_visible.is_(True))
        .order_by(SponsorPackage.display_order.asc(), SponsorPackage.id.asc())
    ).scalars().all()
    return [PublicSponsorPackage.model_validate(r) for r in rows]


@router.get("/sponsors", response_model=list[PublicSponsor])
def public_sponsors(request: Request, db: Session = Depends(get_db)) -> list[PublicSponsor]:
    """Published sponsors (show_on_website) that have an uploaded logo — the
    public 'our sponsors' logo wall. Prospects/pipeline rows never leak here."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = db.execute(
        select(Sponsor)
        .where(Sponsor.archived.is_(False), Sponsor.show_on_website.is_(True))
        .order_by(Sponsor.organisation.asc())
    ).scalars().all()
    if not rows:
        return []
    logos = media_service.list_media_for(
        db, entity_type="sponsor", entity_ids=[s.id for s in rows]
    )
    out: list[PublicSponsor] = []
    for s in rows:
        webp, png = _role_media(logos.get(s.id, []), "logo")
        if not webp:  # only wall sponsors that actually have a logo
            continue
        out.append(
            PublicSponsor(name=s.organisation, website=s.website, logo=webp, logo_png=png)
        )
    return out


@router.get("/partners", response_model=list[PublicPartner])
def public_partners(request: Request, db: Session = Depends(get_db)) -> list[PublicPartner]:
    """Published partners (collaborator clubs opted in via show_on_website) for
    the public 'clubs & partners we work with' wall. Internal-only partners (the
    default) never leak here. A logo is optional — the site falls back to the
    club name — so, unlike sponsors, logo-less partners are still included."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = db.execute(
        select(Partner)
        .where(Partner.archived.is_(False), Partner.show_on_website.is_(True))
        .order_by(Partner.name.asc())
    ).scalars().all()
    if not rows:
        return []
    logos = media_service.list_media_for(
        db, entity_type="partner", entity_ids=[p.id for p in rows]
    )
    out: list[PublicPartner] = []
    for p in rows:
        webp, png = _role_media(logos.get(p.id, []), "logo")
        out.append(
            PublicPartner(name=p.name, website=p.website, logo=webp, logo_png=png)
        )
    return out


@router.get("/linktree", response_model=PublicLinkTree)
def public_linktree(request: Request, db: Session = Depends(get_db)) -> PublicLinkTree:
    """The public DSEC link-tree feed: the page header plus the visible link stack.

    Only visible, non-archived links are returned, ordered by display_order then
    created_at (the same order the dashboard shows). The profile is the singleton
    header (a default object if no row has been saved yet) so the page is never
    empty — dsec-website renders its own hardcoded fallback only when the API is
    unreachable.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    profile = links_service.get_profile(db)
    links = links_service.list_links(db, include_hidden=False, archived=False)
    return PublicLinkTree(
        profile=PublicLinkProfile.model_validate(profile, from_attributes=True),
        links=[PublicLink.model_validate(link, from_attributes=True) for link in links],
    )


def _person_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "member"


def _published_team(db: Session) -> list[Person]:
    """The published roster (show_on_website), in public grid order. Shared by the
    team list feed and the per-person detail page so they agree on slugs."""
    return list(
        db.execute(
            select(Person)
            .where(Person.archived.is_(False), Person.show_on_website.is_(True))
            .order_by(Person.display_order.asc(), Person.name.asc())
        ).scalars().all()
    )


def _team_slugs(rows: list[Person]) -> dict[int, str]:
    """Assign each published person a stable, unique URL slug from their name.

    Same-name people get a `-2`, `-3` … suffix in roster order, so the list feed
    and the `/team/{slug}` detail endpoint always resolve to the same person.
    """
    seen: dict[str, int] = {}
    out: dict[int, str] = {}
    for p in rows:
        base = _person_slug(p.name)
        n = seen.get(base, 0) + 1
        seen[base] = n
        out[p.id] = base if n == 1 else f"{base}-{n}"
    return out


def _person_led_events(db: Session, person_id: int, *, today: date) -> list[PublicPersonEvent]:
    """Published, dated events this person leads (primary lead) — newest first."""
    rows = db.execute(
        select(Event).where(
            Event.event_lead_id == person_id,
            Event.archived.is_(False),
            Event.is_public.is_(True),
            Event.start_date.is_not(None),
        ).order_by(Event.start_date.desc())
    ).scalars().all()
    return [
        PublicPersonEvent(
            slug=_event_slug(e),
            title=e.name,
            date=e.start_date.isoformat() if e.start_date else None,
            upcoming=bool(e.start_date and e.start_date >= today),
        )
        for e in rows
    ]


def _person_led_projects(db: Session, person_id: int) -> list[PublicPersonProject]:
    """Published projects this person leads (primary lead), A–Z."""
    rows = db.execute(
        select(Project).where(
            Project.lead_id == person_id,
            Project.archived.is_(False),
            Project.is_public.is_(True),
        ).order_by(Project.name.asc())
    ).scalars().all()
    return [PublicPersonProject(slug=p.slug, title=p.name, summary=p.summary) for p in rows]


@router.get("/team", response_model=list[PublicPerson])
def public_team(request: Request, db: Session = Depends(get_db)) -> list[PublicPerson]:
    """Published committee/team members for the public About page.

    Only people the exec has opted in (`show_on_website`) are returned, ordered
    by `display_order` then name, each with their uploaded headshot and a stable
    `slug` linking to their `/team/{slug}` profile page. Returns an empty list
    when none are published so dsec-website falls back to its static roster —
    graceful degradation, never a failure.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = _published_team(db)
    if not rows:
        return []
    slugs = _team_slugs(rows)
    photos = media_service.list_media_for(
        db, entity_type="person", entity_ids=[p.id for p in rows]
    )
    out: list[PublicPerson] = []
    for p in rows:
        webp, png = _role_media(photos.get(p.id, []), "photo")
        out.append(
            PublicPerson(
                slug=slugs[p.id],
                name=p.name, role=p.role_title, type=p.type, committee=p.committee,
                bio=p.bio, photo=webp, photo_png=png,
                instagram=p.instagram, linkedin=p.linkedin,
                github=p.github, website=p.website,
            )
        )
    return out


@router.get("/team/{slug}", response_model=PublicPersonDetail)
def public_team_member(
    slug: str, request: Request, db: Session = Depends(get_db)
) -> PublicPersonDetail:
    """One published team member's full profile, with the events and projects
    they lead (published only). 404 for anyone not opted in to the public site —
    the slug must resolve within the same published roster the grid uses."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = _published_team(db)
    slugs = _team_slugs(rows)
    match = next((p for p in rows if slugs[p.id] == slug), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "team member not found")
    webp, png = _role_media(
        media_service.list_media(db, entity_type="person", entity_id=match.id), "photo"
    )
    today = date.today()
    return PublicPersonDetail(
        slug=slug,
        name=match.name, role=match.role_title, type=match.type,
        committee=match.committee, bio=match.bio, photo=webp, photo_png=png,
        instagram=match.instagram, linkedin=match.linkedin,
        github=match.github, website=match.website, discord=match.discord,
        led_events=_person_led_events(db, match.id, today=today),
        led_projects=_person_led_projects(db, match.id),
    )


@router.get("/stats", response_model=SiteStats)
def public_stats(request: Request, db: Session = Depends(get_db)) -> SiteStats:
    """Live social-proof figures (replaces the website's hardcoded placeholders)."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    members = db.execute(
        select(func.count()).select_from(Member).where(Member.is_current.is_(True))
    ).scalar_one()
    dusa = db.execute(
        select(func.count()).select_from(Member)
        .where(Member.is_current.is_(True), Member.dusa_member.is_(True))
    ).scalar_one()
    year_start = date(date.today().year, 1, 1)
    events_year = db.execute(
        select(func.count()).select_from(Event)
        .where(Event.archived.is_(False), Event.start_date >= year_start)
    ).scalar_one()
    shipped = db.execute(
        select(func.count()).select_from(Project)
        .where(Project.archived.is_(False),
               Project.status.in_(["Completed", "Showcased"]))
    ).scalar_one()
    report = db.execute(
        select(FinanceReport).where(FinanceReport.is_current.is_(True))
    ).scalar_one_or_none()
    return SiteStats(
        members=members or 0,
        dusa_members=dusa or 0,
        events_this_year=events_year or 0,
        projects_shipped=shipped or 0,
        current_balance=float(report.closing_balance) if report and report.closing_balance is not None else None,
    )
