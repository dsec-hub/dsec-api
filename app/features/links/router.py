"""Link-tree REST API. Reads need `read`; writes need `write`.

NOTE: the static `/profile` and `/reorder` routes are declared BEFORE the
`/{link_id:int}` routes (and the id route uses the `:int` path converter) so
"profile"/"reorder" can never be parsed as an integer id.
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
    LinkCreate,
    LinkOut,
    LinkProfileOut,
    LinkProfileUpdate,
    LinkUpdate,
    ReorderIn,
)

router = APIRouter()


# --- list (incl. hidden) --------------------------------------------------- #
@router.get("", response_model=list[LinkOut])
def list_links(
    request: Request,
    include_hidden: bool = True,
    include_archived: bool = False,
    limit: int = Query(200, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[LinkOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_links(
        db, include_hidden=include_hidden, archived=include_archived, limit=limit, offset=offset
    )
    return [LinkOut.model_validate(r) for r in rows]


# --- profile (singleton header) -------------------------------------------- #
@router.get("/profile", response_model=LinkProfileOut)
def get_link_profile(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> LinkProfileOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    return LinkProfileOut.model_validate(service.get_profile(db))


@router.patch("/profile", response_model=LinkProfileOut)
def update_link_profile(
    body: LinkProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> LinkProfileOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    profile = service.update_profile(db, body.model_dump(exclude_unset=True))
    return LinkProfileOut.model_validate(profile)


# --- reorder --------------------------------------------------------------- #
@router.post("/reorder", response_model=list[LinkOut])
def reorder_links(
    body: ReorderIn,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> list[LinkOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.reorder_links(db, body.ordered_ids)
    return [LinkOut.model_validate(r) for r in rows]


# --- single link ----------------------------------------------------------- #
@router.get("/{link_id:int}", response_model=LinkOut)
def get_link(
    link_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> LinkOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    link = service.get_link(db, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    return LinkOut.model_validate(link)


@router.post("", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(
    body: LinkCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> LinkOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    link = service.create_link(db, body.model_dump(exclude_unset=True))
    return LinkOut.model_validate(link)


@router.patch("/{link_id:int}", response_model=LinkOut)
def update_link(
    link_id: int,
    body: LinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> LinkOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    link = service.update_link(db, link_id, body.model_dump(exclude_unset=True))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    return LinkOut.model_validate(link)


@router.post("/{link_id:int}/archive", response_model=LinkOut)
def archive_link(
    link_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> LinkOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    link = service.archive_link(db, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    return LinkOut.model_validate(link)
