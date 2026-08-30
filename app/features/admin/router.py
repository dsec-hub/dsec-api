"""Admin API — internal-only API key management.

Admin key management (`/keys`) is basic-auth protected. The raw key is shown
exactly once at creation. `/keys/self` is a narrow exception: a dashboard-user
self-service mint, authenticated by dsec-app's service API key and constrained to
that key's own scopes. The manual Notion sync trigger lives in the events router,
also mounted under /admin.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import require_basic_auth, require_cron_secret
from app.config import settings
from app.core.apikeys import (
    VALID_SCOPES,
    default_key_expiry,
    generate_key,
    require_api_key,
)
from app.core.logging import log_event
from app.core.net import client_ip
from app.features.archive.service import build_export_bundle
from app.features.mcp.auth import has_scope
from app.features.oauth import service as oauth_service
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey, OAuthClient, RateLimit

router = APIRouter()

# SEC-06 deploy-3: dsec-app scopes a user's own keys with an "appuser:<id>" owner
# label, and the hub's list/revoke UI keys off an EXACT `created_by == "appuser:N"`
# match. Validate the shape instead of trusting or removing it. Use fullmatch with
# an ASCII-only [0-9]: `^...$` + `\d` would accept "appuser:12\n" (Python `$`
# matches before a trailing newline) and Unicode digits like "appuser:١٢", both of
# which then fail the hub's exact lookup and leave the key invisible/unrevocable.
_OWNER_RE = re.compile(r"appuser:[0-9]+")


def _cascade_revoke(db: Session, root: APIKey) -> None:
    """Soft-revoke ``root`` and every descendant minted from it via /keys/self.

    BFS over ``parent_key_id`` so a child (or grandchild) key can't outlive the
    revocation of its parent. Soft-revoke throughout (never hard-delete) to keep
    the audit trail; a ``seen`` set guards against any accidental cycle. Shared by
    the basic-auth ``/keys/{id}/revoke`` and the service-key ``/keys/revoke-for-owner``
    so both revoke paths cascade identically (SEC-07d). Caller commits.
    """
    root.revoked = True
    frontier = [root.id]
    seen = {root.id}
    while frontier:
        # SEC-07d: lock each descendant row as it's discovered (FOR UPDATE; a no-op
        # on SQLite, a real row lock on Postgres) so a concurrent /keys/self mint of
        # a child serialises against this revoke instead of slipping a live child in
        # after we've read the tree. Caller must have locked `root` already.
        children = db.execute(
            select(APIKey).where(APIKey.parent_key_id.in_(frontier)).with_for_update()
        ).scalars().all()
        frontier = []
        for child in children:
            if child.id in seen:
                continue  # guard against any accidental cycle
            seen.add(child.id)
            child.revoked = True
            frontier.append(child.id)


class CreateKeyRequest(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])


class SelfKeyRequest(BaseModel):
    """A dashboard-user self-service mint, proxied by dsec-app.

    `owner` is an opaque label dsec-app uses to scope a user's own keys (e.g.
    ``appuser:42``); it is stored as `created_by`. The endpoint never trusts the
    requested scopes blindly — it enforces them against the *calling* key's
    scopes (see `self_create_key`), so a key can never mint a more-powerful key
    than itself.
    """

    name: str
    scopes: list[str] = Field(default_factory=list)
    owner: str


class CreateKeyResponse(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    raw_key: str  # shown exactly once


class KeyInfo(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool
    # SEC-07c: NULL = never expires (all pre-existing keys); new keys get 180 days.
    # Surfaced so dsec-hub's Settings -> API list can show the expiry coming.
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.post("/keys", response_model=CreateKeyResponse)
def create_key(
    req: CreateKeyRequest,
    db: Session = Depends(get_db),
    admin: str = Depends(require_basic_auth),
) -> CreateKeyResponse:
    invalid = set(req.scopes) - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid scope(s): {sorted(invalid)}; allowed: {sorted(VALID_SCOPES)}",
        )
    gen = generate_key()
    row = APIKey(
        name=req.name,
        prefix=gen.prefix,
        key_hash=gen.key_hash,
        scopes=req.scopes,
        created_by=admin,
        expires_at=default_key_expiry(),  # SEC-07c: new keys expire in 180 days
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CreateKeyResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=row.scopes,
        raw_key=gen.raw_key,  # the one and only time it's returned
    )


@router.post("/keys/self", response_model=CreateKeyResponse)
def self_create_key(
    req: SelfKeyRequest,
    request: Request,
    db: Session = Depends(get_db),
    # SEC-06 deploy-3: this stays on require_api_key() (no required scope) FOR NOW.
    # Gating it behind a dedicated scope is the OWNER's step and MUST come AFTER
    # dsec-hub's LIVE DSEC_API_KEY row is granted that scope, or the hub loses the
    # ability to mint tokens. The containment landing here (owner-label validation,
    # cascade revocation, per-caller cap) is safe without that grant.
    caller: APIKey = Depends(require_api_key()),
) -> CreateKeyResponse:
    """Mint a key on behalf of a dashboard user (called by dsec-app's server).

    Authenticated by dsec-app's service API key. The security boundary is that
    the minted key's scopes must be a SUBSET of the calling key's scopes — so
    even if the service key leaked, it could never mint a key more powerful than
    itself. dsec-app does the per-role authorisation (which scopes a given user
    may request) before calling this; this endpoint is the second gate.
    """
    limiter.check_request(db, key_id=caller.id, ip=client_ip(request))
    # SEC-07d: re-read the calling key UNDER LOCK and re-check it's still active
    # before minting. require_api_key() read it earlier without a lock, and the
    # limiter commit above EXPIRED that ORM object; a concurrent _cascade_revoke of
    # the parent could commit between then and the insert below, leaving a live
    # child of a revoked parent.
    #
    # Use Session.refresh(..., with_for_update=True), NOT a bare
    # select(...).with_for_update(): the latter returns the SAME identity-map object
    # and does not overwrite already-loaded attributes, so a stale `revoked=False`
    # (reloaded unlocked the moment we touch caller.id) would survive the lock and
    # the check would wrongly pass. refresh() forces a locked re-SELECT that
    # repopulates the row, so this mint serialises against the revoke (which locks
    # the same row) and sees the revocation. FOR UPDATE is a no-op on SQLite.
    db.refresh(caller, with_for_update=True)
    if caller.revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="calling key is no longer valid",
        )
    # SEC-06 deploy-3: the owner label is trusted by the hub's list/revoke UI, so
    # validate its shape (appuser:<id>) rather than accepting any string. Do NOT
    # remove the field — dropping it breaks that UI (api-tokens.ts:98,126).
    if not _OWNER_RE.fullmatch(req.owner or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="owner must match 'appuser:<id>'",
        )
    # SEC-06 deploy-3: bound how many outstanding (non-revoked) child keys one
    # caller may hold, so a leaked service key can't mint an unbounded persistent
    # fleet. The per-minute throttle is not a key-count limit.
    outstanding = db.execute(
        select(func.count())
        .select_from(APIKey)
        .where(APIKey.parent_key_id == caller.id, APIKey.revoked.is_(False))
    ).scalar_one()
    if outstanding >= settings.SELF_KEY_MAX_OUTSTANDING:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"outstanding child-key limit reached "
                f"({settings.SELF_KEY_MAX_OUTSTANDING}); revoke unused keys first"
            ),
        )
    requested = set(req.scopes)
    invalid = requested - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid scope(s): {sorted(invalid)}; allowed: {sorted(VALID_SCOPES)}",
        )
    # Scope-algebra-aware subset check: a broad caller (e.g. legacy "write") may
    # mint a narrower per-module key (e.g. "write:sponsors"), but never the other
    # way round. So even a leaked service key can't mint something more powerful.
    granted = frozenset(caller.scopes or [])
    escalated = {s for s in requested if not has_scope(granted, s)}
    if escalated:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"cannot mint scope(s) the calling key lacks: {sorted(escalated)}",
        )
    gen = generate_key()
    row = APIKey(
        name=req.name,
        prefix=gen.prefix,
        key_hash=gen.key_hash,
        scopes=sorted(requested),
        created_by=req.owner,
        expires_at=default_key_expiry(),  # SEC-07c: new keys expire in 180 days
        parent_key_id=caller.id,  # SEC-06 deploy-3: lineage for cascade revocation
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CreateKeyResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=row.scopes,
        raw_key=gen.raw_key,
    )


@router.get("/keys", response_model=list[KeyInfo])
def list_keys(
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> list[KeyInfo]:
    rows = db.execute(select(APIKey).order_by(APIKey.created_at.desc())).scalars().all()
    return [KeyInfo.model_validate(r) for r in rows]


@router.post("/keys/{key_id}/revoke", response_model=KeyInfo)
def revoke_key(
    key_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> KeyInfo:
    # Lock the root row (FOR UPDATE) before cascading, so a concurrent mint of a
    # child serialises against the revoke (SEC-07d race). No-op on SQLite.
    row = db.execute(
        select(APIKey).where(APIKey.id == key_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    # SEC-06 deploy-3: cascade to every descendant minted via /keys/self, so a
    # child (or grandchild) key can't outlive the revocation of its parent.
    _cascade_revoke(db, row)
    db.commit()
    db.refresh(row)
    return KeyInfo.model_validate(row)


class RevokeForOwnerRequest(BaseModel):
    """A dashboard-user revoking one of their own self-service keys, proxied by
    dsec-hub. ``owner`` is the same opaque ``appuser:<id>`` label that minting
    stamped onto ``created_by`` (see SelfKeyRequest)."""

    key_id: int
    owner: str


@router.post("/keys/revoke-for-owner", response_model=KeyInfo)
def revoke_key_for_owner(
    req: RevokeForOwnerRequest,
    request: Request,
    db: Session = Depends(get_db),
    # SEC-07d: require the `write:keys` scope. It is a per-module scope, so blanket
    # "write" already satisfies it (has_scope) — the hub's service key holds blanket
    # write, so NO live-key grant is needed and the revoke path never fails closed
    # into "can't revoke". What it EXCLUDES is the point: a narrow service key
    # (write:games, ingest-only) or a read-only admin key does NOT carry write, so a
    # leaked narrow key can't reach this endpoint. See VALID_SCOPES.
    caller: APIKey = Depends(require_api_key("write:keys")),
) -> KeyInfo:
    """Revoke a dashboard user's own self-service key, cascading to its children.

    dsec-hub used to revoke a user's token by writing ``api_key.revoked`` straight
    into Neon (api-tokens.ts), which bypassed the parent->child cascade that
    ``/keys/{id}/revoke`` performs — so revoking a parent key silently left its
    child keys alive and usable (SEC-07d). Routing the hub's revoke through here
    makes the cascade run every time.

    Containment (three independent gates, so none alone is load-bearing):
      1. SCOPE — the caller must hold ``write:keys`` (satisfied by blanket write, so
         no owner grant, but NOT by narrow read-only/games/ingest keys). This stops a
         leaked narrow service key from reaching the endpoint at all.
      2. CALLER — the caller must not be a dashboard user's own self-service key.
         ``require_api_key`` accepts any key with the scope, and a user MAY mint a
         blanket-``write`` token (which satisfies gate 1), so without this a user
         could revoke ANOTHER user's key just by naming their ``appuser:<id>`` label
         and enumerating ids (round-1 finding). A self-service key is exactly one
         whose ``created_by`` is an ``appuser:<id>`` label; reject those as callers.
      3. TARGET — the target's ``created_by`` must equal the passed ``appuser:<id>``
         owner, which (being an appuser label) also refuses to touch admin/service
         keys. A missing key and an owner mismatch return the SAME 404.

    We deliberately do NOT tie containment to ``parent_key_id`` lineage. The lineage
    migration left every pre-existing key ``parent_key_id=NULL``, a user may mint a
    grandchild, and the service key may be rotated — all of which dsec-hub still
    lists. A direct-parent check would 404 those and, treated as benign, silently
    leave live tokens unrevocable (round-2 finding). The cascade still walks
    parent_key_id downward from the target, so descendants are revoked regardless.
    """
    # Bound enumeration: even a legitimate write-capable key can't scan id/owner
    # pairs unthrottled (the per-key limiter, same as /keys/self).
    limiter.check_request(db, key_id=caller.id, ip=client_ip(request))
    if not _OWNER_RE.fullmatch(req.owner or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="owner must match 'appuser:<id>'",
        )
    if _OWNER_RE.fullmatch(caller.created_by or ""):
        # The caller is itself a dashboard user's self-service key — it may never
        # revoke keys on another user's behalf. (created_by is None/username for the
        # service and admin keys that legitimately call this.)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a self-service key cannot revoke keys",
        )
    # Lock the target row before the ownership check and the cascade, so a
    # concurrent mint of a child serialises against this revoke (SEC-07d race).
    row = db.execute(
        select(APIKey).where(APIKey.id == req.key_id).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.created_by != req.owner:
        # Identical 404 for missing and wrong-owner: reveal nothing about keys the
        # named owner doesn't hold.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    _cascade_revoke(db, row)
    db.commit()
    db.refresh(row)
    return KeyInfo.model_validate(row)


class OAuthClientInfo(BaseModel):
    id: int
    client_id: str
    client_name: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)
    scope: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    first_seen_ip: str | None = None
    revoked: bool

    model_config = {"from_attributes": True}


@router.get("/oauth/clients", response_model=list[OAuthClientInfo])
def list_oauth_clients(
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> list[OAuthClientInfo]:
    """List every registered OAuth client (newest first), incl. revoked ones, so
    the committee can see and cull self-registered MCP clients (NEW-APIROUTERS-04).
    """
    rows = db.execute(
        select(OAuthClient).order_by(OAuthClient.created_at.desc())
    ).scalars().all()
    return [OAuthClientInfo.model_validate(r) for r in rows]


@router.post("/oauth/clients/{client_id}/revoke", response_model=OAuthClientInfo)
def revoke_oauth_client(
    client_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> OAuthClientInfo:
    """Revoke a client so it disappears from every OAuth read path. Idempotent —
    revoking an already-revoked client is a no-op. Queries the table directly (not
    service.get_client, which now hides revoked clients)."""
    row = db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")
    row.revoked = True
    # Kill every access/refresh token already issued for this client IN THE SAME
    # transaction, so revocation is immediate rather than lingering until each
    # token's ~1h expiry (NEW-APIROUTERS-04).
    oauth_service.revoke_client_tokens(db, client_id=client_id)
    db.commit()
    db.refresh(row)
    return OAuthClientInfo.model_validate(row)


@router.get("/cron/prune-rate-limit")
def cron_prune_rate_limit(
    db: Session = Depends(get_db),
    _: None = Depends(require_cron_secret),
) -> dict:
    """Nightly (Vercel Cron): delete rate_limit rows older than two days (OPS-05).

    The table grows one permanent row per (IP, minute) for every unauthenticated
    request across the 17 public /website routes and was never pruned. Two days
    is comfortably older than any live window: minute buckets are seconds wide and
    the per-day 'trigger'/global rows key off the START of the current UTC day, so
    a 48-hour cutoff never touches a counter still in use.

    Authorised by CRON_SECRET (Authorization: Bearer <secret>), exactly like the
    games draw cron — not a new scheduling mechanism. The one-time manual prune of
    the historical backlog is a separate OWNER step, done by hand against Neon.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    deleted = db.execute(
        delete(RateLimit).where(RateLimit.window_start < cutoff)
    ).rowcount
    db.commit()
    return {"deleted": int(deleted or 0), "cutoff": cutoff.isoformat()}


@router.get("/archive/export")
def archive_export(
    db: Session = Depends(get_db),
    _: str = Depends(require_basic_auth),
) -> dict:
    """Portable, secret-free workspace manifest for end-of-year handover.

    Basic-auth protected (dashboard owner). Returns the deployment's schema,
    per-table row counts, env var NAMES (never values), and the API key list
    (never hashes) so the next committee can re-deploy to a fresh Vercel + Neon
    without archaeology. Emits no secret and no student PII — a genuine data
    snapshot is intentionally out of scope (use `pg_dump` with DB creds). The
    export itself is audited.
    """
    bundle = build_export_bundle(db)
    log_event(
        db,
        source="admin",
        action="archive_export",
        classification="generated",
        payload={
            "tables": len(bundle["schema"]),
            "env_vars": len(bundle["env_vars"]),
            "api_keys": len(bundle["api_keys"]),
            "alembic_revision": bundle["alembic_revision"],
        },
    )
    return bundle
