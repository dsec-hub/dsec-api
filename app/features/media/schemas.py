"""Pydantic models for the media feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# portal_account — a member-portal login's verification face photo (entity_id is
# the portal_account.id; the portal owns that table and stores the returned url).
# document — images used by a custom-page document's content blocks (entity_id is
# the document.id; blocks reference the returned media URLs).
ENTITY_TYPES = {"event", "project", "sponsor", "speaker", "person", "partner",
                "portal_account", "document"}
# image/poster/banner — gallery roles for events & projects. logo — a sponsor's
# or partner's brand mark (kept transparent). photo — a speaker's headshot, a
# person's (roster member's) profile picture, or a portal member's face photo.
ROLES = {"image", "poster", "banner", "logo", "photo"}


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    role: str
    alt_text: str | None
    webp_url: str
    png_url: str
    width: int | None
    height: int | None
    size_bytes: int | None
    sort_order: int
    created_at: datetime


class MediaUpdate(BaseModel):
    """PATCH metadata — only provided fields are applied. No new file."""

    alt_text: str | None = None
    role: str | None = None
    sort_order: int | None = None
