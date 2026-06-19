"""Document repository functions — pure, Session-based, reused by REST + MCP.

Convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document


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
    doc = Document(**data)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def update_document(db: Session, document_id: int, data: dict) -> Document | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
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
