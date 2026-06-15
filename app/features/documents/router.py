"""Documents REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import DocumentCreate, DocumentOut, DocumentUpdate

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[DocumentOut])
def list_documents(
    request: Request,
    type: str | None = None,
    status: str | None = None,
    assignee_id: int | None = None,
    parent_id: int | None = None,
    top_level: bool = False,
    related_event_id: int | None = None,
    related_sponsor_id: int | None = None,
    related_project_id: int | None = None,
    related_meeting_id: int | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[DocumentOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_documents(
        db, archived=include_archived, type=type, status=status,
        assignee_id=assignee_id, parent_id=parent_id, top_level=top_level,
        related_event_id=related_event_id, related_sponsor_id=related_sponsor_id,
        related_project_id=related_project_id, related_meeting_id=related_meeting_id,
        limit=limit, offset=offset,
    )
    return [DocumentOut.model_validate(r) for r in rows]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> DocumentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    doc = service.get_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentOut.model_validate(doc)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(
    body: DocumentCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> DocumentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    doc = service.create_document(db, body.model_dump(exclude_unset=True))
    return DocumentOut.model_validate(doc)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: int,
    body: DocumentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> DocumentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    doc = service.update_document(db, document_id, body.model_dump(exclude_unset=True))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentOut.model_validate(doc)


@router.post("/{document_id}/archive", response_model=DocumentOut)
def archive_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> DocumentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    doc = service.archive_document(db, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentOut.model_validate(doc)
