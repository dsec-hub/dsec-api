"""Discord webhook bot — slash-command interactions only (no gateway socket).

Discord POSTs every interaction to /discord/interactions. The Ed25519 signature
is verified (see app.auth.verify_webhook_signature("discord")), PING is answered
with PONG, and commands are routed to the in-process games brain. The endpoint
must answer within 3s; in-process DB work is well under that, so we respond
inline without deferring.

Proves the extension pattern: a new integration = a folder + a router + one mount
line in main.py, sharing the same auth / DB / logging core.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import verify_webhook_signature
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db

from . import interactions

router = APIRouter()


async def _interaction_response(request: Request, db: Session) -> dict:
    # The signature dep already ran and read the body; Starlette caches it so
    # json() still works here.
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    interaction = await request.json()
    return interactions.handle_interaction(db, interaction)


@router.post("/interactions")
async def discord_interactions(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_webhook_signature("discord")),
) -> dict:
    return await _interaction_response(request, db)


# Back-compat alias for apps pointed at the old /discord/webhook URL.
@router.post("/webhook")
async def discord_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_webhook_signature("discord")),
) -> dict:
    return await _interaction_response(request, db)
