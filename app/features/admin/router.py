"""Admin API — internal-only API key management.

Admin key management (`/keys`) is basic-auth protected. The raw key is shown
exactly once at creation. `/keys/self` is a narrow exception: a dashboard-user
self-service mint, authenticated by dsec-app's service API key and constrained to
that key's own scopes. The manual Notion sync trigger lives in the events router,
also mounted under /admin.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_basic_auth
from app.core.apikeys import VALID_SCOPES, generate_key, require_api_key
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.db import get_db
from app.models import APIKey

router = APIRouter()


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
    requested = set(req.scopes)
    invalid = requested - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid scope(s): {sorted(invalid)}; allowed: {sorted(VALID_SCOPES)}",
        )
    granted = set(caller.scopes or [])
    escalated = requested - granted
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
    row = db.get(APIKey, key_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    row.revoked = True  # soft-revoke; never hard-delete (keep the audit trail)
    db.commit()
    db.refresh(row)
    return KeyInfo.model_validate(row)
