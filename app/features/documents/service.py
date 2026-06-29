"""Document repository functions — pure, Session-based, reused by REST + MCP.

Convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document


# Top-level website routes a published page must never shadow. Kept in lock-step
# with the website's RESERVED set (dsec-website/src/app/[slug]/page.tsx) and the
# hub's RESERVED_SLUGS — a page with one of these slugs would 404 on the site.
RESERVED_SLUGS = frozenset({
    "about", "api", "contact", "events", "heroes", "join", "links", "projects",
    "scan", "sponsor", "team", "pages", "preview", "p",
})


def slugify(text: str) -> str:
    """URL-safe slug from a title: lowercase, non-alphanumerics → single hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "page"


def get_document_by_slug(db: Session, slug: str) -> Document | None:
    return db.execute(
        select(Document).where(Document.slug == slug)
    ).scalar_one_or_none()


def validate_slug(db: Session, raw: str, *, exclude_id: int | None = None) -> str:
    """Normalise an explicitly-chosen slug and reject reserved/duplicate ones.

    Raises ``ValueError`` (→ 422 at the router) when the slug shadows a real
    website route or is already taken by another document, so a page can never be
    published to an unreachable URL via REST/MCP."""
    slug = slugify(raw)
    if slug in RESERVED_SLUGS:
        raise ValueError(f"slug '{slug}' is reserved by a built-in page")
    existing = get_document_by_slug(db, slug)
    if existing is not None and existing.id != exclude_id:
        raise ValueError(f"slug '{slug}' is already in use")
    return slug


def ensure_unique_slug(db: Session, base: str, *, exclude_id: int | None = None) -> str:
    """Return `base` (slugified) made unique against existing slugs AND the
    reserved website routes, appending -2, -3 … . Skips the row being updated."""
    base = slugify(base)
    candidate = base if base not in RESERVED_SLUGS else f"{base}-page"
    n = 1
    while True:
        clash = candidate in RESERVED_SLUGS
        if not clash:
            existing = get_document_by_slug(db, candidate)
            clash = existing is not None and existing.id != exclude_id
        if not clash:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def list_documents(
    db: Session,
    *,
    archived: bool = False,
    type: str | None = None,
    status: str | None = None,
    assignee_id: int | None = None,
    parent_id: int | None = None,
    top_level: bool = False,
    related_event_id: int | None = None,
    related_sponsor_id: int | None = None,
    related_project_id: int | None = None,
    related_meeting_id: int | None = None,
    related_task_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Document]:
    stmt = select(Document)
    if not archived:
        stmt = stmt.where(Document.archived.is_(False))
    if type:
        stmt = stmt.where(Document.type == type)
    if status:
        stmt = stmt.where(Document.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Document.assignee_id == assignee_id)
    if top_level:
        stmt = stmt.where(Document.parent_id.is_(None))
    elif parent_id is not None:
        stmt = stmt.where(Document.parent_id == parent_id)
    if related_event_id is not None:
        stmt = stmt.where(Document.related_event_id == related_event_id)
    if related_sponsor_id is not None:
        stmt = stmt.where(Document.related_sponsor_id == related_sponsor_id)
    if related_project_id is not None:
        stmt = stmt.where(Document.related_project_id == related_project_id)
    if related_meeting_id is not None:
        stmt = stmt.where(Document.related_meeting_id == related_meeting_id)
    if related_task_id is not None:
        stmt = stmt.where(Document.related_task_id == related_task_id)
    stmt = stmt.order_by(Document.updated_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_document(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def create_document(db: Session, data: dict) -> Document:
    if data.get("slug"):  # an explicitly-chosen page slug — normalise + guard
        data["slug"] = validate_slug(db, data["slug"])
    doc = Document(**data)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def update_document(db: Session, document_id: int, data: dict) -> Document | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    if data.get("slug"):  # changing the page slug — normalise + guard
        data["slug"] = validate_slug(db, data["slug"], exclude_id=document_id)
    for key, value in data.items():
        setattr(doc, key, value)
    db.commit()
    db.refresh(doc)
    return doc


def archive_document(db: Session, document_id: int) -> Document | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    doc.archived = True
    db.commit()
    db.refresh(doc)
    return doc
