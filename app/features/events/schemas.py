"""Pydantic models for the events feature."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class EventBase(BaseModel):
    """All fields optional — reused for create (name overridden) and update."""

    name: str | None = None
    type: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    trimester: str | None = None
    format: str | None = None
    venue: str | None = None
    ticket_url: str | None = None
    ticket_tiers: list | None = None
    event_lead_id: int | None = None
    committee: str | None = None
    dusa_submission_status: str | None = None
    dusa_deadline: date | None = None
    dusa_required: bool | None = None
    food_provided: bool | None = None
    external_guests: bool | None = None
    expected_attendance: int | None = None
    actual_attendance: int | None = None
    description: str | None = None
    budget_aud: float | None = None
    grant_aud: float | None = None
    # Draft (False) vs published (True). Published events show on the public site.
    is_public: bool | None = None
    # Additional owners beyond `event_lead_id` (the primary lead). Full replace on
    # PATCH; omit to leave unchanged, [] to clear. The primary is never duplicated.
    co_owner_ids: list[int] | None = None


class EventCreate(EventBase):
    name: str  # required on create


class EventUpdate(EventBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str | None
    status: str | None
    start_date: date | None
    end_date: date | None
    trimester: str | None
    format: str | None
    venue: str | None
    ticket_url: str | None
    ticket_tiers: list | None
    event_lead_id: int | None
    committee: str | None
    dusa_submission_status: str | None
    dusa_deadline: date | None
    dusa_required: bool
    food_provided: bool
    external_guests: bool
    expected_attendance: int | None
    actual_attendance: int | None
    description: str | None
    budget_aud: float | None
    grant_aud: float | None
    support_types: list | None
    partner_org: str | None
    related_sponsor_id: int | None
    is_public: bool
    co_owner_ids: list[int] = []
    # Post-event review form (Tally) — set by the reviews feature, read-only here.
    review_form_id: str | None
    review_form_url: str | None
    review_form_created_at: datetime | None
    archived: bool
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Event relations: speakers, sponsor links, partner links
# -----------------------------------------------------------------------------
#
# These manage the many-to-* tables that hang off an event (event_speaker,
# event_sponsor, event_partner). The public website reads them via the /website
# feed; the dashboard + MCP write them here. Logos/headshots live in media_asset
# and are reused across events (uploaded once per sponsor/partner/speaker).


class EventSpeakerBase(BaseModel):
    """All fields optional — reused for add and update (PATCH)."""

    # Link an existing roster person (autofills the display name) OR give a
    # free-text name for an external guest not in the directory.
    person_id: int | None = None
    name: str | None = None
    title: str | None = None  # e.g. "CTO at Acme"
    bio: str | None = None
    sort_order: int | None = None


class EventSpeakerCreate(EventSpeakerBase):
    """Add a speaker — needs a person_id or a name (validated in the service)."""


class EventSpeakerUpdate(EventSpeakerBase):
    """Every field optional; only those set are applied."""


class EventSpeakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    person_id: int | None
    name: str | None
    title: str | None
    bio: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class EventSponsorLink(BaseModel):
    """Link a sponsor to an event for the event's sponsor wall."""

    sponsor_id: int
    tier: str | None = None  # optional per-event tier override
    sort_order: int | None = None


class EventSponsorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    sponsor_id: int
    tier: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class EventPartnerLink(BaseModel):
    """Link a partner (collaborator club/org) to an event."""

    partner_id: int
    role: str | None = None  # optional per-event label
    sort_order: int | None = None


class EventPartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    partner_id: int
    role: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class EventConnectionLink(BaseModel):
    """Connect another event to this one (a symmetric, visual-only relation)."""

    other_event_id: int
    label: str | None = None  # optional, shown on both events e.g. "Series"


class EventConnectionOut(BaseModel):
    """One connection, resolved relative to the event it was queried from:
    `other_event_*` describe the event on the far side of the link."""

    id: int           # the connection row id
    event_id: int     # the event this was queried from
    other_event_id: int
    other_event_name: str | None = None
    other_event_status: str | None = None
    other_event_start_date: date | None = None
    label: str | None = None
    created_at: datetime
    updated_at: datetime
