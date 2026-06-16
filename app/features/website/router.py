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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.features.media import service as media_service
from app.features.projects import service as projects_service
from app.models import (
    Event,
    EventPartner,
    EventSpeaker,
    EventSponsor,
    FinanceReport,
    MediaAsset,
    Member,
    Partner,
    Person,
    Project,
    Sponsor,
    SponsorPackage,
)

from .schemas import (
    PublicEvent,
    PublicEventPartner,
    PublicEventSponsor,
    PublicLead,
    PublicMedia,
    PublicPerson,
    PublicProject,
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


def _public_event(
    e: Event,
    assets: list[MediaAsset],
    *,
    today: date,
    lead: PublicLead | None = None,
    speakers: list[PublicSpeaker] | None = None,
    sponsors: list[PublicEventSponsor] | None = None,
    partners: list[PublicEventPartner] | None = None,
) -> PublicEvent:
    image, download, media = _media_block(assets)
    # An event auto-completes once its start date passes; its ticketing (link +
    # prices) is only meaningful while upcoming, so hide it for past events.
    upcoming = bool(e.start_date and e.start_date >= today)
    return PublicEvent(
        slug=_event_slug(e),
        title=e.name, type=e.type, status=e.status,
        description=e.description,
        date=e.start_date.isoformat() if e.start_date else None,
        end_date=e.end_date.isoformat() if e.end_date else None,
        venue=e.venue, format=e.format,
        ticket_url=e.ticket_url if upcoming else None,
        ticket_tiers=(e.ticket_tiers or None) if upcoming else None,
        food_provided=bool(e.food_provided),
        upcoming=upcoming,
        image=image, download=download, media=media,
        lead=lead,
        speakers=speakers or [],
        sponsors=sponsors or [],
        partners=partners or [],
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
    # Speakers/sponsors/partners are only loaded on the detail page (keeps the
    # list lean).
    return _public_event(
        match, assets, today=today, lead=lead,
        speakers=_event_speakers(db, match.id),
        sponsors=_event_sponsors(db, match.id),
        partners=_event_partners(db, match.id),
    )


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


@router.get("/team", response_model=list[PublicPerson])
def public_team(request: Request, db: Session = Depends(get_db)) -> list[PublicPerson]:
    """Published committee/team members for the public About page.

    Only people the exec has opted in (`show_on_website`) are returned, ordered
    by `display_order` then name, each with their uploaded headshot. Returns an
    empty list when none are published so dsec-website falls back to its static
    roster — graceful degradation, never a failure.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    rows = db.execute(
        select(Person)
        .where(Person.archived.is_(False), Person.show_on_website.is_(True))
        .order_by(Person.display_order.asc(), Person.name.asc())
    ).scalars().all()
    if not rows:
        return []
    photos = media_service.list_media_for(
        db, entity_type="person", entity_ids=[p.id for p in rows]
    )
    out: list[PublicPerson] = []
    for p in rows:
        webp, png = _role_media(photos.get(p.id, []), "photo")
        out.append(
            PublicPerson(
                name=p.name, role=p.role_title, type=p.type, committee=p.committee,
                bio=p.bio, photo=webp, photo_png=png,
                instagram=p.instagram, linkedin=p.linkedin,
                github=p.github, website=p.website,
            )
        )
    return out


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
