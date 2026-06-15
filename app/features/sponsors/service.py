"""Sponsor repository functions (same convention as projects/service.py)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Sponsor


def list_sponsors(
    db: Session,
    *,
    archived: bool = False,
    stage: str | None = None,
    tier: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Sponsor]:
    stmt = select(Sponsor)
    if not archived:
        stmt = stmt.where(Sponsor.archived.is_(False))
    if stage:
        stmt = stmt.where(Sponsor.stage == stage)
    if tier:
        stmt = stmt.where(Sponsor.tier == tier)
    stmt = stmt.order_by(Sponsor.updated_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_sponsor(db: Session, sponsor_id: int) -> Sponsor | None:
    return db.get(Sponsor, sponsor_id)


def create_sponsor(db: Session, data: dict) -> Sponsor:
    sponsor = Sponsor(**data)
    db.add(sponsor)
    db.commit()
    db.refresh(sponsor)
    return sponsor


def update_sponsor(db: Session, sponsor_id: int, data: dict) -> Sponsor | None:
    sponsor = db.get(Sponsor, sponsor_id)
    if sponsor is None:
        return None
    for key, value in data.items():
        setattr(sponsor, key, value)
    db.commit()
    db.refresh(sponsor)
    return sponsor


def archive_sponsor(db: Session, sponsor_id: int) -> Sponsor | None:
    sponsor = db.get(Sponsor, sponsor_id)
    if sponsor is None:
        return None
    sponsor.archived = True
    db.commit()
    db.refresh(sponsor)
    return sponsor
