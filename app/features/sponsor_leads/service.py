"""Sponsor lead repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SponsorLead


def create_lead(db: Session, data: dict) -> SponsorLead:
    lead = SponsorLead(**data)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def list_leads(
    db: Session,
    *,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[SponsorLead]:
    stmt = select(SponsorLead)
    if status:
        stmt = stmt.where(SponsorLead.status == status)
    stmt = stmt.order_by(SponsorLead.created_at.desc()).limit(min(limit, 500)).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_lead(db: Session, lead_id: int) -> SponsorLead | None:
    return db.get(SponsorLead, lead_id)


def update_lead(db: Session, lead_id: int, data: dict) -> SponsorLead | None:
    lead = db.get(SponsorLead, lead_id)
    if lead is None:
        return None
    for key, value in data.items():
        setattr(lead, key, value)
    db.commit()
    db.refresh(lead)
    return lead
