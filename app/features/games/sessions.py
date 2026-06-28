"""Short-lived signed play sessions for the spoofable arcade game.

Flappy Duck computes its score on the client, so we bind each play to a token the
server signs when the round is fetched. On submit the token is verified: a valid
signature, an unexpired issue time, and (in the service) that real wall-clock
elapsed since issue covers the reported run. This doesn't make the score
unforgeable — it bounds replay and timing, and the per-day points cap does the
rest. Documented honestly: mitigation, not prevention.
"""

from __future__ import annotations

import hashlib
import hmac

from app.config import settings


def _secret() -> bytes:
    return (settings.GAMES_SESSION_SECRET or settings.AGENT_SECRET).encode("utf-8")


def _sig(seed: str, issued_at: str) -> str:
    return hmac.new(_secret(), f"{seed}:{issued_at}".encode(), hashlib.sha256).hexdigest()[:32]


def sign_session(*, seed: int, issued_at_epoch: int) -> str:
    """Token shape: ``<seed>.<issued_at_epoch>.<hmac>``."""
    seed_s, issued_s = str(seed), str(issued_at_epoch)
    return f"{seed_s}.{issued_s}.{_sig(seed_s, issued_s)}"


def verify_session(token: str, *, now_epoch: int, ttl: int) -> dict:
    """Return ``{"seed": int, "issued_at": int}`` or raise ValueError."""
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise ValueError("malformed session")
    seed_s, issued_s, sig = parts
    if not hmac.compare_digest(sig, _sig(seed_s, issued_s)):
        raise ValueError("bad signature")
    try:
        seed = int(seed_s)
        issued = int(issued_s)
    except ValueError as exc:
        raise ValueError("malformed session") from exc
    if issued - now_epoch > 60:  # issued in the future (clock skew slack)
        raise ValueError("session not yet valid")
    if now_epoch - issued > ttl:
        raise ValueError("session expired")
    return {"seed": seed, "issued_at": issued}
