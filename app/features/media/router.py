"""Media REST API — image upload/management for events & projects.

`POST /media` (scope: ``write``) takes a multipart upload of one already-cropped
image plus `entity_type`/`entity_id`/`role`, processes it into WebP + PNG, stores
both in Supabase, and records the row. Reads need ``read``; writes need
``write``. The public website reads images via the `/website` feed, not here.
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
from .schemas import ENTITY_TYPES, ROLES, MediaOut, MediaUpdate
from .storage import StorageError

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[MediaOut])
def list_media(
    request: Request,
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[MediaOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid entity_type")
    rows = service.list_media(db, entity_type=entity_type, entity_id=entity_id)
    return [MediaOut.model_validate(r) for r in rows]


@router.post("", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    role: str = Form(...),
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> MediaOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))

    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid entity_type")
    if role not in ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid role")
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "file must be an image")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(data) > settings.MEDIA_MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")

    try:
        asset = service.create_media(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            role=role,
            file_bytes=data,
            filename=file.filename,
            alt_text=alt_text,
        )
    except StorageError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ValueError as bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(bad))

    return MediaOut.model_validate(asset)


@router.patch("/{media_id}", response_model=MediaOut)
def update_media(
    media_id: int,
    body: MediaUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> MediaOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    data = body.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid role")
    asset = service.update_media(db, media_id, data)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    return MediaOut.model_validate(asset)


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> None:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    if not service.delete_media(db, media_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
