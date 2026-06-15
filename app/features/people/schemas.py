"""Pydantic models for the people feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PersonBase(BaseModel):
    """All fields optional — reused for create (name overridden) and update."""

    name: str | None = None
    type: str | None = None
    committee: str | None = None
    role_title: str | None = None
    email: str | None = None
    status: str | None = None
    notes: str | None = None
    # Public website fields (see Person model).
    bio: str | None = None
    show_on_website: bool | None = None
    display_order: int | None = None


class PersonCreate(PersonBase):
    name: str  # required on create


class PersonUpdate(PersonBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str | None
    committee: str | None
    role_title: str | None
    email: str | None
    status: str | None
    notes: str | None
    bio: str | None
    show_on_website: bool
    display_order: int
    archived: bool
    created_at: datetime
    updated_at: datetime
