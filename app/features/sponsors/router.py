"""Sponsors REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import SponsorCreate, SponsorOut, SponsorUpdate

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    sponsor = service.archive_sponsor(db, sponsor_id)
    if sponsor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sponsor not found")
    return SponsorOut.model_validate(sponsor)
