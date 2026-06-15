"""Network helpers shared across feature routers.

The single source of truth for "what is the client's IP" — used as the key for
per-IP rate limiting. Previously every router copy-pasted a `_client_ip` helper
that returned the *leftmost* `X-Forwarded-For` entry, which is fully
client-controlled: an attacker can send a fresh fake `X-Forwarded-For` per
request to get a brand-new rate-limit bucket every time (defeating the per-IP
guard and amplifying writes to the `RateLimit` table).

This derives the IP from a value the *platform* sets, not one the caller can
forge. On Vercel (the deploy target — see HOSTING.md, grey-cloud/DNS-only
Cloudflare so nothing else proxies in front), `x-real-ip` is set by the edge to
the true connecting IP and cannot be overridden by the client, and the
*rightmost* `X-Forwarded-For` hop is the one Vercel appends. We trust those, in
order, and fall back to the socket peer.
"""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str:
    """Best-effort trusted client IP for rate limiting.

    Order of preference:
    1. ``x-real-ip`` — set by the Vercel edge, not client-spoofable.
    2. The **rightmost** ``X-Forwarded-For`` entry — the hop the trusted proxy
       appended (the leftmost entries are attacker-supplied and ignored).
    3. The raw socket peer (local/dev, no proxy).
    """
    real = request.headers.get("x-real-ip")
    if real and real.strip():
        return real.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the last hop, which the platform controls — never the spoofable
        # client-supplied leftmost value.
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]

    return request.client.host if request.client else "unknown"
