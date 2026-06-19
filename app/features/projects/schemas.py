"""Pydantic models for the projects feature."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    """All fields optional — reused for create (name overridden) and update."""

    name: str | None = None
    slug: str | None = None
    summary: str | None = None
    description: str | None = None
    status: str | None = None
    category: str | None = None
    tech_tags: list[str] | None = None
    lead_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    repo_url: str | None = None
    demo_url: str | None = None
    image_url: str | None = None
    featured: bool | None = None
    is_public: bool | None = None
    related_event_id: int | None = None
    notes: str | None = None
    # Additional owners beyond `lead_id` (the primary lead). Full replace on PATCH;
    # omit to leave unchanged, [] to clear. The primary is never duplicated here.
    co_owner_ids: list[int] | None = None


class ProjectCreate(ProjectBase):
    name: str  # required on create


class ProjectUpdate(ProjectBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None
    summary: str | None
    description: str | None
    status: str | None
    category: str | None
    tech_tags: list | None
    lead_id: int | None
    start_date: date | None
    end_date: date | None
    repo_url: str | None
    demo_url: str | None
    image_url: str | None
    featured: bool
    is_public: bool
    related_event_id: int | None
    notes: str | None
    co_owner_ids: list[int] = []
    archived: bool
    created_at: datetime
    updated_at: datetime
