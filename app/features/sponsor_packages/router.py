"""Sponsor packages REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import SponsorPackageCreate, SponsorPackageOut, SponsorPackageUpdate

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[SponsorPackageOut])
def list_packages(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[SponsorPackageOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_packages(db)
    return [SponsorPackageOut.model_validate(r) for r in rows]


@router.get("/{package_id}", response_model=SponsorPackageOut)
def get_package(
    package_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> SponsorPackageOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    pkg = service.get_package(db, package_id)
    if pkg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "package not found")
    return SponsorPackageOut.model_validate(pkg)


@router.post("", response_model=SponsorPackageOut, status_code=status.HTTP_201_CREATED)
def create_package(
    body: SponsorPackageCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorPackageOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    pkg = service.create_package(db, body.model_dump(exclude_unset=True))
    return SponsorPackageOut.model_validate(pkg)


@router.patch("/{package_id}", response_model=SponsorPackageOut)
def update_package(
    package_id: int,
    body: SponsorPackageUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorPackageOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    pkg = service.update_package(db, package_id, body.model_dump(exclude_unset=True))
    if pkg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "package not found")
    return SponsorPackageOut.model_validate(pkg)


@router.delete("/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_package(
    package_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    if not service.delete_package(db, package_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "package not found")
