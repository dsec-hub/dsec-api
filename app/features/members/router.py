"""Members REST API (read-only; the roster is owned by the DUSA import)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import MemberCounts, MemberOut, MemberStats, MemberTrendPoint

router = APIRouter()




@router.get("", response_model=list[MemberOut])
def list_members(
    request: Request,
    current_only: bool = True,
    dusa_only: bool | None = None,
    search: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[MemberOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_members(
        db, current_only=current_only, dusa_only=dusa_only, search=search,
        limit=limit, offset=offset,
    )
    return [MemberOut.model_validate(r) for r in rows]


@router.get("/stats", response_model=MemberStats)
def member_stats(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> MemberStats:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    return MemberStats(
        counts=MemberCounts(**service.member_counts(db)),
        trend=[MemberTrendPoint.model_validate(r) for r in service.member_trend(db)],
    )


@router.get("/{member_id}", response_model=MemberOut)
def get_member(
    member_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> MemberOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    m = service.get_member(db, member_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return MemberOut.model_validate(m)
