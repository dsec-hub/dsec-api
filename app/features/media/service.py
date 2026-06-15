"""Media repository functions — process, store, and track image assets.

Convention mirrors the other workspace features, with the addition of object
storage: `create_media` runs the Pillow pipeline and uploads to Supabase before
inserting the row; `delete_media` removes the storage objects then the row so
the bucket and DB never drift.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaAsset

from . import processing, storage
from .schemas import ENTITY_TYPES, ROLES


def list_media(db: Session, *, entity_type: str, entity_id: int) -> list[MediaAsset]:
    stmt = (
        select(MediaAsset)
        .where(
            MediaAsset.archived.is_(False),
            MediaAsset.entity_type == entity_type,
            MediaAsset.entity_id == entity_id,
        )
        .order_by(MediaAsset.sort_order, MediaAsset.id)
    )
    return list(db.execute(stmt).scalars().all())


def list_media_for(
    db: Session, *, entity_type: str, entity_ids: list[int]
) -> dict[int, list[MediaAsset]]:
    """Batched fetch for many entities at once (avoids N+1 in list endpoints)."""
    if not entity_ids:
        return {}
    stmt = (
        select(MediaAsset)
        .where(
            MediaAsset.archived.is_(False),
            MediaAsset.entity_type == entity_type,
            MediaAsset.entity_id.in_(entity_ids),
        )
        .order_by(MediaAsset.sort_order, MediaAsset.id)
    )
    out: dict[int, list[MediaAsset]] = {eid: [] for eid in entity_ids}
    for row in db.execute(stmt).scalars().all():
        out.setdefault(row.entity_id, []).append(row)
    return out


def get_media(db: Session, media_id: int) -> MediaAsset | None:
    return db.get(MediaAsset, media_id)


def create_media(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    role: str,
    file_bytes: bytes,
    filename: str | None = None,
    alt_text: str | None = None,
) -> MediaAsset:
    """Validate, process (WebP + PNG), upload to Supabase, persist the row.

    Raises ``ValueError`` for bad input (entity/role/image) — the router maps
    that to a 422.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")

    # Sponsor logos keep their transparency; everything else flattens to white.
    processed = processing.process_image(file_bytes, keep_transparency=(role == "logo"))

    uid = uuid.uuid4().hex
    base = f"{entity_type}/{entity_id}/{uid}"
    webp_path, png_path = f"{base}.webp", f"{base}.png"

    webp_url = storage.upload_object(webp_path, processed.webp_bytes, "image/webp")
    png_url = storage.upload_object(png_path, processed.png_bytes, "image/png")

    asset = MediaAsset(
        entity_type=entity_type,
        entity_id=entity_id,
        role=role,
        alt_text=alt_text,
        original_filename=filename,
        webp_url=webp_url,
        png_url=png_url,
        webp_path=webp_path,
        png_path=png_path,
        width=processed.width,
        height=processed.height,
        size_bytes=len(processed.webp_bytes),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def update_media(db: Session, media_id: int, data: dict) -> MediaAsset | None:
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        return None
    for key, value in data.items():
        setattr(asset, key, value)
    db.commit()
    db.refresh(asset)
    return asset


def delete_media(db: Session, media_id: int) -> bool:
    """Hard delete: remove the storage objects, then the row. Returns False if
    the row was not found."""
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        return False
    storage.delete_objects([asset.webp_path, asset.png_path])
    db.delete(asset)
    db.commit()
    return True
