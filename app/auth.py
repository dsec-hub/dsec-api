"""Shared auth dependencies reused by every feature.

- `require_agent_secret`  — shared-secret header for Apps-Script-style callers.
- `require_basic_auth`    — HTTP basic auth for the dashboard / admin / docs.
- `verify_webhook_signature(mode)` — dependency factory for third-party webhooks
  (Discord, Cal.com, Notion). The HMAC verification is stubbed where the real
  provider format is needed in v2, but the shape is built now.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

_basic = HTTPBasic()


def require_agent_secret(x_agent_secret: str | None = Header(default=None)) -> None:
    """Validate the `X-Agent-Secret` header against `AGENT_SECRET`."""
    expected = settings.AGENT_SECRET
    if not x_agent_secret or not hmac.compare_digest(x_agent_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing agent secret",
        )


def require_basic_auth(
    credentials: HTTPBasicCredentials = Depends(_basic),
) -> str:
    """HTTP basic auth against the dashboard credentials. Returns the username."""
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
        "notion": settings.NOTION_WEBHOOK_SECRET,
    }.get(mode, "")


def verify_webhook_signature(mode: str) -> Callable:
    """Dependency factory validating an inbound webhook's HMAC signature.

    `mode` selects both the secret and the provider-specific header/format:

    - ``discord`` / ``calcom``: HMAC-SHA256 of the raw body, hex digest, compared
      against the provider's signature header. (Header names finalised in v2.)
    - ``notion``: ``X-Notion-Signature`` = HMAC-SHA256 of the raw body keyed by the
      verification token. The Notion *verification handshake* (a body containing
      ``verification_token``) is handled in the notion router, not here.

    Raw body bytes are read directly from the request so a JSON parser never
    consumes the stream before the signature is computed.
    """

    async def _dep(request: Request) -> None:
        secret = _secret_for_mode(mode)
        # Until each provider is wired in v2, fail closed only when a secret is
        # configured; otherwise allow through so stubs are reachable in dev.
        if not secret:
            return

        raw = await request.body()
        if mode == "notion":
            provided = request.headers.get("X-Notion-Signature", "")
        elif mode == "discord":
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
