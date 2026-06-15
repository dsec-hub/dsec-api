"""Public website feed schemas — only ever expose published, safe fields."""

from __future__ import annotations

from pydantic import BaseModel


class PublicMedia(BaseModel):
    """One uploaded image, in both display (webp) and download (png) forms."""

    role: str               # image|poster|banner
    webp: str               # compressed, for display
    png: str                # for download
    alt: str | None
    width: int | None
    height: int | None


class PublicProject(BaseModel):
    slug: str | None
    title: str
    summary: str | None
    description: str | None
    tags: list | None
    status: str | None
    category: str | None
    repo: str | None
    demo: str | None
    image: str | None       # primary display image (webp); falls back to image_url
    download: str | None     # primary image as PNG download
    media: list[PublicMedia]


class PublicEvent(BaseModel):
    slug: str
    title: str
    type: str | None
    status: str | None
    date: str | None        # ISO YYYY-MM-DD (start)
    end_date: str | None
    venue: str | None
    format: str | None
    ticket_url: str | None   # public buy-tickets / register link (null = none/past)
    ticket_tiers: list | None  # [{label, price}] pricing; price 0 = free (null = none/past)
    food_provided: bool        # catering included for attendees
    upcoming: bool
    image: str | None       # primary display image (webp)
    download: str | None     # primary image as PNG download
    media: list[PublicMedia]


class SiteStats(BaseModel):
    members: int
    dusa_members: int
    events_this_year: int
    projects_shipped: int
    current_balance: float | None


class PublicSponsorPackage(BaseModel):
    """A sponsorship tier exposed on the public /website/sponsor-packages feed."""

    id: int
    name: str
    pitch: str | None
    price: str | None          # display string e.g. "from $500"; gated in UI
    includes: list | None
    featured: bool
    display_order: int
