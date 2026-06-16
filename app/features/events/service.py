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

from app.models import Event


def list_events(
    db: Session,
    *,
    archived: bool = False,
    status: str | None = None,
    type: str | None = None,
    trimester: str | None = None,
    event_lead_id: int | None = None,
    is_public: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Event]:
    stmt = select(Event)
    if not archived:
        stmt = stmt.where(Event.archived.is_(False))
    if is_public is not None:
        stmt = stmt.where(Event.is_public.is_(is_public))
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
    return list(db.execute(stmt).scalars().all())


def get_event(db: Session, event_id: int) -> Event | None:
    return db.get(Event, event_id)


def create_event(db: Session, data: dict) -> Event:
    event = Event(**data)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def update_event(db: Session, event_id: int, data: dict) -> Event | None:
    event = db.get(Event, event_id)
    if event is None:
        return None
    for key, value in data.items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    return event


def archive_event(db: Session, event_id: int) -> Event | None:
    event = db.get(Event, event_id)
    if event is None:
        return None
    event.archived = True
    db.commit()
    db.refresh(event)
    return event
