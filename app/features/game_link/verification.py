"""Discord <-> account link codes — HMAC-derived from the account id.

Same one-way pattern as the membership-card code (features/members/verification):
the code is deterministic from `account_id` + a server secret, so it needs NO
storage and NO migration. To claim a code we recompute it for each candidate
player and compare (HMAC can't be reversed). Codes use a human-safe base32
alphabet (no I/L/O/U) so they read cleanly when typed into a Discord message.
"""

from __future__ import annotations

import hashlib
import hmac

from app.config import settings

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1", "U": "V"})
_CODE_BYTES = 5  # 40 bits -> exactly 8 base32 chars
_PREFIX = "DUCK"


def _secret() -> bytes:
    # Dedicated secret if set, else the always-present AGENT_SECRET. Rotating it
    # invalidates every previously shared link code.
    return (settings.GAMES_LINK_SECRET or settings.AGENT_SECRET).encode("utf-8")


def _b32(data: bytes) -> str:
    bits = int.from_bytes(data, "big")
    width = len(data) * 8
    out = []
    for shift in range(width - 5, -5, -5):
        if shift >= 0:
            out.append(_ALPHABET[(bits >> shift) & 0x1F])
        else:
            out.append(_ALPHABET[(bits << -shift) & 0x1F])
    return "".join(out)


def _core(account_id: int) -> str:
    digest = hmac.new(
        _secret(), f"discord-link:{account_id}".encode(), hashlib.sha256
    ).digest()
    return _b32(digest[:_CODE_BYTES])


def link_code(account_id: int) -> str:
    """The shareable code, e.g. DUCK-AB12-CD34."""
    core = _core(account_id)
    return f"{_PREFIX}-{core[:4]}-{core[4:]}"


def normalize_code(raw: str | None) -> str:
    if not raw:
        return ""
    # Strip the literal prefix BEFORE folding look-alikes — the prefix "DUCK"
    # contains a U, which the fold would turn into V and break the match.
    s = "".join(ch for ch in raw.upper() if ch.isalnum())
    if s.startswith(_PREFIX):
        s = s[len(_PREFIX):]
    return s.translate(_FOLD)


def code_matches(account_id: int, code: str) -> bool:
    core = normalize_code(code)
    if len(core) != _CODE_BYTES * 8 // 5:  # 8 chars
        return False
    return _core(account_id) == core


def find_player_by_link_code(players, code: str):
    """Return the GamePlayer whose code matches, recomputing per candidate."""
    core = normalize_code(code)
    if len(core) != _CODE_BYTES * 8 // 5:
        return None
    for p in players:
        if _core(p.account_id) == core:
            return p
    return None
