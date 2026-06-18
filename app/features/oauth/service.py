"""OAuth token + code primitives: generation, hashing, persistence, validation.

Everything here is storage-layer logic shared by the router. Tokens and codes
are opaque, high-entropy secrets stored only as SHA-256 hashes; we look them up
by a short indexed prefix, then compare the full hash in constant time.

SQLite (used in tests) returns *naive* datetimes for ``DateTime(timezone=True)``
columns, while Postgres returns aware ones. ``_aware()`` normalises both to
UTC-aware so expiry comparisons never raise "can't compare naive and aware".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppUser, OAuthAuthCode, OAuthClient, OAuthToken

# Scopes the authorization server understands and advertises (the coarse model a
# client requests). The issued access-token scope is *expanded* per the user's
# role modules at issue time — see `scopes_for_grant` — so the enforced modules
# (Sponsors, Finance) are carried as per-module scopes, never blanket read/write.
SUPPORTED_SCOPES = ("read", "write", "trigger", "ingest")

# Modules whose MCP tools are isolated behind per-module scopes (PHASE 2A). Every
# other module stays "focus-only": represented by the legacy coarse read/write,
# so the broad tools keep working unchanged.
ENFORCED_MODULES = ("finance", "sponsors")

# Every gateable workspace module (mirrors dsec-hub ROLES.md). "admin" is a
# superuser flag rather than a data module, so it is expanded to "all modules"
# instead of being emitted as a read:admin / write:admin scope.
ALL_MODULES = (
    "events", "people", "sponsors", "finance", "tasks",
    "projects", "members", "meetings", "documents",
)
_PREFIX_RANDOM_LEN = 8


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Treat a naive datetime (SQLite) as UTC so comparisons are always safe."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _gen_token(prefix_const: str) -> tuple[str, str, str]:
    """Return (raw, lookup_prefix, sha256_hash) for a fresh opaque token."""
    rnd = secrets.token_urlsafe(32)
    raw = f"{prefix_const}{rnd}"
    prefix = f"{prefix_const}{rnd[:_PREFIX_RANDOM_LEN]}"
    return raw, prefix, sha256_hex(raw)


def _prefix_of(raw: str, prefix_const: str) -> str:
    rnd = raw[len(prefix_const):]
    return f"{prefix_const}{rnd[:_PREFIX_RANDOM_LEN]}"


# --------------------------------------------------------------------------- #
# PKCE
# --------------------------------------------------------------------------- #

def verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    """Verify an RFC 7636 PKCE code_verifier against the stored challenge.

    Only S256 is accepted (we never advertise ``plain``). A syntactically invalid
    verifier (length out of the 43–128 spec range) is rejected up front.
    """
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    if method != "S256":
        return False
    expected = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return hmac.compare_digest(expected, challenge)


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #

def get_client(db: Session, client_id: str) -> OAuthClient | None:
    if not client_id:
        return None
    return db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    ).scalar_one_or_none()


def verify_client_secret(client: OAuthClient, secret: str | None) -> bool:
    """Confidential clients must present the right secret; public clients (no
    stored secret) authenticate by PKCE alone."""
    if client.client_secret_hash is None:
        return True  # public client
    if not secret:
        return False
    return hmac.compare_digest(client.client_secret_hash, sha256_hex(secret))


def register_client(
    db: Session,
    *,
    redirect_uris: list[str],
    client_name: str | None,
    grant_types: list[str],
    response_types: list[str],
    token_endpoint_auth_method: str,
    scope: str | None,
) -> tuple[OAuthClient, str | None]:
    """Create a client (RFC 7591). Returns (row, raw_secret_or_None)."""
    client_id = "dsec_client_" + secrets.token_urlsafe(16)
    raw_secret: str | None = None
    secret_hash: str | None = None
    if token_endpoint_auth_method != "none":
        raw_secret = secrets.token_urlsafe(32)
        secret_hash = sha256_hex(raw_secret)
    row = OAuthClient(
        client_id=client_id,
        client_secret_hash=secret_hash,
        client_name=client_name,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        response_types=response_types,
        token_endpoint_auth_method=token_endpoint_auth_method,
        scope=scope,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw_secret


# --------------------------------------------------------------------------- #
# Authorization codes (single-use, hashed, short-lived)
# --------------------------------------------------------------------------- #

def create_auth_code(
    db: Session,
    *,
    client_id: str,
    user_id: int,
    redirect_uri: str,
    scope: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str | None,
) -> str:
    raw = "dsec_ac_" + secrets.token_urlsafe(32)
    row = OAuthAuthCode(
        code_hash=sha256_hex(raw),
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
        expires_at=now() + timedelta(seconds=settings.OAUTH_AUTH_CODE_TTL),
    )
    db.add(row)
    db.commit()
    return raw


@dataclass
class CodeResult:
    ok: bool
    code: OAuthAuthCode | None = None
    error: str | None = None  # OAuth error code when ok is False


def consume_auth_code(db: Session, raw_code: str, *, client_id: str, redirect_uri: str | None) -> CodeResult:
    """Look up, validate, and single-use-consume an authorization code.

    Reuse of an already-consumed code (a replay) revokes every token in the
    same user+client family, then fails — defence against a leaked code.
    """
    if not raw_code:
        return CodeResult(False, error="invalid_grant")
    row = db.execute(
        select(OAuthAuthCode).where(OAuthAuthCode.code_hash == sha256_hex(raw_code))
    ).scalar_one_or_none()
    if row is None:
        return CodeResult(False, error="invalid_grant")
    if row.consumed:
        # Replay of a spent code → nuke any tokens minted from this grant.
        revoke_family(db, client_id=row.client_id, user_id=row.user_id)
        return CodeResult(False, error="invalid_grant")
    if _aware(row.expires_at) <= now():
        return CodeResult(False, error="invalid_grant")
    if row.client_id != client_id:
        return CodeResult(False, error="invalid_grant")
    # When the token request supplies redirect_uri it MUST match the one the code
    # was issued against (RFC 6749 §4.1.3); PKCE binds the code when it's omitted.
    if redirect_uri is not None and row.redirect_uri != redirect_uri:
        return CodeResult(False, error="invalid_grant")
    row.consumed = True
    db.commit()
    return CodeResult(True, code=row)


# --------------------------------------------------------------------------- #
# Access + refresh tokens
# --------------------------------------------------------------------------- #

def scopes_for_grant(db: Session, user: AppUser, coarse_granted: set[str]) -> list[str]:
    """Expand a coarse OAuth grant (read/write/trigger/ingest the user actually
    approved) into the module-aware scope list enforced at the MCP tool layer.

    The enforced modules (Sponsors, Finance) are represented EXCLUSIVELY by their
    per-module scopes (``read:sponsors``, ``write:finance``, …) and ONLY for a
    user whose role actually grants the module — so a login without the module
    never receives that module's scope and can't reach the isolated tools by
    name. Every other (focus-only) module additionally yields the legacy coarse
    ``read``/``write`` so the broad tools behave exactly as before.

    The user's role modules come from ``app_role`` via ``app_user.role_id`` (the
    same defensive join ``users.allowed_scopes_for`` uses). When those RBAC
    tables aren't present (e.g. the SQLite test DB, or a pre-RBAC environment) we
    fall back to the unchanged coarse grant — fully backward-compatible.
    """
    from app.features.oauth import users

    coarse = set(coarse_granted)
    modules, write_modules = users._role_perms(db, user)
    if modules is None:
        return sorted(coarse)  # no RBAC module info → legacy coarse behaviour

    read_mods = set(modules)
    write_mods = set(write_modules or [])
    if "admin" in read_mods:  # superuser → the full module universe
        read_mods = set(ALL_MODULES)
        write_mods = set(ALL_MODULES)

    out: set[str] = set(coarse & {"trigger", "ingest"})
    if "read" in coarse:
        out.update(f"read:{m}" for m in read_mods)
        if any(m not in ENFORCED_MODULES for m in read_mods):
            out.add("read")  # legacy read covers the focus-only modules
    if "write" in coarse:
        out.update(f"write:{m}" for m in write_mods)
        if any(m not in ENFORCED_MODULES for m in write_mods):
            out.add("write")
    return sorted(out)


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def issue_tokens(
    db: Session,
    *,
    client_id: str,
    user_id: int,
    scope: str,
    resource: str | None,
) -> IssuedTokens:
    access_raw, access_prefix, access_hash = _gen_token(settings.OAUTH_ACCESS_TOKEN_PREFIX)
    refresh_raw, refresh_prefix, refresh_hash = _gen_token(settings.OAUTH_REFRESH_TOKEN_PREFIX)
    row = OAuthToken(
        access_prefix=access_prefix,
        access_token_hash=access_hash,
        refresh_prefix=refresh_prefix,
        refresh_token_hash=refresh_hash,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        resource=resource,
        access_expires_at=now() + timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_TTL),
        refresh_expires_at=now() + timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL),
    )
    db.add(row)
    db.commit()
    return IssuedTokens(
        access_token=access_raw,
        refresh_token=refresh_raw,
        expires_in=settings.OAUTH_ACCESS_TOKEN_TTL,
        scope=scope,
    )


def verify_access_token(raw: str, db: Session) -> OAuthToken | None:
    """Validate an access token for the MCP middleware. Returns the row or None."""
    if not raw or not raw.startswith(settings.OAUTH_ACCESS_TOKEN_PREFIX):
        return None
    prefix = _prefix_of(raw, settings.OAUTH_ACCESS_TOKEN_PREFIX)
    row = db.execute(
        select(OAuthToken).where(OAuthToken.access_prefix == prefix)
    ).scalar_one_or_none()
    if row is None or row.revoked:
        return None
    if not hmac.compare_digest(row.access_token_hash, sha256_hex(raw)):
        return None
    if _aware(row.access_expires_at) <= now():
        return None
    return row


@dataclass
class RefreshResult:
    ok: bool
    token: OAuthToken | None = None
    error: str | None = None


def use_refresh_token(db: Session, raw: str, *, client_id: str) -> RefreshResult:
    """Validate a refresh token and rotate it. The presented token is revoked and
    a fresh pair is issued by the caller. Reuse of a spent refresh token revokes
    the whole family."""
    if not raw or not raw.startswith(settings.OAUTH_REFRESH_TOKEN_PREFIX):
        return RefreshResult(False, error="invalid_grant")
    prefix = _prefix_of(raw, settings.OAUTH_REFRESH_TOKEN_PREFIX)
    row = db.execute(
        select(OAuthToken).where(OAuthToken.refresh_prefix == prefix)
    ).scalar_one_or_none()
    if row is None or row.refresh_token_hash is None:
        return RefreshResult(False, error="invalid_grant")
    if not hmac.compare_digest(row.refresh_token_hash, sha256_hex(raw)):
        return RefreshResult(False, error="invalid_grant")
    if row.client_id != client_id:
        return RefreshResult(False, error="invalid_grant")
    if row.revoked:
        # Already-rotated refresh token presented again → token theft signal.
        revoke_family(db, client_id=row.client_id, user_id=row.user_id)
        return RefreshResult(False, error="invalid_grant")
    if _aware(row.refresh_expires_at) is not None and _aware(row.refresh_expires_at) <= now():
        return RefreshResult(False, error="invalid_grant")
    return RefreshResult(True, token=row)


def revoke_by_raw(db: Session, raw: str) -> None:
    """Best-effort revoke for the RFC 7009 revocation endpoint. Accepts either an
    access or a refresh token; unknown tokens are a silent no-op (per spec)."""
    if not raw:
        return
    row: OAuthToken | None = None
    if raw.startswith(settings.OAUTH_ACCESS_TOKEN_PREFIX):
        prefix = _prefix_of(raw, settings.OAUTH_ACCESS_TOKEN_PREFIX)
        cand = db.execute(
            select(OAuthToken).where(OAuthToken.access_prefix == prefix)
        ).scalar_one_or_none()
        if cand and hmac.compare_digest(cand.access_token_hash, sha256_hex(raw)):
            row = cand
    elif raw.startswith(settings.OAUTH_REFRESH_TOKEN_PREFIX):
        prefix = _prefix_of(raw, settings.OAUTH_REFRESH_TOKEN_PREFIX)
        cand = db.execute(
            select(OAuthToken).where(OAuthToken.refresh_prefix == prefix)
        ).scalar_one_or_none()
        if cand and cand.refresh_token_hash and hmac.compare_digest(
            cand.refresh_token_hash, sha256_hex(raw)
        ):
            row = cand
    if row is not None and not row.revoked:
        row.revoked = True
        db.commit()


def revoke_family(db: Session, *, client_id: str, user_id: int) -> None:
    """Revoke every active token a given user holds for a given client."""
    rows = db.execute(
        select(OAuthToken).where(
            OAuthToken.client_id == client_id,
            OAuthToken.user_id == user_id,
            OAuthToken.revoked == False,  # noqa: E712 — SQL boolean, not Python
        )
    ).scalars().all()
    changed = False
    for r in rows:
        r.revoked = True
        changed = True
    if changed:
        db.commit()
