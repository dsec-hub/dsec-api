"""Projects REST API. Reads need `read`; writes need `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter()




@router.get("", response_model=list[ProjectOut])
def list_projects(
    request: Request,
    status: str | None = None,
    is_public: bool | None = None,
    featured: bool | None = None,
    include_archived: bool = False,
    limit: int = Query(100, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[ProjectOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_projects(
        db, archived=include_archived, status=status, is_public=is_public,
        featured=featured, limit=limit, offset=offset,
    )
    return [ProjectOut.model_validate(r) for r in rows]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> ProjectOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    proj = service.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return ProjectOut.model_validate(proj)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ProjectOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    proj = service.create_project(db, body.model_dump(exclude_unset=True))
    return ProjectOut.model_validate(proj)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    body: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ProjectOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    proj = service.update_project(db, project_id, body.model_dump(exclude_unset=True))
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return ProjectOut.model_validate(proj)


@router.post("/{project_id}/archive", response_model=ProjectOut)
def archive_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ProjectOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    proj = service.archive_project(db, project_id)
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return ProjectOut.model_validate(proj)
