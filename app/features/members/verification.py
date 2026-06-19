"""Member verification codes — the digital membership card's brains.

A verified DSEC member sees a "membership card" in the portal with a short,
unique code (``DSEC-XXXX-XXXX``) and a QR. Door / event staff scan the QR (or
type the code) to confirm the holder is a current member.

The code is **HMAC-derived from the roster row id** rather than stored:

  * deterministic + stable  → the same member always gets the same code, with
    no new table, no migration, and nothing to keep in sync;
  * unforgeable             → you can't derive a valid code without the secret;
  * non-enumerable          → 40 bits of HMAC output, so codes aren't guessable
    from a member id.

Verification can't reverse the HMAC, so we resolve a code back to a member by
recomputing every *current* member's code and comparing — the roster is a few
hundred rows, so this is microseconds and needs no index.

The human-facing alphabet is Crockford base32 (no I/L/O/U) so the code is
unambiguous to read aloud and type; ``normalize_code`` folds the look-alikes a
user might enter (O→0, I/L→1) back to canonical before comparing.
"""

from __future__ import annotations

import hashlib
import hmac
import io

from app.config import settings
from app.models import Member

# Crockford base32, minus the ambiguous I, L, O, U. 32 symbols → 5 bits each.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Look-alikes a human might type, folded to the canonical symbol.
_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1", "U": "V"})
_CODE_BYTES = 5  # 40 bits → exactly 8 base32 chars, ~1.1e12 space.
_PREFIX = "DSEC"


def _secret() -> bytes:
    # A dedicated secret if set, else the always-present AGENT_SECRET. Either way
    # rotating it rotates every member's code (cards reprint on next view).
    return (settings.MEMBER_CODE_SECRET or settings.AGENT_SECRET).encode("utf-8")


def _b32(data: bytes) -> str:
    """Encode bytes with our clean alphabet (5 bits per symbol, no padding)."""
    bits = int.from_bytes(data, "big")
    width = len(data) * 8
    out = []
    for shift in range(width - 5, -5, -5):
        out.append(_ALPHABET[(bits >> shift) & 0x1F] if shift >= 0
                   else _ALPHABET[(bits << -shift) & 0x1F])
    return "".join(out)


def _core(member_id: int) -> str:
    """The 8-char significant body of a member's code."""
    digest = hmac.new(_secret(), f"dsec-member:{member_id}".encode(), hashlib.sha256).digest()
    return _b32(digest[:_CODE_BYTES])


def member_code(member_id: int) -> str:
    """The display code, e.g. ``DSEC-7K2P-9XQ4``."""
    core = _core(member_id)
    return f"{_PREFIX}-{core[:4]}-{core[4:]}"


def normalize_code(raw: str | None) -> str:
    """Fold user input to the canonical 8-char core for comparison.

    Accepts the full ``DSEC-XXXX-XXXX``, the bare core, lower-case, and the
    common look-alike substitutions. Returns "" if there's nothing usable.
    """
    if not raw:
        return ""
    s = "".join(ch for ch in raw.upper() if ch.isalnum()).translate(_FOLD)
    if s.startswith(_PREFIX):
        s = s[len(_PREFIX):]
    return s


def verify_url(code: str) -> str:
    """The public verify URL the QR encodes."""
    base = settings.MEMBER_VERIFY_BASE_URL.rstrip("/")
    return f"{base}/{code}"


def qr_svg(url: str) -> str | None:
    """A self-contained, CSS-sizable SVG QR for `url`, or None if unavailable.

    Dark modules on white with a quiet zone so it scans off a phone screen. We
    omit width/height (keep the viewBox) so the card can size it responsively.
    Best-effort: a missing/odd `segno` never breaks the code itself.
    """
    try:
        import segno
    except Exception:  # pragma: no cover - dep should be present in prod
        return None
    try:
        qr = segno.make(url, error="m")
        buf = io.BytesIO()
        qr.save(
            buf,
            kind="svg",
            scale=1,
            border=2,
            dark="#0c0a09",
            light="#ffffff",
            omitsize=True,
            xmldecl=False,
            svgns=True,
        )
        return buf.getvalue().decode("utf-8")
    except Exception:
        return None


def find_member_by_code(members: list[Member], code: str) -> Member | None:
    """Resolve a (possibly messy) code to a current member, or None.

    `members` should be the CURRENT roster — we recompute each one's code and
    compare cores. O(roster size), which is tiny.
    """
    core = normalize_code(code)
    if len(core) != _CODE_BYTES * 8 // 5:  # 8 chars
        return None
    for m in members:
        if _core(m.id) == core:
            return m
    return None
