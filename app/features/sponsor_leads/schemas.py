"""Sponsor lead schemas (inbound enquiries from dsec-website + Cal.com)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SponsorLeadCreate(BaseModel):
    # pricing_unlock | enquiry | cal_booking
    source: str
    tier: str | None = None
    name: str | None = None
    email: str
    company: str | None = None
    phone: str | None = None
    budget: str | None = None
    message: str | None = None


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
