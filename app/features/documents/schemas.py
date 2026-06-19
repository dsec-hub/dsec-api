"""Pydantic models for the documents feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentBase(BaseModel):
    """All fields optional — reused for create (title overridden) and update."""

    title: str | None = None
    type: str | None = None
    committee: str | None = None
    content: str | None = None
    content_json: dict | None = None
    status: str | None = None
    parent_id: int | None = None
    assignee_id: int | None = None
    related_event_id: int | None = None
    related_sponsor_id: int | None = None
    related_project_id: int | None = None
    related_meeting_id: int | None = None
    related_task_id: int | None = None
    created_by: str | None = None


class DocumentCreate(DocumentBase):
    title: str  # required on create


class DocumentUpdate(DocumentBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    type: str | None
    committee: str | None
    content: str | None
    content_json: dict | None
    status: str | None
    parent_id: int | None
    assignee_id: int | None
    related_event_id: int | None
    related_sponsor_id: int | None
    related_project_id: int | None
    related_meeting_id: int | None
    related_task_id: int | None
    created_by: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
