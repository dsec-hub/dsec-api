"""Partner repository functions — pure, Session-based, reused by REST + MCP.

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

from app.models import Partner


def list_partners(
    db: Session,
    *,
    archived: bool = False,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Partner]:
    stmt = select(Partner)
    if not archived:
        stmt = stmt.where(Partner.archived.is_(False))
    if search:
        term = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Partner.name).like(term),
                func.lower(Partner.website).like(term),
            )
        )
    stmt = stmt.order_by(Partner.name).limit(min(limit, 200)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_partner(db: Session, partner_id: int) -> Partner | None:
    return db.get(Partner, partner_id)


def create_partner(db: Session, data: dict) -> Partner:
    partner = Partner(**data)
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return partner


def update_partner(db: Session, partner_id: int, data: dict) -> Partner | None:
    partner = db.get(Partner, partner_id)
    if partner is None:
        return None
    for key, value in data.items():
        setattr(partner, key, value)
    db.commit()
    db.refresh(partner)
    return partner


def archive_partner(db: Session, partner_id: int) -> Partner | None:
    partner = db.get(Partner, partner_id)
    if partner is None:
        return None
    partner.archived = True
    db.commit()
    db.refresh(partner)
    return partner
