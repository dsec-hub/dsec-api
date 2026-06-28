"""Event repository functions — pure, Session-based, reused by REST + MCP.

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

from app.core.owners import attach_owner_ids, set_owners
from app.models import Event, EventOwner


def _attach_owners(db: Session, rows):
    """Populate `.co_owner_ids` on an event (or list) for the Out schema."""
    attach_owner_ids(db, EventOwner, EventOwner.event_id, rows)
    return rows


def list_events(
    db: Session,
    *,
    archived: bool = False,
    status: str | None = None,
    type: str | None = None,
    trimester: str | None = None,
    event_lead_id: int | None = None,
    is_public: bool | None = None,
    is_flagship: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Event]:
    stmt = select(Event)
    if not archived:
        stmt = stmt.where(Event.archived.is_(False))
    if is_public is not None:
        stmt = stmt.where(Event.is_public.is_(is_public))
    if is_flagship is not None:
        stmt = stmt.where(Event.is_flagship.is_(is_flagship))
    if status:
        stmt = stmt.where(Event.status == status)
    if type:
        stmt = stmt.where(Event.type == type)
    if trimester:
        stmt = stmt.where(Event.trimester == trimester)
    if event_lead_id is not None:
        stmt = stmt.where(Event.event_lead_id == event_lead_id)
    stmt = (
        stmt.order_by(Event.start_date.desc().nullslast())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return _attach_owners(db, list(db.execute(stmt).scalars().all()))


def get_event(db: Session, event_id: int) -> Event | None:
    return _attach_owners(db, db.get(Event, event_id))


def create_event(db: Session, data: dict) -> Event:
    data = dict(data)
    co_owner_ids = data.pop("co_owner_ids", None)
    event = Event(**data)
    db.add(event)
    db.commit()
    db.refresh(event)
    if co_owner_ids is not None:
        set_owners(db, EventOwner, EventOwner.event_id, event.id, co_owner_ids, exclude=event.event_lead_id)
    return _attach_owners(db, event)


def update_event(db: Session, event_id: int, data: dict) -> Event | None:
    event = db.get(Event, event_id)
    if event is None:
        return None
    data = dict(data)
    co_owner_ids = data.pop("co_owner_ids", None)
    for key, value in data.items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    if co_owner_ids is not None:
        set_owners(db, EventOwner, EventOwner.event_id, event.id, co_owner_ids, exclude=event.event_lead_id)
    return _attach_owners(db, event)


def archive_event(db: Session, event_id: int) -> Event | None:
    event = db.get(Event, event_id)
    if event is None:
        return None
    event.archived = True
    db.commit()
    db.refresh(event)
    return _attach_owners(db, event)
