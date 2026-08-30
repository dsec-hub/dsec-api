"""Public API for the DSEC committee & trusted internal tools.

All routes are API-key authenticated, scoped, and rate limited:
- read routes (`read` scope): no LLM spend.
- trigger routes (`trigger` scope): checked against the per-key daily trigger cap
  AND the global daily LLM cap BEFORE any work is done.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter
from app.core.net import client_ip
from app.core.notify import notify_discord
from app.core import logging as event_logging
from app.db import get_db
from app.features.email.pipeline import run_pipeline
from app.features.email.schemas import EmailRequest
from app.features.public_api.schemas import (
    DraftRequest,
    DraftResponse,
    LogEntry,
    NotifyRequest,
    NotifyResponse,
    StatusResponse,
)
from app.models import APIKey, EventLog, RateLimit

router = APIRouter()




@router.get("/status", response_model=StatusResponse)
def status_route(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> StatusResponse:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
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
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    stmt = select(EventLog).order_by(EventLog.created_at.desc())
    if source:
        stmt = stmt.where(EventLog.source == source)
    if action:
        stmt = stmt.where(EventLog.action == action)
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return [LogEntry.model_validate(r) for r in rows]


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
    ip = client_ip(request)
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


@router.post("/notify", response_model=NotifyResponse)
def notify_route(
    request: Request,
    body: NotifyRequest,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("trigger")),
) -> NotifyResponse:
    """Relay a short message to the committee's Discord channel. Trigger-scoped.

    Sends no LLM spend, but it is an outbound side effect on a public surface, so
    it carries the ``trigger`` scope (not ``read``) and the per-key request limit.
    Returns 503 when no Discord webhook is configured, so a caller can tell "not
    delivered because unconfigured" from a delivery failure. Every relay is logged
    to EventLog for the dashboard audit trail.
    """
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if not settings.DISCORD_NOTIFY_WEBHOOK_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Discord relay not configured (set DISCORD_NOTIFY_WEBHOOK_URL)",
        )
    delivered = notify_discord(body.message, username=body.username)
    event_logging.log_event(
        db,
        source="notify",
        action="discord_relay",
        external_id=key.prefix,
        output=body.message[:512],
        classification="delivered" if delivered else "failed",
    )
    return NotifyResponse(delivered=delivered)
