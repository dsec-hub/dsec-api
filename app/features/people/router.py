"""People REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import PersonCreate, PersonOut, PersonUpdate

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[PersonOut])
def list_people(
    request: Request,
    type: str | None = None,
    committee: str | None = None,
    status: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[PersonOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_people(
        db, archived=include_archived, type=type, committee=committee,
        status=status, search=search, limit=limit, offset=offset,
    )
    return [PersonOut.model_validate(r) for r in rows]


@router.get("/{person_id}", response_model=PersonOut)
def get_person(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> PersonOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    person = service.get_person(db, person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    return PersonOut.model_validate(person)


@router.post("", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
def create_person(
    body: PersonCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PersonOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    person = service.create_person(db, body.model_dump(exclude_unset=True))
    return PersonOut.model_validate(person)


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(
    person_id: int,
    body: PersonUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PersonOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    person = service.update_person(db, person_id, body.model_dump(exclude_unset=True))
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    return PersonOut.model_validate(person)


@router.post("/{person_id}/archive", response_model=PersonOut)
def archive_person(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> PersonOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    person = service.archive_person(db, person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    return PersonOut.model_validate(person)
