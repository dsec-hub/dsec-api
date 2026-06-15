"""Pydantic models for the attachments feature."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# Entity types that can own attachments. Extend as more sections gain uploads.
ENTITY_TYPES = {"sponsor"}


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    kind: str
    title: str | None
    original_filename: str | None
    content_type: str | None
    url: str
    size_bytes: int | None
    original_size_bytes: int | None
    width: int | None
    height: int | None
    sort_order: int
    created_at: datetime


class AttachmentUpdate(BaseModel):
    """PATCH metadata — only provided fields are applied. No new file."""

    title: str | None = None
    sort_order: int | None = None
