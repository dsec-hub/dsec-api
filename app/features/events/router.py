"""Events REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import EventCreate, EventOut, EventUpdate

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[EventOut])
def list_events(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    trimester: str | None = None,
    event_lead_id: int | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_events(
        db, archived=include_archived, status=status, type=type,
        trimester=trimester, event_lead_id=event_lead_id, limit=limit, offset=offset,
    )
    return [EventOut.model_validate(r) for r in rows]


@router.get("/{event_id}", response_model=EventOut)
def get_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> EventOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    event = service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return EventOut.model_validate(event)


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    body: EventCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    event = service.create_event(db, body.model_dump(exclude_unset=True))
    return EventOut.model_validate(event)


@router.patch("/{event_id}", response_model=EventOut)
def update_event(
    event_id: int,
    body: EventUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    event = service.update_event(db, event_id, body.model_dump(exclude_unset=True))
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return EventOut.model_validate(event)


@router.post("/{event_id}/archive", response_model=EventOut)
def archive_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    event = service.archive_event(db, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return EventOut.model_validate(event)
