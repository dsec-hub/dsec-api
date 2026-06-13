"""Admin API — internal-only API key management. Basic-auth protected.

Keys are created/listed/revoked here, never self-serve. Raw key is shown exactly
once at creation. The manual Notion sync trigger lives in the events router,
also mounted under /admin.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_basic_auth
from app.core.apikeys import VALID_SCOPES, generate_key
from app.db import get_db
from app.models import APIKey

router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])


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
