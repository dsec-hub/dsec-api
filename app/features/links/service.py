"""Link-tree repository functions — pure, Session-based, reused by REST + MCP.

Mirrors the conventions shared by every workspace feature (see
app/features/partners/service.py):

    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)

Plus link-tree specifics: `reorder_links` (bulk display_order rewrite) and the
singleton profile helpers `get_profile` / `update_profile` (always row id=1).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Link, LinkProfile

# The singleton link_profile row id (there is only ever one).
PROFILE_ID = 1

# Sensible defaults for the singleton header when no row exists yet (e.g. a fresh
# DB before the seed migration runs). Kept in sync with the seed in the
# `a4e8c2f6d9b1_link_tree` migration and the shared contract defaults.
DEFAULT_PROFILE = {
    "title": "DSEC",
    "tagline": "Deakin Software Engineering Club",
    "mascot": "duck-mascot",
}


def list_links(
    db: Session,
    *,
    include_hidden: bool = True,
    archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Link]:
    """Links ordered for display (display_order asc, then created_at asc).

    `include_hidden=False` drops links with `is_visible == False` (the public
    feed uses this); `archived=False` drops soft-deleted rows.
    """
    stmt = select(Link)
    if not archived:
        stmt = stmt.where(Link.archived.is_(False))
    if not include_hidden:
        stmt = stmt.where(Link.is_visible.is_(True))
    stmt = (
        stmt.order_by(Link.display_order.asc(), Link.created_at.asc(), Link.id.asc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def get_link(db: Session, link_id: int) -> Link | None:
    return db.get(Link, link_id)


def create_link(db: Session, data: dict) -> Link:
    link = Link(**data)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def update_link(db: Session, link_id: int, data: dict) -> Link | None:
    link = db.get(Link, link_id)
    if link is None:
        return None
    for key, value in data.items():
        setattr(link, key, value)
    db.commit()
    db.refresh(link)
    return link


def archive_link(db: Session, link_id: int) -> Link | None:
    link = db.get(Link, link_id)
    if link is None:
        return None
    link.archived = True
    db.commit()
    db.refresh(link)
    return link


def reorder_links(db: Session, ordered_ids: list[int]) -> list[Link]:
    """Persist a new ordering: set each link's display_order to its index in
    `ordered_ids`. Unknown ids are skipped. Returns the full display-ordered
    list afterwards so callers can echo the new state."""
    by_id = {
        link.id: link
        for link in db.execute(select(Link).where(Link.id.in_(ordered_ids))).scalars().all()
    }
    for index, link_id in enumerate(ordered_ids):
        link = by_id.get(link_id)
        if link is not None:
            link.display_order = index
    db.commit()
    return list_links(db, include_hidden=True, archived=False)


def get_profile(db: Session, *, create_if_missing: bool = False) -> LinkProfile:
    """Return the singleton header (row id=1).

    When the row is missing: by default returns a transient (un-persisted)
    default object so reads never fail on a fresh DB; with
    `create_if_missing=True` it inserts and returns the seeded row (used by the
    upsert path).
    """
    profile = db.get(LinkProfile, PROFILE_ID)
    if profile is not None:
        return profile
    if create_if_missing:
        profile = LinkProfile(id=PROFILE_ID, **DEFAULT_PROFILE)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    # Transient default — not added to the session. Populate updated_at so the
    # Pydantic output schema (which requires it) validates.
    return LinkProfile(id=PROFILE_ID, updated_at=datetime.now(timezone.utc), **DEFAULT_PROFILE)


def update_profile(db: Session, data: dict) -> LinkProfile:
    """Upsert the singleton header (row id=1): create it from defaults if absent,
    then apply the provided fields (PATCH semantics)."""
    # An empty PATCH / no-arg MCP call must not materialise the singleton row.
    if not data:
        return get_profile(db)
    profile = get_profile(db, create_if_missing=True)
    for key, value in data.items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile
