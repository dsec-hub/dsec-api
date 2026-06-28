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


def require_cron_secret(authorization: str | None = Header(default=None)) -> None:
    """Authorise a scheduled (Vercel Cron) call.

    Vercel Cron sends ``Authorization: Bearer <CRON_SECRET>`` (its documented
    convention — not a custom header), so we read the bearer token and compare it
    constant-time to ``settings.CRON_SECRET``. Fails CLOSED in production (503 if
    unconfigured, 401 on mismatch); in dev/test a blank secret allows the route
    through so the monthly draw can be exercised without provisioning a secret.
    """
    expected = settings.CRON_SECRET
    if not expected:
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="cron is not configured",
            )
        return
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing cron secret",
        )


def _secret_for_mode(mode: str) -> str:
    return {
        "calcom": settings.CALCOM_WEBHOOK_SECRET,
    }.get(mode, "")


async def _verify_discord_ed25519(request: Request) -> None:
    """Verify a Discord interaction signature (Ed25519, NOT HMAC).

    Discord signs each interaction with its application's private key over
    ``X-Signature-Timestamp`` + the raw request body, and we verify it against the
    application's PUBLIC KEY (a hex string from the Developer Portal). When no
    public key is configured the request fails closed in production (503) and is
    allowed through in dev/test so the endpoint stays exercisable without a key.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public_key = settings.DISCORD_PUBLIC_KEY
    if not public_key:
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="discord webhook is not configured",
            )
        return

    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    body = await request.body()
    if not signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid discord webhook signature",
        )
    try:
        verify_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key))
        verify_key.verify(bytes.fromhex(signature), timestamp.encode() + body)
    except (InvalidSignature, ValueError) as exc:
        # ValueError covers a malformed hex signature/public key.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid discord webhook signature",
        ) from exc


def verify_webhook_signature(mode: str) -> Callable:
    """Dependency factory validating an inbound webhook's signature.

    `mode` selects both the secret and the provider-specific header/format:

    - ``calcom``: HMAC-SHA256 of the raw body (hex digest), compared against the
      ``X-Cal-Signature-256`` header. This is the format Cal.com uses and is one
      live consumer (`/calcom/webhook` creates a SponsorLead).
    - ``discord``: Ed25519 public-key verification over ``X-Signature-Timestamp``
      + raw body against the app's ``DISCORD_PUBLIC_KEY`` (NOT HMAC — Discord
      never sends an HMAC). Powers the `/discord/interactions` webhook bot.

    When no secret/key is configured the request fails closed in production (503)
    and is allowed through in dev/test so the endpoint stays reachable. Raw body
    bytes are read directly from the request so a JSON parser never consumes the
    stream before the signature is computed.
    """

    async def _dep(request: Request) -> None:
        if mode == "discord":
            await _verify_discord_ed25519(request)
            return

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
        provided = request.headers.get("X-Cal-Signature-256", "")  # calcom

        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        # Some providers prefix with "sha256=" — normalise before comparing.
        provided_norm = provided.split("=", 1)[-1]
        if not provided or not secrets.compare_digest(provided_norm, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid {mode} webhook signature",
            )

    return _dep
