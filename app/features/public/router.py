"""Public API for the DSEC committee & trusted internal tools.

All routes are API-key authenticated, scoped, and rate limited:
- read routes (`read` scope): no LLM spend.
- trigger routes (`trigger` scope): checked against the per-key daily trigger cap
  AND the global daily LLM cap BEFORE any work is done.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.db import get_db
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest
from app.features.public.schemas import (
    DraftRequest,
    DraftResponse,
    EventOut,
    LogEntry,
    StatusResponse,
)
from app.models import APIKey, Event, EventLog, RateLimit

router = APIRouter()


def _client_ip(request: Request) -> str:
    # Behind Vercel/Cloudflare the real IP is in X-Forwarded-For.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/status", response_model=StatusResponse)
def status_route(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> StatusResponse:
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    llm_today = db.execute(
        select(func.coalesce(func.sum(RateLimit.trigger_count_today), 0)).where(
            RateLimit.window_start == day, RateLimit.bucket == "trigger"
        )
    ).scalar_one()
    log_count = db.execute(select(func.count(EventLog.id))).scalar_one()
    return StatusResponse(
        status="ok",
        log_count=log_count,
        llm_calls_today=int(llm_today),
        global_daily_cap=settings.GLOBAL_DAILY_LLM_CAP,
    )


@router.get("/logs", response_model=list[LogEntry])
def logs_route(
    request: Request,
    source: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[LogEntry]:
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))
    stmt = select(EventLog).order_by(EventLog.created_at.desc())
    if source:
        stmt = stmt.where(EventLog.source == source)
    if action:
        stmt = stmt.where(EventLog.action == action)
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return [LogEntry.model_validate(r) for r in rows]


@router.get("/events", response_model=list[EventOut])
def events_route(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[EventOut]:
    """Published events from Neon (section 8c). For internal tools; the website
    bypasses this and reads Neon directly."""
    limiter.check_request(db, key_id=key.id, ip=_client_ip(request))
    rows = db.execute(
        select(Event)
        .where(Event.status == "published", Event.deleted.is_(False))
        .order_by(Event.starts_at.asc())
    ).scalars().all()
    return [EventOut.model_validate(r) for r in rows]


@router.post("/draft", response_model=DraftResponse)
def draft_route(
    request: Request,
    body: DraftRequest,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("trigger")),
) -> DraftResponse:
    """Run the email classify+draft pipeline on supplied text. Trigger-scoped.

    Cost guard runs BEFORE any LLM work: per-IP + per-key request limit, then the
    daily trigger / global LLM cap.
    """
    ip = _client_ip(request)
    limiter.check_request(db, key_id=key.id, ip=ip)
    limiter.check_and_count_trigger(db, key_id=key.id)  # raises 429 if capped

    email = EmailRequest(
        threadId=f"public:{key.prefix}",
        messageId="public-draft",
        **{"from": body.from_},
        to="",
        subject=body.subject,
        body=body.body,
        date=datetime.now(timezone.utc).isoformat(),
    )
    result = run_pipeline(email, db)
    return DraftResponse(action=result.action, draftBody=result.draftBody)


# POST /public/notify (relay to Discord) is intentionally deferred until the
# Discord integration ships in v2 — it would live here, trigger-scoped.
