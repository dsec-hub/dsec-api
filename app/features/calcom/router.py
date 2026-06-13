"""Cal.com webhook — v2 PLACEHOLDER (working stub).

Intended use: when a booking is made via the Cal.com link appended to meeting
drafts, log it to EventLog and optionally notify Discord. Cal.com signs webhooks
with an HMAC-SHA256 secret, so the signature dependency shape is wired now.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.auth import verify_webhook_signature

router = APIRouter()


@router.post("/webhook", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def calcom_webhook(_: None = Depends(verify_webhook_signature("calcom"))) -> dict:
    return {"detail": "calcom webhook not yet implemented"}
