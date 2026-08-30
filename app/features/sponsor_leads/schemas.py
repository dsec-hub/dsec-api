"""Sponsor lead schemas (inbound enquiries from dsec-website + Cal.com)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SponsorLeadCreate(BaseModel):
    # Lengths mirror the SponsorLead columns in app/models.py (source 32, tier 64,
    # name 256, email 256, company 256, phone 64, budget 64) exactly. Keep them in
    # step — the point is that an over-length value becomes a 422 naming the field,
    # not a 500 from Postgres at commit time.
    #
    # Known source values: pricing_unlock | enquiry (dsec-website) | cal_booking
    # (Cal.com) | flagship (flagship signup). Not certain the set is closed, so a
    # max_length rather than a Literal — narrowing can be a follow-up.
    source: str = Field(max_length=32)
    tier: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=256)
    email: str = Field(max_length=256)
    company: str | None = Field(default=None, max_length=256)
    phone: str | None = Field(default=None, max_length=64)
    budget: str | None = Field(default=None, max_length=64)
    message: str | None = None      # TEXT column — unbounded is correct


class SponsorLeadUpdate(BaseModel):
    """Partial update — only status and internal notes are exec-editable."""

    status: str | None = None
    notes: str | None = None


class SponsorLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    tier: str | None
    name: str | None
    email: str
    company: str | None
    phone: str | None
    budget: str | None
    message: str | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
