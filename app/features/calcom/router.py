"""Cal.com webhook — creates a SponsorLead for every booking on the
sponsorship Cal link (BOOKING_CREATED / BOOKING_RESCHEDULED events).

Cal.com signs webhooks with HMAC-SHA256; the signature check is active only
when CALCOM_WEBHOOK_SECRET is configured. In dev / without a secret the
endpoint is reachable so the stub can be tested manually.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import verify_webhook_signature
from app.core.net import client_ip
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
    return {"detail": "lead created", "lead_id": lead.id}
