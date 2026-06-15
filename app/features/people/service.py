"""People repository functions — pure, Session-based, reused by REST + MCP.

Convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Person


def list_people(
    db: Session,
    *,
    archived: bool = False,
    type: str | None = None,
    committee: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Person]:
    stmt = select(Person)
    if not archived:
        stmt = stmt.where(Person.archived.is_(False))
    if type:
        stmt = stmt.where(Person.type == type)
    if committee:
        stmt = stmt.where(Person.committee == committee)
    if status:
        stmt = stmt.where(Person.status == status)
    if search:
        term = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Person.name).like(term),
                func.lower(Person.email).like(term),
            )
        )
    stmt = stmt.order_by(Person.name).limit(min(limit, 200)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_person(db: Session, person_id: int) -> Person | None:
    return db.get(Person, person_id)


def create_person(db: Session, data: dict) -> Person:
    person = Person(**data)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def update_person(db: Session, person_id: int, data: dict) -> Person | None:
    person = db.get(Person, person_id)
    if person is None:
        return None
    for key, value in data.items():
        setattr(person, key, value)
    db.commit()
    db.refresh(person)
    return person


def archive_person(db: Session, person_id: int) -> Person | None:
    person = db.get(Person, person_id)
    if person is None:
        return None
    person.archived = True
    db.commit()
    db.refresh(person)
    return person
