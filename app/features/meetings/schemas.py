"""Pydantic models for the meetings feature."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class MeetingBase(BaseModel):
    """All fields optional — reused for create (title overridden) and update."""

    title: str | None = None
    type: str | None = None
    committee: str | None = None
    meeting_date: date | None = None
    location: str | None = None
    attendees: list | None = None
    transcript: str | None = None
    summary: str | None = None
    notes: str | None = None
    action_items: list | None = None
    status: str | None = None
    related_event_id: int | None = None
    created_by: str | None = None


class MeetingCreate(MeetingBase):
    title: str  # required on create


class MeetingUpdate(MeetingBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class GenerateNotes(BaseModel):
    """Body for POST /meetings/{id}/generate-notes."""

    transcript: str | None = None  # if given, replaces the meeting's transcript
    create_document: bool = True   # also create a MeetingNotes document


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    type: str | None
    committee: str | None
    meeting_date: date | None
    location: str | None
    attendees: list | None
    transcript: str | None
    summary: str | None
    notes: str | None
    action_items: list | None
    status: str | None
    related_event_id: int | None
    created_by: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
