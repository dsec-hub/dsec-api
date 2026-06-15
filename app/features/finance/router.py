"""Finance REST API. Reads need `read`; setting a budget needs `write`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service
from .schemas import EventBudgetOut, FinanceSummary, ReportOut, SetBudget, TransactionOut

router = APIRouter()


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/summary", response_model=FinanceSummary)
def summary(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> FinanceSummary:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    return FinanceSummary(**service.finances_summary(db))


@router.get("/transactions", response_model=list[TransactionOut])
def transactions(
    request: Request,
    kind: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[TransactionOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_transactions(db, kind=kind, limit=limit, offset=offset)
    return [TransactionOut.model_validate(r) for r in rows]


@router.get("/reports", response_model=list[ReportOut])
def reports(
    request: Request,
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[ReportOut]:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    rows = service.list_reports(db, limit=limit)
    return [ReportOut.model_validate(r) for r in rows]


@router.post("/events/{event_id}/budget", response_model=EventBudgetOut)
def set_budget(
    event_id: int,
    body: SetBudget,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> EventBudgetOut:
    limiter.check_request(db, key_id=key.id, ip=_ip(request))
    ev = service.set_event_budget(db, event_id, body.budget_aud, body.grant_rate)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return EventBudgetOut.model_validate(ev)
