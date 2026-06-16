"""Sponsor contacts: individual people attached to a sponsorship.

Pure, Session-based functions (reused by REST + MCP). A contact either links an
existing roster person (``person_id``) or carries a free-text ``name``. Removal
is a soft-archive (history preserved; the dashboard filters on ``archived``).
The sponsor's headline ``contact_person_id`` remains the primary contact.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Sponsor, SponsorContact


def sponsor_exists(db: Session, sponsor_id: int) -> bool:
    return db.get(Sponsor, sponsor_id) is not None


def list_contacts(db: Session, sponsor_id: int) -> list[SponsorContact]:
    stmt = (
        select(SponsorContact)
        .where(
            SponsorContact.sponsor_id == sponsor_id,
            SponsorContact.archived.is_(False),
        )
        .order_by(SponsorContact.sort_order, SponsorContact.id)
    )
    return list(db.execute(stmt).scalars().all())


def add_contact(db: Session, sponsor_id: int, data: dict) -> SponsorContact:
    """Add a contact. Requires a linked person_id OR a free-text name."""
    if not data.get("person_id") and not (data.get("name") or "").strip():
        raise ValueError("a contact needs a person_id or a name")
    contact = SponsorContact(sponsor_id=sponsor_id, **data)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact_id: int, data: dict) -> SponsorContact | None:
    contact = db.get(SponsorContact, contact_id)
    if contact is None:
        return None
    for key, value in data.items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


def remove_contact(db: Session, contact_id: int) -> SponsorContact | None:
    contact = db.get(SponsorContact, contact_id)
    if contact is None:
        return None
    contact.archived = True
    db.commit()
    db.refresh(contact)
    return contact
