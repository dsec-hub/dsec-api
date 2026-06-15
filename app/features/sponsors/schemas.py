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
    contact_email: str | None
    website: str | None
    next_action: str | None
    next_action_date: date | None
    last_contact_date: date | None
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
