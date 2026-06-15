"""Shared auth dependencies reused by every feature.

- `require_agent_secret`  — shared-secret header for Apps-Script-style callers.
- `require_basic_auth`    — HTTP basic auth for the dashboard / admin / docs.
- `verify_webhook_signature(mode)` — dependency factory for third-party webhooks
  (Discord, Cal.com). The HMAC verification is stubbed where the real provider
  format is needed in v2, but the shape is built now.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.config import settings
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db

_basic = HTTPBasic()


def _is_production() -> bool:
    """True when running on Vercel (which always exports ``VERCEL=1``)."""
    return os.environ.get("VERCEL") == "1"


def require_agent_secret(x_agent_secret: str | None = Header(default=None)) -> None:
    """Validate the `X-Agent-Secret` header against `AGENT_SECRET`."""
    expected = settings.AGENT_SECRET
    if not x_agent_secret or not hmac.compare_digest(x_agent_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing agent secret",
        )


def require_basic_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(_basic),
    db: Session = Depends(get_db),
) -> str:
    """HTTP basic auth against the dashboard credentials. Returns the username.

    A per-IP rate limit runs *before* the credential check so the single shared
    password protecting API-key issuance (`/admin/keys`), the gated docs, and the
    audit dashboard can't be brute-forced unthrottled. `hmac.compare_digest`
    keeps the comparison constant-time.
    """
    limiter.check_request(db, key_id=None, ip=client_ip(request))
    user_ok = hmac.compare_digest(credentials.username, settings.DASHBOARD_USER)
    pass_ok = hmac.compare_digest(credentials.password, settings.DASHBOARD_PASS)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _secret_for_mode(mode: str) -> str:
    return {
        "discord": settings.DISCORD_WEBHOOK_SECRET,
        "calcom": settings.CALCOM_WEBHOOK_SECRET,
    }.get(mode, "")


def verify_webhook_signature(mode: str) -> Callable:
    """Dependency factory validating an inbound webhook's signature.

    `mode` selects both the secret and the provider-specific header/format:

    - ``calcom``: HMAC-SHA256 of the raw body (hex digest), compared against the
      ``X-Cal-Signature-256`` header. This is the format Cal.com uses and is the
      one live consumer (`/calcom/webhook` creates a SponsorLead).
    - ``discord``: HMAC is a PLACEHOLDER. Discord signs interactions with Ed25519
      (public-key) over ``X-Signature-Timestamp`` + raw body, verified against the
      app's public key — NOT HMAC. The `/discord/webhook` route is a 501 stub, so
      nothing is processed yet; real Ed25519 verification (PyNaCl/`cryptography`)
      must land before it does any work. See TODO.md "v2 integrations".

    When no secret is configured the request fails closed in production (503) and
    is allowed through in dev/test so the stub stays reachable. Raw body bytes are
    read directly from the request so a JSON parser never consumes the stream
    before the signature is computed.
    """

    async def _dep(request: Request) -> None:
        secret = _secret_for_mode(mode)
        if not secret:
            # No secret configured. In production that means the webhook is not
            # provisioned — fail CLOSED (503) so an unauthenticated caller can't
            # reach a handler that writes to the DB (e.g. the Cal.com webhook
            # creates a SponsorLead). In dev/test the stub stays reachable so it
            # can be exercised without a secret.
            if _is_production():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"{mode} webhook is not configured",
                )
            return

        raw = await request.body()
        if mode == "discord":
            provided = request.headers.get("X-Signature-Ed25519", "")
        else:  # calcom
            provided = request.headers.get("X-Cal-Signature-256", "")

        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        # Some providers prefix with "sha256=" — normalise before comparing.
        provided_norm = provided.split("=", 1)[-1]
        if not provided or not secrets.compare_digest(provided_norm, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid {mode} webhook signature",
            )

    return _dep
