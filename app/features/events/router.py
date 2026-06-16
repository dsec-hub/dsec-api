"""Events REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import relations, service
from .schemas import (
    EventConnectionLink,
    EventConnectionOut,
    EventCreate,
    EventOut,
    EventPartnerLink,
    EventPartnerOut,
    EventSpeakerCreate,
    EventSpeakerOut,
    EventSpeakerUpdate,
    EventSponsorLink,
    EventSponsorOut,
    EventUpdate,
)

router = APIRouter()




@router.get("", response_model=list[EventOut])
def list_events(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    trimester: str | None = None,
    event_lead_id: int | None = None,
    is_public: bool | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_events(
        db, archived=include_archived, status=status, type=type,
        trimester=trimester, event_lead_id=event_lead_id, is_public=is_public,
        limit=limit, offset=offset,
    )
    return [EventOut.model_validate(r) for r in rows]


@router.get("/{event_id}", response_model=EventOut)
def get_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> EventOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    event = service.archive_event(db, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return EventOut.model_validate(event)


# --------------------------------------------------------------------------- #
# Event relations: speakers / sponsor links / partner links
# --------------------------------------------------------------------------- #

def _require_event(db: Session, event_id: int) -> None:
    if not relations.event_exists(db, event_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")


@router.get("/{event_id}/speakers", response_model=list[EventSpeakerOut])
def list_speakers(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventSpeakerOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    return [EventSpeakerOut.model_validate(r) for r in relations.list_speakers(db, event_id)]


@router.post("/{event_id}/speakers", response_model=EventSpeakerOut,
             status_code=status.HTTP_201_CREATED)
def add_speaker(
    event_id: int,
    body: EventSpeakerCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventSpeakerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    try:
        speaker = relations.add_speaker(db, event_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return EventSpeakerOut.model_validate(speaker)


@router.patch("/{event_id}/speakers/{speaker_id}", response_model=EventSpeakerOut)
def update_speaker(
    event_id: int,
    speaker_id: int,
    body: EventSpeakerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventSpeakerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    speaker = relations.update_speaker(db, speaker_id, body.model_dump(exclude_unset=True))
    if speaker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "speaker not found")
    return EventSpeakerOut.model_validate(speaker)


@router.delete("/{event_id}/speakers/{speaker_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_speaker(
    event_id: int,
    speaker_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if relations.remove_speaker(db, speaker_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "speaker not found")


@router.get("/{event_id}/sponsors", response_model=list[EventSponsorOut])
def list_event_sponsors(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventSponsorOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    return [EventSponsorOut.model_validate(r) for r in relations.list_event_sponsors(db, event_id)]


@router.post("/{event_id}/sponsors", response_model=EventSponsorOut,
             status_code=status.HTTP_201_CREATED)
def link_event_sponsor(
    event_id: int,
    body: EventSponsorLink,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventSponsorOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    row = relations.link_sponsor(
        db, event_id, body.sponsor_id, tier=body.tier, sort_order=body.sort_order
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")
    return EventSponsorOut.model_validate(row)


@router.delete("/{event_id}/sponsors/{sponsor_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_event_sponsor(
    event_id: int,
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if not relations.unlink_sponsor(db, event_id, sponsor_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor link not found")


@router.get("/{event_id}/partners", response_model=list[EventPartnerOut])
def list_event_partners(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventPartnerOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    return [EventPartnerOut.model_validate(r) for r in relations.list_event_partners(db, event_id)]


@router.post("/{event_id}/partners", response_model=EventPartnerOut,
             status_code=status.HTTP_201_CREATED)
def link_event_partner(
    event_id: int,
    body: EventPartnerLink,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventPartnerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    row = relations.link_partner(
        db, event_id, body.partner_id, role=body.role, sort_order=body.sort_order
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "partner not found")
    return EventPartnerOut.model_validate(row)


@router.delete("/{event_id}/partners/{partner_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_event_partner(
    event_id: int,
    partner_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if not relations.unlink_partner(db, event_id, partner_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "partner link not found")


def _connection_out(link, other, *, event_id: int) -> EventConnectionOut:
    """Shape a (link, other_event) pair into the response, resolved relative to
    the event it was queried from."""
    return EventConnectionOut(
        id=link.id,
        event_id=event_id,
        other_event_id=other.id,
        other_event_name=other.name,
        other_event_status=other.status,
        other_event_start_date=other.start_date,
        label=link.label,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.get("/{event_id}/connections", response_model=list[EventConnectionOut])
def list_event_connections(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventConnectionOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    return [
        _connection_out(link, other, event_id=event_id)
        for link, other in relations.list_connections(db, event_id)
    ]


@router.post("/{event_id}/connections", response_model=EventConnectionOut,
             status_code=status.HTTP_201_CREATED)
def link_event_connection(
    event_id: int,
    body: EventConnectionLink,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventConnectionOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_event(db, event_id)
    try:
        result = relations.link_connection(
            db, event_id, body.other_event_id, label=body.label
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event to connect not found")
    link, other = result
    return _connection_out(link, other, event_id=event_id)


@router.delete("/{event_id}/connections/{other_event_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def unlink_event_connection(
    event_id: int,
    other_event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if not relations.unlink_connection(db, event_id, other_event_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
