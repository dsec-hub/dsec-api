"""Scan-target repository functions — pure, Session-based, reused by REST + MCP.

Mirrors the link-tree service (app/features/links/service.py) exactly, minus the
singleton profile (the QR wall is just an ordered list):

    list_scan_targets(db, *, include_hidden, archived, limit, offset) -> list
    get_scan_target(db, id) -> ScanTarget | None
    create_scan_target(db, data) -> ScanTarget
    update_scan_target(db, id, data) -> ScanTarget | None   (PATCH)
    archive_scan_target(db, id) -> ScanTarget | None        (soft delete)
    reorder_scan_targets(db, ordered_ids) -> list[ScanTarget]
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ScanPage, ScanTarget


def list_scan_targets(
    db: Session,
    *,
    include_hidden: bool = True,
    archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[ScanTarget]:
    """Scan targets ordered for display (display_order asc, then created_at asc).

    `include_hidden=False` drops `is_visible == False` (the public feed uses
    this); `archived=False` drops soft-deleted rows.
    """
    stmt = select(ScanTarget)
    if not archived:
        stmt = stmt.where(ScanTarget.archived.is_(False))
    if not include_hidden:
        stmt = stmt.where(ScanTarget.is_visible.is_(True))
    stmt = (
        stmt.order_by(
            ScanTarget.display_order.asc(), ScanTarget.created_at.asc(), ScanTarget.id.asc()
        )
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def get_scan_target(db: Session, target_id: int) -> ScanTarget | None:
    return db.get(ScanTarget, target_id)


def create_scan_target(db: Session, data: dict) -> ScanTarget:
    target = ScanTarget(**data)
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def update_scan_target(db: Session, target_id: int, data: dict) -> ScanTarget | None:
    target = db.get(ScanTarget, target_id)
    if target is None:
        return None
    for key, value in data.items():
        setattr(target, key, value)
    db.commit()
    db.refresh(target)
    return target


def archive_scan_target(db: Session, target_id: int) -> ScanTarget | None:
    target = db.get(ScanTarget, target_id)
    if target is None:
        return None
    target.archived = True
    db.commit()
    db.refresh(target)
    return target


def reorder_scan_targets(db: Session, ordered_ids: list[int]) -> list[ScanTarget]:
    """Persist a new ordering: set each target's display_order to its index in
    `ordered_ids`. Unknown ids are skipped. Returns the full display-ordered list."""
    by_id = {
        t.id: t
        for t in db.execute(
            select(ScanTarget).where(ScanTarget.id.in_(ordered_ids))
        ).scalars().all()
    }
    for index, target_id in enumerate(ordered_ids):
        target = by_id.get(target_id)
        if target is not None:
            target.display_order = index
    db.commit()
    return list_scan_targets(db, include_hidden=True, archived=False)


# --------------------------------------------------------------------------- #
# scan_page — the singleton header (title/description) shown above the wall
# --------------------------------------------------------------------------- #

# The singleton scan_page row id (there is only ever one).
PAGE_ID = 1

# The built-in heading used when the committee hasn't set its own — a NULL title
# or description falls back to these. Kept in sync with dsec-website's
# DEFAULT_SCAN_PAGE (the feed-unreachable fallback) so every surface shows the
# same default copy.
DEFAULT_PAGE_TITLE = "Point your camera. You're in."
DEFAULT_PAGE_DESCRIPTION = (
    "Scan a code below to connect with DSEC. No app to install, just your phone."
)


def get_page(db: Session, *, create_if_missing: bool = False) -> ScanPage:
    """Return the singleton /scan header (row id=1).

    When the row is missing: by default returns a transient (un-persisted) object
    with NULL copy so reads never fail on a fresh DB (consumers apply the default);
    with `create_if_missing=True` it inserts and returns the row (the upsert path).
    """
    page = db.get(ScanPage, PAGE_ID)
    if page is not None:
        return page
    if create_if_missing:
        page = ScanPage(id=PAGE_ID)
        db.add(page)
        db.commit()
        db.refresh(page)
        return page
    # Transient default — not added to the session. Populate updated_at so the
    # Pydantic output schema (which requires it) validates.
    return ScanPage(id=PAGE_ID, updated_at=datetime.now(timezone.utc))


def update_page(db: Session, data: dict) -> ScanPage:
    """Upsert the singleton header (row id=1): create it from defaults if absent,
    then apply the provided fields (PATCH semantics). A blank title/description
    clears the field (NULL) so the built-in default copy shows again."""
    # An empty PATCH / no-arg MCP call must not materialise the singleton row.
    if not data:
        return get_page(db)
    page = get_page(db, create_if_missing=True)
    for key, value in data.items():
        setattr(page, key, value)
    db.commit()
    db.refresh(page)
    return page
