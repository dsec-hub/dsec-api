"""Members REST API (read-only; the roster is owned by the DUSA import)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service, verification
from .schemas import (
    MemberCounts,
    MemberOut,
    MemberStats,
    MemberTrendPoint,
    MemberVerification,
    PublicVerifyResult,
)

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


@router.get("/verify/{code}", response_model=PublicVerifyResult)
def verify_member_code(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
) -> PublicVerifyResult:
    """PUBLIC (no key): resolve a membership-card code to a current member.

    Capability-gated — you can only look up a member whose code you were shown
    (via the card / QR). Returns the bare minimum to confirm identity at a door:
    name + active status. Per-IP rate limited; never exposes email / student id.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    m = verification.find_member_by_code(service.all_current_members(db), code)
    if m is None:
        return PublicVerifyResult(valid=False)
    return PublicVerifyResult(
        valid=True,
        member_id=m.id,
        full_name=m.full_name,
        membership_type=m.membership_type,
        member_since=m.first_subscription_date,
        is_current=m.is_current,
    )


@router.get("/{member_id}/verification-code", response_model=MemberVerification)
def member_verification_code(
    member_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> MemberVerification:
    """The member's own digital-membership-card data (code + QR + status)."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    m = service.get_member(db, member_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    code = verification.member_code(m.id)
    url = verification.verify_url(code)
    return MemberVerification(
        member_id=m.id,
        code=code,
        full_name=m.full_name,
        membership_type=m.membership_type,
        member_since=m.first_subscription_date,
        is_current=m.is_current,
        verify_url=url,
        qr_svg=verification.qr_svg(url),
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
