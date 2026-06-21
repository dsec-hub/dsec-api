"""Meeting repository functions — pure, Session-based, reused by REST + MCP.

Convention shared by every workspace feature:
    list_<x>(db, *, archived=False, limit, offset, **filters) -> list[Model]
    get_<x>(db, id) -> Model | None
    create_<x>(db, data: dict) -> Model
    update_<x>(db, id, data: dict) -> Model | None        (PATCH; only given keys)
    archive_<x>(db, id) -> Model | None                   (soft delete)

Plus the pre-meeting agenda helpers (set/share/lock + a view builder). The
agenda is built BEFORE a meeting and shared read-only with invitees; it is
distinct from the post-meeting transcript/notes/action-items flow above.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Event, Meeting, Person, Task

from .schemas import AgendaItemOut, AgendaOut

# Agenda lifecycle: private draft -> public shared link -> frozen at start.
AGENDA_STATUSES = ("draft", "shared", "locked")


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
    # An agenda can be set at creation time; normalise + FK-validate it like the
    # dedicated set_meeting_agenda path does.
    if data.get("agenda_items") is not None:
        data["agenda_items"] = _validate_and_normalise(db, data["agenda_items"])
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


# --------------------------------------------------------------------------- #
# Pre-meeting agenda
# --------------------------------------------------------------------------- #

class AgendaLockedError(ValueError):
    """Raised when an agenda edit is attempted after the agenda was locked."""


def _validate_and_normalise(db: Session, raw_items: list) -> list[dict]:
    """Validate cross-entity refs and return canonical item dicts.

    * Each item gets a stable `id` (preserved if already a non-empty string).
    * `order` is renumbered to the list position (the client sends display order).
    * `owner_person_id` / `related_task_id` / `related_event_id` must reference a
      live (non-archived) row, else ValueError listing every bad reference.
    """
    items: list[dict] = []
    person_ids: set[int] = set()
    task_ids: set[int] = set()
    event_ids: set[int] = set()

    for idx, raw in enumerate(raw_items):
        item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        iid = item.get("id")
        item["id"] = iid if isinstance(iid, str) and iid.strip() else uuid.uuid4().hex
        item["order"] = idx
        if item.get("owner_person_id") is not None:
            person_ids.add(int(item["owner_person_id"]))
        if item.get("related_task_id") is not None:
            task_ids.add(int(item["related_task_id"]))
        if item.get("related_event_id") is not None:
            event_ids.add(int(item["related_event_id"]))
        items.append(item)

    problems: list[str] = []
    problems += _missing(db, Person, person_ids, "owner_person_id")
    problems += _missing(db, Task, task_ids, "related_task_id")
    problems += _missing(db, Event, event_ids, "related_event_id")
    if problems:
        raise ValueError("agenda references unknown rows: " + "; ".join(problems))
    return items


def _missing(db: Session, model, ids: set[int], label: str) -> list[str]:
    if not ids:
        return []
    found = set(
        db.execute(
            select(model.id).where(model.id.in_(ids), model.archived.is_(False))
        ).scalars()
    )
    return [f"{label}={i}" for i in sorted(ids - found)]


def set_meeting_agenda(db: Session, meeting_id: int, items: list) -> Meeting | None:
    """Replace the meeting's full agenda item list. Refuses to edit a locked
    agenda (raises AgendaLockedError)."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    if meeting.agenda_status == "locked":
        raise AgendaLockedError("agenda is locked and can no longer be edited")
    meeting.agenda_items = _validate_and_normalise(db, items)
    db.commit()
    db.refresh(meeting)
    return meeting


def share_meeting_agenda(db: Session, meeting_id: int) -> Meeting | None:
    """Make the agenda shareable: status -> shared, stamp shared_at, mint a stable
    share token if absent. Idempotent — re-sharing keeps the same token/URL so the
    link handed to invitees never changes."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    if not meeting.agenda_share_token:
        meeting.agenda_share_token = secrets.token_urlsafe(24)
    if meeting.agenda_status == "draft":
        meeting.agenda_status = "shared"
    if meeting.agenda_shared_at is None:
        from app.models import _utcnow

        meeting.agenda_shared_at = _utcnow()
    db.commit()
    db.refresh(meeting)
    return meeting


def lock_meeting_agenda(db: Session, meeting_id: int) -> Meeting | None:
    """Freeze the agenda once the meeting starts (status -> locked). A locked
    agenda is still publicly viewable but can no longer be edited."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    meeting.agenda_status = "locked"
    db.commit()
    db.refresh(meeting)
    return meeting


def get_by_share_token(db: Session, token: str) -> Meeting | None:
    """Look up a meeting by its public agenda share token (shared/locked only)."""
    stmt = select(Meeting).where(
        Meeting.agenda_share_token == token,
        Meeting.agenda_status.in_(("shared", "locked")),
        Meeting.archived.is_(False),
    )
    return db.execute(stmt).scalars().first()


def share_url_for(token: str | None) -> str | None:
    if not token:
        return None
    return f"{settings.AGENDA_SHARE_BASE_URL.rstrip('/')}/{token}"


def total_estimated_minutes(items: list | None) -> int:
    return sum(int(i.get("duration_minutes") or 0) for i in (items or []))


def agenda_view(meeting: Meeting) -> AgendaOut:
    """Build the AgendaOut view (ordered items + total duration + share state)."""
    raw = meeting.agenda_items or []
    ordered = sorted(raw, key=lambda i: i.get("order", 0))
    return AgendaOut(
        meeting_id=meeting.id,
        agenda_status=meeting.agenda_status,
        agenda_shared_at=meeting.agenda_shared_at,
        agenda_share_token=meeting.agenda_share_token,
        share_url=share_url_for(meeting.agenda_share_token),
        total_estimated_minutes=total_estimated_minutes(raw),
        items=[AgendaItemOut.model_validate(i) for i in ordered],
    )
