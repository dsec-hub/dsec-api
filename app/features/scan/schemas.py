"""Pydantic models for the /scan QR-wall feature.

A `ScanTarget` is one QR card on the public, big-screen `/scan` page. Mirrors the
link-tree's Link but QR-shaped (label/caption/url/pretty/accent). See
app/models.py::ScanTarget and the shared contract.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.urls import validate_public_url

# The 4 light scan accents (ink-on-colour headers). NULL ⇒ auto-cycle by position.
SCAN_ACCENTS = {"blue", "pink", "yellow", "mint"}


class ScanTargetBase(BaseModel):
    """Shared fields — reused for create (label/url overridden) and update."""

    label: str | None = None
    caption: str | None = None
    url: str | None = None
    pretty: str | None = None
    accent: str | None = Field(default=None, max_length=16)
    display_order: int | None = None
    is_visible: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        # A QR card encodes an absolute destination (no relative /paths: a phone
        # camera can't resolve them). javascript:/data:/etc are rejected (XSS).
        return validate_public_url(v, max_length=2048, allow_relative=False)

    @field_validator("accent")
    @classmethod
    def _validate_accent(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            return None
        if v not in SCAN_ACCENTS:
            raise PydanticCustomError(
                "value_error", "accent must be one of: blue, pink, yellow, mint"
            )
        return v


class ScanTargetCreate(ScanTargetBase):
    label: str  # required on create
    url: str    # a destination is required on create


class ScanTargetUpdate(ScanTargetBase):
    """Every field optional; only those set are applied (PATCH semantics)."""


class ScanTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    caption: str | None
    url: str
    pretty: str | None
    accent: str | None
    display_order: int
    is_visible: bool
    archived: bool
    created_at: datetime
    updated_at: datetime


class ReorderIn(BaseModel):
    """Body for POST /scan/reorder — the scan-target ids in their new order."""

    ordered_ids: list[int]


class ScanPageUpdate(BaseModel):
    """The singleton /scan header — both fields optional (PATCH semantics). A
    blank value clears the field (→ NULL) so the built-in default copy shows
    again. The QR cards are managed separately (ScanTarget)."""

    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=300)

    @field_validator("title", "description")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class ScanPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    description: str | None
    updated_at: datetime
