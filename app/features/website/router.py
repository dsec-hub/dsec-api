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
from app.db import get_db
from app.features.media import service as media_service
from app.features.projects import service as projects_service
from app.models import Event, FinanceReport, MediaAsset, Member, Project, SponsorPackage

from .schemas import PublicEvent, PublicMedia, PublicProject, PublicSponsorPackage, SiteStats

router = APIRouter()

# Primary-image preference when an entity has several uploads.
_ROLE_PRIORITY = {"banner": 0, "image": 1, "poster": 2}


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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


def _public_project(p: Project, assets: list[MediaAsset]) -> PublicProject:
    image, download, media = _media_block(assets, fallback_image=p.image_url)
    return PublicProject(
        slug=p.slug, title=p.name, summary=p.summary, description=p.description,
        tags=p.tech_tags, status=p.status, category=p.category,
        repo=p.repo_url, demo=p.demo_url, image=image, download=download, media=media,
    )


@router.get("/projects", response_model=list[PublicProject])
def public_projects(request: Request, db: Session = Depends(get_db)) -> list[PublicProject]:
    limiter.check_request(db, key_id=None, ip=_ip(request))
    rows = projects_service.list_projects(db, is_public=True, limit=100)
    media_map = media_service.list_media_for(
        db, entity_type="project", entity_ids=[p.id for p in rows]
    )
    return [_public_project(p, media_map.get(p.id, [])) for p in rows]


@router.get("/projects/{slug}", response_model=PublicProject)
def public_project(slug: str, request: Request, db: Session = Depends(get_db)) -> PublicProject:
    limiter.check_request(db, key_id=None, ip=_ip(request))
    p = db.execute(
        select(Project).where(Project.slug == slug, Project.is_public.is_(True),
                              Project.archived.is_(False))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    assets = media_service.list_media(db, entity_type="project", entity_id=p.id)
    return _public_project(p, assets)


def _event_slug(e: Event) -> str:
    return (
        f"{_slugify(e.name)}-{e.start_date.isoformat()}"
        if e.start_date
        else _slugify(e.name)
    )


def _public_event(e: Event, assets: list[MediaAsset], *, today: date) -> PublicEvent:
    image, download, media = _media_block(assets)
    # An event auto-completes once its start date passes; its ticketing (link +
    # prices) is only meaningful while upcoming, so hide it for past events.
    upcoming = bool(e.start_date and e.start_date >= today)
    return PublicEvent(
        slug=_event_slug(e),
        title=e.name, type=e.type, status=e.status,
        date=e.start_date.isoformat() if e.start_date else None,
        end_date=e.end_date.isoformat() if e.end_date else None,
        venue=e.venue, format=e.format,
        ticket_url=e.ticket_url if upcoming else None,
        ticket_tiers=(e.ticket_tiers or None) if upcoming else None,
        food_provided=bool(e.food_provided),
        upcoming=upcoming,
        image=image, download=download, media=media,
    )


@router.get("/events", response_model=list[PublicEvent])
def public_events(request: Request, db: Session = Depends(get_db)) -> list[PublicEvent]:
    """Non-archived events with a confirmed date, soonest upcoming first."""
    limiter.check_request(db, key_id=None, ip=_ip(request))
    today = date.today()
    rows = db.execute(
        select(Event).where(Event.archived.is_(False), Event.start_date.is_not(None))
        .order_by(Event.start_date.desc())
    ).scalars().all()
    media_map = media_service.list_media_for(
        db, entity_type="event", entity_ids=[e.id for e in rows]
    )
    out = [_public_event(e, media_map.get(e.id, []), today=today) for e in rows]
    # upcoming first (soonest), then past (most recent)
    upcoming = sorted([e for e in out if e.upcoming], key=lambda e: e.date or "")
    past = [e for e in out if not e.upcoming]
    return upcoming + past


@router.get("/events/{slug}", response_model=PublicEvent)
def public_event(slug: str, request: Request, db: Session = Depends(get_db)) -> PublicEvent:
    """One event by its computed slug (matches the slugs from /website/events)."""
    limiter.check_request(db, key_id=None, ip=_ip(request))
    today = date.today()
    rows = db.execute(
        select(Event).where(Event.archived.is_(False), Event.start_date.is_not(None))
    ).scalars().all()
    match = next((e for e in rows if _event_slug(e) == slug), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    assets = media_service.list_media(db, entity_type="event", entity_id=match.id)
    return _public_event(match, assets, today=today)


@router.get("/sponsor-packages", response_model=list[PublicSponsorPackage])
def public_sponsor_packages(
    request: Request, db: Session = Depends(get_db)
) -> list[PublicSponsorPackage]:
    """Visible sponsorship packages, ordered by display_order.

    Returns an empty list when no packages exist so dsec-website falls back
    to its hardcoded tiers — no failure, just graceful degradation.
    """
    limiter.check_request(db, key_id=None, ip=_ip(request))
    rows = db.execute(
        select(SponsorPackage)
        .where(SponsorPackage.is_visible.is_(True))
        .order_by(SponsorPackage.display_order.asc(), SponsorPackage.id.asc())
    ).scalars().all()
    return [PublicSponsorPackage.model_validate(r) for r in rows]


@router.get("/stats", response_model=SiteStats)
def public_stats(request: Request, db: Session = Depends(get_db)) -> SiteStats:
    """Live social-proof figures (replaces the website's hardcoded placeholders)."""
    limiter.check_request(db, key_id=None, ip=_ip(request))
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
