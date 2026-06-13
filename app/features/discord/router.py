"""Discord webhook — v2 PLACEHOLDER (working stub).

Intended use: relay processed-email summaries, sync results, or committee alerts
into a Discord channel; later, ingest Discord slash-command / interaction events.
Discord signs webhooks (Ed25519), so the signature dependency shape is wired now.

Proves the extension pattern: a new integration = a folder + a router + one mount
line in main.py, sharing the same auth / DB / LLM / logging core.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.auth import verify_webhook_signature

router = APIRouter()


@router.post("/webhook", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def discord_webhook(_: None = Depends(verify_webhook_signature("discord"))) -> dict:
    return {"detail": "discord webhook not yet implemented"}
