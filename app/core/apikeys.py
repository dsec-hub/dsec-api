"""API key generation, hashing, verification, and scope checks.

Audience is the DSEC committee / trusted internal tools — "trusted but
accountable", not hostile-public hardening. Still real, because `trigger` calls
spend LLM credits.

Key format: ``dsec_live_<token_urlsafe(32)>``. The plaintext **prefix** is stored
for lookup/display; the **full key** is argon2-hashed and shown exactly once.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import APIKey

_hasher = PasswordHasher()

# read   — read-only data access, no LLM spend.
# write  — create/update/archive workspace + domain data (no LLM spend).
# trigger — actions that spend LLM credits (email drafting, meeting notes).
# ingest — write DUSA weekly imports (membership / P&L) via /ingest.
# read:<m>/write:<m> — per-module scopes for the "enforced" modules (Sponsors,
#   Finance) so a key can be minted with exactly that module's access instead of
#   blanket read/write. A legacy read/write key still satisfies them (see
#   app/features/mcp/auth.py::has_scope).
VALID_SCOPES = {
    "read", "write", "trigger", "ingest",
    "read:sponsors", "write:sponsors", "read:finance", "write:finance",
}

# Length of the human-facing prefix used for DB lookup, e.g. "dsec_live_a1b2c3d4".
_PREFIX_RANDOM_LEN = 8


@dataclass
class GeneratedKey:
    raw_key: str  # shown exactly once
    prefix: str
    key_hash: str


def generate_key() -> GeneratedKey:
    """Create a new key. Caller persists prefix+hash; raw_key is shown once."""
    random_part = secrets.token_urlsafe(32)
    raw_key = f"{settings.API_KEY_PREFIX}{random_part}"
    # Prefix = configured prefix + first few chars of the random part, for lookup.
    prefix = f"{settings.API_KEY_PREFIX}{random_part[:_PREFIX_RANDOM_LEN]}"
    key_hash = _hasher.hash(raw_key)
    return GeneratedKey(raw_key=raw_key, prefix=prefix, key_hash=key_hash)


def _prefix_of(raw_key: str) -> str:
    random_part = raw_key[len(settings.API_KEY_PREFIX):]
    return f"{settings.API_KEY_PREFIX}{random_part[:_PREFIX_RANDOM_LEN]}"


def verify_key(raw_key: str, db: Session) -> APIKey | None:
    """Look a key up by prefix and verify its hash. Returns the row or None."""
    if not raw_key or not raw_key.startswith(settings.API_KEY_PREFIX):
        return None
    prefix = _prefix_of(raw_key)
    row = db.execute(select(APIKey).where(APIKey.prefix == prefix)).scalar_one_or_none()
    if row is None or row.revoked:
        return None
    try:
        _hasher.verify(row.key_hash, raw_key)
    except VerifyMismatchError:
        return None
    return row


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def require_api_key(*required_scopes: str):
    """Dependency factory: authenticate an API key and enforce `required_scopes`.

    Reads ``Authorization: Bearer <key>`` or ``X-API-Key``, verifies the hash,
    checks the key is not revoked, checks scopes, and stamps `last_used_at`.
    """
    needed = set(required_scopes)
    unknown = needed - VALID_SCOPES
    if unknown:
        raise ValueError(f"unknown scope(s): {unknown}")

    def _dep(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        db: Session = Depends(get_db),
    ) -> APIKey:
        raw = _extract_key(authorization, x_api_key)
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing API key",
            )
        row = verify_key(raw, db)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or revoked API key",
            )
        granted = set(row.scopes or [])
        if not needed.issubset(granted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"key missing required scope(s): {sorted(needed - granted)}",
            )
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return row

    return _dep
