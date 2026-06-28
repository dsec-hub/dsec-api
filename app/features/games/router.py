"""Games REST API. Reads need `read`; submitting a play needs `write`; the
monthly draw cron is authorised by the CRON_SECRET bearer.

All scoring lives in the service layer — these handlers only marshal input and
serialise the official result. No client ever computes points here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.auth import require_cron_secret
from app.core.apikeys import require_api_key
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import draws, service
from .schemas import AttemptRequest, GameOut

router = APIRouter()


@router.get("", response_model=list[GameOut])
def list_games(
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> list[GameOut]:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    return [GameOut.model_validate(g) for g in service.list_games(db)]


@router.get("/leaderboard")
def get_leaderboard(
    request: Request,
    game: str | None = None,
    window: str = "daily",
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if window not in ("daily", "weekly", "cycle"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "window must be daily, weekly or cycle")
    entries = service.leaderboard(db, game_slug=game, window=window, limit=limit)
    return {"window": window, "game": game, "entries": entries}


@router.get("/me")
def get_me(
    request: Request,
    account_id: int,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    summary = service.player_summary(db, account_id=account_id)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plays yet for that account")
    return summary


@router.get("/draw")
def get_draw(
    request: Request,
    period_key: str | None = None,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    """Current (or named) draw-cycle standings — members-only, highest points
    wins. Powers the gift-card note on the leaderboard page."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    pk = period_key or draws.cycle_key(datetime.now(timezone.utc))
    cycle = draws.get_or_create_open_cycle(db, pk)
    return {
        "period_key": pk,
        "status": cycle.status,
        "winner_player_id": cycle.winner_player_id,
        "standings": draws.standings(db, pk, members_only=True, limit=20),
    }


@router.get("/cron/close-draw")
def cron_close_draw(
    request: Request,
    period_key: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_cron_secret),
) -> dict:
    """Close a draw cycle and open the next. Defaults to the month that just
    ended. Idempotent. Triggered monthly by Vercel Cron, which sends a GET with
    `Authorization: Bearer <CRON_SECRET>` (the gate is the secret, not the verb)."""
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    now = datetime.now(timezone.utc)
    pk = period_key or draws.prev_cycle_key(now)
    cycle = draws.close_cycle(db, pk)
    return {
        "period_key": cycle.period_key,
        "status": cycle.status,
        "winner_player_id": cycle.winner_player_id,
        "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
        "snapshot": cycle.total_points_snapshot,
    }


@router.get("/{slug}/round")
def get_round(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    payload = service.public_round(db, slug)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown game")
    return payload


@router.get("/{slug}/state")
def get_state(
    slug: str,
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    """A player's current state for a game (e.g. Codle's board so far), so a client
    can resume after a refresh. Engine-agnostic — no per-game branching."""
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    return service.game_state(db, slug=slug, account_id=account_id)


@router.post("/{slug}/attempt")
def post_attempt(
    slug: str,
    body: AttemptRequest,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    try:
        return service.submit_attempt(
            db,
            slug=slug,
            account_id=body.account_id,
            email=body.email,
            display_name=body.display_name,
            submission=body.submission,
            surface=body.surface,
        )
    except service.GameError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
