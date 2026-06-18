"""Meeting repository functions — pure, Session-based, reused by REST + MCP.

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

from app.models import Meeting


def list_meetings(
    db: Session,
    *,
    archived: bool = False,
    type: str | None = None,
    committee: str | None = None,
    status: str | None = None,
    related_event_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Meeting]:
    stmt = select(Meeting)
    if not archived:
        stmt = stmt.where(Meeting.archived.is_(False))
    if type:
        stmt = stmt.where(Meeting.type == type)
    if committee:
        stmt = stmt.where(Meeting.committee == committee)
    if status:
        stmt = stmt.where(Meeting.status == status)
    if related_event_id is not None:
        stmt = stmt.where(Meeting.related_event_id == related_event_id)
    stmt = (
        stmt.order_by(Meeting.meeting_date.desc().nullslast())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def get_meeting(db: Session, meeting_id: int) -> Meeting | None:
    return db.get(Meeting, meeting_id)


def create_meeting(db: Session, data: dict) -> Meeting:
    meeting = Meeting(**data)
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def update_meeting(db: Session, meeting_id: int, data: dict) -> Meeting | None:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    for key, value in data.items():
        setattr(meeting, key, value)
    db.commit()
    db.refresh(meeting)
    return meeting


def archive_meeting(db: Session, meeting_id: int) -> Meeting | None:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    meeting.archived = True
    db.commit()
    db.refresh(meeting)
    return meeting
