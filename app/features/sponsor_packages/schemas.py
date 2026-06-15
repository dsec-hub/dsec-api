"""Sponsor package schemas (public-facing tier definitions)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SponsorPackageBase(BaseModel):
    name: str | None = None
    pitch: str | None = None
    price: str | None = None
    includes: list[str] | None = None
    featured: bool | None = None
    is_visible: bool | None = None
    display_order: int | None = None


class SponsorPackageCreate(SponsorPackageBase):
    name: str  # required on create


class SponsorPackageUpdate(SponsorPackageBase):
    """All optional (PATCH)."""


class SponsorPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    pitch: str | None
    price: str | None
    includes: list | None
    featured: bool
    is_visible: bool
    display_order: int
    created_at: datetime
    updated_at: datetime
