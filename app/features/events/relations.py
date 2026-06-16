"""Event relations: speakers, sponsor links, and partner links.

Pure, Session-based functions (reused by REST + MCP) for the many-to-* tables
that hang off an event:

* ``event_speaker``  — speakers presenting (soft-archived on remove, keeping
  history; the public feed filters on ``archived``).
* ``event_sponsor`` / ``event_partner`` — link tables with a UNIQUE(event_id,
  other_id) constraint, so they are *hard*-deleted on unlink (a soft-archive
  would keep occupying the unique slot and block a later re-link). ``link_*`` is
  idempotent: it revives/updates an existing row rather than inserting a dupe.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventConnection,
    EventPartner,
    EventSpeaker,
    EventSponsor,
    Partner,
    Sponsor,
)


def event_exists(db: Session, event_id: int) -> bool:
    return db.get(Event, event_id) is not None


# --------------------------------------------------------------------------- #
# speakers
# --------------------------------------------------------------------------- #

def list_speakers(db: Session, event_id: int) -> list[EventSpeaker]:
    stmt = (
        select(EventSpeaker)
        .where(EventSpeaker.event_id == event_id, EventSpeaker.archived.is_(False))
        .order_by(EventSpeaker.sort_order, EventSpeaker.id)
    )
    return list(db.execute(stmt).scalars().all())


def add_speaker(db: Session, event_id: int, data: dict) -> EventSpeaker:
    """Add a speaker. Requires a linked person_id OR a free-text name."""
    if not data.get("person_id") and not (data.get("name") or "").strip():
        raise ValueError("a speaker needs a person_id or a name")
    speaker = EventSpeaker(event_id=event_id, **data)
    db.add(speaker)
    db.commit()
    db.refresh(speaker)
    return speaker


def update_speaker(db: Session, speaker_id: int, data: dict) -> EventSpeaker | None:
    speaker = db.get(EventSpeaker, speaker_id)
    if speaker is None:
        return None
    for key, value in data.items():
        setattr(speaker, key, value)
    db.commit()
    db.refresh(speaker)
    return speaker


def remove_speaker(db: Session, speaker_id: int) -> EventSpeaker | None:
    """Soft-archive a speaker (kept out of the public feed, history preserved)."""
    speaker = db.get(EventSpeaker, speaker_id)
    if speaker is None:
        return None
    speaker.archived = True
    db.commit()
    db.refresh(speaker)
    return speaker


# --------------------------------------------------------------------------- #
# sponsor links (event_sponsor)
# --------------------------------------------------------------------------- #

def list_event_sponsors(db: Session, event_id: int) -> list[EventSponsor]:
    stmt = (
        select(EventSponsor)
        .where(EventSponsor.event_id == event_id, EventSponsor.archived.is_(False))
        .order_by(EventSponsor.sort_order, EventSponsor.id)
    )
    return list(db.execute(stmt).scalars().all())


def link_sponsor(
    db: Session,
    event_id: int,
    sponsor_id: int,
    *,
    tier: str | None = None,
    sort_order: int | None = None,
) -> EventSponsor | None:
    """Idempotently link a sponsor to an event. Returns None if the sponsor
    doesn't exist."""
    if db.get(Sponsor, sponsor_id) is None:
        return None
    row = db.execute(
        select(EventSponsor).where(
            EventSponsor.event_id == event_id, EventSponsor.sponsor_id == sponsor_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = EventSponsor(event_id=event_id, sponsor_id=sponsor_id)
        db.add(row)
    row.archived = False
    if tier is not None:
        row.tier = tier
    if sort_order is not None:
        row.sort_order = sort_order
    db.commit()
    db.refresh(row)
    return row


def unlink_sponsor(db: Session, event_id: int, sponsor_id: int) -> bool:
    """Hard-delete the link so the sponsor can be re-linked later."""
    row = db.execute(
        select(EventSponsor).where(
            EventSponsor.event_id == event_id, EventSponsor.sponsor_id == sponsor_id
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# partner links (event_partner)
# --------------------------------------------------------------------------- #

def list_event_partners(db: Session, event_id: int) -> list[EventPartner]:
    stmt = (
        select(EventPartner)
        .where(EventPartner.event_id == event_id, EventPartner.archived.is_(False))
        .order_by(EventPartner.sort_order, EventPartner.id)
    )
    return list(db.execute(stmt).scalars().all())


def link_partner(
    db: Session,
    event_id: int,
    partner_id: int,
    *,
    role: str | None = None,
    sort_order: int | None = None,
) -> EventPartner | None:
    """Idempotently link a partner to an event. Returns None if the partner
    doesn't exist."""
    if db.get(Partner, partner_id) is None:
        return None
    row = db.execute(
        select(EventPartner).where(
            EventPartner.event_id == event_id, EventPartner.partner_id == partner_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = EventPartner(event_id=event_id, partner_id=partner_id)
        db.add(row)
    row.archived = False
    if role is not None:
        row.role = role
    if sort_order is not None:
        row.sort_order = sort_order
    db.commit()
    db.refresh(row)
    return row


def unlink_partner(db: Session, event_id: int, partner_id: int) -> bool:
    """Hard-delete the link so the partner can be re-linked later."""
    row = db.execute(
        select(EventPartner).where(
            EventPartner.event_id == event_id, EventPartner.partner_id == partner_id
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# event connections (event_connection) — symmetric, visual-only event<->event
# --------------------------------------------------------------------------- #

def _canonical_pair(a: int, b: int) -> tuple[int, int]:
    """A connection is symmetric, so a pair is stored canonically with the
    smaller id first — giving each pair exactly one row."""
    return (a, b) if a <= b else (b, a)


def list_connections(db: Session, event_id: int) -> list[tuple[EventConnection, Event]]:
    """Every event connected to `event_id`, as (link, other_event) pairs. The
    "other" side is whichever column isn't `event_id` (the link is symmetric).
    Archived events are skipped so the dashboard never shows a dangling link."""
    links = db.execute(
        select(EventConnection)
        .where(
            EventConnection.archived.is_(False),
            or_(
                EventConnection.event_a_id == event_id,
                EventConnection.event_b_id == event_id,
            ),
        )
        .order_by(EventConnection.id)
    ).scalars().all()
    out: list[tuple[EventConnection, Event]] = []
    for link in links:
        other_id = link.event_b_id if link.event_a_id == event_id else link.event_a_id
        other = db.get(Event, other_id)
        if other is not None and not other.archived:
            out.append((link, other))
    return out


def link_connection(
    db: Session,
    event_id: int,
    other_event_id: int,
    *,
    label: str | None = None,
) -> tuple[EventConnection, Event] | None:
    """Idempotently connect two events. Returns (link, other_event), or None if
    the other event doesn't exist. Raises ValueError if an event is connected to
    itself. Re-linking an existing pair just updates the label."""
    if event_id == other_event_id:
        raise ValueError("an event can't be connected to itself")
    other = db.get(Event, other_event_id)
    if other is None:
        return None
    a_id, b_id = _canonical_pair(event_id, other_event_id)
    row = db.execute(
        select(EventConnection).where(
            EventConnection.event_a_id == a_id, EventConnection.event_b_id == b_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = EventConnection(event_a_id=a_id, event_b_id=b_id)
        db.add(row)
    row.archived = False
    if label is not None:
        row.label = label
    db.commit()
    db.refresh(row)
    return row, other


def unlink_connection(db: Session, event_id: int, other_event_id: int) -> bool:
    """Hard-delete the connection between two events (order-independent)."""
    a_id, b_id = _canonical_pair(event_id, other_event_id)
    row = db.execute(
        select(EventConnection).where(
            EventConnection.event_a_id == a_id, EventConnection.event_b_id == b_id
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
