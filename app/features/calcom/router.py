"""Cal.com webhook — creates a SponsorLead for every booking on the
sponsorship Cal link (BOOKING_CREATED / BOOKING_RESCHEDULED events).

Cal.com signs webhooks with HMAC-SHA256 (verify_webhook_signature("calcom")).
The check fails CLOSED in production (503 when CALCOM_WEBHOOK_SECRET is unset,
401 on mismatch); in dev / without a secret the endpoint is reachable so it can
be tested manually. When CALCOM_NOTIFY_DISCORD is on and a Discord webhook is
configured, each booking also drops a short alert into the channel.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import verify_webhook_signature
from app.config import settings
from app.core.net import client_ip
from app.core.notify import notify_discord
from app.core.ratelimit import limiter
from app.db import get_db
from app.features.sponsor_leads import service as leads_service

router = APIRouter()
_logger = logging.getLogger("dsec.calcom")

# Cal.com sends these triggers for new and rescheduled bookings.
_BOOKING_TRIGGERS = {"BOOKING_CREATED", "BOOKING_RESCHEDULED"}


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def calcom_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_webhook_signature("calcom")),
) -> dict:
    """Receive a Cal.com booking and create a SponsorLead."""
    # Per-IP throttle: this is a public write endpoint, so bound how fast anyone
    # (even with a valid signature) can inject sponsor leads.
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON payload")

    trigger = payload.get("triggerEvent", "")
    if trigger not in _BOOKING_TRIGGERS:
        return {"detail": f"event '{trigger}' ignored"}

    booking = payload.get("payload", {})

    # Extract invitee details — Cal.com puts the external person as the first
    # attendee; responses hold per-field answers (form-like questions).
    attendees: list[dict] = booking.get("attendees", [])
    invitee = attendees[0] if attendees else {}
    responses: dict = booking.get("responses", {})

    def _resp(key: str) -> str | None:
        v = responses.get(key, {})
        val = v.get("value") if isinstance(v, dict) else None
        return str(val).strip() or None if val else None

    name = invitee.get("name") or _resp("name")
    email = invitee.get("email") or _resp("email") or ""
    company = _resp("company") or _resp("organisation")

    if not email or "@" not in email:
        _logger.warning("calcom webhook: no valid email in booking %s", booking.get("uid"))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "no valid email in booking payload",
        )

    lead = leads_service.create_lead(db, {
        "source": "cal_booking",
        "name": name,
        "email": email,
        "company": company,
        "message": booking.get("description") or None,
    })

    _logger.info("calcom webhook: created sponsor_lead id=%s email=%s", lead.id, email)

    # Optional, best-effort Discord alert — never fails the webhook (notify_discord
    # swallows every error and no-ops when unconfigured). Deliberately does NOT
    # post the booker's email: the channel is broader than the sponsorship inbox,
    # so fall back to the company or a generic label when no name is given rather
    # than leaking a raw address.
    if settings.CALCOM_NOTIFY_DISCORD:
        who = name or company or "a new prospect"
        notify_discord(
            f"📅 New sponsorship booking: **{who}**"
            + (f" ({company})" if company and who != company else ""),
            username="DSEC Bookings",
        )

    return {"detail": "lead created", "lead_id": lead.id}
