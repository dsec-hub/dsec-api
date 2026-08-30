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
from datetime import datetime, timedelta, timezone

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
# read:<m>/write:<m> — per-module scopes so a key can be minted with exactly one
#   module's access instead of blanket read/write. A legacy read/write key still
#   satisfies them (see `has_scope` below).
VALID_SCOPES = {
    "read", "write", "trigger", "ingest",
    "read:sponsors", "write:sponsors", "read:finance", "write:finance",
    # The games surface (/games, /game-link) is public-facing: dsec-games holds
    # a service key on a site anyone can reach. Blanket "write" was the only
    # scope that satisfied POST /games/{slug}/attempt, so shipping games meant
    # handing that site a key that could also write events, tasks, members,
    # documents, links, projects, partners, people and meetings. These let it
    # hold exactly the access it uses.
    "read:games", "write:games",
    # PII-heavy modules isolated so a key can be minted with exactly one of them
    # instead of blanket read/write: the membership roster (names/emails/student
    # ids from DUSA), committee people, and stored documents. A legacy read/write
    # key still satisfies these (see `has_scope`), so tightening the routes below
    # cannot lock out any key already issued. Members is read-only over the API
    # (the roster is imported, never written here), so there is no write:members.
    "read:members",
    "read:people", "write:people",
    "read:documents", "write:documents",
}

# Modules whose per-module scopes are NOT satisfiable by the legacy coarse
# read/write scopes. The isolation is enforced right here in has_scope, so even a
# key holding blanket read/write cannot reach finance/sponsors by name — only an
# explicit read:finance / write:sponsors (etc.) does (SEC-05). Before this, blanket
# "write" satisfied "write:finance", so an Events Lead's OAuth token (which carries
# blanket read/write because it also has focus-only modules) could reach Finance.
#
# Deliberately NARROWER than oauth.service.ENFORCED_MODULES (which also lists
# members/people/documents): those are isolated only at OAuth *issuance* — a token
# scoped to them isn't granted blanket read/write — while a legacy key still
# reaches them here, so tightening those routes never locked out an already-issued
# key. oauth.service builds ENFORCED_MODULES from this tuple so finance/sponsors
# live in exactly one place.
SCOPE_ISOLATED_MODULES = ("finance", "sponsors")

# Length of the human-facing prefix used for DB lookup, e.g. "dsec_live_a1b2c3d4".
_PREFIX_RANDOM_LEN = 8

# Default lifetime stamped on a NEWLY minted key (SEC-07c). Pre-existing keys keep
# NULL (never expires); only new keys carry this, so the enforcement in verify_key
# cannot retroactively kill the old shared service keys.
DEFAULT_KEY_TTL_DAYS = 180


def default_key_expiry() -> datetime:
    """`expires_at` for a freshly minted key: 180 days out. Callers that mint a
    key set this; existing rows stay NULL (see the expires_at migration)."""
    return datetime.now(timezone.utc) + timedelta(days=DEFAULT_KEY_TTL_DAYS)


def _aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes for DateTime(timezone=True); treat a
    naive value as UTC so expiry comparisons never raise naive-vs-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _module_of(required: str) -> str | None:
    """'write:finance' -> 'finance'; 'write' / 'read' / 'trigger' -> None."""
    return required.split(":", 1)[1] if ":" in required else None


def has_scope(scopes, required: str) -> bool:
    """Does a credential carrying ``scopes`` satisfy ``required``?

    Backward-compatible scope algebra so that every existing credential — the
    ``dsec_live_`` keys and OAuth tokens that carry the legacy coarse
    ``read``/``write`` — keeps working everywhere, while the new per-module
    scopes (``read:sponsors``, ``write:games``, …) provide tighter isolation:

    - legacy ``"write"`` is a superset of every ``write:*``, every ``read:*`` and
      legacy ``"read"`` — EXCEPT scopes belonging to a ``SCOPE_ISOLATED_MODULES``
      module (finance, sponsors), which the coarse scopes never cover.
    - legacy ``"read"`` is a superset of every ``read:*``, with the same exception.
    - ``"write:X"`` satisfies ``"read:X"`` (including the isolated modules, so a
      genuine ``write:finance`` key can read finance).
    - any other scope (``trigger``, ``ingest``, an exact module scope) matches
      only itself.

    This lives in core rather than in the MCP feature because ``require_api_key``
    below is its most important caller — the REST API is the surface almost every
    credential actually uses. It was previously defined in
    ``app/features/mcp/auth.py``, which imports *from* this module, so the REST
    layer could not reach it without a circular import and silently fell back to
    an exact subset test. ``mcp.auth`` now re-exports it from here.
    """
    if required in scopes:
        return True
    # The legacy coarse scopes are a broad superset for the focus-only modules,
    # but must NEVER cover a scope belonging to a SCOPE_ISOLATED module (SEC-05).
    if _module_of(required) not in SCOPE_ISOLATED_MODULES:
        # Legacy "write" — the universal superset of every read/write scope, coarse
        # or per-module, plus legacy "read".
        if "write" in scopes and (required == "read" or required.startswith(("read:", "write:"))):
            return True
        # Legacy "read" covers every read scope (exact "read" handled above).
        if "read" in scopes and required.startswith("read:"):
            return True
    # write:X implies read:X (kept for isolated modules too — this is what lets a
    # real write:finance key read finance).
    if required.startswith("read:") and f"write:{required[len('read:'):]}" in scopes:
        return True
    return False


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
    # SEC-07c: a NULL expiry means "never expires" (all pre-existing keys, incl.
    # the shared service keys, are NULL). A key with a set expiry stops working
    # once it passes; new keys default to 180 days (see default_key_expiry).
    if row.expires_at is not None and _aware(row.expires_at) <= datetime.now(timezone.utc):
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
        # Scope-algebra-aware, NOT a plain subset test. A key holding legacy
        # "write" must still satisfy a route asking for "write:games", or
        # narrowing any route would revoke access from every key already issued.
        missing = sorted(s for s in needed if not has_scope(granted, s))
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"key missing required scope(s): {missing}",
            )
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return row

    return _dep
