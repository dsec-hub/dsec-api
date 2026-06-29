"""Pydantic models for the documents feature."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentBase(BaseModel):
    """All fields optional — reused for create (title overridden) and update."""

    title: str | None = None
    type: str | None = None
    committee: str | None = None
    content: str | None = None
    content_json: dict | None = None
    status: str | None = None
    # ---- Custom page publishing fields ----
    slug: str | None = None
    is_public: bool | None = None
    nav_label: str | None = None
    show_in_nav: bool | None = None
    nav_area: Literal["header", "footer"] | None = None
    nav_order: int | None = None
    seo_description: str | None = None
    cover_image_url: str | None = None
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
    slug: str | None
    is_public: bool
    nav_label: str | None
    show_in_nav: bool
    nav_area: str | None
    nav_order: int
    seo_description: str | None
    cover_image_url: str | None
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
