"""Discord <-> account link REST API. Reads need `read`; writes need `write`.

Flow: the portal calls POST /game-link/start to get a short code and shows it to
the player, who runs `/link <code>` in Discord; the bot calls POST
/game-link/claim to bind their Discord id to the same account. From then on a
Discord play and a portal play share one points ledger.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.apikeys import require_api_key
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

from . import service, verification
from .schemas import LinkClaimRequest, LinkStartRequest

router = APIRouter()


@router.post("/start")
def link_start(
    body: LinkStartRequest,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    player = service.upsert_player(
        db,
        account_id=body.account_id,
        email=body.email,
        display_name=body.display_name,
    )
    code = verification.link_code(player.account_id)
    return {
        "account_id": player.account_id,
        "code": code,
        "instructions": f"In the DSEC Discord, run /link {code}",
    }


@router.post("/claim")
def link_claim(
    body: LinkClaimRequest,
    request: Request,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("write")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    player = service.link_discord(
        db, discord_user_id=body.discord_user_id, code=body.code
    )
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no account matches that link code")
    return {
        "linked": True,
        "account_id": player.account_id,
        "display_name": player.display_name,
    }


@router.get("/status")
def link_status(
    request: Request,
    account_id: int | None = None,
    discord_user_id: str | None = None,
    db: Session = Depends(get_db),
    key: APIKey = Depends(require_api_key("read")),
) -> dict:
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    if account_id is None and discord_user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "account_id or discord_user_id required")
    if account_id is not None:
        player = service.get_player_by_account(db, account_id)
    else:
        player = service.get_player_by_discord(db, discord_user_id)
    if player is None:
        return {"linked": False, "account_id": account_id, "discord_user_id": discord_user_id}
    discord_id = service.discord_for_player(db, player.id)
    return {
        "linked": discord_id is not None,
        "account_id": player.account_id,
        "discord_user_id": discord_id,
        "display_name": player.display_name,
    }
