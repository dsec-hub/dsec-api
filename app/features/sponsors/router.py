"""Sponsors REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import contacts, service
from .schemas import (
    SponsorContactCreate,
    SponsorContactOut,
    SponsorContactUpdate,
    SponsorCreate,
    SponsorOut,
    SponsorUpdate,
)

router = APIRouter()




@router.get("", response_model=list[SponsorOut])
def list_sponsors(
    request: Request,
    stage: str | None = None,
    tier: str | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[SponsorOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_sponsors(
        db, archived=include_archived, stage=stage, tier=tier, limit=limit, offset=offset
    )
    return [SponsorOut.model_validate(r) for r in rows]


@router.get("/{sponsor_id}", response_model=SponsorOut)
def get_sponsor(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> SponsorOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    sponsor = service.get_sponsor(db, sponsor_id)
    if sponsor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")
    return SponsorOut.model_validate(sponsor)


@router.post("", response_model=SponsorOut, status_code=status.HTTP_201_CREATED)
def create_sponsor(
    body: SponsorCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    sponsor = service.create_sponsor(db, body.model_dump(exclude_unset=True))
    return SponsorOut.model_validate(sponsor)


@router.patch("/{sponsor_id}", response_model=SponsorOut)
def update_sponsor(
    sponsor_id: int,
    body: SponsorUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    sponsor = service.update_sponsor(db, sponsor_id, body.model_dump(exclude_unset=True))
    if sponsor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")
    return SponsorOut.model_validate(sponsor)


@router.post("/{sponsor_id}/archive", response_model=SponsorOut)
def archive_sponsor(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    sponsor = service.archive_sponsor(db, sponsor_id)
    if sponsor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")
    return SponsorOut.model_validate(sponsor)


# --------------------------------------------------------------------------- #
# Sponsor contacts (people attached to a sponsorship)
# --------------------------------------------------------------------------- #

def _require_sponsor(db: Session, sponsor_id: int) -> None:
    if not contacts.sponsor_exists(db, sponsor_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")


@router.get("/{sponsor_id}/contacts", response_model=list[SponsorContactOut])
def list_contacts(
    sponsor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[SponsorContactOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_sponsor(db, sponsor_id)
    return [SponsorContactOut.model_validate(r) for r in contacts.list_contacts(db, sponsor_id)]


@router.post("/{sponsor_id}/contacts", response_model=SponsorContactOut,
             status_code=status.HTTP_201_CREATED)
def add_contact(
    sponsor_id: int,
    body: SponsorContactCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorContactOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    _require_sponsor(db, sponsor_id)
    try:
        contact = contacts.add_contact(db, sponsor_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return SponsorContactOut.model_validate(contact)


@router.patch("/{sponsor_id}/contacts/{contact_id}", response_model=SponsorContactOut)
def update_contact(
    sponsor_id: int,
    contact_id: int,
    body: SponsorContactUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorContactOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    contact = contacts.update_contact(db, contact_id, body.model_dump(exclude_unset=True))
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    return SponsorContactOut.model_validate(contact)


@router.delete("/{sponsor_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_contact(
    sponsor_id: int,
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if contacts.remove_contact(db, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
