"""Partners REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import PartnerCreate, PartnerOut, PartnerUpdate

router = APIRouter()


@router.get("", response_model=list[PartnerOut])
def list_partners(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[PartnerOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_partners(
        db, archived=include_archived, status=status, search=search, limit=limit, offset=offset
    )
    return [PartnerOut.model_validate(r) for r in rows]


@router.get("/{partner_id}", response_model=PartnerOut)
def get_partner(
    partner_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> PartnerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    partner = service.get_partner(db, partner_id)
    if partner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "partner not found")
    return PartnerOut.model_validate(partner)


@router.post("", response_model=PartnerOut, status_code=status.HTTP_201_CREATED)
def create_partner(
    body: PartnerCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PartnerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    partner = service.create_partner(db, body.model_dump(exclude_unset=True))
    return PartnerOut.model_validate(partner)


@router.patch("/{partner_id}", response_model=PartnerOut)
def update_partner(
    partner_id: int,
    body: PartnerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PartnerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    partner = service.update_partner(db, partner_id, body.model_dump(exclude_unset=True))
    if partner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "partner not found")
    return PartnerOut.model_validate(partner)


@router.post("/{partner_id}/archive", response_model=PartnerOut)
def archive_partner(
    partner_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PartnerOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    partner = service.archive_partner(db, partner_id)
    if partner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "partner not found")
    return PartnerOut.model_validate(partner)
