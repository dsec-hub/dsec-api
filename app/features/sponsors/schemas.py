"""Sponsor schemas (CRM/pipeline)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SponsorBase(BaseModel):
    organisation: str | None = None
    stage: str | None = None
    relationship_type: str | None = None
    contact_person_id: int | None = None
    tier: str | None = None
    value_aud: float | None = None
    support_types: list | None = None
    dusa_approved: bool | None = None
    show_on_website: bool | None = None
    contact_email: str | None = None
    website: str | None = None
    next_action: str | None = None
    next_action_date: date | None = None
    last_contact_date: date | None = None
    notes: str | None = None


class SponsorCreate(SponsorBase):
    organisation: str  # required on create


class SponsorUpdate(SponsorBase):
    """All optional (PATCH)."""


class SponsorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organisation: str
    stage: str | None
    relationship_type: str | None
    contact_person_id: int | None
    tier: str | None
    value_aud: float | None
    support_types: list | None
    dusa_approved: bool
    show_on_website: bool
    contact_email: str | None
    website: str | None
    next_action: str | None
    next_action_date: date | None
    last_contact_date: date | None
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Sponsor contacts (individual people attached to a sponsorship)
# -----------------------------------------------------------------------------


class SponsorContactBase(BaseModel):
    """All fields optional — reused for add and update (PATCH)."""

    # Link an existing roster person OR carry a free-text name for someone not
    # in the directory.
    person_id: int | None = None
    name: str | None = None
    role: str | None = None  # Organiser / Contact / Signatory / Other
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    sort_order: int | None = None


class SponsorContactCreate(SponsorContactBase):
    """Add a contact — needs a person_id or a name (validated in the service)."""


class SponsorContactUpdate(SponsorContactBase):
    """Every field optional; only those set are applied."""


class SponsorContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sponsor_id: int
    person_id: int | None
    name: str | None
    role: str | None
    email: str | None
    phone: str | None
    notes: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime
