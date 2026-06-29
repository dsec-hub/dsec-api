"""Signed, time-limited preview tokens for draft events.

The committee dashboard can open an *unpublished* event on the public marketing
site before it goes live, so they can see exactly how it will look. The link
carries an unguessable token of the form ``<event_id>.<exp>.<sig>`` where ``sig``
is an HMAC over the event id + expiry. This means it:

  * needs no DB column      → fully stateless (no migration, nothing to store);
  * can't be forged         → you can't mint a valid token without the secret;
  * can't be enumerated     → the signature, not the id, is what gates access;
  * is temporary            → it stops resolving after ``EVENT_PREVIEW_TTL``.

Mirrors the member-code signing approach (see members/verification.py): the
secret reuses ``AGENT_SECRET`` unless ``EVENT_PREVIEW_SECRET`` is set, so there
is no new required secret to provision.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import settings

# 128 bits of truncated HMAC — unguessable, and short enough for a tidy URL.
_SIG_BYTES = 16


def _secret() -> bytes:
    return (settings.EVENT_PREVIEW_SECRET or settings.AGENT_SECRET).encode("utf-8")


def _b64(raw: bytes) -> str:
    """URL-safe base64 with the padding stripped (re-added on decode-free verify)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(event_id: int, exp: int) -> str:
    msg = f"event-preview:{event_id}:{exp}".encode()
    digest = hmac.new(_secret(), msg, hashlib.sha256).digest()[:_SIG_BYTES]
    return _b64(digest)


def make_preview_token(event_id: int, ttl: int | None = None) -> str:
    """Mint a fresh preview token for an event, valid for ``ttl`` seconds."""
    window = ttl if ttl is not None else settings.EVENT_PREVIEW_TTL
    exp = int(time.time()) + int(window)
    return f"{event_id}.{exp}.{_sign(event_id, exp)}"


def verify_preview_token(token: str) -> int | None:
    """Return the event id a valid, unexpired token addresses, else ``None``.

    Constant-time on the signature compare; returns ``None`` (never raises) for
    anything malformed, tampered, or past its expiry so callers just 404.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    id_s, exp_s, sig = parts
    try:
        event_id = int(id_s)
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    if not hmac.compare_digest(sig, _sign(event_id, exp)):
        return None
    return event_id


# --------------------------------------------------------------------------- #
# Page (custom-page Document) preview tokens
#
# Same stateless HMAC scheme as events, but with a distinct message prefix so a
# page token can never be replayed as an event token (or vice-versa). Lets the
# committee preview an UNPUBLISHED page (draft Document) on the live site before
# flipping it public.
# --------------------------------------------------------------------------- #


def _sign_page(document_id: int, exp: int) -> str:
    msg = f"page-preview:{document_id}:{exp}".encode()
    digest = hmac.new(_secret(), msg, hashlib.sha256).digest()[:_SIG_BYTES]
    return _b64(digest)


def make_page_preview_token(document_id: int, ttl: int | None = None) -> str:
    """Mint a fresh preview token for a page document, valid for ``ttl`` seconds."""
    window = ttl if ttl is not None else settings.EVENT_PREVIEW_TTL
    exp = int(time.time()) + int(window)
    return f"{document_id}.{exp}.{_sign_page(document_id, exp)}"


def verify_page_preview_token(token: str) -> int | None:
    """Return the document id a valid, unexpired page token addresses, else None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    id_s, exp_s, sig = parts
    try:
        document_id = int(id_s)
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    if not hmac.compare_digest(sig, _sign_page(document_id, exp)):
        return None
    return document_id
