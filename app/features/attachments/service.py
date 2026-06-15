"""Attachment repository functions — compress, store, and track files.

Mirrors media/service.py: `create_attachment` runs the compression pipeline and
uploads to Supabase before inserting the row; `delete_attachment` removes the
storage object then the row so the bucket and DB never drift.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.media import storage

from app.models import Attachment

from . import processing
from .schemas import ENTITY_TYPES


def list_attachments(
    db: Session, *, entity_type: str, entity_id: int
) -> list[Attachment]:
    stmt = (
        select(Attachment)
        .where(
            Attachment.archived.is_(False),
            Attachment.entity_type == entity_type,
            Attachment.entity_id == entity_id,
        )
        .order_by(Attachment.sort_order, Attachment.id)
    )
    return list(db.execute(stmt).scalars().all())


def get_attachment(db: Session, attachment_id: int) -> Attachment | None:
    return db.get(Attachment, attachment_id)


def create_attachment(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    file_bytes: bytes,
    filename: str | None = None,
    content_type: str | None = None,
    title: str | None = None,
) -> Attachment:
    """Validate, compress, upload to Supabase, persist the row.

    Raises ``ValueError`` for bad input (entity/unsupported file) — the router
    maps that to a 422.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")

    processed = processing.process_attachment(
        file_bytes, content_type=content_type, filename=filename
    )

    uid = uuid.uuid4().hex
    path = f"{entity_type}/{entity_id}/{uid}.{processed.ext}"
    url = storage.upload_object(path, processed.data, processed.content_type)

    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        kind=processed.kind,
        title=title,
        original_filename=filename,
        content_type=processed.content_type,
        url=url,
        path=path,
        size_bytes=len(processed.data),
        original_size_bytes=len(file_bytes),
        width=processed.width,
        height=processed.height,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def update_attachment(db: Session, attachment_id: int, data: dict) -> Attachment | None:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        return None
    for key, value in data.items():
        setattr(attachment, key, value)
    db.commit()
    db.refresh(attachment)
    return attachment


def delete_attachment(db: Session, attachment_id: int) -> bool:
    """Hard delete: remove the storage object, then the row."""
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        return False
    storage.delete_objects([attachment.path])
    db.delete(attachment)
    db.commit()
    return True
