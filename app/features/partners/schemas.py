"""Pydantic models for the partners feature (collaborator clubs / orgs).

A partner is deliberately lightweight (no pipeline) — a name, website, notes,
and an uploadable logo (media_asset entity_type="partner", role="logo").
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PartnerBase(BaseModel):
    """All fields optional — reused for create (name overridden) and update."""

    name: str | None = None
    website: str | None = None
    notes: str | None = None


class PartnerCreate(PartnerBase):
    name: str  # required on create


class PartnerUpdate(PartnerBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    website: str | None
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
