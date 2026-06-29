"""Scan-target REST API (the /scan QR wall). Reads need `read`; writes `write`.

NOTE: the static `/reorder` route is declared BEFORE `/{target_id:int}` (which
uses the `:int` converter) so "reorder" can never be parsed as an integer id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import (
    ReorderIn,
    ScanPageOut,
    ScanPageUpdate,
    ScanTargetCreate,
    ScanTargetOut,
    ScanTargetUpdate,
)

router = APIRouter()


# --- list (incl. hidden) --------------------------------------------------- #
@router.get("", response_model=list[ScanTargetOut])
def list_scan_targets(
    request: Request,
    include_hidden: bool = True,
    include_archived: bool = False,
    limit: int = Query(200, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[ScanTargetOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_scan_targets(
        db, include_hidden=include_hidden, archived=include_archived, limit=limit, offset=offset
    )
    return [ScanTargetOut.model_validate(r) for r in rows]


# --- reorder --------------------------------------------------------------- #
@router.post("/reorder", response_model=list[ScanTargetOut])
def reorder_scan_targets(
    body: ReorderIn,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> list[ScanTargetOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.reorder_scan_targets(db, body.ordered_ids)
    return [ScanTargetOut.model_validate(r) for r in rows]


# --- page header (singleton, always id=1) ---------------------------------- #
# Declared BEFORE `/{target_id:int}` so the static "page" path can never be
# parsed as an integer scan-target id.
@router.get("/page", response_model=ScanPageOut)
def get_scan_page(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> ScanPageOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    return ScanPageOut.model_validate(service.get_page(db))


@router.patch("/page", response_model=ScanPageOut)
def update_scan_page(
    body: ScanPageUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ScanPageOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    page = service.update_page(db, body.model_dump(exclude_unset=True))
    return ScanPageOut.model_validate(page)


# --- single target --------------------------------------------------------- #
@router.get("/{target_id:int}", response_model=ScanTargetOut)
def get_scan_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> ScanTargetOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    target = service.get_scan_target(db, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan target not found")
    return ScanTargetOut.model_validate(target)


@router.post("", response_model=ScanTargetOut, status_code=status.HTTP_201_CREATED)
def create_scan_target(
    body: ScanTargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ScanTargetOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    target = service.create_scan_target(db, body.model_dump(exclude_unset=True))
    return ScanTargetOut.model_validate(target)


@router.patch("/{target_id:int}", response_model=ScanTargetOut)
def update_scan_target(
    target_id: int,
    body: ScanTargetUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ScanTargetOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    target = service.update_scan_target(db, target_id, body.model_dump(exclude_unset=True))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan target not found")
    return ScanTargetOut.model_validate(target)


@router.post("/{target_id:int}/archive", response_model=ScanTargetOut)
def archive_scan_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ScanTargetOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    target = service.archive_scan_target(db, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan target not found")
    return ScanTargetOut.model_validate(target)
