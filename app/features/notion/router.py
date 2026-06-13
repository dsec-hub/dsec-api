"""Notion webhook — v2 PLACEHOLDER, but ALREADY drives the events sync.

Intended use: Notion event-DB changes trigger a Neon sync (section 8c); later,
processed emails could be written back to a Notion tracking DB.

Notion auth differs from Discord/Cal.com — handled in two phases:

1. **Verification handshake:** on first subscription Notion POSTs a one-time
   `verification_token` in the body. We detect that and echo/log it (never reject)
   so the subscription can be confirmed in Notion's UI.
2. **Event delivery:** subsequent events are signed via `X-Notion-Signature`
   (HMAC-SHA256 of the RAW body keyed by the verification token). We read the raw
   body before any JSON parsing so the signature can be verified over exact bytes,
   then call `sync_notion_events()` for relevant changes.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import verify_webhook_signature
from app.db import get_db
from app.features.events.sync import sync_notion_events

_logger = logging.getLogger("dsec.notion")

router = APIRouter()


@router.post("/webhook")
async def notion_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle the Notion verification handshake and (later) signed event delivery.

    Raw body is read first so HMAC verification in v2 sees exact bytes.
    """
    raw = await request.body()

    # Phase 1: verification handshake — echo/log the one-time token, never reject.
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        parsed = {}

    if isinstance(parsed, dict) and "verification_token" in parsed:
        token = parsed["verification_token"]
        _logger.info("Notion verification_token received: %s", token)
        return JSONResponse({"verification_token": token})

    # Phase 2: signed event delivery. Signature verification is wired via the
    # dependency factory once NOTION_WEBHOOK_SECRET is set; until then this stub
    # acknowledges and kicks off a reconciliation sync for relevant changes.
    await verify_webhook_signature("notion")(request)

    # A relevant change -> run the single-source-of-truth sync.
    sync_notion_events(db, trigger="webhook")

    # General (non-event) Notion events aren't implemented yet.
    return JSONResponse(
        {"detail": "notion event acknowledged; sync triggered"},
        status_code=status.HTTP_202_ACCEPTED,
    )
