"""Meetings REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.llm import LLMError
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service
from .notes import generate_meeting_notes
from .schemas import (
    AgendaOut,
    AgendaSet,
    GenerateNotes,
    MeetingCreate,
    MeetingOut,
    MeetingUpdate,
)

router = APIRouter()




@router.get("", response_model=list[MeetingOut])
def list_meetings(
    request: Request,
    type: str | None = None,
    committee: str | None = None,
    status: str | None = None,
    related_event_id: int | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[MeetingOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_meetings(
        db, archived=include_archived, type=type, committee=committee, status=status,
        related_event_id=related_event_id, limit=limit, offset=offset,
    )
    return [MeetingOut.model_validate(r) for r in rows]


@router.get("/{meeting_id}", response_model=MeetingOut)
def get_meeting(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> MeetingOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.get_meeting(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return MeetingOut.model_validate(meeting)


@router.post("", response_model=MeetingOut, status_code=status.HTTP_201_CREATED)
def create_meeting(
    body: MeetingCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> MeetingOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.create_meeting(db, body.model_dump(exclude_unset=True))
    return MeetingOut.model_validate(meeting)


@router.patch("/{meeting_id}", response_model=MeetingOut)
def update_meeting(
    meeting_id: int,
    body: MeetingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> MeetingOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.update_meeting(db, meeting_id, body.model_dump(exclude_unset=True))
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return MeetingOut.model_validate(meeting)


@router.post("/{meeting_id}/archive", response_model=MeetingOut)
def archive_meeting(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> MeetingOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.archive_meeting(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return MeetingOut.model_validate(meeting)


# --------------------------------------------------------------------------- #
# Pre-meeting agenda (built before the meeting, shared read-only with invitees)
# --------------------------------------------------------------------------- #

@router.get("/{meeting_id}/agenda", response_model=AgendaOut)
def get_agenda(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> AgendaOut:
    """Return the ordered agenda items plus the total estimated duration and the
    share state (status, token, public URL)."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.get_meeting(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return service.agenda_view(meeting)


@router.put("/{meeting_id}/agenda", response_model=AgendaOut)
def set_agenda(
    meeting_id: int,
    body: AgendaSet,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> AgendaOut:
    """Replace the meeting's full agenda item list. Validates owner/task/event
    references; refuses to edit a locked agenda (409)."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    try:
        meeting = service.set_meeting_agenda(db, meeting_id, body.items)
    except service.AgendaLockedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return service.agenda_view(meeting)


@router.post("/{meeting_id}/agenda/share", response_model=AgendaOut)
def share_agenda(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> AgendaOut:
    """Share the agenda: status -> shared, stamp shared_at, mint a stable share
    token. Returns the public read-only URL. Idempotent."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.share_meeting_agenda(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return service.agenda_view(meeting)


@router.post("/{meeting_id}/agenda/lock", response_model=AgendaOut)
def lock_agenda(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> AgendaOut:
    """Freeze the agenda once the meeting starts (status -> locked). It stays
    publicly viewable but can no longer be edited."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    meeting = service.lock_meeting_agenda(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return service.agenda_view(meeting)


@router.post("/{meeting_id}/generate-notes", response_model=MeetingOut)
def generate_notes(
    meeting_id: int,
    body: GenerateNotes,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("trigger")),
) -> MeetingOut:
    """Summarise a meeting transcript into notes + action items (spends LLM).

    Cost guard runs BEFORE any LLM work: per-IP/per-key request limit, then the
    daily trigger / global LLM cap. Also creates a MeetingNotes document.
    """
    ip = client_ip(request)
    limiter.check_request(db, key_id=key.id, ip=ip)
    limiter.check_and_count_trigger(db, key_id=key.id)  # raises 429 if capped

    meeting = service.get_meeting(db, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    try:
        generate_meeting_notes(
            db, meeting, transcript=body.transcript, create_document=body.create_document
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"LLM error: {exc}")
    return MeetingOut.model_validate(meeting)
