"""Attachments REST API — PDF/image uploads for sponsors (and future owners).

`POST /attachments` (scope: ``write``) takes a multipart upload of one file plus
`entity_type`/`entity_id`, auto-compresses it (WebP for images, pikepdf for
PDFs), stores it in Supabase, and records the row. Reads need ``read``; writes
need ``write``.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.config import settings
from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import ENTITY_TYPES, AttachmentOut, AttachmentUpdate
from app.features.media.storage import StorageNotConfigured

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[AttachmentOut])
def list_attachments(
    request: Request,
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[AttachmentOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid entity_type")
    rows = service.list_attachments(db, entity_type=entity_type, entity_id=entity_id)
    return [AttachmentOut.model_validate(r) for r in rows]


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> AttachmentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))

    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid entity_type")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(data) > settings.ATTACHMENT_MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")

    try:
        attachment = service.create_attachment(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            file_bytes=data,
            filename=file.filename,
            content_type=file.content_type,
            title=title,
        )
    except StorageNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ValueError as bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(bad))

    return AttachmentOut.model_validate(attachment)


@router.patch("/{attachment_id}", response_model=AttachmentOut)
def update_attachment(
    attachment_id: int,
    body: AttachmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> AttachmentOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    attachment = service.update_attachment(
        db, attachment_id, body.model_dump(exclude_unset=True)
    )
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
    return AttachmentOut.model_validate(attachment)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    if not service.delete_attachment(db, attachment_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attachment not found")
