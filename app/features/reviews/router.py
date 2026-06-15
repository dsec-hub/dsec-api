"""Post-event review forms (Tally).

Extends the events resource — mounted at `/events-api`, so the routes live under
`/events-api/{event_id}/review-form`. Reads need `read`; creating a form needs
`write` (it hits Tally but spends no LLM money, so it's `write`, not `trigger`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import ReviewFormOut, ReviewResponsesOut
from .tally import TallyError, TallyNotConfigured

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/{event_id}/review-form", response_model=ReviewFormOut, status_code=status.HTTP_201_CREATED)
def create_review_form(
    event_id: int,
    request: Request,
    force: bool = Query(False, description="Recreate even if a form already exists"),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> ReviewFormOut:
    """Create a post-event review form in Tally for this event (idempotent)."""
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    try:
        event = service.create_review_form(db, event_id, force=force)
    except TallyNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except TallyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return ReviewFormOut(
        event_id=event.id,
        configured=True,
        form_id=event.review_form_id,
        form_url=event.review_form_url,
        created_at=event.review_form_created_at,
        response_count=None,
    )


@router.get("/{event_id}/review-form", response_model=ReviewFormOut)
def get_review_form(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> ReviewFormOut:
    """Whether this event has a review form, its link, and a live response count."""
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    data = service.get_review_status(db, event_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return ReviewFormOut(**data)


@router.get("/{event_id}/review-form/responses", response_model=ReviewResponsesOut)
def get_review_responses(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> ReviewResponsesOut:
    """Submissions for this event's review form, mapped onto the template questions."""
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    try:
        data = service.get_review_summary(db, event_id)
    except TallyNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except TallyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return ReviewResponsesOut(**data)
