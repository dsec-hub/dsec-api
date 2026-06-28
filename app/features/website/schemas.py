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


class PublicLead(BaseModel):
    """The person leading an event/project — name + role + headshot only.

    Never carries contact details or other PII: it's a public byline/avatar.
    Resolved from `Event.event_lead_id` / `Project.lead_id`; `photo` is the
    person's uploaded headshot (media_asset entity_type="person", role="photo").
    """

    name: str
    role: str | None        # role_title, e.g. "Web Development Lead"
    photo: str | None       # headshot (webp); null = no photo uploaded


class PublicPerson(BaseModel):
    """A committee/team member published on the public About page.

    Only ever returned for people with `show_on_website` set. Carries the public
    fields (name, role, bio, headshot, social links) — never internal notes,
    email, student id, or status.
    """

    slug: str               # stable URL key for /team/{slug}, derived from name
    name: str
    role: str | None        # role_title, e.g. "President"
    type: str | None        # Exec / Committee Lead / Committee Member / ...
    committee: str | None
    bio: str | None
    photo: str | None       # headshot (webp); null = no photo uploaded
    photo_png: str | None    # headshot (png download)
    instagram: str | None
    linkedin: str | None
    github: str | None
    website: str | None


class PublicPersonEvent(BaseModel):
    """A published event this person leads — a clickable byline on their profile."""

    slug: str
    title: str
    date: str | None        # ISO YYYY-MM-DD (start)
    upcoming: bool


class PublicPersonProject(BaseModel):
    """A published project this person leads — a clickable byline on their profile."""

    slug: str | None
    title: str
    summary: str | None


class PublicPersonDetail(PublicPerson):
    """One team member's full public profile page (/website/team/{slug}).

    Extends the grid card with the events and projects they lead (published only),
    so the website can render a real per-person profile section. Discord is
    included here (not on the grid card) so members can link it from their page.
    """

    discord: str | None
    led_events: list[PublicPersonEvent] = []
    led_projects: list[PublicPersonProject] = []


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
    lead: PublicLead | None = None  # project lead (name + role + headshot)


class PublicSpeaker(BaseModel):
    """A speaker presenting at an event (name + optional title/bio + headshot)."""

    name: str
    title: str | None
    bio: str | None
    photo: str | None       # headshot (webp); null = no photo uploaded
    photo_png: str | None    # headshot (png download)


class PublicEventSponsor(BaseModel):
    """A sponsor backing an event, shown as a logo on the event page."""

    name: str               # organisation name
    website: str | None
    tier: str | None        # optional per-event tier label
    logo: str | None        # transparent logo (webp); null = no logo uploaded
    logo_png: str | None


class PublicEventPartner(BaseModel):
    """A partner (collaborator club) shown as a logo on the event page. Only
    partners opted in via show_on_website appear here."""

    name: str
    website: str | None
    role: str | None        # optional per-event label, e.g. "Co-host"
    logo: str | None        # transparent logo (webp); null = no logo uploaded
    logo_png: str | None


class PublicSponsor(BaseModel):
    """A published sponsor for the public /website/sponsors logo wall."""

    name: str               # organisation name
    website: str | None
    logo: str | None        # transparent logo (webp)
    logo_png: str | None


class PublicPartner(BaseModel):
    """A published partner (collaborator club) for the public /website/partners
    logo wall. Only partners opted in via show_on_website appear — partners are
    internal by default. Unlike sponsors, a logo is optional: the site renders
    the club name when none is uploaded."""

    name: str
    website: str | None
    logo: str | None        # transparent logo (webp); null = no logo uploaded
    logo_png: str | None


class PublicRelatedEvent(BaseModel):
    """A published event linked to this one (a visual-only relation). Carries
    just enough to render a clickable list item on the event page."""

    slug: str
    title: str
    label: str | None       # optional relation label, e.g. "Series"
    upcoming: bool


class PublicEvent(BaseModel):
    slug: str
    title: str
    type: str | None
    status: str | None
    description: str | None  # free-form Markdown shown on the detail page
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
    lead: PublicLead | None = None  # event lead (name + role + headshot)
    speakers: list[PublicSpeaker] = []
    sponsors: list[PublicEventSponsor] = []
    partners: list[PublicEventPartner] = []
    related_events: list[PublicRelatedEvent] = []
    # Flagship marketing event (see the FLAGSHIP contract). When this is a
    # flagship event still in `teaser` state, the secret specifics above
    # (description, venue, ticket_url, ticket_tiers, speakers, sponsors,
    # partners) are NULLED OUT in this payload so they can't be scraped before
    # reveal — only the title, type, dates, image and these flagship_* fields
    # remain. Non-flagship events are unaffected (flagship=False, rest null).
    flagship: bool = False
    flagship_theme: str | None = None       # arena|blueprint|nightrun
    flagship_state: str | None = None       # teaser|revealed
    flagship_teaser_title: str | None = None
    flagship_teaser_body: str | None = None
    flagship_reveal_at: str | None = None   # ISO 8601 countdown target


class FlagshipSignupIn(BaseModel):
    """A public submission from a flagship event's teaser-page funnel.

    `kind` is `notify` (reveal-email interest) or `sponsor` (a company offering
    to back the event). `company`/`message` are only meaningful for `sponsor`.
    """

    kind: str               # notify|sponsor
    email: str
    name: str | None = None
    company: str | None = None   # sponsor only
    message: str | None = None   # sponsor only


class PublicLink(BaseModel):
    """One tappable button on the public, chromeless `/links` link-tree page.

    Only ever returned for visible, non-archived links. `accent` (one of the 8
    brand accents) is null when the committee left it on "auto" — the website
    then cycles an accent by visible position.
    """

    title: str
    subtitle: str | None
    url: str
    icon: str | None         # a single emoji, e.g. "🎮" (null = none)
    accent: str | None       # brand accent name; null = auto-cycle by position
    display_order: int


class PublicLinkProfile(BaseModel):
    """The link-tree page header (the singleton link_profile row id=1)."""

    title: str
    tagline: str | None
    mascot: str | None       # a PixelDuck sprite name (public/pixel/<mascot>.webp)


class PublicLinkTree(BaseModel):
    """The full public link-tree feed: the header plus the visible link stack."""

    profile: PublicLinkProfile
    links: list[PublicLink]


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
