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
    # Post-event review form (Tally) — set by the reviews feature, read-only here.
    review_form_id: str | None
    review_form_url: str | None
    review_form_created_at: datetime | None
    archived: bool
    created_at: datetime
    updated_at: datetime
