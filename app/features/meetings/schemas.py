"""Pydantic models for the meetings feature."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Pre-meeting agenda
# --------------------------------------------------------------------------- #

class AgendaItemIn(BaseModel):
    """One agenda item as submitted. `id`/`order` are optional on input — the
    service assigns a stable id to new items and renumbers `order` to the list
    position, so the client can just send items in display order."""

    id: str | None = None
    order: int | None = None
    title: str = Field(min_length=1, max_length=512)
    owner_person_id: int | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    notes: str | None = None  # markdown
    related_task_id: int | None = None
    related_event_id: int | None = None


class AgendaSet(BaseModel):
    """Body for PUT /meetings/{id}/agenda — replaces the whole item list."""

    items: list[AgendaItemIn] = Field(default_factory=list)


class AgendaItemOut(BaseModel):
    """One agenda item as stored/returned — id + order always present.

    This snake_case shape is the on-disk contract for the `meeting.agenda_items`
    JSONB, which dsec-hub reads and writes directly via Drizzle. Keep it in sync
    with dsec-hub's `AgendaItem` type (db/workspace-schema.ts) — both apps must
    agree on the exact keys or a field silently drops in round-trips.
    """

    id: str
    order: int
    title: str
    owner_person_id: int | None = None
    duration_minutes: int | None = None
    notes: str | None = None
    related_task_id: int | None = None
    related_event_id: int | None = None


class AgendaOut(BaseModel):
    """The full agenda view: ordered items + the live total estimated duration,
    plus share state (status, token, public URL)."""

    meeting_id: int
    agenda_status: str
    agenda_shared_at: datetime | None = None
    agenda_share_token: str | None = None
    share_url: str | None = None
    total_estimated_minutes: int
    items: list[AgendaItemOut]


# --------------------------------------------------------------------------- #
# Meeting
# --------------------------------------------------------------------------- #

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
    # Optional agenda set at creation time (normalised + FK-validated in service).
    agenda_items: list[AgendaItemIn] | None = None


class MeetingUpdate(MeetingBase):
    """Every field optional; only those set are applied (PATCH semantics).

    Note: the agenda is edited through the dedicated /agenda endpoints, not here,
    so the generic update never touches agenda fields.
    """


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
    # Pre-meeting agenda (read-only here; written via the /agenda endpoints).
    agenda_items: list | None
    agenda_status: str
    agenda_shared_at: datetime | None
    agenda_share_token: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
