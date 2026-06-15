"""Sponsor leads REST API.

POST   /sponsor-leads       — public (no auth), per-IP rate limited
GET    /sponsor-leads       — requires `read` scope
PATCH  /sponsor-leads/{id}  — requires `write` scope
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import SponsorLeadCreate, SponsorLeadOut, SponsorLeadUpdate

router = APIRouter()

_VALID_STATUSES = {"new", "contacted", "converted", "closed"}




@router.post("", response_model=SponsorLeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    body: SponsorLeadCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> SponsorLeadOut:
    """Public ingest — called by dsec-website forms (no API key required)."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    if not body.email or "@" not in body.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "valid email required")
    lead = service.create_lead(db, body.model_dump())
    return SponsorLeadOut.model_validate(lead)


@router.get("", response_model=list[SponsorLeadOut])
def list_leads(
    request: Request,
    lead_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(200, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[SponsorLeadOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    rows = service.list_leads(db, status=lead_status, limit=limit, offset=offset)
    return [SponsorLeadOut.model_validate(r) for r in rows]


@router.patch("/{lead_id}", response_model=SponsorLeadOut)
def update_lead(
    lead_id: int,
    body: SponsorLeadUpdate,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> SponsorLeadOut:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in _VALID_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )
    lead = service.update_lead(db, lead_id, data)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")
    return SponsorLeadOut.model_validate(lead)
